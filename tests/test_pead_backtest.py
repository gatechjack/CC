"""Unit tests for the PEAD backtest engine (`pead_backtest.py`).

Hand-built bar sequences pin the entry timing, the 4-rule exit precedence,
the gap-through fill convention, the friction math, and the portfolio
concurrency / equity-curve logic. Friction is set to 0 in most tests for
exact arithmetic; one test exercises the friction path explicitly.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading_corp.agents.strategies.pead_backtest import (
    Bar,
    BacktestParams,
    EventSignal,
    compute_atr,
    run_backtest,
    simulate_trade,
)


def _mkbars(rows, start=date(2024, 1, 1)) -> list[Bar]:
    return [
        Bar(start + timedelta(days=i), o, h, l, c)
        for i, (o, h, l, c) in enumerate(rows)
    ]


# Shared base scenario: 2 lead bars, announcement pop at idx2, entry at idx3,
# then steady drift up. ATR(period=2, end=3) = 4 -> stop=100, drift_dead=102.
_BASE_ROWS = [
    (100, 101, 99, 100),    # 0
    (100, 101, 99, 100),    # 1  pre-earnings close = 100
    (100, 105, 100, 104),   # 2  announcement (gap up)
    (104, 106, 103, 105),   # 3  entry (open 104)
    (105, 107, 104, 106),   # 4
    (106, 108, 105, 107),   # 5
    (107, 109, 106, 108),   # 6
    (108, 110, 107, 109),   # 7
    (109, 111, 108, 110),   # 8
]

_NO_FRICTION = dict(
    entry_delay_days=1, atr_period=2, hard_stop_atr_mult=2.5,
    drift_dead_giveback=0.5, next_earnings_guard_days=2,
    max_hold_trading_days=5, slippage_bps=0.0, half_spread_bps=0.0,
)


def _sig(rows, **kw) -> EventSignal:
    bars = _mkbars(rows)
    return EventSignal(
        symbol=kw.get("symbol", "TST"),
        announcement_date=bars[2].trade_date,
        sue=kw.get("sue", 2.0),
        bars=bars,
        next_earnings_date=kw.get("next_earnings_date"),
    )


def test_compute_atr_simple_average_true_range():
    bars = _mkbars(_BASE_ROWS)
    # TR(idx2)=5, TR(idx3)=3 -> ATR=4
    assert compute_atr(bars, 3, 2) == pytest.approx(4.0)
    assert compute_atr(bars, 0, 2) is None  # insufficient


def test_entry_is_open_one_day_after_announcement():
    tr = simulate_trade(_sig(_BASE_ROWS), BacktestParams(**_NO_FRICTION))
    assert tr is not None
    assert tr.entry_date == date(2024, 1, 4)   # idx3
    assert tr.entry_price == pytest.approx(104.0)


def test_time_exit_clean_winner():
    tr = simulate_trade(_sig(_BASE_ROWS), BacktestParams(**_NO_FRICTION))
    assert tr.exit_reason == "time"
    assert tr.holding_days == 5
    assert tr.exit_price == pytest.approx(110.0)        # idx8 close
    assert tr.return_pct == pytest.approx((110 - 104) / 104)


def test_hard_stop_fills_at_stop_level():
    rows = list(_BASE_ROWS)
    rows[4] = (104, 104, 98, 99)   # low 98 <= stop 100 (and <= drift_dead 102)
    tr = simulate_trade(_sig(rows), BacktestParams(**_NO_FRICTION))
    assert tr.exit_reason == "hard_stop"               # rule 1 beats drift_dead
    assert tr.exit_price == pytest.approx(100.0)        # open 104 > stop -> fill at stop
    assert tr.holding_days == 1


def test_hard_stop_gap_through_fills_at_open():
    rows = list(_BASE_ROWS)
    rows[4] = (95, 96, 90, 92)     # opens 95, already below stop 100
    tr = simulate_trade(_sig(rows), BacktestParams(**_NO_FRICTION))
    assert tr.exit_reason == "hard_stop"
    assert tr.exit_price == pytest.approx(95.0)         # gap-through -> fill at open


def test_drift_dead_between_stop_and_giveback():
    rows = list(_BASE_ROWS)
    rows[4] = (104, 104, 101, 101)  # low 101: > stop 100, <= drift_dead 102
    tr = simulate_trade(_sig(rows), BacktestParams(**_NO_FRICTION))
    assert tr.exit_reason == "drift_dead"
    assert tr.exit_price == pytest.approx(102.0)        # fill at drift_dead level


def test_drift_dead_measured_from_gap_top_not_entry():
    """Re-align (operator-caught): drift give-back is measured from the earnings
    GAP TOP (announcement-bar close) via the locked pead_pressures contract, NOT
    from our entry. Here the stock gapped to a 110 close (gap_top) then drifted
    down to a 104 entry open. The 50% give-back level is 110 - 0.5*(110-100) =
    105 (gap-top), well ABOVE the old entry-relative 104 - 0.5*(104-100) = 102.
    A dip to 105 must fire drift_dead — it would NOT have under the old level."""
    rows = [
        (100, 101, 99, 100),
        (100, 101, 99, 100),    # pre-earnings close = 100
        (100, 112, 100, 110),   # announcement: close 110 = gap_top
        (104, 106, 103, 104),   # entry: open 104 (already drifted below gap_top)
        (107, 107, 105, 106),   # low 105 == gap-top drift_dead; > stop 100, > old 102
        (106, 108, 105, 107),
        (107, 109, 106, 108),
    ]
    params = BacktestParams(**{**_NO_FRICTION, "max_hold_trading_days": 5})
    tr = simulate_trade(_sig(rows), params)
    assert tr.exit_reason == "drift_dead"
    assert tr.exit_price == pytest.approx(105.0)   # gap-top level (not entry-relative 102)
    assert tr.holding_days == 1


def test_next_earnings_guard_flattens():
    bars = _mkbars(_BASE_ROWS)
    sig = EventSignal("TST", bars[2].trade_date, 2.0, bars,
                      next_earnings_date=bars[6].trade_date)
    tr = simulate_trade(sig, BacktestParams(**_NO_FRICTION))
    # guard_days=2 -> fires at first i with (6 - i) <= 2 -> i=4
    assert tr.exit_reason == "next_earnings_guard"
    assert tr.exit_date == date(2024, 1, 5)             # idx4
    assert tr.exit_price == pytest.approx(106.0)        # idx4 close


def test_friction_makes_entry_higher_and_exit_lower():
    params = BacktestParams(**{**_NO_FRICTION, "slippage_bps": 10.0, "half_spread_bps": 5.0})
    tr = simulate_trade(_sig(_BASE_ROWS), params)
    # buy friction = +15 bps; entry 104 * 1.0015
    assert tr.entry_price == pytest.approx(104.0 * 1.0015)
    # time exit at 110, sell friction -15 bps
    assert tr.exit_price == pytest.approx(110.0 * 0.9985)
    assert tr.return_pct == pytest.approx(
        (110.0 * 0.9985) / (104.0 * 1.0015) - 1.0
    )


def test_simulate_returns_none_on_insufficient_history():
    # announcement at idx0 -> no pre-earnings close -> None
    bars = _mkbars(_BASE_ROWS)
    sig = EventSignal("TST", bars[0].trade_date, 2.0, bars)
    assert simulate_trade(sig, BacktestParams(**_NO_FRICTION)) is None


def test_run_backtest_metrics_and_concurrency():
    # Two non-overlapping winners -> both taken, positive total return.
    s1 = _sig(_BASE_ROWS, symbol="AAA")
    rows2 = [(r[0], r[1], r[2], r[3]) for r in _BASE_ROWS]
    bars2 = _mkbars(rows2, start=date(2024, 3, 1))   # a month later, no overlap
    s2 = EventSignal("BBB", bars2[2].trade_date, 2.5, bars2)
    rep = run_backtest([s1, s2], BacktestParams(**_NO_FRICTION), starting_equity=100_000.0)
    assert rep.metrics["n_trades"] == 2
    assert rep.metrics["win_rate"] == 1.0
    assert rep.ending_equity > rep.starting_equity
    assert rep.skipped_concurrency == 0


def test_run_backtest_enforces_max_concurrent():
    # Three overlapping signals, max_concurrent=1 -> 2 skipped.
    sigs = []
    for i, sym in enumerate(["A", "B", "C"]):
        bars = _mkbars(_BASE_ROWS, start=date(2024, 1, 1) + timedelta(days=i))
        sigs.append(EventSignal(sym, bars[2].trade_date, 2.0, bars))
    params = BacktestParams(**{**_NO_FRICTION, "max_concurrent": 1})
    rep = run_backtest(sigs, params, starting_equity=100_000.0)
    assert rep.metrics["n_trades"] == 1
    assert rep.skipped_concurrency == 2
