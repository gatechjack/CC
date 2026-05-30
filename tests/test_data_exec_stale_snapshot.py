"""Tests for `DataExecAgent._handle_stale_snapshot` + the defense-in-depth
`_assert_snapshot_fresh()` re-check inside `DataExecAgent.place()`.

Gate (a) sub-item 2 (2026-05-30) — mirrors the test pattern in
`test_data_exec_safety_handlers.py` for the position-mode-mismatch consumer.
Verifies downstream effects (audit row re-read, telegram push-bool, halt
latch, exception re-raise) not just "handler ran".
"""
from __future__ import annotations

from typing import Any

import pytest

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.bitunix_exceptions import BitunixStaleSnapshot
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import ProposedOrder


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path) -> str:
    path = tmp_path / "stale_snapshot.db"
    url = f"sqlite:///{path}"
    init_db(url)
    return url


@pytest.fixture
def logger_agent(db_url) -> LoggerAgent:
    return LoggerAgent(db_url=db_url)


# ── fakes ────────────────────────────────────────────────────────────────


class FakePushNotifier:
    """Mirrors safety_notifier contract: `push(text, *, audit_path, audit_context) -> bool`."""

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


class FakeStaleBitunixBroker:
    """Broker that mimics the broker's self-latch-then-raise contract for
    snapshot staleness: latches `_halt_new_orders=True` BEFORE raising."""
    name = "bitunix_futures"
    paper = False

    def __init__(self, *, age_s: float = 125.0, threshold_s: float = 60.0) -> None:
        self._halt_new_orders = False
        self._halt_reason: str | None = None
        self._age_s = age_s
        self._threshold_s = threshold_s

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="bitunix-fake", equity=1000.0, buying_power=1000.0,
            cash=1000.0, positions=[],
        )
    async def quote(self, symbol: str) -> float: return 0.0

    async def _assert_snapshot_fresh(self) -> None:
        self._halt_new_orders = True
        self._halt_reason = f"snapshot_stale:{self._age_s:.1f}s"
        raise BitunixStaleSnapshot(age_s=self._age_s, threshold_s=self._threshold_s)

    async def place_order(self, order):  # should never be reached
        raise AssertionError("place_order should not be called when stale")

    async def cancel_order(self, oid: str) -> bool: return False


class FakeHealthyBitunixBroker:
    """Broker whose `_assert_snapshot_fresh` is a no-op (healthy)."""
    name = "bitunix_futures"
    paper = False

    def __init__(self) -> None:
        self._halt_new_orders = False
        self._halt_reason: str | None = None
        self.placed: list[Any] = []

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="bitunix-fake", equity=1000.0, buying_power=1000.0,
            cash=1000.0, positions=[],
        )
    async def quote(self, symbol: str) -> float: return 0.0

    async def _assert_snapshot_fresh(self) -> None:
        return  # no-op — healthy

    async def place_order(self, order):
        self.placed.append(order)
        from trading_corp.persistence.models import FillEvent
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            qty=float(order.qty), price=100.0,
            ts="2026-05-30T00:00:00+00:00", venue="bitunix_futures",
        )

    async def cancel_order(self, oid: str) -> bool: return False


def _order() -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures",
        symbol="BTC/USDT.P",
        side="buy",
        qty=0.001,
        order_type="market",
    )


def _row_count(db_url: str, kind: str) -> int:
    with db.connect(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_event WHERE kind = ?", (kind,),
        ).fetchone()[0]


def _row_payload(db_url: str, kind: str) -> dict:
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind = ? "
            "ORDER BY id DESC LIMIT 1", (kind,),
        ).fetchone()
    import json
    return json.loads(row["payload_json"]) if row else {}


# ── data_exec.place() defense-in-depth re-check ──────────────────────────


@pytest.mark.asyncio
async def test_place_with_stale_snapshot_raises_audits_and_telegrams(
    db_url, logger_agent,
):
    push = FakePushNotifier(return_value=True)
    agent = DataExecAgent(logger_agent, safety_notifier=push)
    broker = FakeStaleBitunixBroker(age_s=125.0, threshold_s=60.0)
    agent.register_broker("bitunix_futures", broker)

    with pytest.raises(BitunixStaleSnapshot) as ei:
        await agent.place(_order(), division="bitunix_futures")

    # Exception carries diagnostics
    assert ei.value.age_s == pytest.approx(125.0)
    assert ei.value.threshold_s == pytest.approx(60.0)

    # Broker self-latched before raising
    assert broker._halt_new_orders is True

    # Audit row written + content
    assert _row_count(db_url, "snapshot_stale_halt") == 1
    payload = _row_payload(db_url, "snapshot_stale_halt")
    assert payload["age_s"] == pytest.approx(125.0)
    assert payload["threshold_s"] == pytest.approx(60.0)
    assert payload["division"] == "bitunix_futures"
    assert payload["broker_class"] == "FakeStaleBitunixBroker"
    assert payload["broker_halt_latched"] is True

    # Telegram pushed
    assert len(push.calls) == 1
    call = push.calls[0]
    assert call["audit_path"] == "safety_alert"
    assert "stale" in call["text"].lower()
    assert call["audit_context"]["kind"] == "snapshot_stale_halt"


