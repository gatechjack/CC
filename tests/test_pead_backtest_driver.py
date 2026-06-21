"""Offline integration test for the PEAD backtest orchestration
(`pead_backtest_driver.py`): synthetic EPS + bars exercise the full pipeline
group-by-wave -> SUE -> screen -> select -> backtest -> report, with NO
network. Pins that a strong-SUE name is selected and a screen-failing name
(utilities) is dropped even with the same strong SUE.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading_corp.agents.strategies.pead_backtest import Bar, BacktestParams
from trading_corp.agents.strategies.pead_backtest_driver import (
    build_signals,
    format_report,
    run_split_backtest,
    split_is_oos,
)
from trading_corp.agents.strategies.pead_signal import ScreenParams, SueParams
from trading_corp.data.earnings_provider import QuarterlyEPS

_START = date(2024, 2, 15)
_ANNOUNCE = _START + timedelta(days=15)   # 2024-03-01 -> bar index 15


def _make_bars() -> list[Bar]:
    rows: list[tuple[float, float, float, float]] = []
    for _ in range(15):
        rows.append((100, 100, 100, 100))            # flat lead
    rows.append((100, 120, 100, 118))                # 15: announcement pop (gap up)
    rows.append((118, 119, 117, 118.5))              # 16: entry bar
    for j in range(1, 25):                           # 17..40: steady rise, tight range
        o = 118 + j * 0.3
        rows.append((o, o + 0.5, o - 0.5, o + 0.2))
    return [
        Bar(_START + timedelta(days=i), o, h, l, c, 2_000_000.0)
        for i, (o, h, l, c) in enumerate(rows)
    ]


def _make_eps() -> list[QuarterlyEPS]:
    # 8 prior quarters (pre-window) + the in-window announcement at _ANNOUNCE.
    dates = [
        date(2022, 3, 1), date(2022, 6, 1), date(2022, 9, 1), date(2022, 12, 1),
        date(2023, 3, 1), date(2023, 6, 1), date(2023, 9, 1), date(2023, 12, 1),
        _ANNOUNCE,
    ]
    actuals = [10, 10, 10, 10, 12, 11, 13, 11, 16]   # SUE ~3.46 (lookback=3) at the last
    return [
        QuarterlyEPS(f"{d.year}Q{(d.month - 1) // 3 + 1}", d, float(a), None, None)
        for d, a in zip(dates, actuals)
    ]


_SUE = SueParams(lookback=3, sue_threshold=1.5, top_quintile=False)
_PARAMS = BacktestParams(
    entry_delay_days=1, atr_period=2, max_hold_trading_days=10,
    slippage_bps=0.0, half_spread_bps=0.0,
)


def test_build_signals_selects_strong_sue_and_drops_utilities():
    bars = _make_bars()
    eps = _make_eps()
    signals = build_signals(
        {"WIN": eps, "BLOCK": eps},
        {"WIN": bars, "BLOCK": bars},
        {
            "WIN": {"market_cap": 5e9, "sector": "Technology"},
            "BLOCK": {"market_cap": 5e9, "sector": "Utilities"},
        },
        sue_params=_SUE,
        screen_params=ScreenParams(),
        window_start=date(2024, 1, 1),
    )
    assert [s.symbol for s in signals] == ["WIN"]
    assert signals[0].announcement_date == _ANNOUNCE
    assert signals[0].sue == pytest.approx(3.4641, rel=1e-3)


def test_full_pipeline_runs_and_reports_a_winner():
    bars = _make_bars()
    eps = _make_eps()
    signals = build_signals(
        {"WIN": eps}, {"WIN": bars},
        {"WIN": {"market_cap": 5e9, "sector": "Technology"}},
        sue_params=_SUE, screen_params=ScreenParams(), window_start=date(2024, 1, 1),
    )
    reports = run_split_backtest(signals, _PARAMS, split_date=date(2025, 1, 1))
    all_rep = reports["all"]
    assert all_rep.metrics["n_trades"] == 1
    assert all_rep.metrics["win_rate"] == 1.0
    assert all_rep.trades[0].exit_reason == "time"
    assert all_rep.ending_equity > all_rep.starting_equity
    # the single 2024 trade is in-sample; out-of-sample (>=2025) is empty
    assert reports["in_sample"].metrics["n_trades"] == 1
    assert reports["out_of_sample"].metrics.get("n_trades", 0) == 0
    # format_report renders without error
    assert "trades" in format_report("all", all_rep)


def test_split_is_oos_partitions_by_date():
    bars = _make_bars()
    eps = _make_eps()
    signals = build_signals(
        {"WIN": eps}, {"WIN": bars},
        {"WIN": {"market_cap": 5e9, "sector": "Technology"}},
        sue_params=_SUE, window_start=date(2024, 1, 1),
    )
    is_sig, oos_sig = split_is_oos(signals, date(2024, 3, 1))
    assert len(is_sig) == 0       # announcement is exactly 2024-03-01 -> OOS (>=)
    assert len(oos_sig) == 1
