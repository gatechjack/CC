"""Live crypto spot provider for Kalshi crypto market eval.

Maps Kalshi asset prefixes (BTC/ETH/SOL/DOGE/XRP) to Coinbase symbols
and fetches live spot via the existing CoinbaseBroker. Caches each
asset's spot for 10s to bound API hits during a scan cycle.

Annualized volatilities are hard-coded v1; refresh quarterly. v2 can
compute rolling realized vol from Coinbase bar history.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from trading_corp.data.crypto_vol_provider import (
    VolConfig, get_cache as _get_vol_cache, refresh_realized_vols,
)

log = logging.getLogger(__name__)

# Kalshi ticker prefix → Coinbase ccxt symbol. Limited to Coinbase US
# listings; HYPE / BNB skipped (no US spot venue we already have wired).
_KALSHI_TO_COINBASE: dict[str, str] = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "DOGE": "DOGE/USD",
    "XRP": "XRP/USD",
}

# Annualized close-to-close volatility (decimal). v1 hard-coded; refresh
# quarterly from `python -c 'compute_realized_vol(...)'`. Higher = more
# uncertainty band → more near-threshold skips. Conservative numbers
# preferred over aggressive (false-skip costs us a fire; over-confident
# σ costs us a wrong fire).
# v2 (2026-05-20): used as FALLBACK only when crypto_vol_provider can't
# compute realized vol (fetch error, insufficient coverage, staleness,
# or realized_vol.enabled=false). See VolEntry.source in audit for which
# path each asset took on a given refresh.
ANNUAL_VOLS: dict[str, float] = {
    "BTC": 0.60,
    "ETH": 0.75,
    "SOL": 0.90,
    "DOGE": 1.10,
    "XRP": 0.85,
}

UNSUPPORTED_ASSETS: set[str] = {"HYPE", "BNB"}

_SPOT_CACHE_TTL_SEC = 10.0


class CryptoSpotProvider:
    """Async spot-price fetcher. Constructed cheaply; one per scan cycle
    is fine. Cache is instance-local."""

    def __init__(self, coinbase_broker: Any) -> None:
        self._coinbase = coinbase_broker
        self._cache: dict[str, tuple[float, float]] = {}
        # asset → (spot_usd, ts)

    async def get_spot(self, asset: str) -> float | None:
        if asset in UNSUPPORTED_ASSETS:
            return None
        cb_sym = _KALSHI_TO_COINBASE.get(asset)
        if cb_sym is None or self._coinbase is None:
            return None
        cached = self._cache.get(asset)
        if cached and (time.time() - cached[1]) < _SPOT_CACHE_TTL_SEC:
            return cached[0]
        try:
            price = await self._coinbase.quote(cb_sym)
        except Exception as e:
            log.debug("crypto_spot_provider quote(%s) failed: %s", cb_sym, e)
            return None
        if price is None or price <= 0:
            return None
        self._cache[asset] = (float(price), time.time())
        return float(price)

    @staticmethod
    def get_annual_vol(asset: str) -> float | None:
        # v2: prefer realized vol from cache if a fresh entry exists.
        # Falls back to the hardcoded constant when:
        #   - the cache has never been refreshed (first scan after restart)
        #   - vol_v2 is disabled in config
        #   - refresh failed / had insufficient bars / went stale
        entry = _get_vol_cache().get(asset)
        if entry is not None:
            return entry.annual_vol
        return ANNUAL_VOLS.get(asset)

    @staticmethod
    async def refresh_realized_vols_if_due(cfg: VolConfig) -> dict[str, str]:
        """Call once per scan cycle. Internal rate-limit gate honors
        cfg.refresh_interval_minutes -- most calls are cheap no-ops."""
        return await refresh_realized_vols(cfg, _KALSHI_TO_COINBASE, ANNUAL_VOLS)

    @staticmethod
    def is_supported(asset: str) -> bool:
        return asset in _KALSHI_TO_COINBASE


_ASSET_TICKER_RE = re.compile(
    r"^KX(HYPE|DOGE|BTC|ETH|SOL|XRP|BNB)[A-Z0-9]*-"
)


def parse_kalshi_asset_prefix(ticker: str) -> str | None:
    """Extract the asset symbol from a Kalshi crypto-market ticker.

    Kalshi suffixes the asset with a market-type code (E = event-cycle,
    D = daily, 15M / 1H = interval, etc.) — regex matches anchor + asset
    + arbitrary uppercase/digit suffix + dash. Alternation is longest-
    prefix-first so HYPE never collides with H-anything.

    Examples:
      KXBTC15M-26MAY1416-T80000  → 'BTC'
      KXETH-26MAY14-T1900        → 'ETH'
      KXSOLE-26MAY1422-T59       → 'SOL'  (event-cycle format)
      KXBTCD-26MAY15-T80000      → 'BTC'  (daily format)
    """
    if not ticker:
        return None
    m = _ASSET_TICKER_RE.match(ticker)
    return m.group(1) if m else None


_BUCKET_SUFFIX_RE = re.compile(r"-B([0-9]+(?:\.[0-9]+)?)$")
_THRESHOLD_SUFFIX_RE = re.compile(r"-T([0-9]+(?:\.[0-9]+)?)$")


def parse_kalshi_strike_suffix(ticker: str) -> tuple[str, float] | None:
    """Parse the trailing -T<value> or -B<value> from a Kalshi ticker.

    Kalshi crypto markets carry `strike_type='custom'` but leave
    `floor_strike` / `cap_strike` as None — the strike spec lives only
    in the ticker suffix.

    Returns ('B', center) for bucket markets like `-B0.157` or
    ('T', threshold) for single-side like `-T0.1499999`. None if no
    match.
    """
    if not ticker:
        return None
    m = _BUCKET_SUFFIX_RE.search(ticker)
    if m:
        return ("B", float(m.group(1)))
    m = _THRESHOLD_SUFFIX_RE.search(ticker)
    if m:
        return ("T", float(m.group(1)))
    return None
