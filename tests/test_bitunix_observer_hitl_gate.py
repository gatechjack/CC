"""Tests for the HITL first-N gate + monitor-mode transition.

Commit 4 of Stage-1 Session N+1. Validates:

- For orders 1..HITL_FIRST_N_LIVE_ORDERS, the helper routes through
  `pending_registry.wait()` and blocks until the operator decides via
  the web app (approve/reject/modify), or the request times out.
- approve → place; counter increments
- modify (with new_qty) → apply new_qty; place; counter increments
- reject → audit `live_order_skipped_hitl` (re-read confirmed); do
  NOT call data_exec.place; counter does NOT increment
- timeout → BoardDecision(decision='reject', reason='approval timeout')
  surfaces; treated as reject; counter does NOT increment
- Counter persists across observer re-instantiation via agent_state.
- Order #(N+1) onwards skip the gate AND tag telegram suffix as
  `(live, monitor-mode)`.
- pending_registry=None (no HITL wired) → place directly with audit
  tagged `hitl_gate=skipped_no_registry`.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
    HITL_FIRST_N_LIVE_ORDERS,
    LIVE_ORDERS_PLACED_AGENT_STATE_KEY,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import FillEvent
from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision
from trading_corp.persistence import db


# ─── fixtures ───────────────────────────────────────────────────────────


def _make_observer(
    tmp_path: Path,
    monkeypatch,
    *,
    initial_count: int = 0,
    registry_decision: BoardDecision | None = BoardDecision(decision="approve"),
    registry_raises=None,
    place_raises=None,
    auto_execute: bool = True,
):
    """Live-mode observer with a programmable PendingApprovalRegistry stub.

    `initial_count` lets us simulate a system that has already placed N
    live orders (writes `agent_state` directly before the observer
    constructs). `registry_decision` is what `registry.wait()` returns
    (default: approve)."""
    db_path = tmp_path / "hitl.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    if initial_count > 0:
        db.set_agent_state(
            "bitunix_futures",
            LIVE_ORDERS_PLACED_AGENT_STATE_KEY,
            {"count": initial_count},
            db_url=db_url,
        )

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

    logger_agent = LoggerAgent(db_url=db_url)
    telegram_channel = MagicMock()
    telegram_channel.push = AsyncMock(return_value=True)

    # Registry stub: programmable wait() behavior
    pending_registry = MagicMock()
    if registry_raises is not None:
        pending_registry.wait = AsyncMock(side_effect=registry_raises)
    else:
        pending_registry.wait = AsyncMock(return_value=registry_decision)

    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram_channel,
        execution_mode="live",
        pending_registry=pending_registry,
    )
    monkeypatch.setattr(
        obs, "_yaml_auto_execute_for_bitunix",
        lambda: auto_execute,
    )
    return obs, data_exec, logger_agent, telegram_channel, pending_registry


def _set_bull_state(obs):
    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")


def _trigger_payload() -> dict:
    return {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }


# ─── threshold + constant ───────────────────────────────────────────────


def test_hitl_first_n_constant_is_10():
    """The brief specified 10 explicitly. Lock it in so a refactor
    can't silently shift the gate."""
    assert HITL_FIRST_N_LIVE_ORDERS == 10


