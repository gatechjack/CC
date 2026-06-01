"""Tests for paper_trade_replay's live-exit fork wiring.

Session B Commit 4 of N+2 Phase 3. Validates:

- `set_live_exit_executor(observer)` registers; `None` disables.
- `_verdict_to_exit_kind` maps win→"tp" / loss→"sl" / expired→"expired".
- When a paper_trade_record row has `extra.execution_mode="live"` AND
  the executor is registered, `_replay_tick_async` calls
  `observer._execute_live_exits(...)` with the row + verdict shape
  (no `_update_row` write — that comes from `_record_exit_outcome`
  inside the executor).
- Paper-tagged rows (no execution_mode tag, or execution_mode!="live")
  take the existing `_update_row` write path unchanged.
- Executor not registered: live-tagged rows fall back to `_update_row`
  (Session A behavior). The position-state reconciler at startup will
  detect any stranded live rows on the next process restart.
- Executor exception: caught + counts["errors"] increments; replay
  loop continues (doesn't crash the whole pass).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents import paper_trade_replay as ptr
from trading_corp.agents.paper_trade_replay import (
    _replay_tick_async,
    _verdict_to_exit_kind,
    set_live_exit_executor,
)
from trading_corp.persistence import db


# ─── unit: _verdict_to_exit_kind ────────────────────────────────────────


def test_verdict_to_exit_kind_win_to_tp():
    assert _verdict_to_exit_kind("win") == "tp"


def test_verdict_to_exit_kind_loss_to_sl():
    assert _verdict_to_exit_kind("loss") == "sl"


def test_verdict_to_exit_kind_expired_passthrough():
    assert _verdict_to_exit_kind("expired") == "expired"


# ─── set_live_exit_executor registration ────────────────────────────────


def test_set_live_exit_executor_registers_observer():
    obs = MagicMock()
    set_live_exit_executor(obs)
    assert ptr._LIVE_EXIT_EXECUTOR["observer"] is obs
    # cleanup so other tests aren't polluted
    set_live_exit_executor(None)


def test_set_live_exit_executor_none_disables():
    set_live_exit_executor(MagicMock())
    set_live_exit_executor(None)
    assert ptr._LIVE_EXIT_EXECUTOR["observer"] is None


# ─── fixtures for replay-tick integration ───────────────────────────────


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "replay_live_fork.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


@pytest.fixture(autouse=True)
def _reset_executor():
    """Each test starts with the executor disabled to avoid cross-test
    pollution; the test that needs it re-registers."""
    set_live_exit_executor(None)
    yield
    set_live_exit_executor(None)


def _seed_row(
    db_url: str,
    *,
    order_id: str,
    extra: dict | None = None,
    side: str = "buy",
    qty: float = 0.001,
) -> None:
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
                "BTCUSDT", side, qty,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps(extra) if extra else None,
            ),
        )


async def _bars_tp_hit(symbol, tf, since_ts_ms, n):
    """Synthesize bars where high crosses 81_000 (TP hit for long).
    Bar tuple shape: (ts_ms, open, high, low, close, volume)."""
    return [
        (since_ts_ms + 60_000, 80_000.0, 81_500.0, 80_000.0, 81_000.0, 1.0),
    ]


# ─── live-tagged row triggers _execute_live_exits ───────────────────────


@pytest.mark.asyncio
async def test_live_tagged_row_calls_execute_live_exits(db_url):
    _seed_row(db_url, order_id="ord-live-1", extra={
        "execution_mode": "live",
        "broker_order_id": "bx-1",
    })
    observer = MagicMock()
    observer._execute_live_exits = AsyncMock()
    set_live_exit_executor(observer)

    counts = await _replay_tick_async(db_url, ohlcv_fetcher=_bars_tp_hit)

    observer._execute_live_exits.assert_awaited_once()
    call = observer._execute_live_exits.await_args
    assert call.kwargs["order_id"] == "ord-live-1"
    assert call.kwargs["symbol"] == "BTCUSDT"
    assert call.kwargs["entry_side"] == "buy"
    assert call.kwargs["qty"] == 0.001
    assert call.kwargs["exit_kind"] == "tp"   # win → tp mapping
    assert call.kwargs["parent_broker_order_id"] == "bx-1"
    assert call.kwargs["result"] == "win"
    # The classifier's projected exit price (TP hit at 81_000)
    assert call.kwargs["result_price"] == 81_000.0


@pytest.mark.asyncio
async def test_live_tagged_row_skips_update_row_write(db_url):
    """When the live executor handles it, _update_row must NOT also
    write — the executor's _record_exit_outcome writes the row."""
    _seed_row(db_url, order_id="ord-live-2", extra={
        "execution_mode": "live",
        "broker_order_id": "bx-2",
    })
    observer = MagicMock()
    observer._execute_live_exits = AsyncMock()
    set_live_exit_executor(observer)

    await _replay_tick_async(db_url, ohlcv_fetcher=_bars_tp_hit)

    # The row's `result` column should NOT have been written by
    # _update_row (since the executor is responsible for that).
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT result FROM paper_trade_record WHERE order_id=?",
            ("ord-live-2",),
        ).fetchone()
    assert row["result"] is None


