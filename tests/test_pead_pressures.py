"""Tests for the LOCKED pressure contract (`pead_pressures.py`).

Pins the exact stop/drift/guard/time formulas, the gap-top drift reference, the
governing argmax + tie-break, and the urgency fuse-color thresholds — the same
numbers the Phase-2 exit engine will fire on.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies.pead_pressures import (
    PositionPrimitives,
    Pressures,
    compute_pressures,
    drift_dead_level,
    fuse_color,
    primitives_from_extra,
    stop_level,
)

# entry 100, ATR 4 -> entry-2.5*ATR = 90; swing_low 90 -> stop_level 90.
# gap_top 110, pre_close 100 -> gap 10, drift_dead_level 105.
_P = PositionPrimitives(
    entry_price=100.0, entry_atr_14=4.0, post_earnings_swing_low=90.0,
    pre_earnings_close=100.0, earnings_gap_top=110.0,
)


def _press(last, held=0, dne=62) -> Pressures:
    return compute_pressures(_P, last, held_trading_days=held, days_to_next_earnings=dne)


# ── STOP (from entry) ──────────────────────────────────────────────────────
def test_stop_level_value():
    assert stop_level(_P) == pytest.approx(90.0)


@pytest.mark.parametrize("last,expected", [
    (100.0, 0.0),     # at entry
    (95.0, 0.5),      # halfway to stop
    (90.0, 1.0),      # at stop_level -> fires
    (80.0, 1.0),      # gapped through -> clamp 1
    (105.0, 0.0),     # above entry (winner) -> clamp 0
])
def test_stop_pressure(last, expected):
    assert _press(last).stop == pytest.approx(expected)


# ── DRIFT (from the GAP TOP, not entry) ────────────────────────────────────
def test_drift_dead_level_value():
    assert drift_dead_level(_P) == pytest.approx(105.0)


@pytest.mark.parametrize("last,expected", [
    (110.0, 0.0),     # at the gap top -> no give-back
    (107.5, 0.5),     # gave back 25% of the gap
    (105.0, 1.0),     # drift_dead_level (50% given back) -> fires
    (100.0, 1.0),     # back to pre-earnings close (100% given back) -> clamp 1
    (112.0, 0.0),     # drifted UP past the gap top -> clamp 0
])
def test_drift_pressure_measured_from_gap_top(last, expected):
    assert _press(last).drift == pytest.approx(expected)


def test_drift_price_overrides_last_for_drift_only():
    # FIX 2: STOP always reads intraday `last`; DRIFT reads `drift_price` (the daily
    # close) when provided. last=110 (winner -> stop 0, and drift 0 if measured from
    # last); drift_price=105 (= drift_dead) -> drift fires from the daily close only.
    pr = compute_pressures(_P, 110.0, held_trading_days=0, days_to_next_earnings=999,
                           drift_price=105.0)
    assert pr.stop == pytest.approx(0.0)      # stop from last=110 (above entry) -> 0
    assert pr.drift == pytest.approx(1.0)     # drift from drift_price=105 = drift_dead -> fires
    # Backward-compatible: omit drift_price and drift falls back to `last` (dashboard).
    assert compute_pressures(_P, 110.0, held_trading_days=0,
                             days_to_next_earnings=999).drift == pytest.approx(0.0)


# ── GUARD (calendar) ───────────────────────────────────────────────────────
@pytest.mark.parametrize("dne,expected", [
    (2, 1.0),         # exactly at guard (next earnings - 2 trading days)
    (32, 0.5),        # 30 trading days from the guard point
    (62, 0.0),        # a full ramp window out
    (1, 1.0),         # past the guard point -> clamp 1
    (None, 0.0),      # unknown next-earnings -> no guard pressure
])
def test_guard_pressure(dne, expected):
    assert _press(110.0, dne=dne).guard == pytest.approx(expected)


# ── TIME ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("held,expected", [(0, 0.0), (30, 0.5), (60, 1.0), (70, 1.0)])
def test_time_pressure(held, expected):
    assert _press(110.0, held=held).time == pytest.approx(expected)


# ── governing / fuse ───────────────────────────────────────────────────────
def test_governing_is_argmax_with_color_and_fuse():
    # last at gap top -> stop 0, drift 0; far from earnings -> guard 0; held 36 -> time 0.6
    pr = _press(110.0, held=36, dne=62)
    assert pr.governing == "time"
    assert pr.fuse_pct == pytest.approx(0.6)
    assert pr.fuse_color == "amber"
    assert pr.governing_color == "#e0b14a"


def test_governing_tie_breaks_to_earliest_rule():
    # gap_top 100 / pre_close 80 -> gap 20; last 95 -> drift = (100-95)/20/0.5 = 0.5.
    # stop_level 90; last 95 -> stop = (100-95)/10 = 0.5. Tie stop==drift -> 'stop' wins.
    p = PositionPrimitives(100.0, 4.0, 90.0, 80.0, 100.0)
    pr = compute_pressures(p, 95.0, held_trading_days=0, days_to_next_earnings=999)
    assert pr.stop == pytest.approx(0.5)
    assert pr.drift == pytest.approx(0.5)
    assert pr.governing == "stop"


@pytest.mark.parametrize("pct,color", [
    (0.0, "green"), (0.54, "green"), (0.55, "amber"), (0.79, "amber"),
    (0.80, "red"), (0.95, "red"),
])
def test_fuse_color_urgency_thresholds(pct, color):
    assert fuse_color(pct) == color


# ── primitives_from_extra (placeholder gate) ───────────────────────────────
def test_primitives_from_extra_full():
    extra = {
        "entry_atr_14": 4.0, "post_earnings_swing_low": 90.0,
        "pre_earnings_close": 100.0, "earnings_gap_top": 110.0,
    }
    p = primitives_from_extra(extra, 100.0)
    assert p is not None and p == _P


@pytest.mark.parametrize("extra", [
    {},  # nothing written yet (pressure-empty placeholder)
    {"entry_atr_14": 4.0},  # partial
    {"entry_atr_14": "x", "post_earnings_swing_low": 90.0,
     "pre_earnings_close": 100.0, "earnings_gap_top": 110.0},  # bad type
])
def test_primitives_from_extra_missing_or_bad_returns_none(extra):
    assert primitives_from_extra(extra, 100.0) is None