@pytest.mark.asyncio
async def test_place_with_healthy_broker_proceeds_normally(
    db_url, logger_agent,
):
    push = FakePushNotifier(return_value=True)
    agent = DataExecAgent(logger_agent, safety_notifier=push)
    broker = FakeHealthyBitunixBroker()
    agent.register_broker("bitunix_futures", broker)

    fill = await agent.place(_order(), division="bitunix_futures")
    assert fill.venue == "bitunix_futures"
    assert len(broker.placed) == 1
    # No stale-snapshot artifacts on the healthy path.
    assert _row_count(db_url, "snapshot_stale_halt") == 0
    assert not push.calls


@pytest.mark.asyncio
async def test_place_non_bitunix_broker_skips_staleness_check(
    db_url, logger_agent,
):
    """A broker that doesn't expose `_assert_snapshot_fresh` (e.g.
    coinbase/fidelity) is silently skipped — duck-typed check, no halt."""
    push = FakePushNotifier(return_value=True)
    agent = DataExecAgent(logger_agent, safety_notifier=push)

    class FakeOtherBroker:
        name = "coinbase_spot"
        paper = False
        async def connect(self): pass
        async def disconnect(self): pass
        async def snapshot(self):
            return AccountSnapshot(
                account="cb-fake", equity=500.0, buying_power=500.0,
                cash=500.0, positions=[],
            )
        async def quote(self, s): return 0.0
        async def place_order(self, order):
            from trading_corp.persistence.models import FillEvent
            return FillEvent(
                order_id=order.id, symbol=order.symbol, side=order.side,
                qty=float(order.qty), price=99.0,
                ts="2026-05-30T00:00:00+00:00", venue="coinbase_spot",
            )
        async def cancel_order(self, oid): return False

    broker = FakeOtherBroker()
    agent.register_broker("coinbase_spot", broker)
    order = ProposedOrder(
        strategy="otter", symbol="BTC/USD", side="buy", qty=0.01,
        order_type="market",
    )
    fill = await agent.place(order, division="coinbase_spot")
    assert fill.venue == "coinbase_spot"
    # No staleness machinery fired.
    assert _row_count(db_url, "snapshot_stale_halt") == 0


# ── _handle_stale_snapshot direct call ──────────────────────────────────


@pytest.mark.asyncio
async def test_handle_stale_snapshot_telegram_failure_audits_but_does_not_block(
    db_url, logger_agent,
):
    push = FakePushNotifier(return_value=False)  # simulate push failure
    agent = DataExecAgent(logger_agent, safety_notifier=push)
    broker = FakeStaleBitunixBroker(age_s=200.0, threshold_s=60.0)

    exc = BitunixStaleSnapshot(age_s=200.0, threshold_s=60.0)
    # Don't go through place(); call the handler directly.
    await agent._handle_stale_snapshot(exc, _order(), "bitunix_futures", broker)

    # Primary audit still landed.
    assert _row_count(db_url, "snapshot_stale_halt") == 1
    # Telegram-failure audit captured the dropped push.
    assert _row_count(db_url, "telegram_notification_failed") == 1


@pytest.mark.asyncio
async def test_handle_stale_snapshot_without_notifier_still_audits(
    db_url, logger_agent,
):
    agent = DataExecAgent(logger_agent, safety_notifier=None)
    broker = FakeStaleBitunixBroker(age_s=200.0, threshold_s=60.0)
    exc = BitunixStaleSnapshot(age_s=200.0, threshold_s=60.0)

    await agent._handle_stale_snapshot(exc, _order(), "bitunix_futures", broker)

    # Audit row still written.
    assert _row_count(db_url, "snapshot_stale_halt") == 1
    # No telegram path attempted.


@pytest.mark.asyncio
async def test_handle_stale_snapshot_never_snapshotted_age_renders_distinct(
    db_url, logger_agent,
):
    push = FakePushNotifier(return_value=True)
    agent = DataExecAgent(logger_agent, safety_notifier=push)
    broker = FakeStaleBitunixBroker()
    exc = BitunixStaleSnapshot(age_s=float("inf"), threshold_s=60.0)

    await agent._handle_stale_snapshot(exc, _order(), "bitunix_futures", broker)

    # Audit row's age_s is None for the never-snapshotted case (inf is not
    # JSON-serializable as-is; the handler normalizes to None).
    payload = _row_payload(db_url, "snapshot_stale_halt")
    assert payload["age_s"] is None
    # Telegram text shows "never (no successful snapshot yet)".
    assert "never" in push.calls[0]["text"]
