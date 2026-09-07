"""MACE dxFeed candle feed — fail-safe cache writer for OHLCV bars.

Design notes
------------
Each call to ``collect_candles`` opens a short-lived ``DXLinkStreamer`` for
one (symbol, interval) subscription.  The outer ``run_feed`` loop iterates
all symbols x intervals sequentially and opens one streamer per combination
per cycle.  This is deliberately simple and safe: tastytrade 12.4.1's
concurrent-subscription safety on a single streamer is unproven, and the
cycle period (default 60 s) gives ample time for sequential refreshes.

A future optimisation is to hold ONE persistent multi-subscription streamer
and fan out subscriptions together; that would halve round-trip overhead but
requires validating thread/async-safety against the installed SDK version.
For now, one streamer per (symbol, interval) per cycle is the canonical
approach.

tastytrade imports are done INSIDE functions (lazy) so this module can be
imported in test environments where the SDK is not installed.  All network
and SDK failures are caught; the loop never dies.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import timezone

log = logging.getLogger(__name__)

SYMBOLS = ("SPY", "IWM", "GDX", "IBIT", "XLE", "FXI")
INTERVALS = ("5m", "1d")

# Default lookback per interval (days).
_DEFAULT_LOOKBACK: dict[str, int] = {
    "5m": 2,
    "1d": 40,
}

# Maximum bars to retain per (symbol, interval) in the DB cache.
_PRUNE_KEEP = 500

# Per-event read timeout inside collect_candles (seconds).
_EVENT_TIMEOUT_SEC = 5.0

# ── Phase-2b: write-path sanity filter (2026-09-04) ──────────────────────────
# dxFeed's snapshot-end SENTINEL Candle carries bar_time = 2^31 (2147483648 s =
# year 2038) and/or all-zero OHLC. Unfiltered, the zero-OHLC bars drag the chart
# y-axis to 0.00 and the 2038 bar wrecks the x-axis (and the max-bar_time sentinel
# is never pruned). is_valid_bar() rejects those at the write path; the LIVE
# forming bar (positive OHLC, bar_time ~ now) passes.
_SENTINEL_BAR_TIME = 2147483648          # 2^31 — dxFeed snapshot-end marker (epoch sec)
_MAX_FUTURE_SKEW_SEC = 2 * 86400         # allow the forming bar + clock skew (~2 days)
_MAX_PAST_SEC = 400 * 86400              # reject absurdly-old bar_time (~400 days)
# One-time idempotent cleanup of ALREADY-stored garbage (sentinel + zero-OHLC).
# NOT auto-run — invoked as an explicit deploy step (cleanup_bad_bars). sane_max
# bar_time = 2147483648; real bars are < 1.8e9 (2026).
_CLEANUP_SQL = (
    "DELETE FROM mace_candle WHERE close IS NULL OR close<=0 OR open<=0 "
    "OR high<=0 OR low<=0 OR bar_time >= %d" % _SENTINEL_BAR_TIME
)


def _pos(v):
    """Coerce to a strictly-positive finite float, else None (rejects None,
    NaN, non-numeric, and <= 0)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0.0:   # NaN or non-positive
        return None
    return f


def is_valid_bar(bar_time_sec, o, h, l, c, now_sec) -> bool:
    """True only for a real, plottable bar. Rejects the dxFeed snapshot-end
    sentinel (bar_time >= 2^31), far-future / absurdly-old bar_time, and any
    non-positive/None/NaN OHLC. The live forming bar passes (positive OHLC,
    bar_time ~ now)."""
    if any(_pos(v) is None for v in (o, h, l, c)):
        return False
    if bar_time_sec is None:
        return False
    if bar_time_sec >= _SENTINEL_BAR_TIME:
        return False
    if bar_time_sec > now_sec + _MAX_FUTURE_SKEW_SEC:
        return False
    if bar_time_sec < now_sec - _MAX_PAST_SEC:
        return False
    return True


def cleanup_bad_bars(conn) -> int:
    """ONE-TIME idempotent removal of already-stored garbage bars (sentinel +
    zero-OHLC). Returns rows deleted. NOT auto-run — an explicit deploy step."""
    cur = conn.execute(_CLEANUP_SQL)
    conn.commit()
    return cur.rowcount


# ── helpers ──────────────────────────────────────────────────────────────────


