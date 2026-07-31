"""LOCKED canonical exit-pressure contract for the `robinhood_pead` division —
pure functions, the SINGLE source of truth.

Both sides import this module and must NOT re-implement the formulas:
  - the operations dashboard (`web/data.py:build_pead_view`) — to DISPLAY each
    position's nearness to its four competing exits;
  - the Phase-2 live exit engine — which FIRES a rule when its pressure reaches
    1.0 (`stop`/`drift`/`time`) or the guard date arrives.
If they diverged, the dashboard would show a position approaching an exit at a
different price than the engine actually fires it.

The four rules (fixed palette):
  STOP  #f1556c  price ≤ max(entry − 2.5*ATR14, post-earnings swing low)
  DRIFT #56d6e0  gave back ≥50% of the earnings-day GAP (invalidation, not "target")
  GUARD #b990ff  ≤2 trading days before the next earnings date — never hold through it
  TIME  #e0b14a  60 trading days held (max holding window)

Each rule carries a 0..1 pressure; governing = argmax; fuse fill = max; fuse
COLOR is by urgency (green/amber/red), independent of which rule governs.

Reference points are intentionally different per rule:
  stop  — from our ENTRY (entry risk)
  drift — from the earnings-GAP TOP (the reaction level, entry-timing-independent:
          give-back is measured from `earnings_gap_top`, NOT from entry_price)
  guard — calendar (trading days to the next earnings date)
  time  — hold duration
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Locked constants
MAX_HOLD_TRADING_DAYS = 60
DRIFT_GIVEBACK = 0.50          # drift-dead at ≥50% of the gap given back
GUARD_LEAD_DAYS = 2           # flatten ≤2 trading days before next earnings
GUARD_RAMP_DAYS = 60         # pressure ramp window toward the guard date (config knob)
URGENCY_AMBER = 0.55
URGENCY_RED = 0.80

RULE_COLORS = {
    "stop": "#f1556c", "drift": "#56d6e0", "guard": "#b990ff", "time": "#e0b14a",
}
# Governing tie-break order (earliest wins on an exact tie).
_RULE_ORDER = ("stop", "drift", "guard", "time")


@dataclass(frozen=True)
class PositionPrimitives:
    """Static per-position primitives — from `paper_trade_record.extra_json`
    (written by the Phase-2 exit engine) plus the entry price column."""
    entry_price: float
    entry_atr_14: float
    post_earnings_swing_low: float
    pre_earnings_close: float
    earnings_gap_top: float


@dataclass(frozen=True)
class Pressures:
    stop: float
    drift: float
    guard: float
    time: float
    governing: str           # 'stop' | 'drift' | 'guard' | 'time'
    fuse_pct: float          # max(pressures)
    fuse_color: str          # 'green' | 'amber' | 'red' (by urgency)
    governing_color: str     # hex of the governing rule


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    return 1.0 if x > 1.0 else x


def primitives_from_extra(extra: Mapping, entry_price) -> PositionPrimitives | None:
    """Build PositionPrimitives from a `paper_trade_record.extra_json` mapping +
    the entry price. Returns None if ANY required key is absent — the dashboard
    renders the graceful 'awaiting position metadata' placeholder in that case
    (pressure-empty-first: true until the Phase-2 engine writes the keys)."""
    try:
        return PositionPrimitives(
            entry_price=float(entry_price),
            entry_atr_14=float(extra["entry_atr_14"]),
            post_earnings_swing_low=float(extra["post_earnings_swing_low"]),
            pre_earnings_close=float(extra["pre_earnings_close"]),
            earnings_gap_top=float(extra["earnings_gap_top"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def stop_level(p: PositionPrimitives) -> float:
    return max(p.entry_price - 2.5 * p.entry_atr_14, p.post_earnings_swing_low)


def earnings_gap_usd(p: PositionPrimitives) -> float:
    return p.earnings_gap_top - p.pre_earnings_close


def drift_dead_level(p: PositionPrimitives) -> float:
    return p.earnings_gap_top - DRIFT_GIVEBACK * earnings_gap_usd(p)


def fuse_color(fuse_pct: float) -> str:
    if fuse_pct >= URGENCY_RED:
        return "red"
    if fuse_pct >= URGENCY_AMBER:
        return "amber"
    return "green"


def compute_pressures(
    p: PositionPrimitives,
    last: float,
    held_trading_days: int,
    days_to_next_earnings: int | None,
    drift_price: float | None = None,
) -> Pressures:
    """The locked pressure computation. `last` = live INTRADAY price (drives STOP);
    `held_trading_days` and `days_to_next_earnings` are supplied by the caller (they
    need 'now' / a trading-day calendar — keep this function pure).

    `drift_price`, when provided, is the price DRIFT is measured against instead of
    `last`: the live exit engine passes the latest COMPLETED daily-bar close, so
    DRIFT is a daily-close rule while STOP stays intraday. Backward-compatible —
    omit it and DRIFT falls back to `last` (the dashboard's display is unchanged)."""
    # STOP — distance closed from entry toward the stop level (always intraday `last`).
    sl = stop_level(p)
    denom = p.entry_price - sl
    stop = _clamp01((p.entry_price - last) / denom) if denom > 0 else 0.0

    # DRIFT — give-back of the earnings-day gap, from the GAP TOP. Measured against
    # `drift_price` (the completed daily close, live engine) when given, else `last`.
    drift_ref = last if drift_price is None else drift_price
    gap = earnings_gap_usd(p)
    drift = _clamp01(((p.earnings_gap_top - drift_ref) / gap) / DRIFT_GIVEBACK) if gap > 0 else 0.0

    # GUARD — proximity to (next earnings − GUARD_LEAD_DAYS).
    if days_to_next_earnings is None:
        guard = 0.0
    else:
        days_to_guard = days_to_next_earnings - GUARD_LEAD_DAYS
        guard = _clamp01((GUARD_RAMP_DAYS - days_to_guard) / GUARD_RAMP_DAYS)

    # TIME — fraction of the max holding window elapsed.
    time = _clamp01(held_trading_days / MAX_HOLD_TRADING_DAYS)

    vals = {"stop": stop, "drift": drift, "guard": guard, "time": time}
    governing = max(_RULE_ORDER, key=lambda r: vals[r])  # ties → earliest in _RULE_ORDER
    fuse_pct = vals[governing]
    return Pressures(
        stop=stop, drift=drift, guard=guard, time=time,
        governing=governing, fuse_pct=fuse_pct,
        fuse_color=fuse_color(fuse_pct), governing_color=RULE_COLORS[governing],
    )
