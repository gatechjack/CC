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

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from trading_corp.agents.strategies.btc_accumulator import PriceContext

if TYPE_CHECKING:
    from trading_corp.data.live_bar_cache import Bar, LiveBarCache
    from trading_corp.agents.strategies.bitunix_confluence import (
        BitUnixConfluenceConfig,
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