def parse_symbol(event_symbol: str) -> str:
    """Strip the dxFeed subscription suffix and return the base ticker.

    E.g. ``'SPY{=5m,tho=true}'`` -> ``'SPY'``.
    Works for any ``'{...}'`` suffix; returns the input unchanged if no brace.
    """
    brace = event_symbol.find("{")
    if brace == -1:
        return event_symbol
    return event_symbol[:brace]


def candle_bar_seconds(candle_time_ms) -> int:
    """Convert dxFeed candle ``time`` (epoch milliseconds) to epoch seconds."""
    return int(candle_time_ms // 1000)


def dedupe_by_time(candles) -> list:
    """Deduplicate candles by bar time, keeping the LAST event per bar.

    dxFeed sends a snapshot (newest-first) followed by live updates for the
    forming bar.  The live update supersedes the snapshot entry for that bar.
    Keeping the LAST event per ``time`` value ensures the forming bar reflects
    the most-recent update, while completed bars keep their final snapshot.

    Returns a list sorted ascending by bar time.
    """
    by_time: dict[int, object] = {}
    for c in candles:
        try:
            t = candle_bar_seconds(c.time)
        except Exception:  # noqa: BLE001 — malformed event; skip
            continue
        by_time[t] = c
    return [by_time[t] for t in sorted(by_time)]


# ── DB write ─────────────────────────────────────────────────────────────────


def upsert_candle(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    bar_time_sec: int,
    o,
    h,
    l,
    c,
    volume,
    vwap,
    ts: str,
) -> None:
    """INSERT OR REPLACE one bar into ``mace_candle``.

    Does NOT commit — caller is responsible for the transaction boundary.
    """
    conn.execute(
        "INSERT OR REPLACE INTO mace_candle "
        "(symbol, interval, bar_time, open, high, low, close, volume, vwap, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, interval, bar_time_sec,
         _f(o), _f(h), _f(l), _f(c), _f(volume), _f(vwap), ts),
    )


