"""Realized crypto vol provider (v2).

Computes annualized close-to-close realized volatility from Coinbase
OHLCV bars via ccxt. Replaces the static ANNUAL_VOLS dict in
crypto_spot_provider when fresh data is available; falls back to the
static dict on fetch failure, insufficient coverage, or staleness.

Isolation: this module only feeds CryptoSpotProvider.get_annual_vol().
No strategy code paths import it directly -- strategies see the change
through the existing get_annual_vol() surface.

Annualization math (see realized_vol_annualized):
    Var scales linearly with time, so sigma scales with sqrt(time).
    For close-to-close log returns sampled at `bar_seconds` intervals,
    annual_sigma = period_sigma * sqrt(SECONDS_PER_YEAR / bar_seconds).
    At 5m bars: sqrt(365.25 * 24 * 3600 / 300) = sqrt(105192) ~= 324.33.
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

SECONDS_PER_YEAR = 365.25 * 24 * 3600

_TF_TO_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@dataclass
class VolConfig:
    """Configuration for realized-vol refresh.

    Read from strategies.yaml kalshi_crypto_arb.realized_vol on each
    refresh; production caller mtime-cached its parent config.
    """
    enabled: bool = True
    lookback_days: int = 14
    bar_interval: str = "5m"
    refresh_interval_minutes: int = 60
    min_bar_coverage_pct: float = 80.0
    max_staleness_minutes: int = 30  # newest bar must be within this

    @property
    def bar_seconds(self) -> int:
        return _TF_TO_SECONDS[self.bar_interval]

    @property
    def expected_bars(self) -> int:
        return (self.lookback_days * 24 * 3600) // self.bar_seconds


@dataclass
class VolEntry:
    asset: str
    annual_vol: float
    computed_ts: float          # wall-clock time.time() at refresh
    n_bars: int
    source: str                 # "realized" | "fallback_<reason>"


@dataclass
class VolCache:
    """Shared cache of realized vols per asset. One per process.

    Reads are sync (get_annual_vol stays @staticmethod-compatible);
    refresh is async and gated by refresh_interval_minutes.
    """
    entries: dict[str, VolEntry] = field(default_factory=dict)
    last_refresh_ts: float = 0.0
    last_refresh_error: str | None = None
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get(self, asset: str) -> VolEntry | None:
        return self.entries.get(asset)


# Process-singleton cache.
_CACHE = VolCache()


def get_cache() -> VolCache:
    return _CACHE


def annualization_factor(bar_seconds: int) -> float:
    return math.sqrt(SECONDS_PER_YEAR / bar_seconds)


def realized_vol_annualized(closes: list[float], bar_seconds: int) -> float:
    """Annualized sample std of log returns. Raises if too few bars.

    Sample (n-1) std is the unbiased estimator; statistics.stdev uses
    that. Result is dimensionless (decimal, e.g. 0.30 = 30%/yr).
    """
    if len(closes) < 30:
        raise ValueError(f"insufficient closes: {len(closes)}")
    rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] <= 0 or closes[i] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 30:
        raise ValueError(f"insufficient returns: {len(rets)}")
    sigma_period = statistics.stdev(rets)
    return sigma_period * annualization_factor(bar_seconds)


async def _fetch_bars(
    exchange: Any, symbol: str, timeframe: str, limit: int,
) -> list[list]:
    """Paginate fetch_ohlcv backward and dedup bars by timestamp.

    Coinbase's fetch_ohlcv returns ~300 bars/call. Walk `since=`
    backward to assemble more. Seams between pages can overlap (the
    exchange may include the `since=` boundary bar in two consecutive
    pages), so dedup on bar[0] (ms timestamp) before sort+slice.
    Without dedup, duplicate closes inject zero log returns into the
    std calc and bias sigma downward.

    Returns up to `limit` newest bars, sorted ascending by timestamp.
    """
    bar_s = _TF_TO_SECONDS[timeframe]
    by_ts: dict[int, list] = {}
    since_ms: int | None = None
    while len(by_ts) < limit:
        page = await exchange.fetch_ohlcv(
            symbol, timeframe=timeframe,
            since=since_ms,
            limit=min(300, limit - len(by_ts) + 50),  # +50 slack for seam overlap
        )
        if not page:
            break
        new_this_page = 0
        for row in page:
            ts = int(row[0])
            if ts not in by_ts:
                by_ts[ts] = row
                new_this_page += 1
        page_start_ms = page[0][0]
        since_ms = page_start_ms - bar_s * 300 * 1000
        if since_ms <= 0:
            break
        if new_this_page == 0:
            # Pagination has stopped advancing; bail out.
            break
    rows = sorted(by_ts.values(), key=lambda r: r[0])
    return rows[-limit:]


async def refresh_realized_vols(
    cfg: VolConfig,
    asset_to_symbol: dict[str, str],
    fallback_constants: dict[str, float],
) -> dict[str, str]:
    """Refresh _CACHE.entries for all assets. Idempotent under the lock.

    Returns per-asset status: 'realized:<n_bars>' | 'fallback_<reason>'
    | 'cached'. Caller can log the dict after refresh.

    If cfg.enabled is False, all assets get fallback_constants with
    source='fallback_disabled'. Caller still sees a populated cache.
    """
    async with _CACHE.refresh_lock:
        now = time.time()
        interval_s = cfg.refresh_interval_minutes * 60
        if (now - _CACHE.last_refresh_ts) < interval_s and _CACHE.entries:
            return {a: "cached" for a in asset_to_symbol}

        statuses: dict[str, str] = {}

        if not cfg.enabled:
            for asset, hv in fallback_constants.items():
                _CACHE.entries[asset] = VolEntry(
                    asset=asset, annual_vol=hv, computed_ts=now,
                    n_bars=0, source="fallback_disabled",
                )
                statuses[asset] = "fallback_disabled"
            _CACHE.last_refresh_ts = now
            return statuses

        try:
            import ccxt.async_support as ccxt_async  # type: ignore
        except ImportError:
            _CACHE.last_refresh_error = "ccxt not installed"
            for asset, hv in fallback_constants.items():
                _CACHE.entries[asset] = VolEntry(
                    asset=asset, annual_vol=hv, computed_ts=now,
                    n_bars=0, source="fallback_ccxt_missing",
                )
                statuses[asset] = "fallback_ccxt_missing"
            _CACHE.last_refresh_ts = now
            return statuses

        exchange = ccxt_async.coinbase({"enableRateLimit": True})
        bar_s = cfg.bar_seconds
        expected = cfg.expected_bars
        min_required = int(expected * cfg.min_bar_coverage_pct / 100.0)
        try:
            for asset, symbol in asset_to_symbol.items():
                hv = fallback_constants.get(asset)
                try:
                    bars = await _fetch_bars(
                        exchange, symbol, cfg.bar_interval, expected,
                    )
                except Exception as e:
                    log.warning(
                        "vol_v2: %s fetch failed: %s; using fallback", asset, e,
                    )
                    if hv is not None:
                        _CACHE.entries[asset] = VolEntry(
                            asset=asset, annual_vol=hv, computed_ts=now,
                            n_bars=0, source="fallback_fetch_error",
                        )
                        statuses[asset] = f"fallback_fetch_error:{e!r}"
                    continue
                if len(bars) < min_required:
                    log.warning(
                        "vol_v2: %s insufficient bars %d < %d "
                        "(%.0f%% of %d); using fallback",
                        asset, len(bars), min_required,
                        cfg.min_bar_coverage_pct, expected,
                    )
                    if hv is not None:
                        _CACHE.entries[asset] = VolEntry(
                            asset=asset, annual_vol=hv, computed_ts=now,
                            n_bars=len(bars), source="fallback_insufficient_bars",
                        )
                        statuses[asset] = f"fallback_insufficient_bars:{len(bars)}"
                    continue
                newest_ts_ms = bars[-1][0]
                age_min = (now * 1000 - newest_ts_ms) / 60000.0
                if age_min > cfg.max_staleness_minutes:
                    log.warning(
                        "vol_v2: %s newest bar %.1fmin old > %dmin cap; "
                        "using fallback",
                        asset, age_min, cfg.max_staleness_minutes,
                    )
                    if hv is not None:
                        _CACHE.entries[asset] = VolEntry(
                            asset=asset, annual_vol=hv, computed_ts=now,
                            n_bars=len(bars), source="fallback_stale",
                        )
                        statuses[asset] = f"fallback_stale:{age_min:.0f}min"
                    continue
                closes = [b[4] for b in bars]
                try:
                    rv = realized_vol_annualized(closes, bar_s)
                except Exception as e:
                    log.warning(
                        "vol_v2: %s calc failed: %s; using fallback", asset, e,
                    )
                    if hv is not None:
                        _CACHE.entries[asset] = VolEntry(
                            asset=asset, annual_vol=hv, computed_ts=now,
                            n_bars=len(bars), source="fallback_calc_error",
                        )
                        statuses[asset] = f"fallback_calc_error:{e!r}"
                    continue
                _CACHE.entries[asset] = VolEntry(
                    asset=asset, annual_vol=rv, computed_ts=now,
                    n_bars=len(bars), source="realized",
                )
                statuses[asset] = f"realized:{len(bars)}"
            _CACHE.last_refresh_ts = now
            _CACHE.last_refresh_error = None
        except Exception as e:
            _CACHE.last_refresh_error = str(e)
            log.exception("vol_v2: refresh loop fatal: %s", e)
        finally:
            try:
                await exchange.close()
            except Exception:
                pass
        return statuses
