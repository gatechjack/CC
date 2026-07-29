"""Lightweight market-data helpers used outside the broker layer.

These exist so non-broker code (auto-execute gate, scheduler, LLM context) can
peek at market state without holding a live broker connection.

All fetches are best-effort and cached in-process so we don't hammer external
sources (yfinance) during high-frequency checks like the auto-execute gate.

Caches:
  VIX           — yfinance ^VIX, 5-min TTL
  LEAP value    — populated by PMCCAgent during scan, 10-min TTL.
                  Read by the rolling_for_debit_above_5_pct_of_long auto-exec gate.
  Earnings      — EODHD reportDate (primary) / yfinance fallback, 24-hour TTL
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, date
from threading import Lock

log = logging.getLogger(__name__)

# ── VIX cache ──
# (value, fetched_at_unix_ts)
_VIX_CACHE: tuple[float | None, float] = (None, 0.0)
_VIX_CACHE_TTL_SEC = 300        # 5 minutes
_VIX_LOCK = Lock()

# ── LEAP value cache ──
# Populated by PMCCAgent.detect_existing_legs() during each scan.
# Read by the auto-execute "5% of long" gate so it can compare a roll debit
# against the LEAP without a live broker call.
# Value is stored per-contract (mark_price_per_share * 100) so callers can
# directly compare a per-contract roll debit (limit_price * 100).
_LEAP_VALUE_CACHE: dict[str, tuple[float, float]] = {}     # symbol → (value_per_contract, ts)
_LEAP_CACHE_TTL_SEC = 600                                  # 10 minutes
_LEAP_CACHE_LOCK = Lock()

# ── Earnings cache ──
# yfinance Ticker.earnings_dates is a relatively heavy call. 24-hour TTL is
# plenty since earnings dates change rarely.
# Stored as (next_earnings_dt_or_None, fetched_at_unix_ts).
_EARNINGS_CACHE: dict[str, tuple[datetime | None, float]] = {}
_EARNINGS_CACHE_TTL_SEC = 86400                            # 24 hours
_EARNINGS_LOCK = Lock()


def get_vix(force_refresh: bool = False) -> float | None:
    """Return spot ^VIX via yfinance, cached for 5 minutes.

    Returns None if yfinance is unavailable or the fetch fails. Callers must
    handle None explicitly — DO NOT use a sentinel like 0 that could be
    interpreted as "calm market".

    Synchronous (cheap from cache, ~1s on miss). Safe to call from anywhere.
    """
    global _VIX_CACHE

    with _VIX_LOCK:
        val, ts = _VIX_CACHE
        if not force_refresh and val is not None and (time.time() - ts) < _VIX_CACHE_TTL_SEC:
            return val

    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d")
        if hist is None or hist.empty:
            log.warning("get_vix: yfinance returned empty history for ^VIX")
            return None
        new_val = float(hist["Close"].iloc[-1])
        with _VIX_LOCK:
            _VIX_CACHE = (new_val, time.time())
        log.debug("get_vix: refreshed VIX = %.2f", new_val)
        return new_val
    except ImportError:
        log.warning("get_vix: yfinance not installed")
        return None
    except Exception as e:
        log.warning("get_vix: fetch failed: %s", e)
        return None


def reset_vix_cache() -> None:
    """Clear the VIX cache. Useful for tests."""
    global _VIX_CACHE
    with _VIX_LOCK:
        _VIX_CACHE = (None, 0.0)


# ──────────────────────────────────────────────────────────────────────────
# LEAP value cache
# ──────────────────────────────────────────────────────────────────────────

def cache_leap_value(symbol: str, value_per_contract: float) -> None:
    """Store a LEAP's current mark value (dollars per contract = mark_per_share × 100).

    Called by PMCCAgent.detect_existing_legs during each scan so the
    auto-execute gate can compute roll-debit / LEAP-value ratios without a
    live broker call.
    """
    if not symbol or value_per_contract <= 0:
        return
    with _LEAP_CACHE_LOCK:
        _LEAP_VALUE_CACHE[symbol.upper()] = (float(value_per_contract), time.time())


def get_cached_leap_value(symbol: str) -> float | None:
    """Return cached LEAP value (per contract) for `symbol`, or None if stale/missing.

    Callers must treat None as "fail-safe escalate" — this function does not
    fall back to a live fetch by design (auto-execute path must be deterministic).
    """
    if not symbol:
        return None
    with _LEAP_CACHE_LOCK:
        entry = _LEAP_VALUE_CACHE.get(symbol.upper())
    if not entry:
        return None
    val, ts = entry
    if time.time() - ts > _LEAP_CACHE_TTL_SEC:
        return None
    return val


def reset_leap_cache() -> None:
    """Clear the LEAP value cache. Useful for tests."""
    with _LEAP_CACHE_LOCK:
        _LEAP_VALUE_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────────
# Earnings calendar
# ──────────────────────────────────────────────────────────────────────────

def get_next_earnings(symbol: str, force_refresh: bool = False) -> datetime | None:
    """Return the next future earnings datetime (UTC) for `symbol`, or None.

    None means: yfinance had no data, or the fetch failed. Callers should
    treat None as "no earnings data — don't block on this filter" rather than
    fail-safe escalating, because thinly-traded names often lack earnings dates
    in yfinance.
    """
    if not symbol:
        return None
    sym = symbol.upper()

    with _EARNINGS_LOCK:
        entry = _EARNINGS_CACHE.get(sym)
        if not force_refresh and entry is not None:
            cached_val, ts = entry
            if time.time() - ts < _EARNINGS_CACHE_TTL_SEC:
                return cached_val

    # PRIMARY: EODHD reportDate (authoritative next-announcement date).
    # FALLBACK: yfinance .earnings_dates (unreliable — the reason EODHD is now
    # primary), used ONLY when EODHD returns None (key absent / no data). No
    # auto-failover within a succeeded EODHD call.
    next_dt: datetime | None = _eodhd_next_earnings(sym)
    if next_dt is None:
        next_dt = _yfinance_next_earnings(sym)

    with _EARNINGS_LOCK:
        _EARNINGS_CACHE[sym] = (next_dt, time.time())
    return next_dt


def _eodhd_next_earnings(sym: str) -> datetime | None:
    """EODHD primary: next future reportDate as a UTC datetime, or None.

    Convention: the EODHD reportDate is a calendar date (no BMO/AMC time). We
    return it at the END of the announcement day UTC (23:59:59) so a downstream
    'days to earnings' AVOIDANCE gate stays active through the ENTIRE report day
    — covering both before-open and after-close prints (the conservative choice
    for not holding into a print). FLAGGED for operator confirmation: change the
    time-of-day here if a different earnings-window convention is preferred.

    Reuses EarningsProvider's shared 24h fundamentals cache. Never raises.
    """
    try:
        from trading_corp.data.earnings_provider import EarningsProvider
        d = EarningsProvider().get_next_earnings_date(sym)
    except Exception as e:  # never let the EODHD path break the gate
        log.debug("get_next_earnings: EODHD path failed for %s: %s", sym, e)
        return None
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)


def _yfinance_next_earnings(sym: str) -> datetime | None:
    """Labeled yfinance fallback — the prior implementation, used only when the
    EODHD path returns None. yfinance .earnings_dates is unreliable (the reason
    for the EODHD primary); kept under the ABC as a last resort, no auto-failover.
    """
    next_dt: datetime | None = None
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        log.warning("get_next_earnings: yfinance not installed")
        return None
    # yfinance internals can raise on lazy-loaded sub-deps AND ERROR-log "No
    # earnings dates found" on their own logger when a symbol has no data —
    # both degrade silently to None per this function's "no data = don't block".
    try:
        ticker = yf.Ticker(sym)
        df = ticker.earnings_dates    # may be None or DataFrame
        if df is not None and not df.empty:
            now = datetime.now(timezone.utc)
            for idx in df.index:
                try:
                    dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    if dt > now:
                        if next_dt is None or dt < next_dt:
                            next_dt = dt
                except Exception:
                    continue
    except Exception as e:
        log.debug("get_next_earnings: yfinance %s for %s: %s", type(e).__name__, sym, e)
    return next_dt


def reset_earnings_cache() -> None:
    """Clear the earnings cache. Useful for tests."""
    with _EARNINGS_LOCK:
        _EARNINGS_CACHE.clear()


# ---------------------------------------------------------------------------
# Brokerage-first earnings resolution (2026-07-28)
#
# The EODHD/yfinance feed served a STALE date for RIOT (2025's 07-31 print)
# that WRONGLY BLOCKED a liquid roll on 2026-07-28 while the broker-verified
# date was 08-05 (outside the 7d buffer). The same staleness can also FALSELY
# CLEAR a real upcoming print and let a short roll INTO earnings. The brokerage
# the division trades on (Robinhood) publishes a VERIFIED next-earnings date —
# the authoritative source per the brokerage-first data policy.
#
# resolve_earnings() prefers the broker date, falls back to the feed (flagged
# UNVERIFIED), and — when NEITHER source has a confident future date — reports
# that so the caller neither silently blocks a liquid name (the RIOT failure)
# NOR silently clears into a possible print.
#
# get_broker_earnings() is cached per-symbol with a 24h TTL so per-scan
# verification never hammers the API, and is INERT until the Robinhood broker
# has logged in (returns (None, False) in tests / pre-login, so resolution
# falls back to the feed with no network call).
# ---------------------------------------------------------------------------

_BROKER_EARN_CACHE: dict[str, tuple[datetime | None, bool, float]] = {}  # sym -> (dt, verified, ts)
_BROKER_EARN_TTL_SEC = 86400                                             # 24h — rate-limit
_BROKER_EARN_LOCK = Lock()


@dataclass
class EarningsResolution:
    """Brokerage-first next-earnings resolution.

    date         : chosen next FUTURE earnings datetime (UTC, end-of-day) or None
                   when no source has one.
    source       : "broker" | "feed" | "none".
    verified     : True only when the broker CONFIRMED the date.
    broker_date  : the broker's date (or None).
    feed_date    : the EODHD/yfinance date (or None).
    disagreement : broker and feed both present and differ by more than a day
                   (stale-feed drift — logged; broker wins).
    """
    date: datetime | None
    source: str
    verified: bool
    broker_date: datetime | None
    feed_date: datetime | None
    disagreement: bool


def reset_broker_earnings_cache() -> None:
    """Clear the broker-earnings cache. Useful for tests."""
    with _BROKER_EARN_LOCK:
        _BROKER_EARN_CACHE.clear()


def _rh_next_earnings(sym: str) -> tuple[datetime | None, bool]:
    """Robinhood get_earnings -> (earliest FUTURE not-yet-reported report as an
    end-of-day UTC datetime, verified). Returns (None, False) on anything —
    never raises. INERT unless the Robinhood broker has an authenticated session
    (`_LOGIN_DONE`), so tests and pre-login callers make no network request."""
    try:
        import trading_corp.brokers.robinhood as _rh
        if not getattr(_rh, "_LOGIN_DONE", False):
            return None, False   # no authenticated session -> skip broker (feed fallback)
    except Exception:
        return None, False
    try:
        import robin_stocks.robinhood as rs  # global session established at broker login
        rows = rs.get_earnings(sym) or []
    except Exception as e:
        log.debug("broker earnings: get_earnings failed for %s: %s", sym, e)
        return None, False
    today = datetime.now(timezone.utc).date()
    best: date | None = None
    best_verified = False
    for row in rows:
        try:
            rep = (row or {}).get("report") or {}
            eps = (row or {}).get("eps") or {}
            ds = rep.get("date")
            if not ds:
                continue
            # upcoming = not yet reported (actual EPS absent)
            if eps.get("actual") not in (None, "", "null"):
                continue
            d = date.fromisoformat(str(ds)[:10])
            if d < today:
                continue
            if best is None or d < best:
                best = d
                best_verified = bool(rep.get("verified"))
        except Exception:
            continue
    if best is None:
        return None, False
    return datetime(best.year, best.month, best.day, 23, 59, 59, tzinfo=timezone.utc), best_verified


def get_broker_earnings(symbol: str, force_refresh: bool = False) -> tuple[datetime | None, bool]:
    """(next_future_earnings_dt_UTC, verified) from Robinhood, or (None, False).

    Brokerage-first authoritative source. Cached per-symbol (24h TTL) so per-scan
    verification never hammers the API. Never raises."""
    sym = (symbol or "").upper()
    if not sym:
        return None, False
    with _BROKER_EARN_LOCK:
        entry = _BROKER_EARN_CACHE.get(sym)
        if not force_refresh and entry is not None:
            dt, ver, ts = entry
            if time.time() - ts < _BROKER_EARN_TTL_SEC:
                return dt, ver
    dt, ver = _rh_next_earnings(sym)
    with _BROKER_EARN_LOCK:
        _BROKER_EARN_CACHE[sym] = (dt, ver, time.time())
    return dt, ver


def resolve_earnings(symbol: str) -> EarningsResolution:
    """Brokerage-first next-earnings resolution (see EarningsResolution).

    Broker date wins (authoritative/verified); the EODHD/yfinance feed is the
    fallback (unverified); broker/feed drift over a day is logged. When neither
    source has a future date the result carries date=None / source="none" so the
    caller can fail-open WITHOUT silently blocking a liquid name and WITHOUT
    silently clearing into a possible print."""
    bdt, bver = get_broker_earnings(symbol)
    fdt = get_next_earnings(symbol)
    disagreement = bool(bdt is not None and fdt is not None and abs((bdt - fdt).days) > 1)
    if disagreement:
        log.warning(
            "earnings source disagreement %s: broker=%s (verified=%s) feed=%s "
            "-- using broker (stale-feed drift)",
            symbol, bdt.date().isoformat(), bver, fdt.date().isoformat(),
        )
    if bdt is not None:
        return EarningsResolution(bdt, "broker", bver, bdt, fdt, disagreement)
    if fdt is not None:
        return EarningsResolution(fdt, "feed", False, None, fdt, False)
    return EarningsResolution(None, "none", False, None, None, False)


# ──────────────────────────────────────────────────────────────────────────
# Benchmark prices (for per-division comparison: PMCC vs SPY, IRA vs VTI…)
#
# Fetches today's close + the close N days ago, returns the % change.
# 5-minute TTL keeps the dashboard responsive without hammering yfinance.
# Phase 1.5c will add a TradingView WebSocket primary source with this as
# the fallback.
# ──────────────────────────────────────────────────────────────────────────

_BENCHMARK_CACHE: dict[tuple[str, str], tuple[float | None, float]] = {}
_BENCHMARK_TTL_SEC = 300
_BENCHMARK_LOCK = Lock()


def get_benchmark_change(symbol: str, period: str = "ytd") -> float | None:
    """Return the % change of `symbol` over `period`, or None on failure.

    period: "today" | "1w" | "1mo" | "3mo" | "ytd" | "1y"
    Returns a fraction (0.0823 = +8.23%).
    """
    if not symbol:
        return None
    key = (symbol.upper(), period)

    with _BENCHMARK_LOCK:
        entry = _BENCHMARK_CACHE.get(key)
        if entry is not None:
            val, ts = entry
            if time.time() - ts < _BENCHMARK_TTL_SEC:
                return val

    pct: float | None = None
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(symbol)
        # Map period → yfinance history call
        yf_period = {
            "today": "5d",   # need yesterday's close to compute today's %
            "1w":    "1mo",
            "1mo":   "3mo",
            "3mo":   "6mo",
            "ytd":   "ytd",
            "1y":    "2y",
        }.get(period, "ytd")
        hist = ticker.history(period=yf_period)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                if period == "today":
                    pct = float(closes.iloc[-1] / closes.iloc[-2] - 1.0)
                elif period == "ytd":
                    # First close of the calendar year
                    from datetime import datetime as _dt
                    year_start = _dt.now().year
                    yr = closes[closes.index.year == year_start]
                    if len(yr) >= 1:
                        pct = float(closes.iloc[-1] / yr.iloc[0] - 1.0)
                    else:
                        pct = float(closes.iloc[-1] / closes.iloc[0] - 1.0)
                else:
                    pct = float(closes.iloc[-1] / closes.iloc[0] - 1.0)
    except ImportError:
        log.warning("get_benchmark_change: yfinance not installed")
    except Exception as e:
        log.debug("get_benchmark_change(%s, %s) failed: %s", symbol, period, e)

    with _BENCHMARK_LOCK:
        _BENCHMARK_CACHE[key] = (pct, time.time())
    return pct


def reset_benchmark_cache() -> None:
    """Clear the benchmark cache. Useful for tests."""
    with _BENCHMARK_LOCK:
        _BENCHMARK_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────────
# Market-overview quotes (price + today's % change in one call).
# Used by the dashboard's top market ribbon: SPY / QQQ / BTC-USD / VIX.
# ──────────────────────────────────────────────────────────────────────────

_QUOTE_CACHE: dict[str, tuple[dict, float]] = {}
_QUOTE_TTL_SEC = 60        # market quotes refresh once per minute
_QUOTE_LOCK = Lock()


def get_market_quote(symbol: str, force_refresh: bool = False) -> dict:
    """Return {'symbol', 'price', 'change_pct'} for `symbol`.

    `change_pct` is today's close vs yesterday's close as a fraction
    (0.0042 = +0.42%). Returns empty {} on failure. 60s TTL cache.
    """
    if not symbol:
        return {}
    key = symbol.upper()

    with _QUOTE_LOCK:
        entry = _QUOTE_CACHE.get(key)
        if not force_refresh and entry is not None:
            data, ts = entry
            if time.time() - ts < _QUOTE_TTL_SEC:
                return dict(data)

    out: dict = {}
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                change_pct = (last / prev - 1.0) if prev else 0.0
                out = {
                    "symbol": symbol,
                    "price": last,
                    "change_pct": change_pct,
                }
    except ImportError:
        log.warning("get_market_quote: yfinance not installed")
    except Exception as e:
        log.debug("get_market_quote(%s) failed: %s", symbol, e)

    with _QUOTE_LOCK:
        _QUOTE_CACHE[key] = (out, time.time())
    return dict(out)


def reset_quote_cache() -> None:
    with _QUOTE_LOCK:
        _QUOTE_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────────
# Intraday bars — used by the dashboard's ribbon sparklines.
# 24-hour rolling window for crypto (BTC), most recent trading session for
# stocks (SPY/QQQ/VIX). Returns just close prices since sparklines don't
# need OHLC.
# ──────────────────────────────────────────────────────────────────────────

_INTRADAY_CACHE: dict[str, tuple[list[float], float]] = {}
_INTRADAY_TTL_SEC = 60
_INTRADAY_LOCK = Lock()


def get_market_intraday(symbol: str, force_refresh: bool = False) -> list[float]:
    """Return a list of close prices for a 24h-equivalent intraday window.

    For BTC-USD (and other 24/7 instruments): the trailing 24 hours.
    For SPY/QQQ/VIX (regular trading hours): the most recent session.

    Returns [] on failure. 60-second TTL cache to avoid hammering yfinance.
    """
    if not symbol:
        return []
    key = symbol.upper()

    with _INTRADAY_LOCK:
        entry = _INTRADAY_CACHE.get(key)
        if not force_refresh and entry is not None:
            data, ts = entry
            if time.time() - ts < _INTRADAY_TTL_SEC:
                return list(data)

    bars: list[float] = []
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(symbol)
        # 1-day at 5m granularity gives ~78 bars for stocks (6.5h session)
        # and ~288 bars for crypto (24h * 12 bars/h). Both are reasonable
        # for a tiny sparkline.
        hist = ticker.history(period="1d", interval="5m")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            bars = [float(x) for x in hist["Close"].dropna().tolist()]
    except ImportError:
        log.warning("get_market_intraday: yfinance not installed")
    except Exception as e:
        log.debug("get_market_intraday(%s) failed: %s", symbol, e)

    with _INTRADAY_LOCK:
        _INTRADAY_CACHE[key] = (bars, time.time())
    return list(bars)


def reset_intraday_cache() -> None:
    with _INTRADAY_LOCK:
        _INTRADAY_CACHE.clear()
