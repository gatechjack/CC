"""Tests for the 60s position-state sanity poll loop (Session B 5b)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    run_position_state_sanity_poll_loop,
)
from trading_corp.persistence import db
from trading_corp.persistence.models import Position


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "poll.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _seed_live_row(db_url: str, order_id: str = "ord-live"):
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
                json.dumps({"execution_mode": "live", "broker_order_id": "bx-1"}),
            ),
        )


def _broker_stub(positions: list[Position]) -> MagicMock:
    broker = MagicMock()
    broker.get_pending_positions = AsyncMock(return_value=positions)
    broker._halt_new_orders = False
    broker._halt_reason = None
    return broker


@pytest.mark.asyncio
async def test_loop_calls_reconcile_then_sleeps(db_url):
    """Loop ticks reconcile_position_state per interval; test cancels
    after one tick to verify the cycle ran."""
    broker = _broker_stub([])
    task = asyncio.create_task(
        run_position_state_sanity_poll_loop(
            broker, db_url, interval_s=0.05,
        ),
    )
    # Yield enough for at least one tick + the sleep to start
    await asyncio.sleep(0.10)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # At least one reconcile call landed
    assert broker.get_pending_positions.await_count >= 1


@pytest.mark.asyncio
async def test_divergence_fires_notifier_divergence_alert(db_url):
    _seed_live_row(db_url, "ord-d1")
    broker = _broker_stub([])  # bot tracks; broker doesn't → missing_on_broker
    notifier = MagicMock()
    notifier.notify_reconciliation_divergence = AsyncMock()

    task = asyncio.create_task(
        run_position_state_sanity_poll_loop(
            broker, db_url, interval_s=0.05, notifier=notifier,
        ),
    )
    await asyncio.sleep(0.10)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Notifier was called at least once for the missing_on_broker case
    assert notifier.notify_reconciliation_divergence.await_count >= 1
    call = notifier.notify_reconciliation_divergence.await_args
    assert call.kwargs["kind"] == "missing_on_broker"


@pytest.mark.asyncio
async def test_loop_runs_multiple_ticks_under_normal_state(db_url):
    """The loop must continue ticking every interval, not exit after
    one. (Exception resilience is documented behavior: reconcile_position_state
    catches broker errors internally, and the loop's try/except catches
    any other tick-level error and continues.)"""
    broker = _broker_stub([])
    task = asyncio.create_task(
        run_position_state_sanity_poll_loop(
            broker, db_url, interval_s=0.03,
        ),
    )
    await asyncio.sleep(0.15)  # ~5 intervals
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert broker.get_pending_positions.await_count >= 3, (
        "expected at least 3 ticks at 0.03s interval over 0.15s wallclock"
    )