def _f(v) -> float | None:
    """Coerce to float, returning None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── streaming ────────────────────────────────────────────────────────────────


async def collect_candles(
    session,
    symbol: str,
    interval: str,
    start_time,
    collect_secs: float = 12.0,
) -> list:
    """Open a short-lived DXLinkStreamer and collect Candle events.

    Parameters
    ----------
    session:
        Live tastytrade ``Session`` object.
    symbol:
        E.g. ``'SPY'``.
    interval:
        E.g. ``'5m'`` or ``'1d'``.
    start_time:
        Passed verbatim to ``subscribe_candle`` as ``start_time`` (a
        ``datetime`` or anything the SDK accepts).
    collect_secs:
        Wall-clock budget for collecting events.  The streamer sends a
        snapshot burst then live updates; 12 s captures the snapshot plus
        any forming-bar update without blocking the caller too long.

    Returns
    -------
    list[Candle]
        Raw dxFeed Candle events (may include duplicates — call
        ``dedupe_by_time`` before writing).
    """
    from tastytrade import DXLinkStreamer  # type: ignore  # lazy — version-sensitive
    from tastytrade.dxfeed import Candle  # type: ignore

    events: list = []
    deadline = asyncio.get_event_loop().time() + collect_secs
    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe_candle(
                [symbol],
                interval=interval,
                start_time=start_time,
                extended_trading_hours=False,
                refresh_interval=0.1,
            )
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    ev = await asyncio.wait_for(
                        streamer.get_event(Candle),
                        timeout=min(remaining, _EVENT_TIMEOUT_SEC),
                    )
                    events.append(ev)
                except asyncio.TimeoutError:
                    # No more events arriving within the window — done.
                    break
    except Exception as e:  # noqa: BLE001 — streamer init or network failure
        log.warning(
            "candle_feed.collect_candles: streamer failed for %s/%s: %s",
            symbol, interval, e,
        )
    return events


# ── per-symbol refresh ───────────────────────────────────────────────────────


async def refresh_symbol(
    session,
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    lookback_days: int,
) -> int:
    """Collect, dedupe, and upsert bars for one (symbol, interval).

    Returns the count of distinct bars written.  On any failure, logs the
    error and returns 0 without raising.
    """
    try:
        from datetime import datetime, timedelta  # local import — standard lib

        start_time = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        raw = await collect_candles(session, symbol, interval, start_time=start_time)
        if not raw:
            log.debug("candle_feed.refresh_symbol: 0 events for %s/%s", symbol, interval)
            return 0
        bars = dedupe_by_time(raw)
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        now_sec = int(datetime.now(tz=timezone.utc).timestamp())
        n = 0
        for bar in bars:
            bt = candle_bar_seconds(bar.time)
            # Phase-2b: drop the dxFeed sentinel + zero/garbage bars at write.
            if not is_valid_bar(bt, bar.open, bar.high, bar.low, bar.close, now_sec):
                continue
            try:
                upsert_candle(
                    conn,
                    symbol=symbol,
                    interval=interval,
                    bar_time_sec=bt,
                    o=bar.open,
                    h=bar.high,
                    l=bar.low,
                    c=bar.close,
                    volume=bar.volume,
                    vwap=bar.vwap,
                    ts=ts,
                )
                n += 1
            except Exception as e:  # noqa: BLE001 — single-bar failure; skip
                log.warning(
                    "candle_feed.refresh_symbol: upsert failed for %s/%s bar %s: %s",
                    symbol, interval, getattr(bar, "time", "?"), e,
                )
        conn.commit()
        log.debug(
            "candle_feed.refresh_symbol: wrote %d bars for %s/%s", n, symbol, interval
        )
        return n
    except Exception as e:  # noqa: BLE001 — outer catch; never raise
        log.warning(
            "candle_feed.refresh_symbol: failed for %s/%s: %s", symbol, interval, e
        )
        return 0


async def collect_candles_multi(
    session,
    symbols,
    interval: str,
    start_time,
    collect_secs: float = 14.0,
) -> list:
    """Like ``collect_candles`` but subscribes ALL ``symbols`` for one interval
    on a SINGLE DXLinkStreamer (dxFeed multiplexes many symbols on one
    connection — validated live: 6 symbols x 5m -> 157 distinct bars each,
    x 1d -> 31 each, no error). This is the primary path so a full cycle is
    2 streamers (one per interval), not 12 — keeping current-price refresh
    near ~1 min. Runs the full ``collect_secs`` window (does NOT early-break on
    the first idle gap, since a slow symbol's snapshot may still be arriving)."""
    from tastytrade import DXLinkStreamer  # type: ignore  # lazy — version-sensitive
    from tastytrade.dxfeed import Candle  # type: ignore

    events: list = []
    deadline = asyncio.get_event_loop().time() + collect_secs
    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe_candle(
                list(symbols),
                interval=interval,
                start_time=start_time,
                extended_trading_hours=False,
                refresh_interval=0.1,
            )
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    ev = await asyncio.wait_for(
                        streamer.get_event(Candle),
                        timeout=min(remaining, _EVENT_TIMEOUT_SEC),
                    )
                    events.append(ev)
                except asyncio.TimeoutError:
                    continue  # multi: keep collecting until the deadline
    except Exception as e:  # noqa: BLE001 — streamer init or network failure
        log.warning("candle_feed.collect_candles_multi: streamer failed for %s: %s",
                    interval, e)
    return events


async def refresh_interval(
    session,
    conn: sqlite3.Connection,
    symbols,
    interval: str,
    lookback_days: int,
    collect_secs: float = 14.0,
) -> int:
    """Multiplexed refresh: collect ALL symbols for one interval on one
    streamer, group by parsed ticker, dedupe by bar time, upsert, prune.
    Returns total distinct bars written. Fail-safe (logs + returns 0)."""
    try:
        from datetime import datetime, timedelta

        start_time = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        raw = await collect_candles_multi(session, symbols, interval, start_time, collect_secs)
        if not raw:
            log.debug("candle_feed.refresh_interval: 0 events for %s", interval)
            return 0
        by_sym: dict[str, list] = {}
        for ev in raw:
            sym = parse_symbol(getattr(ev, "event_symbol", "") or "")
            if sym:
                by_sym.setdefault(sym, []).append(ev)
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        now_sec = int(datetime.now(tz=timezone.utc).timestamp())
        total = 0
        skipped = 0
        for sym, evs in by_sym.items():
            for bar in dedupe_by_time(evs):
                bt = candle_bar_seconds(bar.time)
                # Phase-2b: drop the dxFeed sentinel + zero/garbage bars at write.
                if not is_valid_bar(bt, bar.open, bar.high, bar.low, bar.close, now_sec):
                    skipped += 1
                    continue
                try:
                    upsert_candle(conn, sym, interval, bt,
                                  bar.open, bar.high, bar.low, bar.close,
                                  bar.volume, bar.vwap, ts)
                    total += 1
                except Exception as e:  # noqa: BLE001 — single-bar failure; skip
                    log.debug("candle_feed.refresh_interval: upsert %s/%s failed: %s",
                              sym, interval, e)
            _prune(conn, sym, interval)
        conn.commit()
        log.debug("candle_feed.refresh_interval: wrote %d bars (skipped %d sentinel/garbage) "
                  "across %d syms for %s", total, skipped, len(by_sym), interval)
        return total
    except Exception as e:  # noqa: BLE001 — never raise
        log.warning("candle_feed.refresh_interval: failed for %s: %s", interval, e)
        return 0


def _prune(conn: sqlite3.Connection, symbol: str, interval: str, keep: int = _PRUNE_KEEP) -> None:
    """Delete oldest bars beyond `keep` for (symbol, interval)."""
    try:
        conn.execute(
            "DELETE FROM mace_candle WHERE symbol=? AND interval=? AND bar_time NOT IN ("
            "  SELECT bar_time FROM mace_candle WHERE symbol=? AND interval=?"
            "  ORDER BY bar_time DESC LIMIT ?"
            ")",
            (symbol, interval, symbol, interval, keep),
        )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("candle_feed._prune: failed for %s/%s: %s", symbol, interval, e)


# ── persistent loop ──────────────────────────────────────────────────────────


async def run_feed(
    store_conn_factory,
    get_session,
    interval_lookback: dict[str, int] | None = None,
    cycle_sec: float = 60.0,
    symbols=SYMBOLS,
) -> None:
    """Persistent candle-feed loop.

    Parameters
    ----------
    store_conn_factory:
        Zero-arg callable returning a ``sqlite3.Connection`` (or compatible).
        Called once at start; the connection is reused across cycles.
    get_session:
        Zero-arg (or zero-arg async) callable returning a live tastytrade
        ``Session``.  Called once; the session is reused across cycles.
    interval_lookback:
        Dict mapping interval string to lookback days.
        Default: ``{'5m': 2, '1d': 40}``.
    cycle_sec:
        Seconds between full refresh cycles (all symbols x intervals).
        Default: 60.
    symbols:
        Tuple/list of ticker strings.  Default: the 6 MACE underlyings.

    The loop is fully fail-safe: any exception in a cycle is caught, logged,
    and followed by a backoff sleep before the next attempt.  The loop never
    exits under normal operation.
    """
    lb = dict(_DEFAULT_LOOKBACK)
    if interval_lookback:
        lb.update(interval_lookback)

    log.info("candle_feed.run_feed: starting (cycle_sec=%.0f, symbols=%s)", cycle_sec, symbols)

    # One-time setup — obtain session and DB connection.
    try:
        if asyncio.iscoroutinefunction(get_session):
            session = await get_session()
        else:
            session = get_session()
    except Exception as e:  # noqa: BLE001
        log.error("candle_feed.run_feed: get_session failed: %s — feed will not start", e)
        return

    try:
        conn = store_conn_factory()
    except Exception as e:  # noqa: BLE001
        log.error("candle_feed.run_feed: store_conn_factory failed: %s — feed will not start", e)
        return

    backoff = 5.0
    while True:
        try:
            # Multiplexed: one streamer per interval (all symbols), not one per
            # (symbol, interval) — keeps a full cycle to ~2 short pulls so the
            # current-price bar stays ~1 min fresh.
            for ivl in INTERVALS:
                written = await refresh_interval(session, conn, symbols, ivl, lb.get(ivl, 5))
                log.debug("candle_feed: %s: %d bars refreshed (all syms)", ivl, written)
            backoff = 5.0  # reset on successful cycle
            await asyncio.sleep(cycle_sec)
        except asyncio.CancelledError:
            log.info("candle_feed.run_feed: cancelled")
            return
        except Exception as e:  # noqa: BLE001 — cycle-level failure; sleep + retry
            log.warning(
                "candle_feed.run_feed: cycle error (backoff %.0fs): %s", backoff, e
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, 300.0)  # cap at 5 min
