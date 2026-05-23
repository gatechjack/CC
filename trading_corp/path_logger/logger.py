"""Core async daemon for the Kalshi BTC order-book path logger.

Runs three parallel asyncio tasks:
  1. kalshi_polling_loop  — REST polling of KXBTC15M + KXBTCD markets with a
                            window-state machine (OPEN_DENSE / CLOSE_DENSE /
                            SHARP_MOVE / HEARTBEAT). Writes to market_ladder.
  2. coinbase_ws_loop     — ccxt.pro WebSocket feeding COINBASE_SPOT dict and
                            sharp-move flag consumed by the Kalshi loop.
  3. heartbeat_loop       — 60s heartbeat rows in logger_jitter.
  4. jitter_report_loop   — 5-min per-ticker jitter statistics.
  5. ntp_recheck_loop     — 5-min NTP sync re-check.

Key constraints:
  - NEVER imports agents.data_exec, agents.risk, or trading_corp.web.*
  - NEVER places orders (no broker write methods called)
  - Uses data/path_logger.db (separate from trading_corp.db)
  - kalshi_quote_dollars() is imported from _weather_math — NOT reimplemented
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from trading_corp.agents.strategies._weather_math import kalshi_quote_dollars
from trading_corp.path_logger import store
from trading_corp.utils.time import now_utc

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SERIES_TICKERS = ("KXBTC15M", "KXBTCD")

# Window cadences in milliseconds
CADENCE_OPEN_DENSE_MS: int = 2_000
CADENCE_CLOSE_DENSE_MS: int = 5_000
CADENCE_SHARP_MOVE_MS: int = 2_000
CADENCE_HEARTBEAT_MS: int = 60_000

# Window durations in milliseconds
OPEN_DENSE_DURATION_MS: int = 15 * 60 * 1_000    # 15 min
CLOSE_DENSE_LEAD_MS: int = 30 * 60 * 1_000       # 30 min before expiry
SHARP_MOVE_DURATION_MS: int = 3 * 60 * 1_000     # 3 min

# Sharp-move threshold: 0.30% BTC move within 30s rolling window
SHARP_MOVE_THRESHOLD: float = 0.003
SHARP_MOVE_WINDOW_MS: int = 30_000

# Kalshi REST rate-limit guard (empirically ~5-10 req/s ceiling)
INTER_CALL_DELAY_SEC: float = 0.15

# Coinbase WS gap threshold before logging a jitter row
COINBASE_GAP_THRESHOLD_SEC: float = 10.0

# Ticker refresh interval
TICKER_REFRESH_INTERVAL_SEC: float = 60.0

# Jitter report interval
JITTER_REPORT_INTERVAL_SEC: float = 5 * 60.0

# NTP re-check interval
NTP_RECHECK_INTERVAL_SEC: float = 5 * 60.0

# Exponential backoff for Kalshi errors
BACKOFF_INITIAL_MS: int = 500
BACKOFF_MAX_MS: int = 60_000

# Per-ticker circular buffer depth for jitter measurement
JITTER_BUFFER_DEPTH: int = 60

# ── Module-level shared state ─────────────────────────────────────────────────

# Coinbase spot snapshot, updated by coinbase_ws_loop and read by Kalshi loop.
# Protected by _CB_LOCK when writing; the Kalshi loop reads without the lock
# for latency reasons (stale-read is acceptable; rows store the snapshot as-of).
COINBASE_SPOT: dict[str, float | None] = {
    "mid": None,
    "bid": None,
    "ask": None,
    "last_update_ms": None,
}

# Asyncio lock protecting COINBASE_SPOT writes
_CB_LOCK: asyncio.Lock | None = None  # initialised in run_logger_tasks()

# Rolling 30s window of (timestamp_ms, mid_price) for sharp-move detection.
# Protected by _CB_LOCK.
_CB_MID_HISTORY: deque[tuple[int, float]] = deque()

# Flag set by Coinbase WS loop when a sharp move is detected. Read and cleared
# atomically by Kalshi loop at start of each scan.
_SHARP_MOVE_FLAG: bool = False


# ── Window state machine ──────────────────────────────────────────────────────

class WindowState(Enum):
    OPEN_DENSE = auto()
    CLOSE_DENSE = auto()
    SHARP_MOVE = auto()
    HEARTBEAT = auto()


@dataclass
class TickerState:
    """Per-ticker state for the window state machine."""
    ticker: str
    event_ticker: str
    expires_at_ms: int               # Unix ms of market expiry
    window_state: WindowState = WindowState.OPEN_DENSE
    window_started_ms: int = 0       # when the current window phase began
    last_capture_ms: int = 0         # when we last successfully captured this ticker
    # Circular buffer of captured_ts values for jitter measurement
    capture_history: deque = field(default_factory=lambda: deque(maxlen=JITTER_BUFFER_DEPTH))
    # When SHARP_MOVE started (0 = not in sharp-move)
    sharp_move_started_ms: int = 0


def _now_ms() -> int:
    """Current Unix time in milliseconds via now_utc() for NTP-traceable source."""
    return int(now_utc().timestamp() * 1_000)


def _cadence_for_state(state: WindowState) -> int:
    """Return the inter-capture cadence in milliseconds for a given state."""
    return {
        WindowState.OPEN_DENSE:  CADENCE_OPEN_DENSE_MS,
        WindowState.CLOSE_DENSE: CADENCE_CLOSE_DENSE_MS,
        WindowState.SHARP_MOVE:  CADENCE_SHARP_MOVE_MS,
        WindowState.HEARTBEAT:   CADENCE_HEARTBEAT_MS,
    }[state]


def _transition_state(ts: TickerState, now_ms: int, sharp_move_triggered: bool) -> None:
    """Apply window-state transitions in-place. Mutates ts."""
    state = ts.window_state

    # Trigger sharp-move on any state if Coinbase BTC moved >=0.30% in 30s
    if sharp_move_triggered and state != WindowState.SHARP_MOVE:
        ts.window_state = WindowState.SHARP_MOVE
        ts.sharp_move_started_ms = now_ms
        log.debug("ticker %s → SHARP_MOVE (sharp_move_triggered)", ts.ticker)
        return

    if state == WindowState.OPEN_DENSE:
        # After 15 min of open-dense, downgrade to heartbeat
        if now_ms - ts.window_started_ms >= OPEN_DENSE_DURATION_MS:
            ts.window_state = WindowState.HEARTBEAT
            log.debug("ticker %s → HEARTBEAT (open_dense timeout)", ts.ticker)

    elif state == WindowState.HEARTBEAT:
        # Upgrade to close-dense when within 30 min of expiry
        if ts.expires_at_ms - now_ms <= CLOSE_DENSE_LEAD_MS:
            ts.window_state = WindowState.CLOSE_DENSE
            ts.window_started_ms = now_ms
            log.debug("ticker %s → CLOSE_DENSE (within 30 min of expiry)", ts.ticker)

    elif state == WindowState.SHARP_MOVE:
        # Return to HEARTBEAT after 3 min (unless re-triggered)
        if now_ms - ts.sharp_move_started_ms >= SHARP_MOVE_DURATION_MS:
            ts.window_state = WindowState.HEARTBEAT
            ts.sharp_move_started_ms = 0
            log.debug("ticker %s → HEARTBEAT (sharp_move timeout)", ts.ticker)

    # CLOSE_DENSE has no time-based exit; it runs until market expiry


# ── Kalshi polling helpers ────────────────────────────────────────────────────

async def _refresh_tickers(
    client: Any,
    known: dict[str, TickerState],
    conn: Any,
) -> None:
    """Poll Kalshi for open KXBTC15M + KXBTCD markets; add new tickers."""
    now_ms = _now_ms()

    for series in SERIES_TICKERS:
        try:
            markets = await client.get_markets(series_ticker=series, status="open")
        except Exception as exc:
            log.warning("_refresh_tickers: get_markets(%s) failed: %s", series, exc)
            continue

        for m in markets:
            ticker = getattr(m, "ticker", None)
            event_ticker = getattr(m, "event_ticker", None) or series
            if not ticker:
                continue

            # Parse expires_at from the market object (ISO string or already datetime)
            expires_at_ms = _parse_expires_at_ms(m)

            if ticker in known:
                # Update expires_at in case it changed (Kalshi can amend)
                known[ticker].expires_at_ms = expires_at_ms
                continue

            # New ticker — enter OPEN_DENSE
            ts = TickerState(
                ticker=ticker,
                event_ticker=event_ticker,
                expires_at_ms=expires_at_ms,
                window_state=WindowState.OPEN_DENSE,
                window_started_ms=now_ms,
                last_capture_ms=0,
            )
            known[ticker] = ts
            log.info("path_logger: new ticker %s (expires_at_ms=%d)", ticker, expires_at_ms)

            # Log startup/new-ticker event
            store.insert_jitter(
                conn,
                event_type="startup",
                ticker=ticker,
                intended_ts=None,
                captured_ts=now_ms,
                gap_ms=None,
                payload_json=json.dumps({"event_ticker": event_ticker, "expires_at_ms": expires_at_ms}),
            )

        await asyncio.sleep(INTER_CALL_DELAY_SEC)


def _parse_expires_at_ms(m: Any) -> int:
    """Extract expires_at from a pykalshi Market object as Unix ms.

    pykalshi may return expires_at as ISO-8601 string or datetime. We coerce
    both to Unix ms. Returns a far-future default (24h) on parse failure so
    the ticker isn't prematurely expired.
    """
    raw = getattr(m, "expires_at", None) or getattr(m, "close_time", None)
    if raw is None:
        # Defensive default: 24h from now
        return _now_ms() + 86_400_000

    if hasattr(raw, "timestamp"):
        # datetime object
        return int(raw.timestamp() * 1_000)

    # ISO string — handle both space separator and 'T' separator
    from datetime import datetime, timezone
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000)
    except (ValueError, AttributeError):
        log.warning("_parse_expires_at_ms: cannot parse %r; using +24h default", raw)
        return _now_ms() + 86_400_000


async def _fetch_and_build_row(
    client: Any,
    ts: TickerState,
    intended_ts: int,
    backoff_state: dict[str, int],
) -> dict[str, Any] | None:
    """Fetch one market from Kalshi REST and build a market_ladder row dict.

    Returns None on error (backoff recorded in backoff_state). On 429 or 5xx
    the backoff doubles up to BACKOFF_MAX_MS. The caller should call
    store.insert_jitter for error events.
    """
    ticker = ts.ticker
    try:
        m = await client.get_market(ticker)
    except Exception as exc:
        exc_str = str(exc)
        event_type = "kalshi_429" if "429" in exc_str else "kalshi_error"
        now_ms = _now_ms()
        gap_ms = now_ms - intended_ts

        # Exponential backoff
        current_backoff = backoff_state.get(ticker, BACKOFF_INITIAL_MS)
        next_backoff = min(current_backoff * 2, BACKOFF_MAX_MS)
        backoff_state[ticker] = next_backoff
        log.warning(
            "path_logger: %s for %s: %s (backoff=%dms)", event_type, ticker, exc, current_backoff
        )

        return {"_error": True, "event_type": event_type, "ticker": ticker,
                "intended_ts": intended_ts, "captured_ts": now_ms, "gap_ms": gap_ms,
                "backoff_ms": current_backoff}

    # Reset backoff on success
    backoff_state.pop(ticker, None)

    captured_ts = _now_ms()

    # kalshi_quote_dollars returns (yes_ask, no_ask, yes_bid, no_bid)
    yes_ask, no_ask, yes_bid, no_bid = kalshi_quote_dollars(m)

    last_trade = getattr(m, "last_price_dollars", None)
    if last_trade is None:
        lp = getattr(m, "last_price", None)
        last_trade = float(lp) / 100.0 if lp is not None else None
    else:
        try:
            last_trade = float(last_trade) if last_trade != "" else None
        except (TypeError, ValueError):
            last_trade = None

    # implied_prob: yes_ask when available, else 1 - no_ask (mirrors existing convention)
    implied_prob: float | None
    if yes_ask and yes_ask > 0:
        implied_prob = yes_ask
    elif no_ask and no_ask > 0:
        implied_prob = 1.0 - no_ask
    else:
        implied_prob = None

    # Read Coinbase snapshot (no lock — stale-read acceptable)
    cb = COINBASE_SPOT

    row: dict[str, Any] = {
        "event_ticker": ts.event_ticker,
        "ticker": ticker,
        "intended_ts": intended_ts,
        "captured_ts": captured_ts,
        "yes_bid": yes_bid if yes_bid > 0 else None,
        "yes_ask": yes_ask if yes_ask > 0 else None,
        "no_bid": no_bid if no_bid > 0 else None,
        "no_ask": no_ask if no_ask > 0 else None,
        "last_trade": last_trade,
        "implied_prob": implied_prob,
        "cb_spot_mid": cb.get("mid"),
        "cb_spot_bid": cb.get("bid"),
        "cb_spot_ask": cb.get("ask"),
    }
    return row


# ── Sharp-move detection helper ───────────────────────────────────────────────

def _update_sharp_move_flag(now_ms: int, mid: float) -> bool:
    """Append mid to rolling 30s history; return True if >=0.30% move detected.

    Called inside _CB_LOCK by the Coinbase WS loop.
    """
    global _SHARP_MOVE_FLAG

    # Prune entries older than SHARP_MOVE_WINDOW_MS
    cutoff = now_ms - SHARP_MOVE_WINDOW_MS
    while _CB_MID_HISTORY and _CB_MID_HISTORY[0][0] < cutoff:
        _CB_MID_HISTORY.popleft()

    _CB_MID_HISTORY.append((now_ms, mid))

    if len(_CB_MID_HISTORY) < 2:
        return False

    oldest_mid = _CB_MID_HISTORY[0][1]
    if oldest_mid == 0:
        return False

    pct_move = abs((mid - oldest_mid) / oldest_mid)
    if pct_move >= SHARP_MOVE_THRESHOLD:
        if not _SHARP_MOVE_FLAG:
            log.info(
                "path_logger: sharp move detected: %.3f%% BTC/USD move in 30s window",
                pct_move * 100,
            )
        _SHARP_MOVE_FLAG = True
        return True
    return False


# ── Main async task loops ─────────────────────────────────────────────────────

async def kalshi_polling_loop(client: Any, conn: Any) -> None:
    """Main Kalshi REST polling loop. Runs indefinitely until cancelled."""
    global _SHARP_MOVE_FLAG

    known: dict[str, TickerState] = {}
    backoff_state: dict[str, int] = {}  # ticker → current backoff ms
    last_refresh_ms = 0
    commits_since_checkpoint = 0
    last_checkpoint_ts = time.monotonic()

    log.info("path_logger: kalshi_polling_loop started")

    while True:
        now_ms = _now_ms()

        # Refresh ticker set every 60s
        if now_ms - last_refresh_ms >= int(TICKER_REFRESH_INTERVAL_SEC * 1_000):
            await _refresh_tickers(client, known, conn)
            last_refresh_ms = now_ms

        # Consume and reset sharp-move flag atomically
        sharp_triggered = _SHARP_MOVE_FLAG
        _SHARP_MOVE_FLAG = False

        # Apply SHARP_MOVE to ALL tickers when flag is set
        for ts in known.values():
            _transition_state(ts, now_ms, sharp_triggered)

        # Remove expired tickers
        expired = [t for t, ts in known.items() if ts.expires_at_ms < now_ms]
        for t in expired:
            log.info("path_logger: ticker %s expired, removing", t)
            del known[t]

        # Collect rows for this scan cycle
        ladder_rows: list[dict[str, Any]] = []
        error_rows: list[dict[str, Any]] = []

        for ticker, ts in list(known.items()):
            cadence = _cadence_for_state(ts.window_state)
            intended_ts = ts.last_capture_ms + cadence if ts.last_capture_ms else now_ms

            if now_ms < intended_ts:
                continue  # not yet due

            result = await _fetch_and_build_row(client, ts, intended_ts, backoff_state)
            await asyncio.sleep(INTER_CALL_DELAY_SEC)

            if result is None:
                continue

            if result.get("_error"):
                error_rows.append(result)
                continue

            captured_ts = result["captured_ts"]
            ts.last_capture_ms = captured_ts
            ts.capture_history.append(captured_ts)

            # Apply backoff sleep if this ticker had a prior error
            backoff_ms = backoff_state.get(ticker, 0)
            if backoff_ms > 0:
                await asyncio.sleep(backoff_ms / 1_000.0)

            ladder_rows.append(result)

        # Batch-write ladder rows
        if ladder_rows:
            try:
                store.batch_insert_ladder(conn, ladder_rows)
                commits_since_checkpoint += 1
            except sqlite3.OperationalError as exc:
                log.critical("path_logger: disk write failed (disk full?): %s", exc)
                raise SystemExit(1) from exc

        # Write error jitter rows
        for err in error_rows:
            store.insert_jitter(
                conn,
                event_type=err["event_type"],
                ticker=err["ticker"],
                intended_ts=err["intended_ts"],
                captured_ts=err["captured_ts"],
                gap_ms=err["gap_ms"],
                payload_json=json.dumps({"backoff_ms": err.get("backoff_ms")}),
            )

        # WAL checkpoint
        did_ckpt, last_checkpoint_ts = store.checkpoint_if_needed(
            conn,
            commits_since_checkpoint,
            max_commits=500,
            max_seconds=300.0,
            last_checkpoint_ts=last_checkpoint_ts,
        )
        if did_ckpt:
            commits_since_checkpoint = 0

        # Sleep until the next 1-second scan tick
        await asyncio.sleep(1.0)


async def coinbase_ws_loop(conn: Any) -> None:
    """Coinbase ccxt.pro WebSocket loop. Pushes BTC/USD ticks into COINBASE_SPOT.

    ccxt.pro auto-reconnects on disconnect. We detect gaps > 10s and log a
    jitter row. Sharp-move detection runs on every tick.

    Note: ccxt.pro's coinbase exchange does not require auth for watch_ticker
    on public BTC/USD market data; apiKey/secret are left unset intentionally.
    """
    global _CB_LOCK, _SHARP_MOVE_FLAG

    log.info("path_logger: coinbase_ws_loop started")

    try:
        import ccxt.pro as ccxtpro  # type: ignore[import]
    except ImportError:
        log.error("path_logger: ccxt[async] not installed — coinbase_ws_loop disabled")
        return

    exchange = ccxtpro.coinbase()
    last_update_ms: int | None = None

    try:
        while True:
            try:
                ticker = await exchange.watch_ticker("BTC/USD")
                now_ms = _now_ms()

                bid = ticker.get("bid")
                ask = ticker.get("ask")
                last = ticker.get("last")

                # Compute mid: prefer bid+ask average, fall back to last
                mid: float | None
                if bid is not None and ask is not None:
                    mid = (float(bid) + float(ask)) / 2.0
                elif last is not None:
                    mid = float(last)
                else:
                    mid = None

                # Detect gap before updating last_update_ms
                if last_update_ms is not None:
                    gap_ms = now_ms - last_update_ms
                    if gap_ms > COINBASE_GAP_THRESHOLD_SEC * 1_000:
                        log.warning(
                            "path_logger: Coinbase WS gap: %.1fs", gap_ms / 1_000
                        )
                        store.insert_jitter(
                            conn,
                            event_type="coinbase_ws_gap",
                            ticker=None,
                            intended_ts=last_update_ms,
                            captured_ts=now_ms,
                            gap_ms=gap_ms,
                            payload_json=None,
                        )

                async with _CB_LOCK:  # type: ignore[union-attr]
                    COINBASE_SPOT["mid"] = mid
                    COINBASE_SPOT["bid"] = float(bid) if bid is not None else None
                    COINBASE_SPOT["ask"] = float(ask) if ask is not None else None
                    COINBASE_SPOT["last_update_ms"] = now_ms

                    if mid is not None:
                        _update_sharp_move_flag(now_ms, mid)

                last_update_ms = now_ms

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("path_logger: coinbase_ws_loop error (will retry): %s", exc)
                await asyncio.sleep(2.0)  # brief pause before ccxt.pro reconnect
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def heartbeat_loop(conn: Any) -> None:
    """Write a logger_jitter heartbeat row every 60s."""
    log.info("path_logger: heartbeat_loop started")
    while True:
        await asyncio.sleep(CADENCE_HEARTBEAT_MS / 1_000.0)
        now_ms = _now_ms()
        store.insert_jitter(
            conn,
            event_type="heartbeat",
            ticker=None,
            intended_ts=None,
            captured_ts=now_ms,
            gap_ms=None,
            payload_json=None,
        )


async def jitter_report_loop(client: Any, conn: Any, known_ref: dict[str, TickerState] | None = None) -> None:
    """Every 5 min, compute per-ticker jitter stats and write logger_jitter rows.

    known_ref is a mutable reference to the Kalshi loop's `known` dict. Since
    Python dicts are shared by reference, the loop sees live ticker state.
    Passing None disables the report (used in unit tests).
    """
    log.info("path_logger: jitter_report_loop started")
    while True:
        await asyncio.sleep(JITTER_REPORT_INTERVAL_SEC)
        if known_ref is None:
            continue

        now_ms = _now_ms()
        for ticker, ts in list(known_ref.items()):
            history = list(ts.capture_history)
            if len(history) < 2:
                continue

            # Inter-capture gaps
            gaps = [history[i] - history[i - 1] for i in range(1, len(history))]
            gaps.sort()
            n = len(gaps)

            def _pct(p: float) -> float:
                idx = int(p * n)
                return gaps[min(idx, n - 1)]

            payload = {
                "n_samples": n,
                "median_gap_ms": statistics.median(gaps),
                "p95_gap_ms": _pct(0.95),
                "p99_gap_ms": _pct(0.99),
                "max_gap_ms": max(gaps),
                "window_state": ts.window_state.name,
            }
            store.insert_jitter(
                conn,
                event_type="jitter_report",
                ticker=ticker,
                intended_ts=None,
                captured_ts=now_ms,
                gap_ms=None,
                payload_json=json.dumps(payload),
            )


async def ntp_recheck_loop(conn: Any) -> None:
    """Every 5 min, re-check NTP sync. Logs WARNING + jitter row if sync lost."""
    log.info("path_logger: ntp_recheck_loop started")
    while True:
        await asyncio.sleep(NTP_RECHECK_INTERVAL_SEC)
        try:
            proc = await asyncio.create_subprocess_exec(
                "timedatectl", "show", "--property=NTPSynchronized",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode().strip()
            # output is e.g. "NTPSynchronized=yes"
            if "NTPSynchronized=yes" not in output:
                now_ms = _now_ms()
                log.warning(
                    "path_logger: NTP sync lost mid-run (timedatectl: %r) — "
                    "timestamps may drift; continuing",
                    output,
                )
                store.insert_jitter(
                    conn,
                    event_type="ntp_lost",
                    ticker=None,
                    intended_ts=None,
                    captured_ts=now_ms,
                    gap_ms=None,
                    payload_json=json.dumps({"timedatectl_output": output}),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("path_logger: ntp_recheck_loop: timedatectl failed: %s", exc)


async def run_logger_tasks(client: Any, db_path: str) -> None:
    """Wire up all async tasks and run them concurrently.

    This is the top-level coroutine called from main.py. It initialises
    shared state (asyncio.Lock, DB connection) and gathers all five task
    loops. Cancellation propagates to all sub-tasks.
    """
    global _CB_LOCK

    _CB_LOCK = asyncio.Lock()

    conn = store.connect(db_path)
    store.init_schema(conn)

    # Startup jitter row
    store.insert_jitter(
        conn,
        event_type="startup",
        ticker=None,
        intended_ts=None,
        captured_ts=_now_ms(),
        gap_ms=None,
        payload_json=json.dumps({"series": list(SERIES_TICKERS)}),
    )

    # known dict shared between _full_kalshi_loop (closure below) and
    # jitter_report_loop. Both coroutines reference the same dict object.
    # Design choice: inline re-implementation rather than threading queues;
    # all tasks are single-process asyncio — closures over a shared dict are
    # the idiomatic pattern here.
    known: dict[str, TickerState] = {}
    async def _full_kalshi_loop() -> None:
        """Full Kalshi polling loop with known dict exposed to jitter loop."""
        global _SHARP_MOVE_FLAG

        backoff_state: dict[str, int] = {}
        last_refresh_ms = 0
        commits_since_checkpoint = 0
        last_checkpoint_ts = time.monotonic()

        log.info("path_logger: kalshi_polling_loop started (full)")

        while True:
            now_ms = _now_ms()

            if now_ms - last_refresh_ms >= int(TICKER_REFRESH_INTERVAL_SEC * 1_000):
                await _refresh_tickers(client, known, conn)
                last_refresh_ms = now_ms

            sharp_triggered = _SHARP_MOVE_FLAG
            _SHARP_MOVE_FLAG = False

            for ts in known.values():
                _transition_state(ts, now_ms, sharp_triggered)

            expired = [t for t, ts in known.items() if ts.expires_at_ms < now_ms]
            for t in expired:
                log.info("path_logger: ticker %s expired, removing", t)
                del known[t]

            ladder_rows: list[dict[str, Any]] = []
            error_rows: list[dict[str, Any]] = []

            for ticker, ts in list(known.items()):
                cadence = _cadence_for_state(ts.window_state)
                intended_ts = ts.last_capture_ms + cadence if ts.last_capture_ms else now_ms

                if now_ms < intended_ts:
                    continue

                result = await _fetch_and_build_row(client, ts, intended_ts, backoff_state)
                await asyncio.sleep(INTER_CALL_DELAY_SEC)

                if result is None:
                    continue

                if result.get("_error"):
                    error_rows.append(result)
                    continue

                captured_ts = result["captured_ts"]
                ts.last_capture_ms = captured_ts
                ts.capture_history.append(captured_ts)

                backoff_ms = backoff_state.get(ticker, 0)
                if backoff_ms > 0:
                    await asyncio.sleep(backoff_ms / 1_000.0)

                ladder_rows.append(result)

            if ladder_rows:
                try:
                    store.batch_insert_ladder(conn, ladder_rows)
                    commits_since_checkpoint += 1
                except sqlite3.OperationalError as exc:
                    log.critical("path_logger: disk write failed (disk full?): %s", exc)
                    raise SystemExit(1) from exc

            for err in error_rows:
                store.insert_jitter(
                    conn,
                    event_type=err["event_type"],
                    ticker=err["ticker"],
                    intended_ts=err["intended_ts"],
                    captured_ts=err["captured_ts"],
                    gap_ms=err["gap_ms"],
                    payload_json=json.dumps({"backoff_ms": err.get("backoff_ms")}),
                )

            did_ckpt, last_checkpoint_ts = store.checkpoint_if_needed(
                conn,
                commits_since_checkpoint,
                max_commits=500,
                max_seconds=300.0,
                last_checkpoint_ts=last_checkpoint_ts,
            )
            if did_ckpt:
                commits_since_checkpoint = 0

            await asyncio.sleep(1.0)

    try:
        await asyncio.gather(
            _full_kalshi_loop(),
            coinbase_ws_loop(conn),
            heartbeat_loop(conn),
            jitter_report_loop(client, conn, known_ref=known),
            ntp_recheck_loop(conn),
        )
    finally:
        # Write shutdown jitter row before closing
        try:
            store.insert_jitter(
                conn,
                event_type="shutdown",
                ticker=None,
                intended_ts=None,
                captured_ts=_now_ms(),
                gap_ms=None,
                payload_json=None,
            )
        except Exception:
            pass
        conn.close()
