"""Shared Robinhood daily-bars fetcher for PEAD (live scan + backtest).

SPLIT-ADJUSTED bars — the classic /quotes/historicals/ default (verified: NVDA
across the 2024 10:1 split returns a smooth ~$121, not the raw ~$1,150). ONE
implementation so the live scan and the backtest see IDENTICAL bars.

Reuses the EXISTING process-wide robin_stocks session — it does NOT log in. The
live engine logs in once via RobinhoodBroker.connect(); the offline backtest CLI
logs in once in its own main(). RAISES RHBarsError on any failure — it NEVER
falls back to a banned source. Per-symbol in-memory cache (6h TTL). Callers run
this off the asyncio event loop (the live path via asyncio.to_thread).
"""
from __future__ import annotations

import logging
import time
from datetime import date
from threading import Lock

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 21_600          # 6h — daily bars change at most once per day
_MIN_INTERVAL_S = 0.20           # pace ~5 req/s; the RH 429 threshold is undocumented
_VALID_SPANS = ("day", "week", "month", "3month", "year", "5year")

# cache key = (symbol.upper(), span, bounds) -> (list[dict], monotonic-ish wall ts)
_CACHE: dict[tuple, tuple[list[dict], float]] = {}
_CACHE_LOCK = Lock()
_LAST_CALL = [0.0]
_PACE_LOCK = Lock()


class RHBarsError(RuntimeError):
    """Robinhood bars could not be fetched — callers must NOT fall back to a banned source."""


def _pace() -> None:
    """Serialize network fetches to ~1 per _MIN_INTERVAL_S (avoids RH 429s)."""
    with _PACE_LOCK:
        dt = time.monotonic() - _LAST_CALL[0]
        if dt < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - dt)
        _LAST_CALL[0] = time.monotonic()


def fetch_rh_daily_bars(symbol: str, *, span: str = "5year", bounds: str = "regular") -> list[dict]:
    """SPLIT-ADJUSTED daily OHLCV for `symbol`, oldest->newest, as
    list[dict{date, open, high, low, close, volume}].

    Uses the existing robin_stocks session (must already be logged in — this does
    NOT log in). Raises RHBarsError on any failure; NO fallback. Cached per
    (symbol, span, bounds) for 6h so re-runs do not refetch.
    """
    if span not in _VALID_SPANS:
        raise RHBarsError(f"invalid span {span!r} (allowed: {_VALID_SPANS})")
    sym = symbol.upper()
    key = (sym, span, bounds)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None and (time.time() - hit[1]) < _CACHE_TTL_SEC:
        return hit[0]

    _pace()
    import robin_stocks.robinhood as rs  # process-wide session; do NOT log in here
    try:
        raw = rs.stocks.get_stock_historicals(sym, interval="day", span=span, bounds=bounds)
    except Exception as e:  # noqa: BLE001
        raise RHBarsError(f"get_stock_historicals({sym}) raised: {e!r}") from e
    if not raw or raw == [None] or raw[0] is None:
        raise RHBarsError(
            f"Robinhood returned no bars for {sym} (unauthenticated session or unknown symbol) "
            f"— refusing to fall back to a banned source")

    out: list[dict] = []
    for r in raw:
        if not isinstance(r, dict) or r.get("close_price") is None:
            continue
        try:
            out.append({
                "date": date.fromisoformat(str(r.get("begins_at", ""))[:10]),
                "open": float(r["open_price"]), "high": float(r["high_price"]),
                "low": float(r["low_price"]), "close": float(r["close_price"]),
                "volume": float(r.get("volume") or 0.0),
            })
        except Exception:  # noqa: BLE001
            continue
    if not out:
        raise RHBarsError(f"Robinhood bars for {sym} were unparseable/empty — refusing to fall back")
    out.sort(key=lambda b: b["date"])
    with _CACHE_LOCK:
        _CACHE[key] = (out, time.time())
    return out
