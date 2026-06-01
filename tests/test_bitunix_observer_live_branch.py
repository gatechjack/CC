"""Tests for the live-mode branch INSIDE the canonical placement helper.

Commit 3 of Stage-1 Session N+1. Validates the structural fork between
paper-mode (commit 1 byte-identical) and live-mode (data_exec.place
routing). Hard contracts:

- Paper-mode NEVER calls `data_exec.place()` under ANY condition.
  This is the load-bearing isolation property.
- Live-mode + auto_execute=true routes through `data_exec.place()`.
- Live-mode + auto_execute=false falls back to paper-write behavior
  (auto_execute is the runtime kill switch).
- Live entries are `reduce_only=False` (explicit, not by accident).
- `live_order_placed` audit fires BEFORE `data_exec.place()` (intent
  capture) and is re-read for confirmed-delivery.
- `live_order_rejected` audit fires on exception (re-read confirmed).
- Telegram pushed with `(live)` suffix; push-bool checked; False →
  `telegram_notification_failed` audit.
- Daily-risk accrues on ATTEMPT (not only on success).
- Path C (Phase 3): live path writes `paper_trade_record` tagged
  `extra["execution_mode"]="live"` + `extra["broker_order_id"]` so the
  existing replay loop tracks the open live position. Reverses the
  N+1 commit-3 "no paper_trade_record on live" decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions import bitunix_futures_observer as obs_mod
from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import FillEvent
from trading_corp.persistence import db


# ─── fixtures ───────────────────────────────────────────────────────────


def _logger_agent(db_url: str) -> LoggerAgent:
    """Real LoggerAgent backed by the same DB the observer writes paper
    records to. We need real DB writes so the audit re-read confirmation
    can actually find the row."""
    return LoggerAgent(db_url=db_url)


def _make_observer_paper(tmp_path: Path):
    db_path = tmp_path / "live_branch_paper.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    risk_agent = MagicMock()
    risk_verdict = MagicMock()
    risk_verdict.verdict = "approve"
    risk_verdict.reason = "ok"
    risk_verdict.new_qty = None
    risk_agent.evaluate.return_value = risk_verdict

    snap = MagicMock()
    snap.equity = 5_000.0
    snap.positions = []
    broker = MagicMock()
    broker.snapshot = AsyncMock(return_value=snap)
    # gate (a) sub-item 2: observer's pre-trade gate awaits this. Healthy no-op.
    broker._assert_snapshot_fresh = AsyncMock()

    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.flatten_division = AsyncMock()
    data_exec.place = AsyncMock(return_value=FillEvent(
        order_id="x", symbol="BTCUSDT", side="buy",
        qty=0.001, price=80_000.0, ts="2026-05-29T12:00:00+00:00",
        venue="bitunix",
    ))

    logger_agent = _logger_agent(db_url)
    telegram_channel = MagicMock()
    telegram_channel.push = AsyncMock(return_value=True)

    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram_channel,
        execution_mode="paper",  # explicit paper
    )
    return obs, data_exec, logger_agent, telegram_channel


def _make_observer_live(tmp_path: Path, monkeypatch, *, place_raises=None,
                        telegram_returns=True, auto_execute=True):
    db_path = tmp_path / "live_branch.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    risk_agent = MagicMock()
    risk_verdict = MagicMock()
    risk_verdict.verdict = "approve"
    risk_verdict.reason = "ok"
    risk_verdict.new_qty = None
    risk_agent.evaluate.return_value = risk_verdict

    snap = MagicMock()
    snap.equity = 5_000.0
    snap.positions = []
    broker = MagicMock()
    broker.snapshot = AsyncMock(return_value=snap)
    # gate (a) sub-item 2: observer's pre-trade gate awaits this. Healthy no-op.
    broker._assert_snapshot_fresh = AsyncMock()

    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.flatten_division = AsyncMock()
    if place_raises is not None:
        data_exec.place = AsyncMock(side_effect=place_raises)
    else:
        data_exec.place = AsyncMock(return_value=FillEvent(
            order_id="x", symbol="BTCUSDT", side="buy",
            qty=0.001, price=80_000.0, ts="2026-05-29T12:00:00+00:00",
            venue="bitunix",
        ))

    logger_agent = _logger_agent(db_url)
    telegram_channel = MagicMock()
    telegram_channel.push = AsyncMock(return_value=telegram_returns)

    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram_channel,
        execution_mode="live",
    )
    # Stub the YAML auto_execute reader so tests don't depend on
    # the prod strategies.yaml. This is the runtime kill switch.
    monkeypatch.setattr(
        obs, "_yaml_auto_execute_for_bitunix",
        lambda: auto_execute,
    )
    return obs, data_exec, logger_agent, telegram_channel


def _set_bull_state(obs):
    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")


def _trigger_payload() -> dict:
    return {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }


# ─── paper-mode isolation (load-bearing safety claim) ───────────────────


@pytest.mark.asyncio
async def test_paper_mode_never_calls_data_exec_place(tmp_path):
    """The most load-bearing test in this commit: paper-mode CANNOT
    call data_exec.place() under any condition. Encapsulation is the
    structural safety property."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_paper(tmp_path)
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")
    data_exec.place.assert_not_called()