# ─── approve path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_approve_at_first_order_calls_place_and_increments(
    tmp_path, monkeypatch,
):
    obs, data_exec, logger_agent, telegram_channel, registry = _make_observer(
        tmp_path, monkeypatch,
        initial_count=0,
        registry_decision=BoardDecision(decision="approve"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    registry.wait.assert_awaited_once()
    data_exec.place.assert_called_once()
    # Counter incremented 0 → 1
    assert obs._live_orders_placed_count() == 1


@pytest.mark.asyncio
async def test_hitl_summary_includes_position_in_first_n(
    tmp_path, monkeypatch,
):
    obs, *_, registry = _make_observer(
        tmp_path, monkeypatch,
        initial_count=3,
        registry_decision=BoardDecision(decision="approve"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    req: ApprovalRequest = registry.wait.await_args.args[0]
    assert "live (#4/10)" in req.summary, (
        f"approval summary must show progress through first-N; got {req.summary!r}"
    )
    # Detail dict must carry the order + audit fields the web app renders
    assert req.detail["division"] == "bitunix_futures"
    assert req.detail["hitl_first_n_position"] == 4
    assert req.detail["hitl_first_n_total"] == HITL_FIRST_N_LIVE_ORDERS
    assert "order" in req.detail


# ─── modify path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_modify_applies_new_qty_then_places(tmp_path, monkeypatch):
    obs, data_exec, *_ = _make_observer(
        tmp_path, monkeypatch,
        initial_count=0,
        registry_decision=BoardDecision(decision="modify", new_qty=0.0005),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_called_once()
    placed_order = data_exec.place.call_args.args[0]
    assert placed_order.qty == pytest.approx(0.0005)
    # Modify counts as a placed order — counter incremented
    assert obs._live_orders_placed_count() == 1


# ─── reject path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_reject_does_not_call_place_and_audits_skip(
    tmp_path, monkeypatch,
):
    obs, data_exec, logger_agent, telegram_channel, registry = _make_observer(
        tmp_path, monkeypatch,
        initial_count=0,
        registry_decision=BoardDecision(
            decision="reject", reason="not the right setup",
        ),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_not_called()
    # Counter NOT incremented on reject
    assert obs._live_orders_placed_count() == 0
    # Skip audit (re-read confirmed by virtue of being in the DB)
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_skipped_hitl'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["hitl_decision"] == "reject"
    assert "not the right setup" in p["hitl_reason"]


@pytest.mark.asyncio
async def test_hitl_reject_pushes_telegram_alert(tmp_path, monkeypatch):
    obs, _de, _la, telegram_channel, _r = _make_observer(
        tmp_path, monkeypatch,
        registry_decision=BoardDecision(decision="reject", reason="no"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    msgs = [c.args[0] for c in telegram_channel.push.await_args_list]
    assert any("HITL-REJECTED" in m for m in msgs), (
        f"expected HITL-REJECTED telegram; got {msgs}"
    )


# ─── timeout path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_timeout_treated_as_reject(tmp_path, monkeypatch):
    """PendingApprovalRegistry.wait() returns BoardDecision(decision='reject',
    reason='approval timeout') on timeout (per pending_registry.py:137-139).
    Observer must treat that the same as an operator reject."""
    obs, data_exec, *_ = _make_observer(
        tmp_path, monkeypatch,
        registry_decision=BoardDecision(
            decision="reject", reason="approval timeout",
        ),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_not_called()
    assert obs._live_orders_placed_count() == 0
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_skipped_hitl'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert "timeout" in p["hitl_reason"]


# ─── registry-side bug: fail closed ────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_registry_exception_fails_closed(tmp_path, monkeypatch):
    """If `registry.wait()` raises (registry bug, DB lock, etc.), the
    observer must NOT proceed to place — fail-closed."""
    obs, data_exec, *_ = _make_observer(
        tmp_path, monkeypatch,
        registry_raises=RuntimeError("registry imploded"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_not_called()
    assert obs._live_orders_placed_count() == 0
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_skipped_hitl'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["hitl_decision"] == "error"
    assert "registry imploded" in p["hitl_reason"]


# ─── monitor-mode transition at order N+1 ──────────────────────────────


@pytest.mark.asyncio
async def test_monitor_mode_at_count_10_skips_hitl_gate(tmp_path, monkeypatch):
    """At counter == HITL_FIRST_N_LIVE_ORDERS, the next attempt is the
    11th — skip the gate, place automatically with monitor-mode telegram."""
    obs, data_exec, _la, telegram_channel, registry = _make_observer(
        tmp_path, monkeypatch,
        initial_count=HITL_FIRST_N_LIVE_ORDERS,  # 10 → 11th attempt
        registry_decision=BoardDecision(decision="approve"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    # Gate skipped
    registry.wait.assert_not_called()
    # Order placed
    data_exec.place.assert_called_once()
    # Counter incremented 10 → 11
    assert obs._live_orders_placed_count() == HITL_FIRST_N_LIVE_ORDERS + 1
    # Telegram suffix tag
    msgs = [c.args[0] for c in telegram_channel.push.await_args_list]
    assert any("(live, monitor-mode)" in m for m in msgs), (
        f"expected monitor-mode telegram suffix; got {msgs}"
    )


@pytest.mark.asyncio
async def test_audit_intent_payload_marks_hitl_gate_required(
    tmp_path, monkeypatch,
):
    obs, *_ = _make_observer(
        tmp_path, monkeypatch,
        initial_count=0,
        registry_decision=BoardDecision(decision="approve"),
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_placed' LIMIT 1"
        ).fetchone()
    p = json.loads(row["payload_json"])
    assert p["hitl_gate"] == "required"
    assert p["live_orders_placed_before"] == 0


@pytest.mark.asyncio
async def test_audit_intent_payload_marks_monitor_mode(tmp_path, monkeypatch):
    obs, *_ = _make_observer(
        tmp_path, monkeypatch,
        initial_count=15,
    )
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = 'live_order_placed' LIMIT 1"
        ).fetchone()
    p = json.loads(row["payload_json"])
    assert p["hitl_gate"] == "monitor_mode"
    assert p["live_orders_placed_before"] == 15


# ─── counter persistence across observer re-init ───────────────────────


@pytest.mark.asyncio
async def test_counter_survives_observer_reinstantiation(
    tmp_path, monkeypatch,
):
    """The persistence claim: the live-orders-placed counter survives
    observer re-construction (simulating a process restart)."""
    obs1, _de, _la, _tg, _r = _make_observer(
        tmp_path, monkeypatch,
        initial_count=0,
        registry_decision=BoardDecision(decision="approve"),
    )
    _set_bull_state(obs1)
    await obs1.observe_and_decide(_trigger_payload(), source="lord_otter")
    assert obs1._live_orders_placed_count() == 1

    # Discard obs1 — construct a fresh observer with the SAME db_url to
    # simulate a process restart.
    obs2 = BitunixFuturesObserver(
        db_url=obs1.db_url,
        execution_mode="live",
    )
    assert obs2._live_orders_placed_count() == 1, (
        "counter must persist via agent_state — survived re-init"
    )


# ─── no-registry path (back-compat with commit 3 fixtures) ─────────────


@pytest.mark.asyncio
async def test_no_pending_registry_skips_hitl_gracefully(tmp_path, monkeypatch):
    """Tests that pre-commit-4 callers (no pending_registry wired) still
    place orders — the live path doesn't require HITL to be present.
    Audit tags it as `hitl_gate=skipped_no_registry`."""
    db_path = tmp_path / "no_reg.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"
    risk_agent = MagicMock()
    risk_verdict = MagicMock()
    risk_verdict.verdict = "approve"
    risk_verdict.reason = "ok"
    risk_verdict.new_qty = None
    risk_agent.evaluate.return_value = risk_verdict
    snap = MagicMock(); snap.equity = 5_000.0; snap.positions = []
    broker = MagicMock(); broker.snapshot = AsyncMock(return_value=snap)
    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.flatten_division = AsyncMock()
    data_exec.place = AsyncMock(return_value=FillEvent(
        order_id="x", symbol="BTCUSDT", side="buy",
        qty=0.001, price=80_000.0, ts="2026-05-29T12:00:00+00:00",
        venue="bitunix",
    ))
    obs = BitunixFuturesObserver(
        db_url=db_url, risk_agent=risk_agent, data_exec=data_exec,
        logger_agent=LoggerAgent(db_url=db_url),
        telegram_channel=MagicMock(push=AsyncMock(return_value=True)),
        execution_mode="live",
        pending_registry=None,
    )
    monkeypatch.setattr(obs, "_yaml_auto_execute_for_bitunix", lambda: True)
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    data_exec.place.assert_called_once()
    with db.connect(obs.db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind='live_order_placed'"
        ).fetchone()
    p = json.loads(row["payload_json"])
    assert p["hitl_gate"] == "skipped_no_registry"


# ─── post-rejection state hygiene ──────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_reject_does_not_pollute_daily_risk_below_attempt_semantic(
    tmp_path, monkeypatch,
):
    """Daily-risk commits on attempt regardless of HITL outcome (per
    commit 3 semantic: budget was committed when we decided to propose).
    Lock this so a future refactor doesn't decide to "save" the budget
    on reject — that would let the same signal re-fire and waste
    operator attention."""
    from datetime import datetime, timezone
    obs, *_ = _make_observer(
        tmp_path, monkeypatch,
        registry_decision=BoardDecision(decision="reject", reason="no"),
    )
    today = datetime.now(timezone.utc).date().isoformat()
    _set_bull_state(obs)
    cum_before, n_before = obs._read_daily_risk(today)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")
    cum_after, n_after = obs._read_daily_risk(today)
    assert n_after == n_before + 1
    assert cum_after > cum_before
