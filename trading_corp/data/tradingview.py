"""TradingView supplemental signal source — NEVER on the execution critical path.

Uses `tradingview-ta` (screener API) for indicator snapshots and `tvdatafeed`
for historical OHLCV bars. Both are community libraries that reverse-engineer
TV's internal APIs; they break occasionally on TV updates. Every function here
degrades gracefully: callers get an empty dict / None rather than an exception,
and execution proceeds via yfinance/ccxt as the authoritative price source.

Opt-in: set env var ENABLE_TRADINGVIEW=1. When absent (or when neither library
is installed), all calls return immediately with empty results.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple TTL cache: symbol -> (timestamp, result)
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SEC = 300  # 5 minutes


def _cache_get(key: str) -> dict | None:
    entry = _CACHE.get(key)
    if entry and time.monotonic() - entry[0] < _CACHE_TTL_SEC:
        return entry[1]
    return None


def _cache_set(key: str, value: dict) -> None:
    _CACHE[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _tradingview_ta_available() -> bool:
    try:
        import tradingview_ta  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _tvdatafeed_available() -> bool:
    try:
        import tvdatafeed  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def is_available() -> bool:
    """True only when at least one TV library is importable AND env-opt-in is set."""
    if os.getenv("ENABLE_TRADINGVIEW", "0") != "1":
        return False
    return _tradingview_ta_available() or _tvdatafeed_available()


# ---------------------------------------------------------------------------
# Exchange / screener resolution
# ---------------------------------------------------------------------------

_CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "XRP", "ADA", "DOT"}

_EXCHANGE_MAP: dict[str, tuple[str, str]] = {
    # symbol -> (screener, exchange)
    "AAPL": ("america", "NASDAQ"),
    "MSFT": ("america", "NASDAQ"),
    "NVDA": ("america", "NASDAQ"),
    "TSLA": ("america", "NASDAQ"),
    "AMD":  ("america", "NASDAQ"),
    "AMZN": ("america", "NASDAQ"),
    "GOOG": ("america", "NASDAQ"),
    "META": ("america", "NASDAQ"),
    "SPY":  ("america", "AMEX"),
    "QQQ":  ("america", "NASDAQ"),
    "IWM":  ("america", "AMEX"),
}


def _resolve_screener_exchange(symbol: str) -> tuple[str, str]:
    """Return (screener, exchange) for the given symbol, best-effort."""
    # Strip Coinbase-style "BTC/USD" -> "BTCUSD" or just "BTC"
    base = symbol.split("/")[0].upper()
    if base in _CRYPTO_SYMBOLS:
        return ("crypto", "COINBASE")
    return _EXCHANGE_MAP.get(base, ("america", "NASDAQ"))


# ---------------------------------------------------------------------------
# Core indicator fetch (tradingview-ta)
# ---------------------------------------------------------------------------

def _fetch_ta_indicators(symbol: str) -> dict[str, Any]:
    """Synchronous fetch using tradingview_ta.TA_Handler. Returns {} on any failure."""
    if not _tradingview_ta_available():
        return {}

    from tradingview_ta import TA_Handler, Interval  # type: ignore

    base = symbol.split("/")[0].upper()
    screener, exchange = _resolve_screener_exchange(symbol)

    try:
        handler = TA_Handler(
            symbol=base,
            screener=screener,
            exchange=exchange,
            interval=Interval.INTERVAL_1_DAY,
        )
        analysis = handler.get_analysis()
    except Exception as e:
        log.debug("tradingview-ta fetch failed for %s: %s", symbol, e)
        return {}

    summary = getattr(analysis, "summary", {}) or {}
    indicators = getattr(analysis, "indicators", {}) or {}

    return {
        "tv_recommendation": summary.get("RECOMMENDATION"),
        "tv_buy":    summary.get("BUY"),
        "tv_sell":   summary.get("SELL"),
        "tv_neutral": summary.get("NEUTRAL"),
        # Key indicators useful for Trend/Regime agent
        "rsi":       indicators.get("RSI"),
        "macd":      indicators.get("MACD.macd"),
        "macd_signal": indicators.get("MACD.signal"),
        "ema_20":    indicators.get("EMA20"),
        "ema_50":    indicators.get("EMA50"),
        "ema_200":   indicators.get("EMA200"),
        "adx":       indicators.get("ADX"),
        "atr":       indicators.get("ATR"),
        "bb_upper":  indicators.get("BB.upper"),
        "bb_lower":  indicators.get("BB.lower"),
        "volume":    indicators.get("volume"),
        "close":     indicators.get("close"),
    }


# ---------------------------------------------------------------------------
# Historical bars fetch (tvdatafeed) — optional, heavier
# ---------------------------------------------------------------------------

def _fetch_historical_bars(symbol: str, n_bars: int = 100) -> list[dict] | None:
    """Return last `n_bars` daily OHLCV bars or None on failure."""
    if not _tvdatafeed_available():
        return None

    from tvdatafeed import TvDatafeed, Interval as TvInterval  # type: ignore

    base = symbol.split("/")[0].upper()
    _, exchange = _resolve_screener_exchange(symbol)

    try:
        # Anonymous login — sufficient for EOD data; no creds required.
        tv = TvDatafeed()
        df = tv.get_hist(base, exchange, interval=TvInterval.in_daily, n_bars=n_bars)
        if df is None or df.empty:
            return None
        # Convert to list of plain dicts (drop pandas dependency for callers)
        return df.reset_index().rename(columns=str.lower).to_dict("records")
    except Exception as e:
        log.debug("tvdatafeed historical fetch failed for %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Public async API (called by Trend/Regime Agent)
# ---------------------------------------------------------------------------

async def supplemental_indicators(symbol: str) -> dict[str, Any]:
    """Best-effort fetch of TV indicator snapshot for `symbol`.

    Returns empty dict on any failure so callers can always treat it as
    'no extra signal'. Never raises.
    """
    if not is_available():
        return {}

    cached = _cache_get(symbol)
    if cached is not None:
        return cached

    try:
        result = await asyncio.to_thread(_fetch_ta_indicators, symbol)
    except Exception as e:
        log.warning("TradingView supplemental fetch failed for %s: %s", symbol, e)
        result = {}

    if result:
        _cache_set(symbol, result)
        log.debug("TradingView indicators fetched for %s: recommendation=%s",
                  symbol, result.get("tv_recommendation"))
    return result


async def supplemental_bars(symbol: str, n_bars: int = 100) -> list[dict] | None:
    """Best-effort fetch of last `n_bars` daily OHLCV bars via tvdatafeed.

    Returns None when tvdatafeed is unavailable or the fetch fails.
    """
    if not is_available():
        return None
    try:
        return await asyncio.to_thread(_fetch_historical_bars, symbol, n_bars)
    except Exception as e:
        log.warning("TradingView bars fetch failed for %s: %s", symbol, e)
        return None