@pytest.mark.asyncio
async def test_paper_mode_writes_would_have_placed_not_live(tmp_path):
    """Paper-mode writes the existing `would_have_placed` audit kind,
    NOT `live_order_placed`. Audit lineage clarity."""
    obs, *_ = _make_observer_paper(tmp_path)
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor = 'bitunix_futures'"
        ).fetchall()]
    assert "would_have_placed" in kinds
    assert "live_order_placed" not in kinds
    assert "live_order_rejected" not in kinds


# ─── live-mode happy path ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_with_auto_execute_calls_data_exec_place(
    tmp_path, monkeypatch,
):
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_called_once()
    call = data_exec.place.call_args
    placed_order = call.args[0]
    assert call.kwargs.get("division") == "bitunix_futures"
    # Live entry must be reduce_only=False (broker constructs OPEN body)
    assert placed_order.extra.get("reduce_only") is False, (
        "live entry must be explicitly reduce_only=False"
    )


@pytest.mark.asyncio
async def test_live_mode_emits_live_order_placed_audit_with_reread(
    tmp_path, monkeypatch,
):
    """live_order_placed audit row is written BEFORE data_exec.place
    (intent capture) AND is verifiable via independent DB re-read."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_placed'"
        ).fetchall()
    assert len(rows) == 1, "exactly one live_order_placed audit per live placement"
    p = json.loads(rows[0]["payload_json"])
    assert p["execution_mode"] == "live"
    assert p["auto_execute_at_decision"] is True
    assert p["reduce_only"] is False
    assert p["strategy"] == "bitunix_futures"
    assert p["division"] == "bitunix_futures"


@pytest.mark.asyncio
async def test_live_mode_pushes_telegram_with_live_suffix(
    tmp_path, monkeypatch,
):
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    telegram_channel.push.assert_awaited()
    msgs = [c.args[0] for c in telegram_channel.push.await_args_list]
    assert any("(live)" in m for m in msgs), (
        f"expected telegram message with (live) suffix; got {msgs}"
    )


@pytest.mark.asyncio
async def test_live_mode_writes_paper_trade_record_with_execution_mode_tag(
    tmp_path, monkeypatch,
):
    """Path C (Phase 3): live path writes a `paper_trade_record` row
    tagged with `extra["execution_mode"]="live"` so the replay loop
    walks the open live position. Reverses the N+1 commit-3 "no
    paper_trade_record on live" decision."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trade_record WHERE strategy = 'bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1, (
        "live entry must write exactly one paper_trade_record row"
    )
    extra = json.loads(rows[0]["extra_json"])
    assert extra.get("execution_mode") == "live"


@pytest.mark.asyncio
async def test_live_mode_paper_trade_record_carries_broker_order_id(
    tmp_path, monkeypatch,
):
    """Path C: the broker's returned `FillEvent.order_id` is stamped
    into `extra["broker_order_id"]` so the exit helper + reconciler can
    link the row to broker truth (history_trades, position-state)."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
    )
    # Override the broker fill to a known order_id so we can verify the stamp.
    data_exec.place = AsyncMock(return_value=FillEvent(
        order_id="bx-order-9999", symbol="BTCUSDT", side="buy",
        qty=0.001, price=80_000.0, ts="2026-05-29T12:00:00+00:00",
        venue="bitunix",
    ))
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT extra_json FROM paper_trade_record "
            "WHERE strategy = 'bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"])
    assert extra.get("broker_order_id") == "bx-order-9999"


@pytest.mark.asyncio
async def test_live_mode_rejection_does_not_write_paper_trade_record(
    tmp_path, monkeypatch,
):
    """Path C is conditional on broker-place success. A rejection (no
    real money placed) must NOT leave a stray live-tagged row in the
    replay loop's queue, otherwise the loop would forever try to exit a
    position that never opened."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
        place_raises=RuntimeError("broker says no"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trade_record WHERE strategy = 'bitunix_futures'"
        ).fetchall()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_paper_mode_paper_trade_record_no_execution_mode_tag(
    tmp_path,
):
    """Paper-mode rows must NOT carry `execution_mode="live"`. The
    replay loop's exit-side fork keys off this tag; a misstamped paper
    row would route into the live-broker-truth branch and either fail
    closed (no broker_order_id present) or worse, attempt to close a
    nonexistent broker position."""
    obs, *_ = _make_observer_paper(tmp_path)
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT extra_json FROM paper_trade_record "
            "WHERE strategy = 'bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"]) if rows[0]["extra_json"] else {}
    assert extra.get("execution_mode") != "live"
    assert "broker_order_id" not in extra


