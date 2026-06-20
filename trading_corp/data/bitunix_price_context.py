"""BitUnix Phase 3.2.2 — price-context computation from the LiveBarCache.

Pure-function helpers that read 3m bars from a `LiveBarCache` and compute
the `PriceContext` the score engine needs:

  - Session VWAP (typical-price × volume since 00:00 UTC of latest bar)
  - Higher-highs / lower-lows on 4h resampled buckets (excludes
    in-progress bucket to avoid look-ahead within the bar)
  - 20-bar trailing volume average comparison (current bar excluded
    from the average — current is the comparison target)
  - % change over the guard windows (60 min default)

No I/O, no async. The bar cache provides the data; this module turns it
into the PriceContext shape `evaluate_confluence_futures()` consumes.

Phase 3.2.1 ran with a zero-filled PriceContext — no PA contributions,
no guard penalties. Phase 3.2.2 wires the live signal so PA factors
match what the backtest used.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from trading_corp.agents.strategies.btc_accumulator import PriceContext

if TYPE_CHECKING:
    from trading_corp.data.live_bar_cache import Bar, LiveBarCache
    from trading_corp.agents.strategies.bitunix_confluence import (
        BitUnixConfluenceConfig,
    )
    from trading_corp.agents.strategies.bitunix_confluence_gate import (
        ConfluenceGateConfig,
        GateInputs,
    )


# Number of 3m bars in a 60-min window (matches the default guard window).
_BARS_PER_60MIN = 20

# 20-bar trailing average for the volume_above_20bar_avg factor.
_VOLUME_AVG_LOOKBACK = 20


def _bar_dt(bar) -> datetime:  # noqa: ANN001
    """Convert Bar.ts_ms to a UTC datetime."""
    return datetime.fromtimestamp(bar.ts_ms / 1000.0, tz=timezone.utc)


def session_vwap(bars) -> float | None:  # noqa: ANN001
    """VWAP from 00:00 UTC of the LATEST bar's date through the latest
    bar. Returns None if no bars or zero cumulative volume.

    "Session" = UTC day. Crypto runs 24/7 — picking UTC as the daily
    reset boundary matches what TradingView's session VWAP shows on
    most BTC charts.
    """
    if not bars:
        return None
    latest = bars[-1]
    latest_dt = _bar_dt(latest)
    day_start_dt = latest_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_ms = int(day_start_dt.timestamp() * 1000)

    sum_pv = 0.0
    sum_v = 0.0
    for b in bars:
        if b.ts_ms < day_start_ms:
            continue
        if b.ts_ms > latest.ts_ms:
            break
        typical = (b.high + b.low + b.close) / 3.0
        sum_pv += typical * b.volume
        sum_v += b.volume
    if sum_v == 0:
        return None
    return sum_pv / sum_v


def pct_change_in_window(bars, window_minutes: int) -> float:  # noqa: ANN001
    """% change from the bar N minutes ago to the latest bar.

    Positive = rose, negative = fell. Returns 0.0 if insufficient bars.
    """
    if not bars:
        return 0.0
    target_ms = bars[-1].ts_ms - (window_minutes * 60 * 1000)
    # Walk backwards finding the bar whose ts_ms is the largest <= target_ms
    then_bar = None
    for b in reversed(bars):
        if b.ts_ms <= target_ms:
            then_bar = b
            break
    if then_bar is None or then_bar.close == 0:
        return 0.0
    return (bars[-1].close - then_bar.close) / then_bar.close * 100.0


def volume_above_20bar_avg(bars) -> bool:  # noqa: ANN001
    """True iff the latest bar's volume > average of the 20 PRIOR bars.

    The latest bar is the comparison target; it's NOT included in the
    average. Mirrors `backtest_btc_accumulator.volume_above_20bar_avg_at`.
    """
    if len(bars) < _VOLUME_AVG_LOOKBACK + 1:
        return False
    prior = bars[-(_VOLUME_AVG_LOOKBACK + 1):-1]
    avg = sum(b.volume for b in prior) / _VOLUME_AVG_LOOKBACK
    return bars[-1].volume > avg


def _resample_to_4h(bars):  # noqa: ANN001
    """Group 3m bars into 4h buckets keyed to UTC 00:00 / 04:00 / 08:00
    / 12:00 / 16:00 / 20:00. Each output bucket is a dict with
    aggregated open / high / low / close / volume / start_ts_ms.

    Returns chronological list of buckets. The latest bucket may be
    incomplete (in-progress) — callers that need only completed buckets
    should slice off the last element.
    """
    out: list[dict] = []
    cur: dict | None = None
    for b in bars:
        bucket_dt = _bar_dt(b).replace(
            hour=(_bar_dt(b).hour // 4) * 4,
            minute=0, second=0, microsecond=0,
        )
        bucket_ts = int(bucket_dt.timestamp() * 1000)
        if cur is None or cur["start_ts_ms"] != bucket_ts:
            if cur is not None:
                out.append(cur)
            cur = {
                "start_ts_ms": bucket_ts,
                "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "volume": b.volume,
            }
        else:
            cur["high"] = max(cur["high"], b.high)
            cur["low"] = min(cur["low"], b.low)
            cur["close"] = b.close
            cur["volume"] += b.volume
    if cur is not None:
        out.append(cur)
    return out


def higher_highs_lower_lows_4h(bars) -> tuple[bool, bool]:  # noqa: ANN001
    """Compare the most-recently-COMPLETED 4h bucket to the one before
    it.

    Returns (higher_highs_4h, lower_lows_4h). Both False if fewer than
    2 completed buckets exist (the in-progress bucket — the LAST entry
    from `_resample_to_4h` — is excluded; we need 2 entries BEFORE it
    in the list).
    """
    buckets = _resample_to_4h(bars)
    # We need: at least 3 entries (2 completed + 1 in-progress) OR 2
    # entries where the latest bar's bucket IS one of them (caller's
    # responsibility to ensure latest bar is included). Simpler rule:
    # require ≥ 3 buckets, compare buckets[-2] (last completed) vs
    # buckets[-3] (prior completed).
    if len(buckets) < 3:
        return (False, False)
    last_completed = buckets[-2]
    prior = buckets[-3]
    return (
        last_completed["high"] > prior["high"],
        last_completed["low"] < prior["low"],
    )


def compute_price_context(
    bar_cache,                                  # LiveBarCache | None  # noqa: ANN001
    sell_on_rush_window_minutes: int = 60,
    buy_on_fall_window_minutes: int = 60,
) -> "PriceContext | None":
    """Build a PriceContext from the live bar cache.

    Returns None if the cache is None or empty (caller falls back to
    a zero context — same conservative behavior as Phase 3.2.1).
    """
    if bar_cache is None:
        return None
    bars = getattr(bar_cache, "bars", None)
    if not bars:
        return None

    current_price = bars[-1].close
    vwap = session_vwap(bars)
    above_vwap = vwap is not None and current_price > vwap
    below_vwap = vwap is not None and current_price < vwap
    hh4h, ll4h = higher_highs_lower_lows_4h(bars)
    vol_above = volume_above_20bar_avg(bars)
    pct_sell = pct_change_in_window(bars, sell_on_rush_window_minutes)
    pct_buy = pct_change_in_window(bars, buy_on_fall_window_minutes)

    return PriceContext(
        current_price=current_price,
        pct_change_in_window_sell=pct_sell,
        pct_change_in_window_buy=pct_buy,
        above_session_vwap=above_vwap,
        below_session_vwap=below_vwap,
        higher_highs_4h=hh4h,
        lower_lows_4h=ll4h,
        volume_above_20bar_avg=vol_above,
    )


# ─── Confluence-gate Phase B helpers ────────────────────────────────────


def prior_day_session_vwap(bars) -> float | None:  # noqa: ANN001
    """VWAP of YESTERDAY (the full UTC day before the latest bar's day).

    Returns `None` if `bars` doesn't span back into the prior day or
    if the prior-day volume is zero. Pattern mirrors
    `bitunix_htf_context._prior_day_high_low` — "the most recent
    closed day before today's in-progress day."

    Used by Factor 2 of the 5-factor confluence gate. Boot warm-up
    consideration: 3m bars × 480/day = 480 bars needed to cover one
    UTC day. `bitunix_bar_cache.max_bars=500` covers it; a fresh
    boot mid-day may not. `None` → factor fails closed.
    """
    if not bars:
        return None
    latest = bars[-1]
    latest_dt = _bar_dt(latest)
    today_start = latest_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    prior_start = today_start - timedelta(days=1)
    prior_start_ms = int(prior_start.timestamp() * 1000)
    today_start_ms = int(today_start.timestamp() * 1000)

    sum_pv = 0.0
    sum_v = 0.0
    for b in bars:
        if b.ts_ms < prior_start_ms:
            continue
        if b.ts_ms >= today_start_ms:
            break
        typical = (b.high + b.low + b.close) / 3.0
        sum_pv += typical * b.volume
        sum_v += b.volume
    if sum_v == 0:
        return None
    return sum_pv / sum_v


def cvd_from_bars_tick_rule(
    bars,                                   # noqa: ANN001
    window_minutes: int = 15,
) -> tuple[float | None, bool]:
    """CVD slope over the last `window_minutes` of bars, using the
    tick-rule fallback (intra-bar candle-direction sign).

    Returns `(slope, fallback_used)`. `fallback_used` is always `True`
    for v1 — BitUnix's public feed has no aggressor-side trade flag,
    so true CVD isn't reconstructable without a WebSocket trade-stream
    consumer (out of scope for this phase). The flag is surfaced on
    every gate eval so the dashboard banner + audit row can show that
    Factor 4 is running coarse.

    Algorithm (v1.1 — see post-mortem in
    `reports/gate_backtest_2026-05-17_v2.md`):
      1. Slice bars to the last `window_minutes`.
      2. For each bar: `sign = +1 if close > open, -1 if close < open,
         0 if equal`. `delta_i = sign * volume_i`. (Intra-bar
         green/red candle direction. v1.0 incorrectly used
         `sign(close - prev_close)` — that's inter-bar momentum, not
         CVD flow; switched to per-spec close-vs-open.)
      3. Cumulative series `c_i = sum(delta_0 .. delta_i)`.
      4. Slope = `linregress_slope(c)` — slope of the CUMULATIVE CVD
         curve, not of per-bar deltas. (v1.0 took slope of per-bar
         deltas directly, which produced `slope ≈ 0` on sustained
         one-direction tapes — both buy AND sell incorrectly failed
         the factor exactly when the flow signal was strongest.)

    Returns `(None, True)` if `len(bars) < 2` or the window contains
    fewer than 2 bars (slope undefined).
    """
    from trading_corp.agents.strategies._ta_helpers import linregress_slope

    if not bars or len(bars) < 2:
        return None, True

    latest_ts = bars[-1].ts_ms
    cutoff_ts = latest_ts - (window_minutes * 60 * 1000)
    window_bars = [b for b in bars if b.ts_ms >= cutoff_ts]
    if len(window_bars) < 2:
        return None, True

    cumulative: list[float] = []
    running = 0.0
    for b in window_bars:
        if b.close > b.open:
            sign = 1.0
        elif b.close < b.open:
            sign = -1.0
        else:
            sign = 0.0
        running += sign * b.volume
        cumulative.append(running)

    slope = linregress_slope(cumulative)
    return slope, True


def build_gate_inputs(
    bar_cache_3m,                           # noqa: ANN001
    bar_cache_5m,                           # noqa: ANN001
    bar_cache_15m,                          # noqa: ANN001
    *,
    side: str,
    config: "ConfluenceGateConfig",
) -> "GateInputs":
    """Read the three caches and produce a `GateInputs` snapshot.

    All cache args may be `None`. Per-factor inputs that can't be
    computed (cache None, cache empty, insufficient bars) come back
    as `None`; the gate evaluator translates `None` to `passed=False`
    so the gate stays conservative during cache warm-up.

    `side` is accepted for API symmetry but doesn't affect the
    inputs — the side-dependent comparisons happen inside each
    `_factor_*` function in `bitunix_confluence_gate`.

    NOTE on CVD: per the module's design (see
    `bitunix_confluence_gate.CvdFactorConfig`), the tick-rule
    fallback always reads the 3m cache regardless of
    `config.cvd_factor.bucket_minutes` — that YAML field is
    documentation only for v1.
    """
    from trading_corp.agents.strategies._ta_helpers import (
        atr as _atr,
        bb_width_series,
        ema,
        ema_series,
        linregress_slope,
        percentile_rank,
        sma,
    )
    from trading_corp.agents.strategies.bitunix_confluence_gate import GateInputs

    # ── Factor 1: 15m EMA alignment ──
    # v1.1: compute slope for ALL three EMAs (was: ema_8 only).
    bars_15m = list(getattr(bar_cache_15m, "bars", None) or [])
    closes_15m = [b.close for b in bars_15m]
    p8, p21, p50 = config.ema_factor.periods
    ema_8 = ema(closes_15m, p8)
    ema_21 = ema(closes_15m, p21)
    ema_50 = ema(closes_15m, p50)
    lookback = config.ema_factor.slope_lookback

    def _slope(closes: list[float], period: int) -> float | None:
        s = ema_series(closes, period)
        if len(s) < lookback:
            return None
        return linregress_slope(s[-lookback:])

    ema_8_slope = _slope(closes_15m, p8)
    ema_21_slope = _slope(closes_15m, p21)
    ema_50_slope = _slope(closes_15m, p50)

    # ── Factor 2: VWAP ──
    bars_3m = list(getattr(bar_cache_3m, "bars", None) or [])
    current_price: float | None = bars_3m[-1].close if bars_3m else None
    session_v = session_vwap(bars_3m) if bars_3m else None
    prior_v = prior_day_session_vwap(bars_3m) if bars_3m else None

    # ── Factor 3: Volatility (5m) ──
    bars_5m = list(getattr(bar_cache_5m, "bars", None) or [])
    highs_5m = [b.high for b in bars_5m]
    lows_5m = [b.low for b in bars_5m]
    closes_5m = [b.close for b in bars_5m]
    atr_5m_val = _atr(
        highs_5m, lows_5m, closes_5m, config.volatility_factor.atr_period,
    )
    # Build the ATR series for the SMA — uses `_wilder_average` from
    # bitunix_htf_regime via internal access. To avoid a private import
    # we recompute the ATR series here using the same TR sequence.
    atr_series = _atr_series_from_bars(
        highs_5m, lows_5m, closes_5m, config.volatility_factor.atr_period,
    )
    atr_5m_sma = sma(atr_series, config.volatility_factor.atr_sma_period)
    bb_widths = bb_width_series(
        closes_5m,
        period=config.volatility_factor.bb_period,
        n_stdev=config.volatility_factor.bb_stdev,
    )
    bb_width_5m: float | None = bb_widths[-1] if bb_widths else None
    if len(bb_widths) >= config.volatility_factor.bb_pct_rank_window + 1:
        bb_window = bb_widths[
            -(config.volatility_factor.bb_pct_rank_window + 1):-1
        ]
        bb_pct_rank = percentile_rank(bb_width_5m, bb_window)
    else:
        bb_pct_rank = None

    # ── Factor 4: CVD (3m, tick-rule) ──
    cvd_slope, cvd_fallback = cvd_from_bars_tick_rule(
        bars_3m, window_minutes=config.cvd_factor.slope_window_minutes,
    )

    # ── Factor 5: Volume z-score (3m, 20-bar) ──
    volume_z: float | None = None
    vol_window = config.volume_z_factor.period
    if len(bars_3m) >= vol_window + 1:
        prior_vols = [b.volume for b in bars_3m[-(vol_window + 1):-1]]
        mean_v = sum(prior_vols) / vol_window
        var_v = sum((v - mean_v) ** 2 for v in prior_vols) / vol_window
        std_v = var_v ** 0.5
        if std_v > 0:
            volume_z = (bars_3m[-1].volume - mean_v) / std_v

    return GateInputs(
        ema_8_15m=ema_8, ema_21_15m=ema_21, ema_50_15m=ema_50,
        ema_8_15m_slope=ema_8_slope,
        ema_21_15m_slope=ema_21_slope,
        ema_50_15m_slope=ema_50_slope,
        current_price=current_price,
        session_vwap=session_v,
        prior_day_session_vwap=prior_v,
        atr_5m=atr_5m_val, atr_5m_sma=atr_5m_sma,
        bb_width_5m=bb_width_5m, bb_width_5m_pct_rank=bb_pct_rank,
        cvd_slope=cvd_slope, cvd_fallback_used=cvd_fallback,
        volume_z=volume_z,
    )


def _atr_series_from_bars(
    highs, lows, closes, period: int,                          # noqa: ANN001
) -> list[float]:
    """Wilder-smoothed ATR series for the 5m volatility factor's
    `SMA(ATR, atr_sma_period)` consumer.

    Mirrors `bitunix_htf_regime._wilder_average(_true_range_series(...))`
    locally so we don't reach into a private symbol. Output length =
    `max(0, n - period)` where `n` is the bar count.
    """
    n = len(highs)
    if n < period + 1 or len(lows) != n or len(closes) != n:
        return []
    tr: list[float] = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(tr) < period:
        return []
    avg = sum(tr[:period]) / period
    out = [avg]
    for v in tr[period:]:
        avg = (avg * (period - 1) + v) / period
        out.append(avg)
    return out


# Hard requirements at the default `ConfluenceGateConfig` — used by the
# boot warm-up logger. Recompute when the defaults change.
#   5m: max(ATR_period + ATR_SMA_period - 1, BB_period + pct_rank_window) = 120
#       atr 14 + sma 50 = 63 → not the binding constraint
#       bb 20 + pct_rank 100 = 120 → binding
#   15m: EMA_50 + slope_lookback - 1 = 54
_GATE_WARM_REQUIRED_5M = 120
_GATE_WARM_REQUIRED_15M = 54


def log_gate_cache_warmup_status(
    bar_cache_5m,                              # noqa: ANN001
    bar_cache_15m,                             # noqa: ANN001
    *,
    log: logging.Logger | None = None,
) -> dict[str, Any]:
    """Board mod #4 — emit boot-time warm-up ETA for the gate caches.

    Logs an INFO line per cache showing held vs required bars, plus
    an `eta_seconds` estimate for the remaining bars at the timeframe's
    cadence. Returns the structured snapshot for the dashboard banner
    (Phase D consumer).
    """
    logger = log or logging.getLogger(__name__)

    def _snap(cache, required: int, label: str) -> dict[str, Any]:
        if cache is None:
            return {
                "label": label, "have": 0, "required": required,
                "warm": False, "eta_seconds": None,
                "reason": "cache is None",
            }
        bars = list(getattr(cache, "bars", None) or [])
        tf_sec = getattr(cache, "timeframe_seconds", 0) or 0
        have = len(bars)
        warm = have >= required
        eta = None
        if not warm and tf_sec > 0:
            eta = (required - have) * tf_sec
        return {
            "label": label, "have": have, "required": required,
            "warm": warm, "eta_seconds": eta,
            "tf_seconds": tf_sec,
        }

    snap_5m = _snap(bar_cache_5m, _GATE_WARM_REQUIRED_5M, "5m")
    snap_15m = _snap(bar_cache_15m, _GATE_WARM_REQUIRED_15M, "15m")
    all_warm = snap_5m["warm"] and snap_15m["warm"]

    for s in (snap_5m, snap_15m):
        if s["warm"]:
            logger.info(
                "BitUnix confluence-gate %s cache WARM: %d/%d bars",
                s["label"], s["have"], s["required"],
            )
        else:
            eta_h = (s["eta_seconds"] / 3600.0) if s["eta_seconds"] else None
            logger.info(
                "BitUnix confluence-gate %s cache COLD: %d/%d bars (eta ~%.1fh)",
                s["label"], s["have"], s["required"],
                eta_h if eta_h is not None else float("nan"),
            )
    logger.info(
        "BitUnix confluence-gate overall warm-up: %s",
        "READY" if all_warm else "WARMING",
    )
    return {"5m": snap_5m, "15m": snap_15m, "all_warm": all_warm}