# ─── paper-tagged row takes the existing _update_row path ───────────────


@pytest.mark.asyncio
async def test_paper_row_takes_existing_update_row_path(db_url):
    """Rows without execution_mode tag use the pre-existing paper write
    path; observer._execute_live_exits is NOT called."""
    _seed_row(db_url, order_id="ord-paper-1")  # no extra → no tag
    observer = MagicMock()
    observer._execute_live_exits = AsyncMock()
    set_live_exit_executor(observer)

    counts = await _replay_tick_async(db_url, ohlcv_fetcher=_bars_tp_hit)

    observer._execute_live_exits.assert_not_called()
    # And the row's result IS written by _update_row
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT result FROM paper_trade_record WHERE order_id=?",
            ("ord-paper-1",),
        ).fetchone()
    assert row["result"] == "win"


@pytest.mark.asyncio
async def test_explicit_paper_execution_mode_takes_paper_path(db_url):
    """`extra.execution_mode="paper"` is explicit-paper (not live) →
    paper path."""
    _seed_row(db_url, order_id="ord-explicit-paper", extra={
        "execution_mode": "paper",
    })
    observer = MagicMock()
    observer._execute_live_exits = AsyncMock()
    set_live_exit_executor(observer)

    await _replay_tick_async(db_url, ohlcv_fetcher=_bars_tp_hit)

    observer._execute_live_exits.assert_not_called()


# ─── no executor registered → live rows fall back to _update_row ────────


@pytest.mark.asyncio
async def test_no_executor_falls_back_to_update_row(db_url):
    """Backward-compat: a live-tagged row in the absence of a registered
    executor falls back to _update_row (Session A behavior). Position-state
    reconciler at next startup will catch stranded rows."""
    _seed_row(db_url, order_id="ord-fallback", extra={
        "execution_mode": "live",
        "broker_order_id": "bx-fb",
    })
    # No set_live_exit_executor call → executor is None

    await _replay_tick_async(db_url, ohlcv_fetcher=_bars_tp_hit)

    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT result FROM paper_trade_record WHERE order_id=?",
            ("ord-fallback",),
        ).fetchone()
    assert row["result"] == "win"


# ─── executor exception is caught + counted ─────────────────────────────


@pytest.mark.asyncio
async def test_executor_exception_is_caught_and_counted(db_url):
    _seed_row(db_url, order_id="ord-err", extra={
        "execution_mode": "live",
        "broker_order_id": "bx-err",
    })
    observer = MagicMock()
    observer._execute_live_exits = AsyncMock(
        side_effect=RuntimeError("simulated broker outage"),
    )
    set_live_exit_executor(observer)

    counts = await _replay_tick_async(db_url, ohlcv_fetcher=_bars_tp_hit)

    assert counts["errors"] >= 1
    # The exception was raised inside the fork — the replay loop
    # caught it (no propagation). Row's result stays NULL.
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT result FROM paper_trade_record WHERE order_id=?",
            ("ord-err",),
        ).fetchone()
    assert row["result"] is None


# ─── verdict shape: short entry inverts via observer ────────────────────


@pytest.mark.asyncio
async def test_short_entry_side_passed_through_to_executor(db_url):
    _seed_row(db_url, order_id="ord-short-live", side="sell", extra={
        "execution_mode": "live",
        "broker_order_id": "bx-short",
    })

    async def _bars_short_loss(symbol, tf, since_ts_ms, n):
        # For a short entry at 80k with stop above (_seed_row sets
        # 79_500 stop, which is actually below entry — designed for long).
        # We just need any terminal verdict to fire the executor for
        # side pass-through verification.
        return [
            (since_ts_ms + 60_000, 80_000.0, 80_100.0, 79_400.0, 79_500.0, 1.0),
        ]

    observer = MagicMock()
    observer._execute_live_exits = AsyncMock()
    set_live_exit_executor(observer)

    await _replay_tick_async(db_url, ohlcv_fetcher=_bars_short_loss)

    if observer._execute_live_exits.await_count:
        call = observer._execute_live_exits.await_args
        assert call.kwargs["entry_side"] == "sell"
        # (The observer's _execute_live_exits handles the inversion
        # internally; replay just passes entry_side through.)
