"""Fixture tests for the Phase C 5-factor backtest arm.

Three coverage goals:
  1. `_shim_cache_at` — verifies the as-of-`ts` cache slice respects
     in-progress-bucket exclusion and `max_bars` truncation.
  2. End-to-end: synthetic bars where the 5-factor gate should reject
     every alert → zero fires.
  3. End-to-end: synthetic bars where the gate should PASS every alert
     → expected fires (one per non-cooldown alert).
  4. Pre-committed acceptance evaluator — known PASS/FAIL inputs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.backtest_bitunix_confluence import (
    ACCEPTANCE_THRESHOLDS,
    BacktestResult,
    _crosstab_2x2,
    _evaluate_acceptance,
    _shim_cache_at,
    run_backtest,
)
from trading_corp.agents.strategies.bitunix_confluence import (
    AlertEvent,
    BitUnixConfluenceConfig,
    Tier,
)
from trading_corp.agents.strategies.btc_accumulator import (
    FactorConfig,
    GuardConfig,
)
from trading_corp.agents.strategies.bitunix_confluence_gate import (
    ConfluenceGateConfig,
)


# ─── _shim_cache_at ─────────────────────────────────────────────────────


def _make_15m_bars(start: datetime, n: int) -> list[dict]:
    return [
        {
            "ts": start + timedelta(minutes=15 * i),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 + i, "volume": 10.0,
        }
        for i in range(n)
    ]


def test_shim_cache_excludes_in_progress_bucket():
    """ts sits inside the 5th bar → that bar is excluded; 4 complete
    bars come back."""
    start = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
    bars = _make_15m_bars(start, 10)
    # ts in the middle of bar 5 (starts at 1:00 UTC)
    ts = start + timedelta(hours=1, minutes=7)
    cache = _shim_cache_at(bars, ts, 900, max_bars=20)
    # Bars 0..3 (start 00:00, 00:15, 00:30, 00:45) are complete
    assert len(cache.bars) == 4
    assert cache.bars[-1].close == 103.0


def test_shim_cache_max_bars_truncates_to_recent_window():
    start = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
    bars = _make_15m_bars(start, 100)
    ts = start + timedelta(hours=30)
    cache = _shim_cache_at(bars, ts, 900, max_bars=5)
    assert len(cache.bars) == 5
    # Most recent 5 complete bars
    assert cache.bars[-1].close > cache.bars[0].close


def test_shim_cache_handles_empty_bars():
    ts = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
    cache = _shim_cache_at([], ts, 900, max_bars=10)
    assert cache.bars == []


# ─── end-to-end with synthetic alerts + bars ────────────────────────────


def _alert(ts: datetime, signal_name: str = "accubuy") -> AlertEvent:
    return AlertEvent(ts=ts, signal_name=signal_name)


def _make_1m_bars(
    *, start: datetime, n: int,
    base_close: float = 30000.0,
    close_walk: float = 1.0,
    vol_walk: float = 0.0,
) -> list[dict]:
    return [
        {
            "ts": start + timedelta(minutes=i),
            "open": base_close + close_walk * i,
            "high": base_close + close_walk * i + 5,
            "low": base_close + close_walk * i - 5,
            "close": base_close + close_walk * i,
            "volume": 10.0 + vol_walk * i,
        }
        for i in range(n)
    ]


def _simple_config() -> BitUnixConfluenceConfig:
    """Minimal scorer config that fires PREMIUM on the synthetic
    `AccuBuy` factor at weight=4. PA factors / guards turned off so the
    bare scorer doesn't drag in price-context signals that would shift
    the verdict."""
    empty_guard = GuardConfig(window_minutes=60, brackets=())
    return BitUnixConfluenceConfig(
        enabled=True,
        min_score_to_fire=1,
        premium_threshold=4, standard_threshold=2, weak_threshold=1,
        cooldown_seconds=240 * 60,
        dedupe_within_ttl=False,
        factors={
            "accubuy": FactorConfig(
                name="accubuy", weight=4, side="buy", ttl_minutes=180,
            ),
        },
        sell_on_rush=empty_guard,
        buy_on_fall=empty_guard,
        pa_factors_in_score=False,
        guards_in_score=False,
    )


def test_five_factor_arm_zero_fires_when_gate_disabled_yet_min_score_high():
    """Gate enabled with min_gate_score=5 on synthetic flat bars → no factor
    can pass → zero fires, all rejected."""
    start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    # 24 hours of flat bars (1m × 1440)
    bars = _make_1m_bars(start=start, n=24 * 60 * 18, close_walk=0.0)
    alerts = [_alert(start + timedelta(hours=12))]
    config = _simple_config()
    gate_cfg = ConfluenceGateConfig(enabled=True, min_gate_score=5)
    _ledger, trades, summary = run_backtest(
        alerts=alerts, bars=bars, config=config,
        starting_equity=10_000.0,
        gate="five_factor", gate_config=gate_cfg,
        arm_name="test_5f_strict",
    )
    assert summary.gate_kind == "five_factor"
    assert summary.n_fires == 0
    assert summary.n_gate_rejected >= 1
    # CVD-fallback flag must surface on every 5f eval
    assert summary.cvd_fallback_evals == summary.gate_evals_total


def test_five_factor_arm_some_fires_when_threshold_loose():
    """min_gate_score=0 effectively passes the gate every time (sum>=0 always),
    so fires == number of non-skip alerts."""
    start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    bars = _make_1m_bars(start=start, n=24 * 60 * 18, close_walk=0.5)
    alerts = [_alert(start + timedelta(hours=12))]
    config = _simple_config()
    gate_cfg = ConfluenceGateConfig(enabled=True, min_gate_score=0)
    _ledger, _trades, summary = run_backtest(
        alerts=alerts, bars=bars, config=config,
        starting_equity=10_000.0,
        gate="five_factor", gate_config=gate_cfg,
        arm_name="test_5f_loose",
    )
    # 1 alert in, scorer fires PREMIUM, gate at min_score=0 always passes
    assert summary.n_gate_passed == 1
    assert summary.n_fires == 1


# ─── _evaluate_acceptance ──────────────────────────────────────────────


def _result(
    *, profit_factor: float, win_rate: float, n_round_trips: int,
    n_fires: int, n_alerts: int, total_r: float,
) -> BacktestResult:
    return BacktestResult(
        starting_equity=10000, final_equity=10000,
        pct_return=0.0, max_drawdown_pct=0.0,
        n_alerts=n_alerts, n_fires=n_fires, n_skips=0,
        n_cooldown_blocked=0, n_daily_kill_blocked=0,
        fires_by_tier={"PREMIUM": n_fires, "STANDARD": 0, "WEAK": 0},
        fires_by_side={"buy": n_fires, "sell": 0},
        n_round_trips=n_round_trips,
        n_tp=int(n_round_trips * win_rate / 100),
        n_sl=n_round_trips - int(n_round_trips * win_rate / 100),
        n_timeout=0,
        win_rate_pct=win_rate, avg_r=0.0, total_r=total_r,
        avg_bars_held=0.0,
        profit_factor=profit_factor,
    )


def test_acceptance_passes_when_all_thresholds_met():
    gate = _result(
        profit_factor=1.5, win_rate=55.0, n_round_trips=30,
        n_fires=30, n_alerts=200, total_r=10.0,
    )
    pa = _result(
        profit_factor=1.0, win_rate=40.0, n_round_trips=5,
        n_fires=5, n_alerts=200, total_r=2.0,
    )
    res = _evaluate_acceptance(gate, pa)
    assert res["passed_all"] is True


def test_acceptance_fails_when_profit_factor_below_threshold():
    gate = _result(
        profit_factor=1.0,         # below 1.20
        win_rate=55.0, n_round_trips=30, n_fires=30, n_alerts=200,
        total_r=10.0,
    )
    pa = _result(profit_factor=1.0, win_rate=40.0, n_round_trips=5,
                 n_fires=5, n_alerts=200, total_r=2.0)
    res = _evaluate_acceptance(gate, pa)
    assert res["passed_all"] is False
    assert res["checks"]["profit_factor"][0] is False


def test_acceptance_fails_when_fire_rate_outside_band():
    gate = _result(
        profit_factor=1.5, win_rate=55.0, n_round_trips=30,
        n_fires=180,         # 180/200 = 90% → above 50% ceiling
        n_alerts=200, total_r=10.0,
    )
    pa = _result(profit_factor=1.0, win_rate=40.0, n_round_trips=5,
                 n_fires=5, n_alerts=200, total_r=2.0)
    res = _evaluate_acceptance(gate, pa)
    assert res["checks"]["fire_rate"][0] is False
    assert res["passed_all"] is False


def test_acceptance_relative_check_informational_when_pa_thin():
    """PA has fewer than 20 round-trips → relative check passes regardless."""
    gate = _result(
        profit_factor=1.5, win_rate=55.0, n_round_trips=30,
        n_fires=30, n_alerts=200,
        total_r=-99.0,                # worse than PA
    )
    pa = _result(profit_factor=1.0, win_rate=40.0, n_round_trips=5,
                 n_fires=5, n_alerts=200, total_r=2.0)
    res = _evaluate_acceptance(gate, pa)
    assert res["checks"]["relative_total_r"][0] is True


def test_acceptance_relative_check_blocking_when_pa_robust():
    """PA has 25 round-trips → relative check enforced."""
    gate = _result(
        profit_factor=1.5, win_rate=55.0, n_round_trips=30,
        n_fires=30, n_alerts=200,
        total_r=2.0,                  # worse than PA's 10.0
    )
    pa = _result(profit_factor=1.0, win_rate=40.0, n_round_trips=25,
                 n_fires=25, n_alerts=200, total_r=10.0)
    res = _evaluate_acceptance(gate, pa)
    assert res["checks"]["relative_total_r"][0] is False
    assert res["passed_all"] is False


# ─── _crosstab_2x2 ─────────────────────────────────────────────────────


def test_crosstab_counts_by_outcome_pair():
    from scripts.backtest_bitunix_confluence import LedgerEntry

    def _e(ts: str, fired: bool) -> LedgerEntry:
        return LedgerEntry(
            ts=ts, signal_name="x", tier="PREMIUM", side="buy",
            cooldown_blocked=False, net_score=4, final_buy_score=4,
            final_sell_score=0, buy_contributions=[], sell_contributions=[],
            fired=fired, trade_id=None, reason="",
        )

    pa = [_e("a", True), _e("b", True), _e("c", False), _e("d", False)]
    g5 = [_e("a", True), _e("b", False), _e("c", True), _e("d", False)]
    tab = _crosstab_2x2(pa, g5)
    assert tab["both_fire"] == 1
    assert tab["pa_only"] == 1
    assert tab["gate_only"] == 1
    assert tab["neither"] == 1


def test_acceptance_thresholds_match_committed_values():
    """Regression guard: the pre-committed thresholds shouldn't drift."""
    assert ACCEPTANCE_THRESHOLDS["min_profit_factor"] == 1.20
    assert ACCEPTANCE_THRESHOLDS["min_win_rate_pct"] == 45.0
    assert ACCEPTANCE_THRESHOLDS["min_round_trips"] == 20
    assert ACCEPTANCE_THRESHOLDS["fire_rate_pct_range"] == (5.0, 50.0)
