"""Unit tests for the BitUnix Futures HTF Regime classifier.

Pin behaviors that the gate at `bitunix_futures_observer` will rely on
once PR 3 wires it in. The classifier is pure-function — these tests
cover synthetic OHLCV across every regime state (clear bull, clear
bear, ranging, transitional, insufficient), the composite math at
each threshold boundary, and every hard-zero override on the trade-
permission matrix.

What we DON'T test here (deferred to PR 2/3):
  - HTF context provider (impure I/O — caches + funding fetch)
  - Observer integration (separate test file)
  - Actual BitUnix kline endpoint
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_corp.agents.strategies.bitunix_htf_regime import (
    HTFContext,
    HTFRegimeConfig,
    Regime,
    RegimeVerdict,
    Session,
    TimeframeBars,
    TimeframeClassification,
    TimeframeRegime,
    TradePermission,
    VolatilityTier,
    adx,
    atr,
    classify_timeframe,
    compute_regime,
    current_session,
    ema,
    find_swing_points,
    get_trade_permissions,
    macd_hist,
    market_structure,
)


# ── PR 4: HTFRegimeConfig.from_dict ─────────────────────────────────


def test_htf_regime_config_from_dict_absent_returns_defaults():
    cfg = HTFRegimeConfig.from_dict({})
    d = HTFRegimeConfig.defaults()
    assert cfg == d
    assert cfg.enabled is False


def test_htf_regime_config_from_dict_present_defaults_enabled_true():
    cfg = HTFRegimeConfig.from_dict({"htf_regime": {"adx_period": 21}})
    assert cfg.enabled is True
    assert cfg.adx_period == 21
    # Other fields fall back to defaults
    d = HTFRegimeConfig.defaults()
    assert cfg.adx_trend_threshold == d.adx_trend_threshold
    assert cfg.swing_lookback == d.swing_lookback


def test_htf_regime_config_from_dict_explicit_enabled_false():
    cfg = HTFRegimeConfig.from_dict({"htf_regime": {"enabled": False, "adx_period": 21}})
    assert cfg.enabled is False
    assert cfg.adx_period == 21


def test_htf_regime_config_from_dict_dict_field_merge():
    cfg = HTFRegimeConfig.from_dict({"htf_regime": {
        "composite_weights": {"d1": 0.6, "h4": 0.3, "h1": 0.1},
    }})
    assert cfg.composite_weights == {"d1": 0.6, "h4": 0.3, "h1": 0.1}


def test_htf_regime_config_from_dict_tuple_field_coerced():
    cfg = HTFRegimeConfig.from_dict({"htf_regime": {
        "ema_periods": [10, 30, 100],
        "macd_periods": [8, 21, 5],
    }})
    assert cfg.ema_periods == (10, 30, 100)
    assert cfg.macd_periods == (8, 21, 5)


# ─── synthetic-bar helpers ──────────────────────────────────────────────


def _bars_from_closes(
    timeframe: str, closes: list[float],
    *, vol: float = 1.0, range_pct: float = 0.5,
) -> TimeframeBars:
    """Build a TimeframeBars where high/low straddle close by `range_pct`."""
    half = range_pct / 100.0 / 2.0
    highs = tuple(c * (1 + half) for c in closes)
    lows = tuple(c * (1 - half) for c in closes)
    opens = tuple(closes)              # opens unused by indicators
    volumes = tuple(vol for _ in closes)
    return TimeframeBars(
        timeframe=timeframe,
        opens=opens, highs=highs, lows=lows,
        closes=tuple(closes), volumes=volumes,
        last_bar_close_ts=datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
    )


def _ramp(start: float, end: float, n: int) -> list[float]:
    """Linear interpolation start→end over n points."""
    if n <= 1:
        return [end]
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


def _flat(value: float, n: int, jitter: float = 0.0) -> list[float]:
    """Constant series with optional small alternating jitter."""
    if jitter == 0:
        return [value] * n
    return [value + (jitter if i % 2 else -jitter) for i in range(n)]


def _zigzag(center: float, amp: float, n: int) -> list[float]:
    """Triangle wave around `center` with amplitude `amp`. Useful for ranging."""
    out: list[float] = []
    for i in range(n):
        # Period of 8 bars; symmetric up/down
        phase = (i % 8) / 4.0 - 1.0       # in [-1, +1]
        out.append(center + amp * phase)
    return out


def _trending_with_swings(
    start: float, end: float, n: int = 250,
    *, swing_amplitude: float = 2.0,
) -> list[float]:
    """Linear trend overlaid with 6-bar cycles producing clean swing
    structure: each cycle has a peak at offset 2 and a trough at
    offset 4. Successive peaks/troughs ascend (or descend) with the
    overall trend — produces bull/bear market structure that
    `find_swing_points(n=2)` can detect.

    Cycle offsets relative to per-bar trend value: [0, +1, +2, +1, -2, 0]
    Peak at offset 2 needs all 4 bars in [-2..+2] window lower → ✓
    Trough at offset 4 needs all 4 bars in [+2..+6] window higher → ✓
    """
    closes: list[float] = []
    slope = (end - start) / max(n - 1, 1)
    cycle_pattern = [0.0, 1.0, 2.0, 1.0, -2.0, 0.0]
    for i in range(n):
        trend = start + slope * i
        offset = cycle_pattern[i % 6] * (swing_amplitude / 2.0)
        closes.append(trend + offset)
    return closes


# ─── ema ────────────────────────────────────────────────────────────────


def test_ema_returns_none_when_insufficient():
    assert ema([1.0, 2.0, 3.0], period=5) is None


def test_ema_constant_series_equals_constant():
    # SMA seed = 10; exponential update of 10s preserves 10.
    assert ema([10.0] * 50, period=10) == pytest.approx(10.0)


def test_ema_period_zero_returns_none():
    assert ema([1.0, 2.0], period=0) is None


def test_ema_increasing_series_is_below_latest_value():
    closes = list(range(1, 101))      # 1..100
    e = ema(closes, period=20)
    assert e is not None
    # Latest = 100; EMA lags rising trend
    assert e < 100.0
    assert e > 50.0                    # but well above the start


# ─── adx ────────────────────────────────────────────────────────────────


def test_adx_returns_none_when_insufficient():
    # ADX needs 2*period + 1 = 29 bars minimum
    closes = [100.0] * 28
    highs = [101.0] * 28
    lows = [99.0] * 28
    assert adx(highs, lows, closes, period=14) is None


def test_adx_high_on_strong_uptrend():
    closes = _ramp(100.0, 200.0, 60)
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    val = adx(highs, lows, closes, period=14)
    assert val is not None
    assert val > 30.0                  # strong trend


def test_adx_low_on_ranging_market():
    closes = _zigzag(100.0, 1.0, 60)
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    val = adx(highs, lows, closes, period=14)
    assert val is not None
    assert val < 25.0                  # ranging — well below trending threshold


# ─── macd_hist ──────────────────────────────────────────────────────────


def test_macd_returns_none_when_insufficient():
    # Needs slow + signal - 1 = 34 bars
    assert macd_hist(_flat(100.0, 33)) is None


def test_macd_positive_on_accelerating_uptrend():
    """MACD measures change in trend — perfect linear ramps give ~0
    histogram. Use a power curve (slow → fast growth) so the recent
    MACD line is rising vs. its signal-line EMA."""
    closes = [100.0 + 50.0 * ((i / 59) ** 1.5) for i in range(60)]
    h = macd_hist(closes)
    assert h is not None
    assert h > 0


def test_macd_negative_on_accelerating_downtrend():
    closes = [150.0 - 50.0 * ((i / 59) ** 1.5) for i in range(60)]
    h = macd_hist(closes)
    assert h is not None
    assert h < 0


# ─── atr ────────────────────────────────────────────────────────────────


def test_atr_returns_none_when_insufficient():
    assert atr([100.0] * 10, [99.0] * 10, [99.5] * 10, period=14) is None


def test_atr_constant_range_yields_constant_atr():
    n = 30
    closes = _flat(100.0, n)
    highs = [101.0] * n
    lows = [99.0] * n
    a = atr(highs, lows, closes, period=14)
    assert a is not None
    assert a == pytest.approx(2.0, abs=0.01)


# ─── swing points + market_structure ────────────────────────────────────


def test_find_swing_points_simple():
    # Highs: a swing high at index 5 (value 110, surrounded by lower)
    highs = [100, 102, 101, 103, 105, 110, 108, 107, 106, 104]
    lows  = [ 95,  93,  94,  92,  91,  90,  89,  87,  88,  90]
    sh, sl = find_swing_points(highs, lows, n=2)
    assert 5 in sh
    assert 7 in sl


def test_market_structure_bull():
    # Use the trending-with-swings generator: ascending peaks at
    # cycle-offset 2, ascending troughs at cycle-offset 4.
    closes = _trending_with_swings(100.0, 130.0, n=24, swing_amplitude=4.0)
    bars = _bars_from_closes("test", closes, range_pct=0.5)
    assert market_structure(list(bars.highs), list(bars.lows),
                            lookback=20, n=2) == "bull"


def test_market_structure_bear():
    closes = _trending_with_swings(130.0, 100.0, n=24, swing_amplitude=4.0)
    bars = _bars_from_closes("test", closes, range_pct=0.5)
    assert market_structure(list(bars.highs), list(bars.lows),
                            lookback=20, n=2) == "bear"


def test_market_structure_insufficient_data():
    assert market_structure([100, 102], [99, 101], lookback=20) == "insufficient"


# ─── session ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("hour,expected", [
    (3,  Session.Asia),
    (8,  Session.London),
    (13, Session.Overlap),
    (17, Session.NewYork),
    (22, Session.Asia),
])
def test_current_session_buckets(hour, expected):
    ts = datetime(2026, 5, 14, hour, 0, tzinfo=timezone.utc)
    assert current_session(ts) == expected


def test_current_session_naive_treated_as_utc():
    ts = datetime(2026, 5, 14, 13, 0)         # naive
    assert current_session(ts) == Session.Overlap


# ─── per-TF classifier ──────────────────────────────────────────────────


def _config() -> HTFRegimeConfig:
    """Default config for tests."""
    return HTFRegimeConfig.defaults()


def test_classify_timeframe_bull():
    # Trending with swing structure → satisfies the
    # (struct=bull) branch of the Bull combiner cleanly.
    closes = _trending_with_swings(100.0, 200.0, n=250, swing_amplitude=2.0)
    bars = _bars_from_closes("1d", closes, range_pct=1.0)
    cls = classify_timeframe(bars, _config())
    assert cls.regime == TimeframeRegime.Bull
    assert cls.ema_alignment == "bull"


def test_classify_timeframe_bear():
    closes = _trending_with_swings(200.0, 100.0, n=250, swing_amplitude=2.0)
    bars = _bars_from_closes("1d", closes, range_pct=1.0)
    cls = classify_timeframe(bars, _config())
    assert cls.regime == TimeframeRegime.Bear
    assert cls.ema_alignment == "bear"


def test_classify_timeframe_range():
    # Persistent zigzag — EMAs collapse to the centerline (mixed),
    # ADX stays low.
    closes = _zigzag(100.0, 1.5, 250)
    bars = _bars_from_closes("4h", closes, range_pct=0.4)
    cls = classify_timeframe(bars, _config())
    assert cls.regime == TimeframeRegime.Range
    assert cls.ema_alignment == "mixed"
    assert cls.adx is not None and cls.adx < 20


def test_classify_timeframe_insufficient():
    closes = _flat(100.0, 50)         # < 200 bars for EMA200
    bars = _bars_from_closes("1h", closes)
    cls = classify_timeframe(bars, _config())
    assert cls.regime == TimeframeRegime.Insufficient
    assert cls.ema200 is None
    assert "insufficient" in cls.reason.lower()


def test_classify_timeframe_transitional():
    # Strong rise then sharp pullback at the end — EMA stack still
    # bullish but price has dropped below EMA20, breaking the perfect
    # bull alignment AND structure isn't cleanly bull → Transitional.
    closes = _ramp(100.0, 200.0, 240) + _ramp(200.0, 170.0, 10)
    bars = _bars_from_closes("4h", closes, range_pct=0.6)
    cls = classify_timeframe(bars, _config())
    # Regime should NOT be cleanly Bull (alignment broken at the tail)
    assert cls.regime in (TimeframeRegime.Transitional, TimeframeRegime.Range)


# ─── composite regime ────────────────────────────────────────────────────


def _bull_bars(tf: str) -> TimeframeBars:
    return _bars_from_closes(
        tf, _trending_with_swings(100.0, 200.0, n=250, swing_amplitude=2.0),
        range_pct=1.0,
    )


def _bear_bars(tf: str) -> TimeframeBars:
    return _bars_from_closes(
        tf, _trending_with_swings(200.0, 100.0, n=250, swing_amplitude=2.0),
        range_pct=1.0,
    )


def _range_bars(tf: str) -> TimeframeBars:
    return _bars_from_closes(tf, _zigzag(100.0, 1.5, 250), range_pct=0.4)


def _ctx(
    h1: TimeframeBars | None,
    h4: TimeframeBars | None,
    d1: TimeframeBars | None,
    *, current_price: float = 200.0, funding: float | None = 0.0,
    prior_day_high: float | None = None, prior_day_low: float | None = None,
) -> HTFContext:
    return HTFContext(
        h1=h1, h4=h4, d1=d1,
        current_price=current_price,
        prior_day_high=prior_day_high,
        prior_day_low=prior_day_low,
        funding_rate=funding,
        ts=datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
    )


def test_compute_regime_strong_bull_when_all_three_bull():
    ctx = _ctx(_bull_bars("1h"), _bull_bars("4h"), _bull_bars("1d"))
    v = compute_regime(ctx, _config())
    assert v.regime == Regime.STRONG_BULL
    assert v.score == pytest.approx(1.0)


def test_compute_regime_strong_bear_when_all_three_bear():
    ctx = _ctx(_bear_bars("1h"), _bear_bars("4h"), _bear_bars("1d"),
               current_price=100.0)
    v = compute_regime(ctx, _config())
    assert v.regime == Regime.STRONG_BEAR
    assert v.score == pytest.approx(-1.0)


def test_compute_regime_neutral_when_all_three_range():
    ctx = _ctx(_range_bars("1h"), _range_bars("4h"), _range_bars("1d"),
               current_price=100.0)
    v = compute_regime(ctx, _config())
    assert v.regime == Regime.NEUTRAL
    assert v.score == pytest.approx(0.0)


def test_compute_regime_bull_with_h1_bear_pullback():
    # 1D + 4H = Bull, 1H = Bear → composite = 0.5 + 0.3 - 0.2 = 0.6 → BULL
    ctx = _ctx(_bear_bars("1h"), _bull_bars("4h"), _bull_bars("1d"))
    v = compute_regime(ctx, _config())
    assert v.regime == Regime.BULL
    assert v.score == pytest.approx(0.6)
    assert v.h1.regime == TimeframeRegime.Bear


def test_compute_regime_safe_mode_when_all_three_missing():
    ctx = _ctx(None, None, None)
    v = compute_regime(ctx, _config())
    assert v.regime == Regime.SAFE_MODE
    assert v.safe_mode_reason is not None
    assert "all timeframes" in v.safe_mode_reason.lower()


def test_compute_regime_h1_insufficient_does_not_force_safe_mode():
    # 1H insufficient (cold start) but 4H + 1D bull → BULL composite
    h1 = _bars_from_closes("1h", _flat(100.0, 30))      # < 200 bars
    ctx = _ctx(h1, _bull_bars("4h"), _bull_bars("1d"))
    v = compute_regime(ctx, _config())
    assert v.regime != Regime.SAFE_MODE
    assert v.h1.regime == TimeframeRegime.Insufficient
    # 0.5 + 0.3 + 0 = 0.8 → STRONG_BULL
    assert v.regime == Regime.STRONG_BULL


def test_compute_regime_threshold_boundaries():
    """Score thresholds: STRONG_BULL >= 0.7, BULL >= 0.3."""
    # 1D Bull (+0.5), 4H Range (0), 1H Range (0) → 0.5 → BULL (not STRONG)
    ctx = _ctx(_range_bars("1h"), _range_bars("4h"), _bull_bars("1d"))
    v = compute_regime(ctx, _config())
    assert v.score == pytest.approx(0.5)
    assert v.regime == Regime.BULL


def test_compute_regime_funding_extreme_flag():
    # +0.06% per 8h > 0.05% threshold → funding_extreme
    ctx = _ctx(_bull_bars("1h"), _bull_bars("4h"), _bull_bars("1d"),
               funding=0.0006)
    v = compute_regime(ctx, _config())
    assert v.funding_extreme is True
    assert v.funding_rate == pytest.approx(0.0006)


def test_compute_regime_funding_below_threshold():
    ctx = _ctx(_bull_bars("1h"), _bull_bars("4h"), _bull_bars("1d"),
               funding=0.0001)
    v = compute_regime(ctx, _config())
    assert v.funding_extreme is False


def test_compute_regime_volatility_tier_from_d1_atr():
    # Construct 1D bars where ATR ≈ 2% of price → "high" tier
    # (default thresholds: low<0.5, normal<1.5, high<3.0, extreme>=3.0)
    n = 250
    closes = _flat(100.0, n)
    # Wide range bars: high=101, low=99 → TR ~ 2 → ATR ~ 2 → 2% of 100
    highs = tuple(101.0 for _ in range(n))
    lows = tuple(99.0 for _ in range(n))
    d1 = TimeframeBars(
        timeframe="1d",
        opens=tuple(closes), highs=highs, lows=lows,
        closes=tuple(closes), volumes=tuple(1.0 for _ in range(n)),
        last_bar_close_ts=datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
    )
    ctx = _ctx(_bull_bars("1h"), _bull_bars("4h"), d1, current_price=100.0)
    v = compute_regime(ctx, _config())
    assert v.atr_pct_d1 is not None
    assert v.atr_pct_d1 == pytest.approx(2.0, abs=0.05)
    assert v.volatility_tier == VolatilityTier.High


# ─── trade-permission matrix ────────────────────────────────────────────


def _verdict(
    regime: Regime,
    h1_regime: TimeframeRegime = TimeframeRegime.Bull,
    *, vol_tier: VolatilityTier = VolatilityTier.Normal,
    distance_to_resistance_pct: float | None = 5.0,
    distance_to_support_pct: float | None = 5.0,
    funding_extreme: bool = False,
    funding_rate: float | None = 0.0,
    atr_pct_d1: float | None = 1.0,
) -> RegimeVerdict:
    """Synthetic verdict for permission-matrix testing — bypasses
    `compute_regime` so we can pin every input."""
    h1 = TimeframeClassification(
        timeframe="1h", regime=h1_regime,
        ema20=None, ema50=None, ema200=None,
        ema_alignment="bull", structure="bull",
        adx=None, macd_hist=None, reason="synthetic",
    )
    h4 = TimeframeClassification(
        timeframe="4h", regime=TimeframeRegime.Bull,
        ema20=None, ema50=None, ema200=None,
        ema_alignment="bull", structure="bull",
        adx=None, macd_hist=None, reason="synthetic",
    )
    d1 = TimeframeClassification(
        timeframe="1d", regime=TimeframeRegime.Bull,
        ema20=None, ema50=None, ema200=None,
        ema_alignment="bull", structure="bull",
        adx=None, macd_hist=None, reason="synthetic",
    )
    return RegimeVerdict(
        regime=regime, score=0.0,
        h1=h1, h4=h4, d1=d1,
        volatility_tier=vol_tier,
        atr_pct_d1=atr_pct_d1,
        nearest_resistance=None, nearest_support=None,
        distance_to_resistance_pct=distance_to_resistance_pct,
        distance_to_support_pct=distance_to_support_pct,
        session=Session.NewYork,
        funding_rate=funding_rate,
        funding_extreme=funding_extreme,
        safe_mode_reason=None,
    )


def test_strong_bull_allows_long_full_size_blocks_short():
    cfg = _config()
    v = _verdict(Regime.STRONG_BULL)
    long = get_trade_permissions(v, "buy", cfg)
    short = get_trade_permissions(v, "sell", cfg)
    assert long.size_multiplier == 1.0
    assert short.size_multiplier == 0.0
    assert short.hard_zero_reason == "regime_forbids_side"


def test_strong_bear_allows_short_full_size_blocks_long():
    cfg = _config()
    v = _verdict(Regime.STRONG_BEAR)
    long = get_trade_permissions(v, "buy", cfg)
    short = get_trade_permissions(v, "sell", cfg)
    assert long.size_multiplier == 0.0
    assert short.size_multiplier == 1.0


def test_bull_with_h1_bear_halves_long_size():
    cfg = _config()
    v = _verdict(Regime.BULL, h1_regime=TimeframeRegime.Bear)
    long = get_trade_permissions(v, "buy", cfg)
    assert long.size_multiplier == 0.5
    assert "pullback" in long.reason.lower()


def test_bear_with_h1_bull_halves_short_size():
    cfg = _config()
    v = _verdict(Regime.BEAR, h1_regime=TimeframeRegime.Bull)
    short = get_trade_permissions(v, "sell", cfg)
    assert short.size_multiplier == 0.5
    assert "bounce" in short.reason.lower()


def test_neutral_allows_both_at_half_size():
    cfg = _config()
    v = _verdict(Regime.NEUTRAL, h1_regime=TimeframeRegime.Range)
    long = get_trade_permissions(v, "buy", cfg)
    short = get_trade_permissions(v, "sell", cfg)
    assert long.size_multiplier == 0.5
    assert short.size_multiplier == 0.5


def test_safe_mode_blocks_both():
    cfg = _config()
    v = _verdict(Regime.SAFE_MODE)
    long = get_trade_permissions(v, "buy", cfg)
    short = get_trade_permissions(v, "sell", cfg)
    assert long.size_multiplier == 0.0
    assert short.size_multiplier == 0.0
    assert long.hard_zero_reason == "safe_mode"
    assert short.hard_zero_reason == "safe_mode"


def test_proximity_to_resistance_blocks_long():
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BULL, distance_to_resistance_pct=0.2,    # < 0.3% block
    )
    long = get_trade_permissions(v, "buy", cfg)
    assert long.size_multiplier == 0.0
    assert long.hard_zero_reason == "proximity_to_resistance"


def test_proximity_to_support_blocks_short():
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BEAR, distance_to_support_pct=0.1,
    )
    short = get_trade_permissions(v, "sell", cfg)
    assert short.size_multiplier == 0.0
    assert short.hard_zero_reason == "proximity_to_support"


def test_proximity_does_not_block_when_far_enough():
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BULL, distance_to_resistance_pct=0.5,   # > 0.3%
    )
    long = get_trade_permissions(v, "buy", cfg)
    assert long.size_multiplier == 1.0


def test_volatility_extreme_blocks_all_trades():
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BULL, vol_tier=VolatilityTier.Extreme, atr_pct_d1=6.0,
    )
    long = get_trade_permissions(v, "buy", cfg)
    assert long.size_multiplier == 0.0
    assert long.hard_zero_reason == "vol_tier_extreme"


def test_funding_extreme_long_blocked_when_positive_funding():
    """Positive funding → longs are crowded → block longs."""
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BULL,
        funding_extreme=True, funding_rate=0.0008,    # +0.08% per 8h
    )
    long = get_trade_permissions(v, "buy", cfg)
    short = get_trade_permissions(v, "sell", cfg)
    assert long.size_multiplier == 0.0
    assert long.hard_zero_reason == "funding_extreme_crowded"
    # Short would be blocked by regime forbidding side, not by funding
    assert short.hard_zero_reason == "regime_forbids_side"


def test_funding_extreme_short_blocked_when_negative_funding():
    """Negative funding → shorts are crowded → block shorts."""
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BEAR,
        funding_extreme=True, funding_rate=-0.0008,
    )
    short = get_trade_permissions(v, "sell", cfg)
    assert short.size_multiplier == 0.0
    assert short.hard_zero_reason == "funding_extreme_crowded"


def test_funding_extreme_does_not_block_uncrowded_side():
    """Positive funding crowds longs but does NOT block shorts.
    Pair a STRONG_BEAR regime with positive funding → short still fires
    at full size (uncrowded shorts) — but in a real scenario the regime
    likely wouldn't disagree with funding this hard. Synthetic test
    pins the logic."""
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BEAR,
        funding_extreme=True, funding_rate=0.0008,
    )
    short = get_trade_permissions(v, "sell", cfg)
    assert short.size_multiplier == 1.0
    assert short.hard_zero_reason is None


def test_invalid_proposed_side_returns_zero():
    cfg = _config()
    v = _verdict(Regime.STRONG_BULL)
    p = get_trade_permissions(v, "long", cfg)         # not "buy"
    assert p.size_multiplier == 0.0
    assert p.hard_zero_reason == "invalid_side"


def test_hard_zero_priority_proximity_over_vol_tier():
    """If multiple hard-zero conditions hit, the FIRST checked wins
    (proximity is checked before vol_tier in the implementation)."""
    cfg = _config()
    v = _verdict(
        Regime.STRONG_BULL,
        vol_tier=VolatilityTier.Extreme,
        distance_to_resistance_pct=0.1,
    )
    long = get_trade_permissions(v, "buy", cfg)
    assert long.size_multiplier == 0.0
    assert long.hard_zero_reason == "proximity_to_resistance"
