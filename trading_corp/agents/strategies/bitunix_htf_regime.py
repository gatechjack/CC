"""BitUnix Futures — Higher-Timeframe (HTF) Regime Classifier.

Pure-function classifier that consumes 1H / 4H / 1D OHLCV and produces a
unified market-regime verdict + trade-permission decision. This module is
the explicit replacement for the implicit HTF context that used to come
from Cypher 4h/1D webhooks: instead of relying on TradingView indicator
fires for "what is the daily trend doing?", we compute it ourselves from
raw bars.

The HTF gate is an additive direction-and-sizing layer ON TOP of the
score accumulator (`bitunix_confluence.py`). The flow is:

    score eval  →  PA validation  →  HTF gate (this module)  →  risk gate

The score accumulator decides which direction the 3m signal stack
favors. The HTF gate decides whether that direction is *permitted at
all*, and at what size multiplier.

Pipeline:

  1. Per-TF classification (1H, 4H, 1D run independently):

        - EMA alignment of (20, 50, 200) on closed bars
            Bull: price > EMA20 > EMA50 > EMA200
            Bear: price < EMA20 < EMA50 < EMA200
            Mixed: any other ordering
        - Market structure: swing-high / swing-low pattern over the
          last `swing_lookback` closed bars (swing point = local
          extreme with `swing_n` bars on each side)
            Bull struct: most-recent SH > prior SH AND most-recent
                         SL > prior SL
            Bear struct: inverse
            Range: neither
        - ADX(14): >`adx_trend_threshold` = trending, <= = ranging
        - MACD histogram sign: momentum direction tiebreaker

        Each TF maps to {Bull, Bear, Range, Transitional, Insufficient}:
            Bull         = EMA Bull AND (struct Bull OR (ADX trending
                                                          AND MACD>0))
            Bear         = EMA Bear AND (struct Bear OR (ADX trending
                                                          AND MACD<0))
            Range        = ADX < threshold AND EMA Mixed
            Transitional = anything else (conflicting signals)
            Insufficient = not enough bars for one of the indicators

  2. Composite regime score (weighted sum of per-TF contributions):

        score = w_d1 * d1 + w_h4 * h4 + w_h1 * h1
        Bull = +1, Bear = -1, others = 0
        Default weights: 0.5 / 0.3 / 0.2 (1D dominates, 4H confirms,
        1H modulates timing).

        score >= +0.7 → STRONG_BULL
        score >= +0.3 → BULL
       -0.3 < s < +0.3 → NEUTRAL
        score <= -0.3 → BEAR
        score <= -0.7 → STRONG_BEAR

  3. Context fields derived from the same data (used by hard-zero
     checks in step 5):

        - volatility_tier from 1D ATR(14) % of price
        - nearest_support / nearest_resistance from 4H+1D swings +
          prior-day H/L (whichever is closest above/below current)
        - distance_to_resistance_pct, distance_to_support_pct
        - session from UTC clock (Asia / London / NY / Overlap)
        - funding_rate (caller-supplied) and funding_extreme

  4. Trade-permission matrix consumes (composite_regime, h1_class,
     proposed_side). Returns base allow + size_multiplier per spec:

        STRONG_BULL          → long 1.0×, no short
        BULL  + h1=Bear      → long 0.5× (pullback)
        BULL  + h1=other     → long 1.0×
        NEUTRAL              → both directions 0.5× (mean-reversion)
        BEAR  + h1=Bull      → short 0.5× (bounce)
        BEAR  + h1=other     → short 1.0×
        STRONG_BEAR          → short 1.0×, no long

  5. Hard-zero overrides applied LAST (each forces multiplier=0):

        a. proposed side conflicts with the matrix permission
        b. price within `proximity_block_pct` of nearest opposing
           HTF level (don't long into 4H resistance, etc.)
        c. volatility_tier == Extreme (skip until normalized)
        d. funding_extreme AND side matches the crowded side
           (positive funding → long crowded → block longs;
            negative funding → short crowded → block shorts)

  6. SAFE_MODE: if the caller cannot supply ANY HTF data (all three
     timeframes missing), regime returns SAFE_MODE → permission
     returns multiplier=0 universally. This is the fail-closed
     contract that mirrors CLAUDE.md's "VIX-feed-unavailable is
     fail-safe to Board" principle: no data = no trades.

Pure-function design — all I/O (cache reads, funding-rate fetch) is the
caller's responsibility (see `data/bitunix_htf_context.py`, PR 2). This
module is testable with synthetic OHLCV fixtures: no network, no clock,
no mutable global state.

Repainting protection: callers MUST supply only closed bars (the in-
progress bar dropped). `LiveBarCache` already enforces this upstream;
the classifier trusts the input.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import Enum
from typing import Sequence

__all__ = [
    "HTFContext",
    "HTFRegimeConfig",
    "Regime",
    "RegimeVerdict",
    "Session",
    "TimeframeBars",
    "TimeframeClassification",
    "TimeframeRegime",
    "TradePermission",
    "VolatilityTier",
    "adx",
    "classify_timeframe",
    "compute_regime",
    "current_session",
    "ema",
    "find_swing_points",
    "get_trade_permissions",
    "macd_hist",
    "market_structure",
]


# ─── enums ──────────────────────────────────────────────────────────────


class TimeframeRegime(str, Enum):
    Bull = "bull"
    Bear = "bear"
    Range = "range"
    Transitional = "transitional"
    Insufficient = "insufficient"


class Regime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    SAFE_MODE = "SAFE_MODE"


class VolatilityTier(str, Enum):
    Low = "low"
    Normal = "normal"
    High = "high"
    Extreme = "extreme"
    Unknown = "unknown"


class Session(str, Enum):
    Asia = "asia"
    London = "london"
    NewYork = "new_york"
    Overlap = "overlap"          # London/NY overlap (12:00–16:00 UTC)


# ─── input dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TimeframeBars:
    """Closed OHLCV bars for one timeframe.

    Caller is responsible for dropping the in-progress bar before
    constructing this. Bars are ordered oldest → newest.
    """
    timeframe: str                     # "1h" | "4h" | "1d" (cosmetic, audit)
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...]
    last_bar_close_ts: datetime        # UTC; close-time of the most recent bar


@dataclass(frozen=True)
class HTFContext:
    """Caller-built snapshot of all HTF inputs at one moment in time."""
    h1: TimeframeBars | None
    h4: TimeframeBars | None
    d1: TimeframeBars | None
    current_price: float
    prior_day_high: float | None
    prior_day_low: float | None
    funding_rate: float | None         # decimal: 0.0001 = 0.01% per 8h
    ts: datetime                       # UTC; eval moment


@dataclass(frozen=True)
class HTFRegimeConfig:
    """Parsed `bitunix_futures.htf_regime` block from strategies.yaml.

    All numeric knobs live here so the classifier stays pure.
    """
    enabled: bool
    ema_periods: tuple[int, int, int]            # (20, 50, 200)
    adx_period: int                              # 14
    adx_trend_threshold: float                   # 20.0
    swing_lookback: int                          # 20 closed bars
    swing_n: int                                 # 2 (bars each side)
    macd_periods: tuple[int, int, int]           # (fast=12, slow=26, signal=9)
    composite_weights: dict[str, float]          # {"d1": 0.5, "h4": 0.3, "h1": 0.2}
    regime_thresholds: dict[str, float]          # {"strong_bull": 0.7, "bull": 0.3,
                                                 #  "bear": -0.3, "strong_bear": -0.7}
    vol_tier_atr_pct: dict[str, float]           # {"low": 0.5, "normal": 1.5,
                                                 #  "high": 3.0, "extreme": 5.0}
    funding_extreme_pct_per_8h: float            # 0.05  (= 0.05%)
    proximity_block_pct: float                   # 0.3   (= 0.3%)

    @classmethod
    def defaults(cls) -> "HTFRegimeConfig":
        """Spec-default config. Tests + PR 3 YAML use the same numbers."""
        return cls(
            enabled=False,
            ema_periods=(20, 50, 200),
            adx_period=14,
            adx_trend_threshold=20.0,
            swing_lookback=20,
            swing_n=2,
            macd_periods=(12, 26, 9),
            composite_weights={"d1": 0.5, "h4": 0.3, "h1": 0.2},
            regime_thresholds={
                "strong_bull": 0.7, "bull": 0.3,
                "bear": -0.3, "strong_bear": -0.7,
            },
            vol_tier_atr_pct={
                "low": 0.5, "normal": 1.5, "high": 3.0, "extreme": 5.0,
            },
            funding_extreme_pct_per_8h=0.05,
            proximity_block_pct=0.3,
        )

    @classmethod
    def from_dict(cls, bx_block: dict) -> "HTFRegimeConfig":
        """Parse the bitunix_futures.htf_regime sub-block. Falls back to
        defaults for any missing key. Returns enabled=False if the
        htf_regime block is entirely absent (preserving pre-from_dict
        behavior where the YAML knobs were inert).
        """
        htf_block = bx_block.get("htf_regime") or {}
        if not htf_block:
            return cls.defaults()
        d = cls.defaults()
        ema_raw = htf_block.get("ema_periods", list(d.ema_periods))
        macd_raw = htf_block.get("macd_periods", list(d.macd_periods))
        return cls(
            enabled=bool(htf_block.get("enabled", True)),
            ema_periods=tuple(int(x) for x in ema_raw),  # type: ignore[arg-type]
            adx_period=int(htf_block.get("adx_period", d.adx_period)),
            adx_trend_threshold=float(htf_block.get("adx_trend_threshold", d.adx_trend_threshold)),
            swing_lookback=int(htf_block.get("swing_lookback", d.swing_lookback)),
            swing_n=int(htf_block.get("swing_n", d.swing_n)),
            macd_periods=tuple(int(x) for x in macd_raw),  # type: ignore[arg-type]
            composite_weights={**d.composite_weights, **(htf_block.get("composite_weights") or {})},
            regime_thresholds={**d.regime_thresholds, **(htf_block.get("regime_thresholds") or {})},
            vol_tier_atr_pct={**d.vol_tier_atr_pct, **(htf_block.get("vol_tier_atr_pct") or {})},
            funding_extreme_pct_per_8h=float(htf_block.get("funding_extreme_pct_per_8h", d.funding_extreme_pct_per_8h)),
            proximity_block_pct=float(htf_block.get("proximity_block_pct", d.proximity_block_pct)),
        )


# ─── output dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TimeframeClassification:
    """Audit-grade per-TF result. Every component is exposed so the
    audit reader can reconstruct WHY a TF was classified that way."""
    timeframe: str
    regime: TimeframeRegime
    ema20: float | None
    ema50: float | None
    ema200: float | None
    ema_alignment: str                 # "bull" | "bear" | "mixed" | "insufficient"
    structure: str                     # "bull" | "bear" | "range" | "insufficient"
    adx: float | None
    macd_hist: float | None
    reason: str


@dataclass(frozen=True)
class RegimeVerdict:
    """Composite output from one HTF eval."""
    regime: Regime
    score: float                       # weighted composite, ~ -1.0..+1.0
    h1: TimeframeClassification
    h4: TimeframeClassification
    d1: TimeframeClassification
    volatility_tier: VolatilityTier
    atr_pct_d1: float | None           # 1D ATR as % of current price
    nearest_resistance: float | None
    nearest_support: float | None
    distance_to_resistance_pct: float | None
    distance_to_support_pct: float | None
    session: Session
    funding_rate: float | None         # decimal
    funding_extreme: bool
    safe_mode_reason: str | None       # populated iff regime == SAFE_MODE


@dataclass(frozen=True)
class TradePermission:
    """Final gate output for ONE proposed side."""
    allow_long: bool                   # base matrix permission (informational)
    allow_short: bool                  # base matrix permission (informational)
    size_multiplier: float             # 0.0 / 0.5 / 1.0 — what to apply to the trade
    reason: str                        # human-readable matrix path
    hard_zero_reason: str | None       # populated iff size_multiplier was forced to 0
                                       # by a hard-zero override (not just regime mismatch)


# ─── pure indicator functions ───────────────────────────────────────────


def ema(values: Sequence[float], period: int) -> float | None:
    """Latest EMA value. None if `len(values) < period`.

    SMA seed for the first `period` values; standard exponential update
    thereafter with alpha = 2/(period+1).
    """
    n = len(values)
    if n < period or period <= 0:
        return None
    alpha = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = alpha * v + (1.0 - alpha) * e
    return e


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    """Full EMA series. Output length = max(0, len(values) - period + 1)."""
    n = len(values)
    if n < period or period <= 0:
        return []
    alpha = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    out = [e]
    for v in values[period:]:
        e = alpha * v + (1.0 - alpha) * e
        out.append(e)
    return out


def _true_range_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
) -> list[float]:
    """TR_i = max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|).

    Output aligned to bars[1:] (no value for the first bar — needs prev close).
    """
    n = len(highs)
    if n < 2:
        return []
    out = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def _directional_movement_series(
    highs: Sequence[float], lows: Sequence[float],
) -> tuple[list[float], list[float]]:
    """Wilder's +DM, -DM. Aligned to bars[1:]."""
    n = len(highs)
    if n < 2:
        return [], []
    plus, minus = [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus.append(up if (up > down and up > 0) else 0.0)
        minus.append(down if (down > up and down > 0) else 0.0)
    return plus, minus


def _wilder_average(values: Sequence[float], period: int) -> list[float]:
    """Wilder's smoothed-average series. First value is the SMA of the
    first `period` inputs; thereafter the recursive average update.
    Output length = max(0, len(values) - period + 1).

    Used for ATR, +DI/-DI smoothing, and ADX.
    """
    n = len(values)
    if n < period or period <= 0:
        return []
    avg = sum(values[:period]) / period
    out = [avg]
    for v in values[period:]:
        avg = (avg * (period - 1) + v) / period
        out.append(avg)
    return out


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Latest ATR(period). None if insufficient data.

    Wilder's smoothing on the True Range series.
    """
    if len(highs) != len(lows) or len(highs) != len(closes):
        return None
    if len(highs) < period + 1:
        return None
    tr = _true_range_series(highs, lows, closes)
    series = _wilder_average(tr, period)
    return series[-1] if series else None


def adx(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Latest ADX(period). None if insufficient data.

    Standard Wilder ADX:
      1. Compute TR, +DM, -DM series (aligned to bars[1:]).
      2. Wilder-smooth all three over `period`.
      3. Compute +DI = 100 * smoothed(+DM)/smoothed(TR), -DI similarly.
      4. DX_i = 100 * |+DI - -DI| / (+DI + -DI).
      5. ADX = Wilder-smoothed DX over `period`.

    Needs roughly 2*period + 1 bars to produce a stable first ADX value.
    """
    if len(highs) != len(lows) or len(highs) != len(closes):
        return None
    if len(highs) < 2 * period + 1:
        return None

    tr = _true_range_series(highs, lows, closes)
    plus_dm, minus_dm = _directional_movement_series(highs, lows)

    tr_avg = _wilder_average(tr, period)
    plus_avg = _wilder_average(plus_dm, period)
    minus_avg = _wilder_average(minus_dm, period)

    if not tr_avg:
        return None

    dx_series: list[float] = []
    for tr_s, p_s, m_s in zip(tr_avg, plus_avg, minus_avg):
        if tr_s == 0:
            dx_series.append(0.0)
            continue
        plus_di = 100.0 * p_s / tr_s
        minus_di = 100.0 * m_s / tr_s
        denom = plus_di + minus_di
        if denom == 0:
            dx_series.append(0.0)
        else:
            dx_series.append(100.0 * abs(plus_di - minus_di) / denom)

    if len(dx_series) < period:
        return None

    adx_series = _wilder_average(dx_series, period)
    return adx_series[-1] if adx_series else None


def macd_hist(
    closes: Sequence[float],
    fast: int = 12, slow: int = 26, signal: int = 9,
) -> float | None:
    """Latest MACD histogram (= MACD line - signal line).

    None if insufficient data. Requires len(closes) >= slow + signal - 1
    for the signal-line EMA to have a value.
    """
    if fast >= slow or fast <= 0 or signal <= 0:
        return None
    if len(closes) < slow + signal - 1:
        return None

    fast_series = _ema_series(closes, fast)
    slow_series = _ema_series(closes, slow)

    # Align: fast starts (slow - fast) values earlier, trim to slow's start.
    align_offset = slow - fast
    fast_aligned = fast_series[align_offset:]
    macd_line = [f - s for f, s in zip(fast_aligned, slow_series)]

    if len(macd_line) < signal:
        return None
    signal_line = _ema_series(macd_line, signal)
    if not signal_line:
        return None
    return macd_line[-1] - signal_line[-1]


def find_swing_points(
    highs: Sequence[float], lows: Sequence[float], n: int = 2,
) -> tuple[list[int], list[int]]:
    """Indices of swing highs and swing lows. A swing point at index i
    requires `n` bars on each side that are strictly lower (for highs)
    or strictly higher (for lows). Endpoints (first/last n bars) cannot
    be swing points.
    """
    sh: list[int] = []
    sl: list[int] = []
    if len(highs) != len(lows) or len(highs) < 2 * n + 1:
        return sh, sl
    for i in range(n, len(highs) - n):
        h = highs[i]
        l = lows[i]
        if all(h > highs[j] for j in range(i - n, i)) and \
           all(h > highs[j] for j in range(i + 1, i + n + 1)):
            sh.append(i)
        if all(l < lows[j] for j in range(i - n, i)) and \
           all(l < lows[j] for j in range(i + 1, i + n + 1)):
            sl.append(i)
    return sh, sl


def market_structure(
    highs: Sequence[float], lows: Sequence[float],
    lookback: int = 20, n: int = 2,
) -> str:
    """Classify market structure on the last `lookback` bars.

    Returns "bull" | "bear" | "range" | "insufficient".

    Bull = recent SH > prior SH AND recent SL > prior SL.
    Bear = recent SH < prior SH AND recent SL < prior SL.
    Anything else → range. Insufficient if fewer than 2 swings of
    either type exist in the window.
    """
    if len(highs) != len(lows):
        return "insufficient"
    if len(highs) < lookback:
        return "insufficient"
    h_window = list(highs[-lookback:])
    l_window = list(lows[-lookback:])
    sh, sl = find_swing_points(h_window, l_window, n=n)
    if len(sh) < 2 or len(sl) < 2:
        return "insufficient"
    recent_h, prior_h = h_window[sh[-1]], h_window[sh[-2]]
    recent_l, prior_l = l_window[sl[-1]], l_window[sl[-2]]
    higher_highs = recent_h > prior_h
    higher_lows = recent_l > prior_l
    if higher_highs and higher_lows:
        return "bull"
    if (not higher_highs) and (not higher_lows):
        return "bear"
    return "range"


def current_session(ts: datetime) -> Session:
    """UTC-clock session classification.

    Asia    : 00:00–07:00 UTC
    London  : 07:00–12:00 UTC
    Overlap : 12:00–16:00 UTC  (London/NY overlap)
    NewYork : 16:00–21:00 UTC
    Asia    : 21:00–24:00 UTC  (Asia open returning)
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    h = ts.astimezone(timezone.utc).time()
    if time(0, 0) <= h < time(7, 0):
        return Session.Asia
    if time(7, 0) <= h < time(12, 0):
        return Session.London
    if time(12, 0) <= h < time(16, 0):
        return Session.Overlap
    if time(16, 0) <= h < time(21, 0):
        return Session.NewYork
    return Session.Asia


# ─── per-TF classifier ──────────────────────────────────────────────────


def _ema_alignment(
    price: float, e20: float | None, e50: float | None, e200: float | None,
) -> str:
    if e20 is None or e50 is None or e200 is None:
        return "insufficient"
    if price > e20 > e50 > e200:
        return "bull"
    if price < e20 < e50 < e200:
        return "bear"
    return "mixed"


def _fmt_opt(v: float | None, fmt: str = "{:.2f}") -> str:
    return "n/a" if v is None else fmt.format(v)


def classify_timeframe(
    bars: TimeframeBars, config: HTFRegimeConfig,
) -> TimeframeClassification:
    """Classify ONE timeframe into Bull/Bear/Range/Transitional/Insufficient.

    All inputs (bars) must be CLOSED. Caller drops in-progress bar
    upstream (`LiveBarCache` does this).
    """
    closes = list(bars.closes)
    highs = list(bars.highs)
    lows = list(bars.lows)

    e20p, e50p, e200p = config.ema_periods
    e20 = ema(closes, e20p)
    e50 = ema(closes, e50p)
    e200 = ema(closes, e200p)

    if e200 is None:
        return TimeframeClassification(
            timeframe=bars.timeframe,
            regime=TimeframeRegime.Insufficient,
            ema20=e20, ema50=e50, ema200=e200,
            ema_alignment="insufficient",
            structure="insufficient",
            adx=None, macd_hist=None,
            reason=(
                f"insufficient bars: have {len(closes)}, need >= {e200p} "
                f"for EMA{e200p}"
            ),
        )

    price = closes[-1]                    # most recent CLOSED bar's close
    align = _ema_alignment(price, e20, e50, e200)
    struct = market_structure(
        highs, lows, lookback=config.swing_lookback, n=config.swing_n,
    )
    adx_val = adx(highs, lows, closes, period=config.adx_period)
    macd_val = macd_hist(closes, *config.macd_periods)

    is_trending = adx_val is not None and adx_val > config.adx_trend_threshold
    macd_pos = macd_val is not None and macd_val > 0
    macd_neg = macd_val is not None and macd_val < 0

    if align == "bull" and (struct == "bull" or (is_trending and macd_pos)):
        regime = TimeframeRegime.Bull
        reason = (
            f"EMA bull aligned; struct={struct}; "
            f"ADX={_fmt_opt(adx_val, '{:.1f}')}, MACD_h={_fmt_opt(macd_val, '{:.4f}')}"
        )
    elif align == "bear" and (struct == "bear" or (is_trending and macd_neg)):
        regime = TimeframeRegime.Bear
        reason = (
            f"EMA bear aligned; struct={struct}; "
            f"ADX={_fmt_opt(adx_val, '{:.1f}')}, MACD_h={_fmt_opt(macd_val, '{:.4f}')}"
        )
    elif align == "mixed" and adx_val is not None and adx_val < config.adx_trend_threshold:
        regime = TimeframeRegime.Range
        reason = (
            f"EMA mixed; ADX={adx_val:.1f} < threshold "
            f"{config.adx_trend_threshold} (ranging)"
        )
    else:
        regime = TimeframeRegime.Transitional
        reason = (
            f"conflicting: EMA={align}; struct={struct}; "
            f"ADX={_fmt_opt(adx_val, '{:.1f}')}, MACD_h={_fmt_opt(macd_val, '{:.4f}')}"
        )

    return TimeframeClassification(
        timeframe=bars.timeframe,
        regime=regime,
        ema20=e20, ema50=e50, ema200=e200,
        ema_alignment=align,
        structure=struct,
        adx=adx_val,
        macd_hist=macd_val,
        reason=reason,
    )


def _missing_classification(tf: str, reason: str) -> TimeframeClassification:
    return TimeframeClassification(
        timeframe=tf,
        regime=TimeframeRegime.Insufficient,
        ema20=None, ema50=None, ema200=None,
        ema_alignment="insufficient",
        structure="insufficient",
        adx=None, macd_hist=None,
        reason=reason,
    )


# ─── composite regime + context fields ──────────────────────────────────


def _tf_to_signed(regime: TimeframeRegime) -> int:
    if regime == TimeframeRegime.Bull:
        return 1
    if regime == TimeframeRegime.Bear:
        return -1
    return 0


def _score_to_regime(score: float, thresholds: dict[str, float]) -> Regime:
    """Map composite score to 5-state regime per thresholds.

    Default thresholds: strong_bull 0.7, bull 0.3, bear -0.3, strong_bear -0.7.
    Inclusive at the boundaries (>=, <=).
    """
    if score >= thresholds["strong_bull"]:
        return Regime.STRONG_BULL
    if score >= thresholds["bull"]:
        return Regime.BULL
    if score <= thresholds["strong_bear"]:
        return Regime.STRONG_BEAR
    if score <= thresholds["bear"]:
        return Regime.BEAR
    return Regime.NEUTRAL


def _atr_pct_to_tier(
    atr_pct: float | None, tier_thresholds: dict[str, float],
) -> VolatilityTier:
    """ATR-as-%-of-price → tier. Thresholds are upper bounds for each tier."""
    if atr_pct is None:
        return VolatilityTier.Unknown
    if atr_pct < tier_thresholds["low"]:
        return VolatilityTier.Low
    if atr_pct < tier_thresholds["normal"]:
        return VolatilityTier.Normal
    if atr_pct < tier_thresholds["high"]:
        return VolatilityTier.High
    return VolatilityTier.Extreme


def _atr_pct_for_d1(
    bars: TimeframeBars | None, period: int, current_price: float,
) -> float | None:
    """1D ATR as percentage of current price."""
    if bars is None or current_price <= 0:
        return None
    a = atr(bars.highs, bars.lows, bars.closes, period=period)
    if a is None:
        return None
    return 100.0 * a / current_price


def _nearest_levels(
    ctx: HTFContext, config: HTFRegimeConfig,
) -> tuple[float | None, float | None]:
    """Aggregate swing points from 4H + 1D plus prior-day H/L.
    Return (nearest_resistance_above, nearest_support_below) relative
    to current_price.
    """
    candidates_above: list[float] = []
    candidates_below: list[float] = []
    price = ctx.current_price

    for tf_bars in (ctx.h4, ctx.d1):
        if tf_bars is None:
            continue
        if len(tf_bars.highs) < 2 * config.swing_n + 1:
            continue
        sh, sl = find_swing_points(
            list(tf_bars.highs), list(tf_bars.lows), n=config.swing_n,
        )
        for idx in sh:
            level = tf_bars.highs[idx]
            if level > price:
                candidates_above.append(level)
        for idx in sl:
            level = tf_bars.lows[idx]
            if level < price:
                candidates_below.append(level)

    if ctx.prior_day_high is not None and ctx.prior_day_high > price:
        candidates_above.append(ctx.prior_day_high)
    if ctx.prior_day_low is not None and ctx.prior_day_low < price:
        candidates_below.append(ctx.prior_day_low)

    nearest_r = min(candidates_above) if candidates_above else None
    nearest_s = max(candidates_below) if candidates_below else None
    return nearest_r, nearest_s


def _distance_pct(price: float, level: float | None) -> float | None:
    if level is None or price <= 0:
        return None
    return 100.0 * abs(level - price) / price


def _safe_mode_verdict(
    ctx: HTFContext,
    h1_class: TimeframeClassification,
    h4_class: TimeframeClassification,
    d1_class: TimeframeClassification,
    reason: str,
) -> RegimeVerdict:
    return RegimeVerdict(
        regime=Regime.SAFE_MODE,
        score=0.0,
        h1=h1_class, h4=h4_class, d1=d1_class,
        volatility_tier=VolatilityTier.Unknown,
        atr_pct_d1=None,
        nearest_resistance=None,
        nearest_support=None,
        distance_to_resistance_pct=None,
        distance_to_support_pct=None,
        session=current_session(ctx.ts),
        funding_rate=ctx.funding_rate,
        funding_extreme=False,
        safe_mode_reason=reason,
    )


def compute_regime(
    ctx: HTFContext, config: HTFRegimeConfig,
) -> RegimeVerdict:
    """Top-level entry point.

    Returns SAFE_MODE if all three timeframes are missing or
    insufficient. Otherwise computes per-TF classifications, the
    composite weighted score, the 5-state regime, and all context
    fields used by `get_trade_permissions`.
    """
    h1_class = (
        classify_timeframe(ctx.h1, config) if ctx.h1 is not None
        else _missing_classification("1h", "no h1 bars supplied")
    )
    h4_class = (
        classify_timeframe(ctx.h4, config) if ctx.h4 is not None
        else _missing_classification("4h", "no h4 bars supplied")
    )
    d1_class = (
        classify_timeframe(ctx.d1, config) if ctx.d1 is not None
        else _missing_classification("1d", "no d1 bars supplied")
    )

    insufficient_count = sum(
        1 for c in (h1_class, h4_class, d1_class)
        if c.regime == TimeframeRegime.Insufficient
    )
    if insufficient_count == 3:
        return _safe_mode_verdict(
            ctx, h1_class, h4_class, d1_class,
            reason="all timeframes insufficient (cold start or data outage)",
        )

    w = config.composite_weights
    score = (
        w["d1"] * _tf_to_signed(d1_class.regime)
        + w["h4"] * _tf_to_signed(h4_class.regime)
        + w["h1"] * _tf_to_signed(h1_class.regime)
    )
    regime = _score_to_regime(score, config.regime_thresholds)

    atr_pct = _atr_pct_for_d1(ctx.d1, config.adx_period, ctx.current_price)
    vol_tier = _atr_pct_to_tier(atr_pct, config.vol_tier_atr_pct)

    nearest_r, nearest_s = _nearest_levels(ctx, config)
    dist_r = _distance_pct(ctx.current_price, nearest_r)
    dist_s = _distance_pct(ctx.current_price, nearest_s)

    funding_extreme = (
        ctx.funding_rate is not None
        and abs(ctx.funding_rate) * 100.0 > config.funding_extreme_pct_per_8h
    )

    return RegimeVerdict(
        regime=regime,
        score=score,
        h1=h1_class, h4=h4_class, d1=d1_class,
        volatility_tier=vol_tier,
        atr_pct_d1=atr_pct,
        nearest_resistance=nearest_r,
        nearest_support=nearest_s,
        distance_to_resistance_pct=dist_r,
        distance_to_support_pct=dist_s,
        session=current_session(ctx.ts),
        funding_rate=ctx.funding_rate,
        funding_extreme=funding_extreme,
        safe_mode_reason=None,
    )


# ─── trade-permission matrix ────────────────────────────────────────────


def _matrix_base(
    regime: Regime, h1_regime: TimeframeRegime,
) -> tuple[bool, bool, float, float, str]:
    """Return (allow_long, allow_short, mult_long, mult_short, reason).

    Pre-hard-zero. SAFE_MODE forces full block; other regimes follow
    the spec matrix.
    """
    if regime == Regime.SAFE_MODE:
        return (False, False, 0.0, 0.0, "SAFE_MODE: data unavailable")
    if regime == Regime.STRONG_BULL:
        return (True, False, 1.0, 0.0, "STRONG_BULL: longs full size, no shorts")
    if regime == Regime.STRONG_BEAR:
        return (False, True, 0.0, 1.0, "STRONG_BEAR: shorts full size, no longs")
    if regime == Regime.BULL:
        if h1_regime == TimeframeRegime.Bear:
            return (
                True, False, 0.5, 0.0,
                "BULL + H1=Bear: long 0.5x (pullback only)",
            )
        return (
            True, False, 1.0, 0.0,
            f"BULL + H1={h1_regime.value}: long full size",
        )
    if regime == Regime.BEAR:
        if h1_regime == TimeframeRegime.Bull:
            return (
                False, True, 0.0, 0.5,
                "BEAR + H1=Bull: short 0.5x (bounce only)",
            )
        return (
            False, True, 0.0, 1.0,
            f"BEAR + H1={h1_regime.value}: short full size",
        )
    if regime == Regime.NEUTRAL:
        return (
            True, True, 0.5, 0.5,
            "NEUTRAL: both directions 0.5x (mean-reversion preferred)",
        )
    return (False, False, 0.0, 0.0, f"unknown regime {regime}")


def get_trade_permissions(
    verdict: RegimeVerdict, proposed_side: str, config: HTFRegimeConfig,
) -> TradePermission:
    """Apply the matrix + hard-zero overrides for ONE proposed side.

    proposed_side: "buy" or "sell" (the score accumulator's winning_side).
    Returns a TradePermission with the final size_multiplier.
    """
    side = (proposed_side or "").lower()
    if side not in ("buy", "sell"):
        return TradePermission(
            allow_long=False, allow_short=False, size_multiplier=0.0,
            reason=f"invalid proposed_side={proposed_side!r}",
            hard_zero_reason="invalid_side",
        )

    al, asho, mlong, mshort, matrix_reason = _matrix_base(
        verdict.regime, verdict.h1.regime,
    )
    base_mult = mlong if side == "buy" else mshort
    base_allow = al if side == "buy" else asho

    # Side conflicts with matrix permission → forced 0.
    if not base_allow or base_mult == 0.0:
        return TradePermission(
            allow_long=al, allow_short=asho, size_multiplier=0.0,
            reason=f"{matrix_reason}; side={side} not permitted",
            hard_zero_reason=(
                "safe_mode" if verdict.regime == Regime.SAFE_MODE
                else "regime_forbids_side"
            ),
        )

    # Hard-zero override 1: proximity to opposing HTF level.
    if side == "buy" and verdict.distance_to_resistance_pct is not None:
        if verdict.distance_to_resistance_pct < config.proximity_block_pct:
            return TradePermission(
                allow_long=al, allow_short=asho, size_multiplier=0.0,
                reason=(
                    f"{matrix_reason}; within {config.proximity_block_pct}% "
                    f"of resistance ({verdict.distance_to_resistance_pct:.2f}%)"
                ),
                hard_zero_reason="proximity_to_resistance",
            )
    if side == "sell" and verdict.distance_to_support_pct is not None:
        if verdict.distance_to_support_pct < config.proximity_block_pct:
            return TradePermission(
                allow_long=al, allow_short=asho, size_multiplier=0.0,
                reason=(
                    f"{matrix_reason}; within {config.proximity_block_pct}% "
                    f"of support ({verdict.distance_to_support_pct:.2f}%)"
                ),
                hard_zero_reason="proximity_to_support",
            )

    # Hard-zero override 2: extreme volatility.
    if verdict.volatility_tier == VolatilityTier.Extreme:
        return TradePermission(
            allow_long=al, allow_short=asho, size_multiplier=0.0,
            reason=(
                f"{matrix_reason}; vol_tier=Extreme "
                f"(1D ATR {verdict.atr_pct_d1:.2f}%)"
                if verdict.atr_pct_d1 is not None
                else f"{matrix_reason}; vol_tier=Extreme"
            ),
            hard_zero_reason="vol_tier_extreme",
        )

    # Hard-zero override 3: funding extreme + crowded side.
    if verdict.funding_extreme and verdict.funding_rate is not None:
        crowded_side = "buy" if verdict.funding_rate > 0 else "sell"
        if side == crowded_side:
            return TradePermission(
                allow_long=al, allow_short=asho, size_multiplier=0.0,
                reason=(
                    f"{matrix_reason}; funding_extreme "
                    f"({verdict.funding_rate * 100:.4f}% per 8h) "
                    f"— {side} is crowded side"
                ),
                hard_zero_reason="funding_extreme_crowded",
            )

    return TradePermission(
        allow_long=al, allow_short=asho, size_multiplier=base_mult,
        reason=matrix_reason,
        hard_zero_reason=None,
    )
