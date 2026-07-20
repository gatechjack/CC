"""Regression tests for the auto-pause forward-window fix (2026-07-20).

Bug: `should_autopause` aggregated a whale's round-trips over FULL history,
while the operator dashboard scopes per-whale P&L to the forward metrics-epoch
window (`entry_ts >= epoch`). A whale profitable pre-epoch but toxic post-epoch
(the live superbeter007 case: full +$5.85 vs forward -$69.43) escaped the
breaker. The fix threads `since_ts` (resolved from agent_state metrics_epoch)
into the aggregate so the breaker evaluates the SAME rows the operator sees.
"""
from __future__ import annotations

import sqlite3

import pytest

from trading_corp.agents.strategies._whale_autopause import (
    resolve_epoch,
    should_autopause,
)

EPOCH = "2026-07-01T00:00:00+00:00"
PRE = "2026-06-15T00:00:00+00:00"   # before epoch
POST = "2026-07-10T00:00:00+00:00"  # after epoch


def _mk_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE polymarket_round_trips (
               division TEXT, won INTEGER, realized_pnl REAL,
               entry_ts TEXT, extra_json TEXT)"""
    )
    conn.execute(
        """CREATE TABLE agent_state (
               agent TEXT, key TEXT, value_json TEXT,
               PRIMARY KEY (agent, key))"""
    )
    return conn


def _seed(conn, whale, entry_ts, *, wins, losses, win_pnl, loss_pnl):
    ex = f'{{"whale_user_name": "{whale}"}}'
    rows = (
        [(("polymarket_copy_trading"), 1, win_pnl, entry_ts, ex)] * wins
        + [(("polymarket_copy_trading"), 0, loss_pnl, entry_ts, ex)] * losses
    )
    conn.executemany(
        "INSERT INTO polymarket_round_trips "
        "(division, won, realized_pnl, entry_ts, extra_json) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()


def _call(conn, whale, since_ts):
    return should_autopause(
        conn,
        whale_name=whale,
        table="polymarket_round_trips",
        name_field="whale_user_name",
        division="polymarket_copy_trading",
        since_ts=since_ts,
    )


def test_masked_whale_escapes_full_history_but_trips_forward():
    """superbeter007-shape: profitable pre-epoch, toxic post-epoch."""
    conn = _mk_conn()
    # Pre-epoch: 40 wins @ +2.0 = +80 (WR 100%)
    _seed(conn, "superbeter007", PRE, wins=40, losses=0, win_pnl=2.0, loss_pnl=0.0)
    # Post-epoch: n=40, 2 wins @ +1.0, 38 losses @ -2.0 = +2 - 76 = -74 (WR 5%)
    _seed(conn, "superbeter007", POST, wins=2, losses=38, win_pnl=1.0, loss_pnl=-2.0)

    # FULL history (pre-fix behavior): +6 total, WR 52.5% -> NOT triggered.
    trig_full, s_full = _call(conn, "superbeter007", None)
    assert trig_full is False
    assert s_full["n_resolved"] == 80
    assert s_full["total_realized_pnl"] == pytest.approx(6.0)

    # FORWARD window (the fix): n=40, WR 5%, -$74 -> TRIGGERED.
    trig_fwd, s_fwd = _call(conn, "superbeter007", EPOCH)
    assert trig_fwd is True
    assert s_fwd["n_resolved"] == 40
    assert s_fwd["win_rate_pct"] == pytest.approx(5.0)
    assert s_fwd["total_realized_pnl"] == pytest.approx(-74.0)


def test_backward_compat_full_history_loser_still_trips():
    """A whale toxic across ALL history still trips with since_ts=None."""
    conn = _mk_conn()
    _seed(conn, "damed21", POST, wins=0, losses=40, win_pnl=0.0, loss_pnl=-2.0)
    trig, s = _call(conn, "damed21", None)
    assert trig is True
    assert s["n_resolved"] == 40 and s["total_realized_pnl"] == pytest.approx(-80.0)


def test_pre_epoch_only_loser_not_paused_in_forward_window():
    """Stale pre-epoch losses must NOT pause when the forward window is empty."""
    conn = _mk_conn()
    _seed(conn, "olddog", PRE, wins=0, losses=40, win_pnl=0.0, loss_pnl=-2.0)
    trig, s = _call(conn, "olddog", EPOCH)
    assert trig is False
    assert s["n_resolved"] == 0  # no rows inside the forward window


def test_small_forward_sample_not_paused():
    """n < 30 in-window is insufficient sample -> no pause even if negative."""
    conn = _mk_conn()
    _seed(conn, "newbie", POST, wins=1, losses=10, win_pnl=1.0, loss_pnl=-2.0)
    trig, s = _call(conn, "newbie", EPOCH)
    assert trig is False and s["n_resolved"] == 11


def test_resolve_epoch_reads_agent_state_and_defaults():
    conn = _mk_conn()
    # Absent -> default
    assert resolve_epoch(conn, "polymarket_copy_trader") is None
    assert resolve_epoch(conn, "kalshi_copy_trader", default="FALLBACK") == "FALLBACK"
    # Present (JSON-encoded string, as set_agent_state writes it) -> ISO value
    conn.execute(
        "INSERT INTO agent_state (agent, key, value_json) VALUES (?,?,?)",
        ("polymarket_copy_trader", "metrics_epoch", f'"{EPOCH}"'),
    )
    conn.commit()
    assert resolve_epoch(conn, "polymarket_copy_trader") == EPOCH
    # Garbage epoch -> treated as unset (falls back to default)
    conn.execute(
        "UPDATE agent_state SET value_json=? WHERE agent=? AND key='metrics_epoch'",
        ('"not-a-date"', "polymarket_copy_trader"),
    )
    conn.commit()
    assert resolve_epoch(conn, "polymarket_copy_trader", default=None) is None