# ─── live-mode + auto_execute=false (soft disable) ──────────────────────


@pytest.mark.asyncio
async def test_live_mode_with_auto_execute_false_falls_back_to_paper(
    tmp_path, monkeypatch,
):
    """auto_execute=false is the runtime soft-disable. Even with
    execution_mode=live, the helper writes the paper-mode audit and
    NEVER calls data_exec.place. Operator's emergency kill switch."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch, auto_execute=False,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_not_called()
    with db.connect(obs.db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor = 'bitunix_futures'"
        ).fetchall()]
    assert "would_have_placed" in kinds
    assert "live_order_placed" not in kinds


# ─── live-mode rejection paths ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_broker_exception_writes_rejected_audit(
    tmp_path, monkeypatch,
):
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
        place_raises=RuntimeError("broker says no"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_rejected'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["error_type"] == "RuntimeError"
    assert "broker says no" in p["error"]
    # The intent audit MUST also be present — write-ahead-of-side-effect
    with db.connect(obs.db_url) as conn:
        n_intent = conn.execute(
            "SELECT COUNT(*) c FROM audit_event WHERE kind = 'live_order_placed'"
        ).fetchone()["c"]
    assert n_intent == 1


@pytest.mark.asyncio
async def test_live_mode_rejection_pushes_telegram_rejected_message(
    tmp_path, monkeypatch,
):
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
        place_raises=RuntimeError("broker says no"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    msgs = [c.args[0] for c in telegram_channel.push.await_args_list]
    assert any("REJECTED" in m and "(live)" in m for m in msgs), (
        f"expected REJECTED + (live) telegram message; got {msgs}"
    )


@pytest.mark.asyncio
async def test_live_mode_rejection_observer_loop_survives(
    tmp_path, monkeypatch,
):
    """Critical: even if data_exec.place raises, observe_and_decide
    must NOT re-raise — the alert-processing loop has to survive a
    broker hiccup."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
        place_raises=RuntimeError("broker says no"),
    )
    _set_bull_state(obs)
    # Should not raise.
    verdict = await obs.observe_and_decide(_trigger_payload(), source="lord_otter")
    assert verdict is not None  # observer returned its TierVerdict normally


# ─── telegram push-bool failure ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_telegram_push_false_writes_failure_audit(
    tmp_path, monkeypatch,
):
    """push() returning False (per
    `comms.telegram_bot:_send_message`'s confirmed-delivery semantics)
    must produce a `telegram_notification_failed` audit row tagged with
    the failure_channel."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
        telegram_returns=False,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'telegram_notification_failed'"
        ).fetchall()
    assert len(rows) >= 1
    found = False
    for r in rows:
        p = json.loads(r["payload_json"])
        if p.get("channel") == "live_placement_alert":
            found = True
            break
    assert found, "expected telegram_notification_failed with channel='live_placement_alert'"


@pytest.mark.asyncio
async def test_live_mode_telegram_push_raises_does_not_block(
    tmp_path, monkeypatch,
):
    """telegram_channel.push() raising must not block placement audit
    nor crash the observer."""
    obs, data_exec, logger_agent, telegram_channel = _make_observer_live(
        tmp_path, monkeypatch,
    )
    telegram_channel.push = AsyncMock(side_effect=RuntimeError("tg down"))
    _set_bull_state(obs)
    # Should not raise
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")
    # Failure audit still written
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) c FROM audit_event "
            "WHERE kind = 'telegram_notification_failed'"
        ).fetchone()
    assert rows["c"] >= 1


# ─── daily-risk semantics ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_records_daily_risk_on_success(
    tmp_path, monkeypatch,
):
    obs, *_ = _make_observer_live(tmp_path, monkeypatch)
    _set_bull_state(obs)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    cum_before, n_before = obs._read_daily_risk(today)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")
    cum_after, n_after = obs._read_daily_risk(today)
    assert n_after == n_before + 1
    assert cum_after > cum_before


@pytest.mark.asyncio
async def test_live_mode_records_daily_risk_on_rejection(
    tmp_path, monkeypatch,
):
    """Daily risk accrues on ATTEMPT, not only success. The budget was
    committed the moment we decided to place the order."""
    obs, *_ = _make_observer_live(
        tmp_path, monkeypatch,
        place_raises=RuntimeError("broker says no"),
    )
    _set_bull_state(obs)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    cum_before, n_before = obs._read_daily_risk(today)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")
    cum_after, n_after = obs._read_daily_risk(today)
    assert n_after == n_before + 1
    assert cum_after > cum_before
