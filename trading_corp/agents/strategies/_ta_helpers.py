"""Shared TA primitives used by the BitUnix confluence gate (and any
future strategy that wants the same pure-Python building blocks).

Design constraints:
  * Pure functions. No I/O, no dataclasses, no logging.
  * No numpy / pandas / statistics imports — keeps the dashboard light
    and matches the style of the rest of `trading_corp.data` and
    `trading_corp.agents.strategies`.
  * All functions return `None` (or empty list) on insufficient data
    rather than raising. Callers in the confluence gate translate
    `None` inputs into `passed=False` so the gate stays conservative
    during cache warm-up.

The `ema` / `ema_series` / `atr` re-exports keep call sites in the
gate (and any future module) pointed at this single helpers file
instead of importing from `bitunix_htf_regime` directly — the regime
module exists for a different concern.
"""
from __future__ import annotations

from typing import Sequence

from trading_corp.agents.strategies.bitunix_htf_regime import (
    _ema_series as _ema_series_impl,
    atr as _atr_impl,
    ema as _ema_impl,
)

__all__ = [
    "atr",
    "bb_width_series",
    "bollinger_band_width",
    "ema",
    "ema_series",
    "linregress_slope",
    "percentile_rank",
    "sma",
    "sma_series",
    "stdev",
    "zscore",
]


def ema(values: Sequence[float], period: int) -> float | None:
    """Re-export of `bitunix_htf_regime.ema`."""
    return _ema_impl(values, period)


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Re-export of `bitunix_htf_regime._ema_series` under a public name."""
    return _ema_series_impl(values, period)


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Re-export of `bitunix_htf_regime.atr`."""
    return _atr_impl(highs, lows, closes, period)


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last `period` values."""
    n = len(values)
    if period <= 0 or n < period:
        return None
    return sum(values[-period:]) / period


def sma_series(values: Sequence[float], period: int) -> list[float]:
    """Rolling SMA series. Output length = max(0, len(values) - period + 1)."""
    n = len(values)
    if period <= 0 or n < period:
        return []
    out = [sum(values[:period]) / period]
    window_sum = out[0] * period
    for i in range(period, n):
        window_sum += values[i] - values[i - period]
        out.append(window_sum / period)
    return out


def stdev(values: Sequence[float], period: int) -> float | None:
    """Population standard deviation of the last `period` values.

    Population (N) not sample (N-1) — matches the convention Bollinger
    Bands use in TradingView / most charting platforms.
    """
    n = len(values)
    if period <= 1 or n < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    var = sum((v - mean) ** 2 for v in window) / period
    return var ** 0.5


def zscore(value: float | None, mean: float | None, std: float | None) -> float | None:
    """Standard z-score: (value - mean) / std. `None` if any input is
    `None` or `std` is zero (degenerate)."""
    if value is None or mean is None or std is None:
        return None
    if std == 0:
        return None
    return (value - mean) / std


def linregress_slope(values: Sequence[float]) -> float | None:
    """Slope of the best-fit line through `values` using x = 0..n-1.

    Closed-form least-squares — `(n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²)`.
    Returns `None` if `len(values) < 2`. Matches the slope you'd get
    from `numpy.polyfit(range(n), values, 1)[0]` but avoids the numpy
    import (consistent with the rest of `agents/strategies/`).
    """
    n = len(values)
    if n < 2:
        return None
    sum_x = (n - 1) * n / 2.0                     # 0 + 1 + ... + (n-1)
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6.0      # 0² + 1² + ... + (n-1)²
    sum_y = 0.0
    sum_xy = 0.0
    for i, v in enumerate(values):
        sum_y += v
        sum_xy += i * v
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return None
    return (n * sum_xy - sum_x * sum_y) / denom


def bollinger_band_width(
    values: Sequence[float], period: int = 20, n_stdev: float = 2.0,
) -> float | None:
    """Latest Bollinger Band width = (upper - lower) / middle.

    Middle = SMA(period). Upper / lower = middle ± n_stdev * stdev.
    Returns `None` if insufficient data or middle == 0 (degenerate).
    """
    mid = sma(values, period)
    s = stdev(values, period)
    if mid is None or s is None:
        return None
    if mid == 0:
        return None
    return (2.0 * n_stdev * s) / mid


def bb_width_series(
    values: Sequence[float], period: int = 20, n_stdev: float = 2.0,
) -> list[float]:
    """Rolling Bollinger Band width series. Each element corresponds to
    the BB width at that bar (window ending at that index). Output
    length = max(0, len(values) - period + 1).

    Skips bars where the SMA is zero (would divide-by-zero) — those
    points are omitted from the output. In practice this only happens
    for synthetic test fixtures, never real prices.
    """
    n = len(values)
    if period <= 1 or n < period:
        return []
    out: list[float] = []
    for end in range(period, n + 1):
        window = values[end - period:end]
        mid = sum(window) / period
        if mid == 0:
            continue
        var = sum((v - mid) ** 2 for v in window) / period
        s = var ** 0.5
        out.append((2.0 * n_stdev * s) / mid)
    return out


def percentile_rank(value: float | None, window: Sequence[float]) -> float | None:
    """Fraction of `window` that is strictly less than `value`, on [0, 1].

    Returns `None` if `value is None` or `window` is empty. A value
    at the 60th percentile returns `0.60`. Ties below count, ties
    equal do not (strict `<`) — matches `numpy`'s `'mean'` percentile
    handling closely enough for the gate's bottom-decile check.

    Extends the spirit of `web/routes.py:_percentile` (which goes the
    other direction: rank → value).
    """
    if value is None or not window:
        return None
    n = len(window)
    below = sum(1 for v in window if v < value)
    return below / n
