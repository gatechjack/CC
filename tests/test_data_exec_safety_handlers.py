"""Tests for DataExecAgent safety handlers + observer flatten trigger.

Session N defensive scaffolding (Stage-1 sub-task; branch
`bitunix-orderpath-safety-2026-05-29`). Covers:

- Mode-mismatch consumer in `DataExecAgent.place()` — catches
  `BitunixPositionModeMismatch` raised by the broker, audits +
  telegrams + re-raises. The broker's own `_halt_new_orders` self-latch
  is the halt mechanism (per the Phase 2a sub-diagnostic decision); the
  consumer's job is the response side, not the halt itself.
- `DataExecAgent.flatten_division()` — happy/idempotent/failure/non-bitunix
  paths. Verifies via broker snapshot (positions=0), not just "function
  returned without raising".
- Observer's `_maybe_flatten_on_risk_verdict` helper — fires
  `flatten_division` when a risk verdict signals `flatten_account=True`.

Test discipline (operator-mandated, per
`[[telegram-audit-success-is-confirmed-delivery]]`): downstream-effect
verification, not just "the handler ran". Audit rows are re-read via an
independent sqlite3 path (not the LoggerAgent's own connection); broker
halt-latch is asserted; telegram `push()` return-bool is asserted;
exceptions are asserted to re-raise.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.bitunix_exceptions import BitunixPositionModeMismatch
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import Position, ProposedOrder


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path) -> str:
    path = tmp_path / "safety_handlers.db"
    url = f"sqlite:///{path}"
    init_db(url)
    return url


@pytest.fixture
def logger_agent(db_url) -> LoggerAgent:
    return LoggerAgent(db_url=db_url)


# ── fakes ─────────────────────────────────────────────────────────────


class FakePushNotifier:
    """Mimics `telegram_bot.push(text, *, audit_path, audit_context) -> bool`.
    Returns the configured bool; records every call so tests can assert on
    text content + audit_path."""

    def __init__(self, return_value: bool = True) -> None:
        self.calls: list[dict] = []
        self.return_value = return_value

    async def push(
        self,
        text: str,
        *,
        audit_path: str = "other",
        audit_context: dict | None = None,
    ) -> bool:
        self.calls.append({
            "text": text,
            "audit_path": audit_path,
            "audit_context": audit_context or {},
        })
        return self.return_value


class FakeBitunixBrokerMismatch:
    """Broker that mimics the live-engine branch's mode-mismatch flow:
    self-latches `_halt_new_orders=True` BEFORE raising the exception."""
    name = "bitunix_futures"
    paper = False

    def __init__(self, current_mode: str = "HEDGE") -> None:
        self._halt_new_orders = False
        self._halt_reason: str | None = None
        self._current_mode = current_mode

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass

    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="bitunix-fake", equity=1000.0, buying_power=1000.0,
            cash=1000.0, positions=[],
        )

    async def quote(self, symbol: str) -> float: return 0.0

    async def place_order(self, order):
        # Mirror the live broker: latch FIRST, then raise (the consumer
        # then *confirms* the latch was set; never re-sets it).
        self._halt_new_orders = True
        self._halt_reason = f"position_mode_mismatch:{self._current_mode}"
        raise BitunixPositionModeMismatch(current=self._current_mode)

    async def cancel_order(self, oid: str) -> bool: return False


class FakeBitunixBrokerFlatten:
    """Broker with a `flatten()` method for flatten_division tests."""
    name = "bitunix_futures"
    paper = False

    def __init__(
        self,
        *,
        positions: list | None = None,
        flatten_raises: Exception | None = None,
        flatten_succeeds: bool = True,
    ) -> None:
        self._positions = list(positions) if positions else []
        self.flatten_calls: list[str | None] = []
        self.flatten_raises = flatten_raises
        self.flatten_succeeds = flatten_succeeds
        self._halt_new_orders = False

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass

    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="bitunix-fake", equity=1000.0, buying_power=1000.0,
            cash=1000.0, positions=list(self._positions),
        )

    async def quote(self, symbol: str) -> float: return 0.0
    async def place_order(self, order): raise NotImplementedError
    async def cancel_order(self, oid: str) -> bool: return False

    async def flatten(self, symbol: str | None = None) -> dict:
        self.flatten_calls.append(symbol)
        if self.flatten_raises:
            raise self.flatten_raises
        if self.flatten_succeeds:
            self._positions = []  # simulate successful flatten
        return {"halted": True, "cancel_all_orders": {}, "close_all_position": {}}


class FakeNonBitunixBroker:
    """No `flatten` attribute — covers the graceful-degrade path."""
    name = "fake-other"
    paper = False

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="other", equity=100.0, buying_power=100.0,
            cash=100.0, positions=[],
        )
    async def quote(self, symbol: str) -> float: return 0.0
    async def place_order(self, order): raise NotImplementedError
    async def cancel_order(self, oid: str) -> bool: return False
    # NO `flatten` method


def _make_order() -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side="buy",
        qty=0.001, order_type="market",
        extra={"leverage": 8, "tier": "PREMIUM"},
    )


def _last_audit_event_by_kind(db_url: str, kind: str) -> dict | None:
    """Independent-read-path verifier — uses raw sqlite3, not LoggerAgent's
    own connection. Per `[[reference_real_audit_row_raw_sqlite3]]`."""
    path = db.resolve_db_path(db_url)
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, actor, kind, payload_json FROM audit_event "
            "WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"], "actor": row["actor"], "kind": row["kind"],
            "payload": json.loads(row["payload_json"]),
        }
    finally:
        conn.close()


def _open_btc_position() -> Position:
    return Position(
        account="bitunix-fake", symbol="BTCUSDT", qty=0.001,
        avg_price=65000.0, opened_ts="2026-05-29T20:00:00",
    )


# ── mode-mismatch consumer ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mode_mismatch_audit_row_written_and_rereadable(logger_agent, db_url):
    broker = FakeBitunixBrokerMismatch(current_mode="HEDGE")
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixPositionModeMismatch):
        await de.place(_make_order(), division="bitunix_futures")

    row = _last_audit_event_by_kind(db_url, "position_mode_mismatch_detected")
    assert row is not None, "expected position_mode_mismatch_detected audit row"
    assert row["payload"]["current"] == "HEDGE"
    assert row["payload"]["expected"] == "ONE_WAY"
    assert row["payload"]["division"] == "bitunix_futures"
    assert row["payload"]["broker_class"] == "FakeBitunixBrokerMismatch"
    assert row["actor"] == "data_exec"


@pytest.mark.asyncio
async def test_mode_mismatch_broker_halt_latch_confirmed(logger_agent):
    broker = FakeBitunixBrokerMismatch()
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixPositionModeMismatch):
        await de.place(_make_order(), division="bitunix_futures")

    # Broker's own self-latch was set BEFORE raising.
    assert broker._halt_new_orders is True


@pytest.mark.asyncio
async def test_mode_mismatch_telegram_push_called_with_safety_alert(logger_agent):
    broker = FakeBitunixBrokerMismatch(current_mode="HEDGE")
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixPositionModeMismatch):
        await de.place(_make_order(), division="bitunix_futures")

    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call["audit_path"] == "safety_alert"
    assert "HEDGE" in call["text"] or "mismatch" in call["text"].lower()


@pytest.mark.asyncio
async def test_mode_mismatch_telegram_failure_writes_failure_audit_but_continues(
    logger_agent, db_url,
):
    broker = FakeBitunixBrokerMismatch()
    notifier = FakePushNotifier(return_value=False)  # delivery failed
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixPositionModeMismatch):
        await de.place(_make_order(), division="bitunix_futures")

    # Primary safety audit still landed (telegram failure must NOT block it).
    assert _last_audit_event_by_kind(db_url, "position_mode_mismatch_detected") is not None
    # AND a telegram_notification_failed row was written.
    assert _last_audit_event_by_kind(db_url, "telegram_notification_failed") is not None


@pytest.mark.asyncio
async def test_mode_mismatch_no_notifier_still_audits_and_re_raises(logger_agent, db_url):
    broker = FakeBitunixBrokerMismatch()
    de = DataExecAgent(logger_agent, safety_notifier=None)  # no notifier wired
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixPositionModeMismatch):
        await de.place(_make_order(), division="bitunix_futures")

    assert _last_audit_event_by_kind(db_url, "position_mode_mismatch_detected") is not None


@pytest.mark.asyncio
async def test_unrelated_exception_bubbles_through_unchanged(logger_agent, db_url):
    """Non-mode-mismatch broker exceptions are NOT caught — bubble cleanly."""
    class BrokerRaisesValueError:
        name = "bx"
        paper = False
        _halt_new_orders = False
        async def connect(self): pass
        async def disconnect(self): pass
        async def snapshot(self):
            return AccountSnapshot(
                account="x", equity=0, buying_power=0, cash=0, positions=[],
            )
        async def quote(self, s): return 0.0
        async def place_order(self, order): raise ValueError("not a mode mismatch")
        async def cancel_order(self, oid): return False

    notifier = FakePushNotifier()
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bx", BrokerRaisesValueError())

    with pytest.raises(ValueError, match="not a mode mismatch"):
        await de.place(_make_order(), division="bx")

    assert _last_audit_event_by_kind(db_url, "position_mode_mismatch_detected") is None
    assert notifier.calls == []


# ── flatten_division ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flatten_division_happy_path(logger_agent, db_url):
    broker = FakeBitunixBrokerFlatten(
        positions=[_open_btc_position()], flatten_succeeds=True,
    )
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    await de.flatten_division("bitunix_futures")

    assert len(broker.flatten_calls) == 1
    row = _last_audit_event_by_kind(db_url, "flatten_account_executed")
    assert row is not None
    assert row["payload"]["division"] == "bitunix_futures"
    assert row["payload"]["positions_before"] == 1
    assert row["payload"]["positions_after"] == 0
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["audit_path"] == "safety_alert"


@pytest.mark.asyncio
async def test_flatten_division_idempotent_already_flat(logger_agent, db_url):
    broker = FakeBitunixBrokerFlatten(positions=[])  # already flat
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    await de.flatten_division("bitunix_futures")

    assert broker.flatten_calls == []  # no broker call when already flat
    row = _last_audit_event_by_kind(db_url, "flatten_account_noop_already_flat")
    assert row is not None
    assert row["payload"]["division"] == "bitunix_futures"


@pytest.mark.asyncio
async def test_flatten_division_failure_positions_remain(logger_agent, db_url):
    broker = FakeBitunixBrokerFlatten(
        positions=[_open_btc_position()], flatten_succeeds=False,
    )
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(RuntimeError):
        await de.flatten_division("bitunix_futures")

    row = _last_audit_event_by_kind(db_url, "flatten_account_failed")
    assert row is not None
    assert row["payload"]["positions_after"] >= 1  # didn't clear


@pytest.mark.asyncio
async def test_flatten_division_failure_flatten_raises(logger_agent, db_url):
    broker = FakeBitunixBrokerFlatten(
        positions=[_open_btc_position()],
        flatten_raises=RuntimeError("network down"),
    )
    notifier = FakePushNotifier(return_value=True)
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("bitunix_futures", broker)

    with pytest.raises(RuntimeError, match="network down"):
        await de.flatten_division("bitunix_futures")

    assert _last_audit_event_by_kind(db_url, "flatten_account_failed") is not None


@pytest.mark.asyncio
async def test_flatten_division_non_bitunix_graceful_degrade(logger_agent, db_url):
    broker = FakeNonBitunixBroker()
    notifier = FakePushNotifier()
    de = DataExecAgent(logger_agent, safety_notifier=notifier)
    de.register_broker("other_div", broker)

    await de.flatten_division("other_div")  # no error

    row = _last_audit_event_by_kind(db_url, "flatten_account_skipped_no_flatten_method")
    assert row is not None
    assert row["payload"]["division"] == "other_div"


# ── observer flatten trigger ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_observer_maybe_flatten_trigger_when_verdict_flag_true(tmp_path):
    """Observer's `_maybe_flatten_on_risk_verdict` awaits flatten_division
    when verdict.flatten_account=True."""
    from trading_corp.agents.divisions.bitunix_futures_observer import (
        BitunixFuturesObserver,
    )
    from trading_corp.agents.risk import RiskVerdict

    mock_data_exec = MagicMock()
    mock_data_exec.flatten_division = AsyncMock()

    observer = BitunixFuturesObserver(
        db_url=f"sqlite:///{tmp_path}/observer.db",
        data_exec=mock_data_exec,
    )

    verdict = RiskVerdict(
        verdict="reject", reason="dd",
        flatten_account=True, halt_strategy=False,
    )

    await observer._maybe_flatten_on_risk_verdict(verdict)
    mock_data_exec.flatten_division.assert_awaited_once_with("bitunix_futures")


@pytest.mark.asyncio
async def test_observer_maybe_flatten_trigger_skipped_when_flag_false(tmp_path):
    from trading_corp.agents.divisions.bitunix_futures_observer import (
        BitunixFuturesObserver,
    )
    from trading_corp.agents.risk import RiskVerdict

    mock_data_exec = MagicMock()
    mock_data_exec.flatten_division = AsyncMock()

    observer = BitunixFuturesObserver(
        db_url=f"sqlite:///{tmp_path}/observer.db",
        data_exec=mock_data_exec,
    )

    verdict = RiskVerdict(
        verdict="approve", reason="ok",
        flatten_account=False, halt_strategy=False,
    )

    await observer._maybe_flatten_on_risk_verdict(verdict)
    mock_data_exec.flatten_division.assert_not_awaited()
