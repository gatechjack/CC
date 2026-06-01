"""Tests for `_execute_live_exits` — live-mode close-path primitive.

Commit 3 of Stage-1 Session N+2 Phase 3 — the exit-side counterpart
to `_place_live`. Validates:

- Happy path: data_exec.place called with reduce_only=True + inverted
  side; success calls `_record_exit_outcome(is_live=True, fill_event=...)`.
- Exit order's `extra_json` carries `exit_kind`, `parent_order_id`,
  `parent_broker_order_id`, `leg` (audit + reconciler lineage).
- TP path: closes with same shape as SL path (the function is exit-kind
  agnostic; the verdict comes from upstream).
- SL path: closes 100% of remainder by passing through the qty kwarg.
- Stuck-cancelled exception (Finding #6.4 wiring): rejection audit +
  alert + return False; row stays NULL so replay loop retries.
- Stuck-cancel-failed exception: elevated-priority alert + halt-and-page
  audit kind; return False.
- Generic broker exception: rejected audit + alert + return False.
- Intent audit (`live_exit_order_placed`) fires BEFORE the broker call
  (write-ahead-of-side-effect).

Session A scope: this commit ADDS `_execute_live_exits` and the
broker's `get_pending_positions` extraction. Wiring into the replay
loop (Session B) is deferred.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.bitunix_exceptions import (
    BitunixStuckOrderCancelFailed,
    BitunixStuckOrderCancelled,
)
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent


# ─── fixtures ───────────────────────────────────────────────────────────


def _make_observer(
    tmp_path: Path,
    *,
    place_raises: Exception | None = None,
    fill: FillEvent | None = None,
    telegram_returns: bool = True,
) -> tuple[BitunixFuturesObserver, MagicMock, MagicMock]:
    db_path = tmp_path / "exec_live_exits.db"
    db_url = f"sqlite:///{db_path}"
    db.init_db(db_url)

    if fill is None:
        fill = FillEvent(
            order_id="bx-exit-1", symbol="BTCUSDT", side="sell",
            qty=0.001, price=81_000.0, ts="2026-06-01T11:00:00+00:00",
            venue="bitunix",
        )

    data_exec = MagicMock()
    if place_raises is not None:
        data_exec.place = AsyncMock(side_effect=place_raises)
    else:
        data_exec.place = AsyncMock(return_value=fill)

    telegram = MagicMock()
    telegram.push = AsyncMock(return_value=telegram_returns)

    logger_agent = LoggerAgent(db_url=db_url)
    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=MagicMock(),
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram,
        execution_mode="live",
    )
    return obs, data_exec, telegram


def _seed_live_row(db_url: str, order_id: str, *, broker_order_id: str) -> None:
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id, "2026-06-01T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTCUSDT", "buy", 0.001,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps({
                    "execution_mode": "live",
                    "broker_order_id": broker_order_id,
                }),
            ),
        )


# ─── happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_calls_data_exec_with_reduce_only_inverted_side(
    tmp_path,
):
    obs, data_exec, _ = _make_observer(tmp_path)
    _seed_live_row(obs.db_url, "ord-tp1", broker_order_id="bx-entry-7")

    ok = await obs._execute_live_exits(
        order_id="ord-tp1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="tp1",
        parent_broker_order_id="bx-entry-7",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        actual_pnl_dollars=1.0,
        actual_r_multiple=2.0,
        bars_to_resolution=60,
        leg="tp1",
    )

    assert ok is True
    data_exec.place.assert_called_once()
    placed_order = data_exec.place.call_args.args[0]
    assert placed_order.side == "sell", "exit side must invert entry"
    assert placed_order.extra["reduce_only"] is True
    assert placed_order.extra["exit_kind"] == "tp1"
    assert placed_order.extra["parent_order_id"] == "ord-tp1"
    assert placed_order.extra["parent_broker_order_id"] == "bx-entry-7"
    assert placed_order.extra["leg"] == "tp1"
    # Deterministic exit id (links to parent in audit grep + reconciler dedup)
    assert placed_order.id == "ord-tp1-exit-tp1"


@pytest.mark.asyncio
async def test_happy_path_writes_exit_outcome_with_broker_truth_price(tmp_path):
    """Broker FillEvent.price overrides the classifier-projected
    result_price — broker truth is authoritative once the fill lands."""
    fill = FillEvent(
        order_id="bx-exit-99", symbol="BTCUSDT", side="sell",
        qty=0.001, price=81_234.0, ts="2026-06-01T11:00:30+00:00",
        venue="bitunix",
    )
    obs, _data_exec, _ = _make_observer(tmp_path, fill=fill)
    _seed_live_row(obs.db_url, "ord-tp1", broker_order_id="bx-entry-7")

    await obs._execute_live_exits(
        order_id="ord-tp1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="tp1",
        parent_broker_order_id="bx-entry-7",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        leg="tp1",
    )

    with db.connect(obs.db_url) as conn:
        row = conn.execute(
            "SELECT result, result_price, extra_json FROM paper_trade_record "
            "WHERE order_id=?",
            ("ord-tp1",),
        ).fetchone()
    assert row["result"] == "win"
    assert row["result_price"] == 81_234.0  # broker truth, not classifier 81_000
    extra = json.loads(row["extra_json"])
    assert extra["result_source"] == "live_broker_truth"
    assert extra["exit_broker_order_id"] == "bx-exit-99"


@pytest.mark.asyncio
async def test_short_entry_inverts_to_buy_exit(tmp_path):
    obs, data_exec, _ = _make_observer(tmp_path)
    _seed_live_row(obs.db_url, "ord-short", broker_order_id="bx-entry-s")

    await obs._execute_live_exits(
        order_id="ord-short",
        symbol="BTCUSDT",
        entry_side="sell",
        qty=0.001,
        exit_kind="sl",
        parent_broker_order_id="bx-entry-s",
        result="loss",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=80_500.0,
    )

    placed_order = data_exec.place.call_args.args[0]
    assert placed_order.side == "buy", "short entry exits via buy"
    assert placed_order.extra["exit_kind"] == "sl"


# ─── intent audit fires before broker call (write-ahead) ────────────────


@pytest.mark.asyncio
async def test_intent_audit_fires_before_data_exec_place(tmp_path):
    """`live_exit_order_placed` audit must land even if data_exec.place
    later raises. The audit-row-write-before-side-effect discipline
    means a transient broker hiccup never leaves us with a silent loss."""
    obs, _data_exec, _ = _make_observer(
        tmp_path, place_raises=RuntimeError("transient"),
    )
    _seed_live_row(obs.db_url, "ord-w1", broker_order_id="bx-entry-w")

    await obs._execute_live_exits(
        order_id="ord-w1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="tp1",
        parent_broker_order_id="bx-entry-w",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
    )

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='live_exit_order_placed'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["exit_kind"] == "tp1"
    assert p["parent_order_id"] == "ord-w1"
    assert p["strategy"] == "bitunix_futures"
    assert p["division"] == "bitunix_futures"


# ─── Finding #6.4 wiring: stuck-order primitive integration ─────────────


@pytest.mark.asyncio
async def test_stuck_cancelled_returns_false_and_writes_audit(tmp_path):
    """`BitunixStuckOrderCancelled` from `_observe_fill` timeout-and-halt
    primitive means: order never filled, broker cancelled it. Position
    remains open at broker. The replay loop / reconciler will retry."""
    obs, _data_exec, telegram = _make_observer(
        tmp_path,
        place_raises=BitunixStuckOrderCancelled(
            order_id="bx-exit-stuck", status="PARTIAL_FILLED",
        ),
    )
    _seed_live_row(obs.db_url, "ord-s1", broker_order_id="bx-entry-s1")

    ok = await obs._execute_live_exits(
        order_id="ord-s1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="sl",
        parent_broker_order_id="bx-entry-s1",
        result="loss",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=79_500.0,
    )

    assert ok is False, "stuck-cancelled means exit didn't happen"

    with db.connect(obs.db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor='bitunix_futures'"
        ).fetchall()]
    assert "live_exit_order_stuck_cancelled" in kinds
    # Row stays in result IS NULL state — replay loop will retry
    with db.connect(obs.db_url) as conn:
        row = conn.execute(
            "SELECT result FROM paper_trade_record WHERE order_id=?",
            ("ord-s1",),
        ).fetchone()
    assert row["result"] is None
    # Operator was notified
    msgs = [c.args[0] for c in telegram.push.await_args_list]
    assert any("STUCK→CANCELLED" in m for m in msgs)


@pytest.mark.asyncio
async def test_stuck_cancel_failed_writes_halt_audit_and_elevated_alert(
    tmp_path,
):
    """`BitunixStuckOrderCancelFailed` is the worst case — observe_fill
    timed out AND cancel ALSO failed. Broker state unknown; surface as
    halt-and-page elevated priority."""
    obs, _data_exec, telegram = _make_observer(
        tmp_path,
        place_raises=BitunixStuckOrderCancelFailed(
            order_id="bx-exit-fail", status="UNKNOWN",
        ),
    )
    _seed_live_row(obs.db_url, "ord-f1", broker_order_id="bx-entry-f1")

    ok = await obs._execute_live_exits(
        order_id="ord-f1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="sl",
        parent_broker_order_id="bx-entry-f1",
        result="loss",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=79_500.0,
    )

    assert ok is False
    with db.connect(obs.db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor='bitunix_futures'"
        ).fetchall()]
    assert "live_exit_order_halt" in kinds
    msgs = [c.args[0] for c in telegram.push.await_args_list]
    assert any(
        "STUCK→CANCEL-FAILED" in m and "broker state UNKNOWN" in m
        for m in msgs
    )


# ─── generic rejection path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generic_exception_writes_rejected_audit(tmp_path):
    obs, _data_exec, telegram = _make_observer(
        tmp_path, place_raises=RuntimeError("BitUnix says no"),
    )
    _seed_live_row(obs.db_url, "ord-r1", broker_order_id="bx-entry-r1")

    ok = await obs._execute_live_exits(
        order_id="ord-r1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="tp2",
        parent_broker_order_id="bx-entry-r1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
    )

    assert ok is False
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='live_exit_order_rejected'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["error_type"] == "RuntimeError"
    assert "BitUnix says no" in p["error"]
    msgs = [c.args[0] for c in telegram.push.await_args_list]
    assert any("REJECTED" in m and "(live, exit)" in m for m in msgs)


# ─── partial-qty (multi-leg) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_qty_passed_through_for_multi_leg_close(tmp_path):
    """Multi-leg TP1 closes 25% of qty (not 100%). The function
    accepts the qty kwarg as-is — leg-fraction math is the caller's
    responsibility (replay loop / reconciler does it)."""
    obs, data_exec, _ = _make_observer(tmp_path)
    _seed_live_row(obs.db_url, "ord-m1", broker_order_id="bx-entry-m1")

    await obs._execute_live_exits(
        order_id="ord-m1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.00025,  # 25% of 0.001
        exit_kind="tp1",
        parent_broker_order_id="bx-entry-m1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        leg="tp1",
    )

    placed = data_exec.place.call_args.args[0]
    assert placed.qty == 0.00025
