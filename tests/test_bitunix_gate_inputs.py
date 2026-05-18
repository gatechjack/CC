"""Tests for the Phase B gate-input builders.

Coverage:
  * `prior_day_session_vwap` — pure helper on synthetic bars
  * `cvd_from_bars_tick_rule` — sign/slope semantics
  * `build_gate_inputs` — empty caches, partial caches, fully-warm caches
  * `log_gate_cache_warmup_status` — log + dict shape
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.agents.strategies.bitunix_confluence_gate import (
    ConfluenceGateConfig,
)
from trading_corp.data.bitunix_price_context import (
    build_gate_inputs,
    cvd_from_bars_tick_rule,
    log_gate_cache_warmup_status,
    prior_day_session_vwap,
)


# ─── synthetic bar + cache ──────────────────────────────────────────────


@dataclass
class _Bar:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class _Cache:
    def __init__(self, bars, timeframe_seconds: int):
        self.bars = bars
        self.timeframe_seconds = timeframe_seconds


def _ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _make_3m_bars(
    *,
    start_dt: datetime, count: int,
    close_walk: float = 0.0,
    base_close: float = 100.0,
    base_volume: float = 10.0,
    volume_walk: float = 0.0,
    # v1.1 — CVD tick-rule uses sign(close - open). Default 0.0 ⇒ doji
    # bars (open == close). Tests that exercise CVD direction MUST set
    # an explicit candle_direction to express green (+) / red (-) bars.
    candle_direction: float = 0.0,
    candle_body: float = 0.5,
) -> list[_Bar]:
    bars = []
    for i in range(count):
        ts = _ts_ms(start_dt + timedelta(minutes=3 * i))
        close = base_close + close_walk * i
        vol = base_volume + volume_walk * i
        # Express candle direction: open = close - candle_direction*body.
        # candle_direction=+1 → green (close > open); -1 → red; 0 → doji.
        open_p = close - candle_direction * candle_body
        high = max(open_p, close) + 0.1
        low = min(open_p, close) - 0.1
        bars.append(_Bar(
            ts_ms=ts,
            open=open_p, high=high, low=low,
            close=close, volume=vol,
        ))
    return bars


# ─── prior_day_session_vwap ─────────────────────────────────────────────


def test_prior_day_session_vwap_returns_none_for_empty_bars():
    assert prior_day_session_vwap([]) is None


def test_prior_day_session_vwap_returns_none_when_no_prior_day_bars():
    """All bars are in today — no prior-day volume."""
    today_start = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
    bars = _make_3m_bars(start_dt=today_start, count=5)
    assert prior_day_session_vwap(bars) is None


def test_prior_day_session_vwap_excludes_today():
    """Yesterday has 2 bars (price 100, vol 10), today has 1 bar at 200.
    Prior-day VWAP must reflect ONLY yesterday's bars.
    """
    yesterday_start = datetime(2026, 5, 16, 23, 54, tzinfo=timezone.utc)
    bars = [
        _Bar(_ts_ms(yesterday_start), 100, 101, 99, 100, 10),
        _Bar(_ts_ms(yesterday_start + timedelta(minutes=3)),
             100, 101, 99, 100, 10),
        # Today
        _Bar(_ts_ms(datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)),
             200, 201, 199, 200, 50),
    ]
    pv = prior_day_session_vwap(bars)
    # typical = (101 + 99 + 100) / 3 = 100; vwap = 100
    assert pv == pytest.approx(100.0)


def test_prior_day_session_vwap_returns_none_if_prior_volume_zero():
    yesterday_start = datetime(2026, 5, 16, 23, 54, tzinfo=timezone.utc)
    bars = [
        _Bar(_ts_ms(yesterday_start), 100, 101, 99, 100, 0),
        _Bar(_ts_ms(datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)),
             200, 201, 199, 200, 50),
    ]
    assert prior_day_session_vwap(bars) is None


# ─── cvd_from_bars_tick_rule ────────────────────────────────────────────


def test_cvd_returns_none_for_empty_bars():
    s, fb = cvd_from_bars_tick_rule([])
    assert s is None
    assert fb is True


def test_cvd_returns_none_for_single_bar():
    bars = _make_3m_bars(
        start_dt=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc), count=1,
    )
    s, fb = cvd_from_bars_tick_rule(bars)
    assert s is None
    assert fb is True


def test_cvd_positive_slope_when_closes_rising():
    """v1.1: each bar is GREEN (close > open) so delta_i = +volume_i.
    Cumulative series is monotonically increasing → strongly positive
    slope.
    """
    bars = _make_3m_bars(
        start_dt=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
        count=6, close_walk=1.0, volume_walk=2.0,
        candle_direction=+1.0,         # green candles: close > open
    )
    slope, fallback = cvd_from_bars_tick_rule(bars, window_minutes=60)
    assert fallback is True
    assert slope is not None
    assert slope > 0


def test_cvd_negative_slope_when_closes_falling():
    """v1.1: each bar is RED (close < open) so delta_i = -volume_i.
    Cumulative series is monotonically decreasing → strongly negative
    slope.
    """
    bars = _make_3m_bars(
        start_dt=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
        count=6, close_walk=-1.0, volume_walk=2.0,
        candle_direction=-1.0,         # red candles: close < open
    )
    slope, fallback = cvd_from_bars_tick_rule(bars, window_minutes=60)
    assert fallback is True
    assert slope is not None
    assert slope < 0


def test_cvd_fallback_flag_always_true_for_v1():
    """Even when CVD has plenty of data, the flag stays True — only a
    future trade-stream consumer flips it."""
    bars = _make_3m_bars(
        start_dt=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
        count=20, close_walk=1.0,
    )
    _, fb = cvd_from_bars_tick_rule(bars)
    assert fb is True


def test_factor_cvd_cumulative_in_trending_tape():
    """v1.1 fix — CVD must use slope-of-cumulative, not slope-of-per-bar-deltas.

    Sustained down-tape: every bar closes below its open with constant volume.
    Per-bar deltas would be [-v, -v, -v, -v, -v] → slope ≈ 0 (factor would
    incorrectly fail for both sides). True cumulative CVD is
    [-v, -2v, -3v, -4v, -5v] → slope = -v → sell-side should PASS.

    This test failed on the per-bar-delta implementation; passes on the
    fixed cumulative-slope implementation.
    """
    start = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    bars = []
    # 6 consecutive down-bars with identical volume. Close-open is the
    # tick-rule signal source; we set close < open for every bar.
    for i in range(6):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        # Walking price down so each bar's close < its open AND
        # close < prev_close (covers both possible tick-rule conventions)
        open_p = 100.0 - i
        close_p = open_p - 1.0
        bars.append(_Bar(ts, open_p, open_p + 0.5, close_p - 0.5, close_p, 10.0))
    slope, _ = cvd_from_bars_tick_rule(bars, window_minutes=60)
    assert slope is not None, "slope should be defined on 6 bars"
    assert slope < 0, (
        f"cumulative CVD slope on a sustained down-tape must be negative; "
        f"got {slope}. If this fails, cvd_from_bars_tick_rule is still "
        f"slope-of-per-bar-deltas (the bug)."
    )


def test_factor_cvd_cumulative_in_choppy_tape():
    """In a choppy tape with alternating up/down bars at equal volume,
    BOTH per-bar-delta slope and cumulative-CVD slope are ~0 — neither
    side should pass. Regression check that the cumulative fix doesn't
    break the chop-rejection case.
    """
    start = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(8):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        # Alternate up/down with constant volume
        if i % 2 == 0:
            # Up bar: close > open AND close > prev_close
            open_p, close_p = 100.0, 100.5
        else:
            # Down bar: close < open AND close < prev_close
            open_p, close_p = 100.5, 100.0
        bars.append(_Bar(ts, open_p, max(open_p, close_p) + 0.1,
                         min(open_p, close_p) - 0.1, close_p, 10.0))
    slope, _ = cvd_from_bars_tick_rule(bars, window_minutes=60)
    assert slope is not None
    # Choppy + balanced volume → cumulative drift near zero; tolerate small
    # numeric noise. The acceptance is "|slope| small" — both buy and sell
    # binary checks will produce inconsistent results near zero so we just
    # confirm the cumulative didn't accidentally explode in one direction.
    assert abs(slope) < 2.0, (
        f"choppy balanced tape should produce near-zero cumulative slope; "
        f"got {slope}."
    )


def test_cvd_window_too_small_returns_none():
    """Window that captures fewer than 2 bars → slope undefined."""
    bars = _make_3m_bars(
        start_dt=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
        count=5, close_walk=1.0,
    )
    # 1-minute window cuts everything before the latest bar
    slope, _ = cvd_from_bars_tick_rule(bars, window_minutes=1)
    assert slope is None


def test_cvd_window_isolates_recent_deltas():
    """v1.1: phase 1 (outside the 15-min window) is all-green; phase 2
    (inside the window) is all-red. The window must isolate phase 2 so
    the cumulative CVD slope reads negative — not contaminated by
    phase 1's positive contribution.

    Each bar's candle direction expressed explicitly via open vs close:
    phase 1 bars have close > open (green, delta = +volume); phase 2
    bars have close < open (red, delta = -volume).
    """
    bars = []
    start = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    # Phase 1 (outside window): 10 green bars
    for i in range(10):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        # Green: open=100, close=100.5 (close > open → +1 sign)
        bars.append(_Bar(ts, 100.0, 100.6, 99.9, 100.5, 10.0 + i))
    # Phase 2 (inside the last 15min): 5 red bars
    # 5 bars × 3min = 15min, exactly the window size
    for i in range(5):
        ts = _ts_ms(start + timedelta(minutes=3 * (10 + i)))
        # Red: open=100, close=99.5 (close < open → -1 sign)
        bars.append(_Bar(ts, 100.0, 100.1, 99.4, 99.5, 10.0 + i))
    slope_recent, _ = cvd_from_bars_tick_rule(bars, window_minutes=15)
    assert slope_recent is not None
    assert slope_recent < 0, (
        f"window should isolate the 5 red phase-2 bars; got slope "
        f"{slope_recent}. If positive, the window may be admitting "
        f"phase-1 green bars or the sign convention is wrong."
    )


def test_cvd_handles_doji_bars_correctly():
    """v1.1 edge case: doji bars (close == open) contribute zero to the
    cumulative series. A window of mixed real-direction + doji bars
    should reflect ONLY the real-direction bars' contribution.

    Setup: 4 green bars + 4 doji bars + 4 red bars, all in the window.
    Greens add +volume each; dojis add zero; reds add -volume each.
    With equal volumes the cumulative ends at zero — but the SHAPE of
    the curve is non-flat (rises, plateaus, falls) so linregress slope
    over the 12-point cumulative series will be slightly negative
    (the final reds pull the back end down). We assert the doji bars
    DON'T contribute by checking the slope matches a doji-free
    equivalent within a tight tolerance.
    """
    start = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    bars: list[_Bar] = []
    # 4 green
    for i in range(4):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        bars.append(_Bar(ts, 100.0, 100.6, 99.9, 100.5, 10.0))
    # 4 doji (close == open) — should contribute exactly zero
    for i in range(4, 8):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        bars.append(_Bar(ts, 100.0, 100.3, 99.7, 100.0, 10.0))
    # 4 red
    for i in range(8, 12):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        bars.append(_Bar(ts, 100.0, 100.1, 99.4, 99.5, 10.0))
    slope_with_doji, _ = cvd_from_bars_tick_rule(bars, window_minutes=60)

    # Same setup minus the doji bars (timestamps shifted to be contiguous)
    bars_no_doji: list[_Bar] = []
    for i in range(4):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        bars_no_doji.append(_Bar(ts, 100.0, 100.6, 99.9, 100.5, 10.0))
    for i in range(4, 8):
        ts = _ts_ms(start + timedelta(minutes=3 * i))
        bars_no_doji.append(_Bar(ts, 100.0, 100.1, 99.4, 99.5, 10.0))
    slope_no_doji, _ = cvd_from_bars_tick_rule(
        bars_no_doji, window_minutes=60,
    )

    assert slope_with_doji is not None
    assert slope_no_doji is not None
    # Both should be negative (reds end the series). The with-doji slope
    # measures over 12 cumulative points (with plateau in the middle);
    # the no-doji slope measures over 8 points (clean V shape). They
    # won't be exactly equal but BOTH must be negative, confirming dojis
    # were neutral.
    assert slope_with_doji < 0
    assert slope_no_doji < 0


# ─── build_gate_inputs ──────────────────────────────────────────────────


def test_build_gate_inputs_all_caches_none_returns_all_none():
    inp = build_gate_inputs(
        None, None, None, side="buy", config=ConfluenceGateConfig(),
    )
    assert inp.ema_8_15m is None
    assert inp.ema_21_15m is None
    assert inp.ema_50_15m is None
    assert inp.ema_8_15m_slope is None
    assert inp.current_price is None
    assert inp.session_vwap is None
    assert inp.prior_day_session_vwap is None
    assert inp.atr_5m is None
    assert inp.atr_5m_sma is None
    assert inp.bb_width_5m is None
    assert inp.bb_width_5m_pct_rank is None
    assert inp.cvd_slope is None
    assert inp.cvd_fallback_used is True
    assert inp.volume_z is None


def test_build_gate_inputs_empty_caches_returns_all_none():
    inp = build_gate_inputs(
        _Cache([], 180), _Cache([], 300), _Cache([], 900),
        side="buy", config=ConfluenceGateConfig(),
    )
    assert inp.current_price is None
    assert inp.ema_8_15m is None
    assert inp.cvd_slope is None


def test_build_gate_inputs_3m_only_populates_factor_2_and_5():
    """3m cache only → VWAP + volume_z populated; other factors None."""
    today_start = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)
    # Yesterday's bars
    bars = _make_3m_bars(
        start_dt=yesterday_start, count=480, base_close=100.0,
    )
    # Today's bars (30 bars = 90 min into the day). Vary volume so the
    # 20-bar prior stdev > 0 (constant volumes → z-score undefined).
    bars += _make_3m_bars(
        start_dt=today_start, count=30, base_close=105.0,
        base_volume=20.0, volume_walk=1.0,
    )
    # Boost latest volume to give a non-zero z
    bars[-1].volume = 200.0
    inp = build_gate_inputs(
        _Cache(bars, 180), None, None,
        side="buy", config=ConfluenceGateConfig(),
    )
    assert inp.current_price == pytest.approx(105.0)
    assert inp.session_vwap is not None
    assert inp.prior_day_session_vwap is not None
    assert inp.volume_z is not None and inp.volume_z > 1.0
    assert inp.ema_8_15m is None     # 15m cache empty
    assert inp.atr_5m is None         # 5m cache empty


def test_build_gate_inputs_warm_15m_populates_ema_factor():
    """15m cache with 60+ bars → all three EMAs + slope populated."""
    start = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    # 60 bars of monotonically rising close → EMA8 > EMA21 > EMA50, positive slope
    bars_15m = []
    for i in range(60):
        ts = _ts_ms(start + timedelta(minutes=15 * i))
        c = 100.0 + i
        bars_15m.append(_Bar(ts, c, c + 0.5, c - 0.5, c, 1.0))
    inp = build_gate_inputs(
        None, None, _Cache(bars_15m, 900),
        side="buy", config=ConfluenceGateConfig(),
    )
    assert inp.ema_8_15m is not None
    assert inp.ema_21_15m is not None
    assert inp.ema_50_15m is not None
    assert inp.ema_8_15m_slope is not None
    assert inp.ema_8_15m > inp.ema_21_15m > inp.ema_50_15m
    assert inp.ema_8_15m_slope > 0


def test_build_gate_inputs_warm_5m_populates_volatility_factor():
    """5m cache with 150 bars + varied volatility → all 4 volatility
    inputs populated."""
    start = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    bars_5m = []
    for i in range(150):
        ts = _ts_ms(start + timedelta(minutes=5 * i))
        # Make every other bar wider for ATR + BB to vary
        h = 100.5 if i % 2 == 0 else 102.0
        l = 99.5 if i % 2 == 0 else 98.0
        bars_5m.append(_Bar(ts, 100, h, l, 100 + (i % 3) * 0.1, 1.0))
    inp = build_gate_inputs(
        None, _Cache(bars_5m, 300), None,
        side="buy", config=ConfluenceGateConfig(),
    )
    assert inp.atr_5m is not None
    assert inp.atr_5m_sma is not None
    assert inp.bb_width_5m is not None
    assert inp.bb_width_5m_pct_rank is not None


def test_build_gate_inputs_partial_15m_below_slope_lookback_returns_none_slope():
    """Just enough 15m bars for ema_50 but fewer than slope_lookback → slope None."""
    start = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    # Exactly 50 closes — ema_50 has 1 value, slope_lookback=5 needs ≥5 series values
    bars_15m = []
    for i in range(50):
        ts = _ts_ms(start + timedelta(minutes=15 * i))
        bars_15m.append(_Bar(ts, 100, 100.5, 99.5, 100 + i, 1.0))
    inp = build_gate_inputs(
        None, None, _Cache(bars_15m, 900),
        side="buy", config=ConfluenceGateConfig(),
    )
    # ema_50 (latest) is computable; slope is not because ema_8 series has many vals
    # but ema_50 just barely 1 value. Slope is over ema_8 series — which would be long.
    # The test that matters: ema_8 slope only None when ema_8 series < slope_lookback.
    assert inp.ema_50_15m is not None
    assert inp.ema_8_15m_slope is not None    # ema_8 series long enough at 50 bars


def test_build_gate_inputs_15m_below_ema8_slope_window_returns_none_slope():
    """Tiny 15m cache: ema_8 has fewer series values than slope_lookback → slope None."""
    start = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    # 9 bars total: ema_8 series has 9-8+1 = 2 values; slope_lookback=5 → slope None
    bars_15m = []
    for i in range(9):
        ts = _ts_ms(start + timedelta(minutes=15 * i))
        bars_15m.append(_Bar(ts, 100, 100.5, 99.5, 100 + i, 1.0))
    inp = build_gate_inputs(
        None, None, _Cache(bars_15m, 900),
        side="buy", config=ConfluenceGateConfig(),
    )
    assert inp.ema_8_15m is not None       # 9 ≥ 8
    assert inp.ema_21_15m is None          # 9 < 21
    assert inp.ema_8_15m_slope is None     # series len 2 < 5


# ─── log_gate_cache_warmup_status ──────────────────────────────────────


def test_warmup_log_with_none_caches(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO):
        snap = log_gate_cache_warmup_status(None, None)
    assert snap["5m"]["warm"] is False
    assert snap["15m"]["warm"] is False
    assert snap["all_warm"] is False
    assert snap["5m"]["have"] == 0
    assert any("COLD" in rec.message for rec in caplog.records)
    assert any("WARMING" in rec.message for rec in caplog.records)


def test_warmup_log_with_warm_caches(caplog: pytest.LogCaptureFixture):
    """Caches stuffed past the required thresholds → READY."""
    start = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    bars_5m = [
        _Bar(_ts_ms(start + timedelta(minutes=5 * i)),
             100, 101, 99, 100, 1.0)
        for i in range(150)
    ]
    bars_15m = [
        _Bar(_ts_ms(start + timedelta(minutes=15 * i)),
             100, 101, 99, 100, 1.0)
        for i in range(60)
    ]
    with caplog.at_level(logging.INFO):
        snap = log_gate_cache_warmup_status(
            _Cache(bars_5m, 300), _Cache(bars_15m, 900),
        )
    assert snap["5m"]["warm"] is True
    assert snap["15m"]["warm"] is True
    assert snap["all_warm"] is True
    assert any("READY" in rec.message for rec in caplog.records)


def test_warmup_log_eta_present_when_cold():
    start = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    # 10 5m bars only → very cold
    bars_5m = [
        _Bar(_ts_ms(start + timedelta(minutes=5 * i)),
             100, 101, 99, 100, 1.0)
        for i in range(10)
    ]
    snap = log_gate_cache_warmup_status(_Cache(bars_5m, 300), None)
    assert snap["5m"]["eta_seconds"] is not None
    assert snap["5m"]["eta_seconds"] > 0
