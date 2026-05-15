"""HTF support/resistance helpers for adaptive TP placement.

Resamples 3m OHLCV bars into higher-timeframe (default 15m) buckets,
drops the in-progress HTF bar, and reports the nearest confirmed
swing-high above the current price (resistance) and the nearest
swing-low below it (support). These levels feed the trade-plan
builder's TP2 snap rule.

Pure-function design — caller supplies the 3m bar sequence. In
production, that's likely either the in-memory 3m LiveBarCache (if
the cache holds enough history) or a query against
`bitunix_bar_history`. The resample-per-call approach is the spec's
"simple version first"; an incremental 15m cache can replace this
later without changing the public API.
"""
from __future__ import annotations

from collections.abc import Sequence

from trading_corp.agents.strategies.bitunix_htf_regime import find_swing_points
from trading_corp.data.live_bar_cache import Bar

__all__ = ["resample_3m_to_htf", "get_htf_levels"]


def resample_3m_to_htf(
    bars_3m: Sequence[Bar],
    htf_minutes: int,
) -> list[Bar]:
    """Bucket 3m bars into htf_minutes windows, drop the last (in-progress)
    bucket to avoid lookahead. Returns a list of HTF Bars sorted by ts_ms
    ascending.
    """
    if not bars_3m or htf_minutes <= 0:
        return []
    bucket_ms = htf_minutes * 60 * 1000
    grouped: dict[int, list[Bar]] = {}
    for b in bars_3m:
        bucket_ts = (b.ts_ms // bucket_ms) * bucket_ms
        grouped.setdefault(bucket_ts, []).append(b)
    sorted_buckets = sorted(grouped.keys())
    if len(sorted_buckets) < 2:
        return []
    out: list[Bar] = []
    for bucket_ts in sorted_buckets[:-1]:
        chunk = sorted(grouped[bucket_ts], key=lambda x: x.ts_ms)
        out.append(Bar(
            ts_ms=bucket_ts,
            open=chunk[0].open,
            high=max(b.high for b in chunk),
            low=min(b.low for b in chunk),
            close=chunk[-1].close,
            volume=sum(b.volume for b in chunk),
        ))
    return out


def get_htf_levels(
    bars_3m: Sequence[Bar],
    current_idx: int,
    htf_minutes: int = 15,
    lookback_bars_htf: int = 40,
    n: int = 2,
    current_price: float | None = None,
) -> tuple[float | None, float | None]:
    """Return (resistance, support) — nearest confirmed HTF swing strictly
    above / below current_price.

    "Nearest" is measured in price space, not time — the wall we'd hit
    first if price moves in that direction. Returns (None, None) when
    fewer than 2*n+1 HTF bars are available or no swing exists on the
    correct side.

    No-lookahead: input is sliced to bars_3m[:current_idx + 1] before
    resampling, and the last HTF bucket is dropped (could be in-progress).
    """
    if current_idx < 0 or current_idx >= len(bars_3m):
        return None, None
    if current_price is None:
        current_price = bars_3m[current_idx].close

    sliced = list(bars_3m[: current_idx + 1])
    htf_bars = resample_3m_to_htf(sliced, htf_minutes)
    if len(htf_bars) < 2 * n + 1:
        return None, None
    window = htf_bars[-lookback_bars_htf:] if len(htf_bars) > lookback_bars_htf else htf_bars

    highs = [b.high for b in window]
    lows = [b.low for b in window]
    sh, sl = find_swing_points(highs, lows, n=n)

    above = [highs[j] for j in sh if highs[j] > current_price]
    resistance = min(above) if above else None
    below = [lows[j] for j in sl if lows[j] < current_price]
    support = max(below) if below else None
    return resistance, support
