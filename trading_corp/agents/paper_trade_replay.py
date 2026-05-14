"""Phase C of the would_have_placed enrichment (BACKLOG.md 2026-05-01).

Walks `paper_trade_record` rows where `result IS NULL` and replays the
post-alert price path to decide whether the trade would have hit its
take-profit, hit its stop-loss, or expired without resolution. Updates
the row's result_* columns in place.

Public entry points:
- `replay_pending_paper_trades(db_url, *, ohlcv_fetcher=None) -> dict`
  Single tick. Returns counts: scanned, resolved_win, resolved_loss,
  resolved_expired, marked_pre_phase_a, errors.
- `mark_pre_phase_a_rows(db_url) -> int`
  One-shot startup helper: marks rows that lack tp_price OR stop_price
  with `result='pre_phase_a'` so they're never re-scanned (the replay
  can't make a win/loss call without those fields).
- `start_replay_loop(db_url, *, interval_sec=900, ohlcv_fetcher=None) ->
  asyncio.Task` — spawn the periodic background tick. Mirrors the
  PMCC scan scheduler pattern in main.py.

**Tie-handling (conservative).** When a single 1m bar's high reaches
the take-profit AND the bar's low reaches the stop-loss, we cannot tell
intra-bar order from OHLC alone. We resolve to LOSS for longs (buy)
and LOSS for shorts (sell) — i.e. assume the worse outcome. This biases
the win-rate stat downward, which is the safer direction for an
auto_execute=true gating decision.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from trading_corp.persistence import db as _db

log = logging.getLogger(__name__)

# Type alias for the OHLCV fetcher we inject. Returns a list of
# [ts_ms, open, high, low, close, volume] entries, ccxt-shaped.
OhlcvFetcher = Callable[[str, str, int, int], Awaitable[list[list[float]]]]


@dataclass
class _PendingRow:
    order_id: str
    ts: str
    strategy: str
    symbol: str
    side: str
    qty: float
    stop_price: float | None
    tp_price: float | None
    tp_r_multiple: float | None
    expected_loss: float | None
    expected_gain: float | None
    max_hold_seconds: int | None


@dataclass
class _Resolved:
    # 'win' | 'loss' | 'expired' | 'pre_phase_a' | 'still_open'
    #
    # 'still_open' is a TRANSIENT verdict — the trade has neither hit
    # TP/SL nor exhausted its max_hold window yet. Caller MUST NOT
    # update the row when it sees this; the row stays at result=NULL
    # so the next replay tick picks it up again (when more bars are
    # available from the venue).
    result: str
    result_ts: str | None
    result_price: float | None
    actual_pnl_dollars: float | None
    actual_r_multiple: float | None
    bars_to_resolution: int | None


# ── public entry points ────────────────────────────────────────────────


def mark_pre_phase_a_rows(
    db_url: str = "sqlite:///data/trading_corp.db",
) -> int:
    """Set result='pre_phase_a' on rows that are missing tp_price OR
    stop_price (Phase A wasn't shipped at the alert time, so the replay
    can't decide win/loss). Idempotent: only updates rows where
    `result IS NULL`. Returns rows updated."""
    with _db.connect(db_url) as conn:
        cur = conn.execute(
            "UPDATE paper_trade_record SET result='pre_phase_a' "
            "WHERE result IS NULL AND (tp_price IS NULL OR stop_price IS NULL)"
        )
        return cur.rowcount or 0


def replay_pending_paper_trades(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    ohlcv_fetcher: OhlcvFetcher | None = None,
) -> dict:
    """Single tick. Returns counts dict.

    Synchronous wrapper around the async core so callers in non-async
    contexts (CLI, tests) can use it cleanly. Inside the project's
    asyncio main loop, prefer `_replay_tick_async` directly.
    """
    return asyncio.run(
        _replay_tick_async(db_url, ohlcv_fetcher=ohlcv_fetcher)
    )


async def replay_pending_paper_trades_async(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    ohlcv_fetcher: OhlcvFetcher | None = None,
) -> dict:
    """Async-native version for use inside the existing event loop."""
    return await _replay_tick_async(db_url, ohlcv_fetcher=ohlcv_fetcher)


def start_replay_loop(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    interval_sec: int = 900,
    ohlcv_fetcher: OhlcvFetcher | None = None,
) -> asyncio.Task:
    """Spawn the periodic background replay task. Caller (main.py)
    is responsible for cancelling it on shutdown."""
    return asyncio.create_task(
        _replay_loop(db_url, interval_sec, ohlcv_fetcher),
        name="paper_trade_replay_loop",
    )


# ── core: walk-forward classifier (synchronous, pure-function) ─────────


def _classify(
    row: _PendingRow,
    bars: list[list[float]],
) -> _Resolved:
    """Walk OHLCV bars [ts_ms, o, h, l, c, v] forward from row.ts.
    Bars MUST be in ascending ts order and SHOULD start at or after
    row.ts. Returns the resolved verdict.

    For a 'buy' (long): TP if bar.high >= tp_price, SL if bar.low <=
    stop_price. For a 'sell' (short): TP if bar.low <= tp_price, SL
    if bar.high >= stop_price. Tie within a single bar resolves to
    LOSS — see module docstring."""
    if row.tp_price is None or row.stop_price is None:
        return _Resolved("pre_phase_a", None, None, None, None, None)

    side = (row.side or "").lower()
    tp = float(row.tp_price)
    sl = float(row.stop_price)
    expected_loss = float(row.expected_loss or 0.0)
    expected_gain = float(row.expected_gain or 0.0)
    tp_r = float(row.tp_r_multiple or 0.0)

    for idx, bar in enumerate(bars):
        if len(bar) < 5:
            continue
        ts_ms = int(bar[0])
        high = float(bar[2])
        low = float(bar[3])
        close = float(bar[4])
        bar_ts_iso = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )

        if side == "buy":
            tp_hit = high >= tp
            sl_hit = low <= sl
        elif side == "sell":
            tp_hit = low <= tp
            sl_hit = high >= sl
        else:
            # Unknown side — bail out as expired-style.
            continue

        if tp_hit and sl_hit:
            # Same-bar both — assume worse: LOSS.
            return _Resolved(
                result="loss",
                result_ts=bar_ts_iso,
                result_price=sl,
                actual_pnl_dollars=expected_loss,  # already negative
                actual_r_multiple=-1.0,
                bars_to_resolution=idx + 1,
            )
        if tp_hit:
            return _Resolved(
                result="win",
                result_ts=bar_ts_iso,
                result_price=tp,
                actual_pnl_dollars=expected_gain,
                actual_r_multiple=tp_r if tp_r else None,
                bars_to_resolution=idx + 1,
            )
        if sl_hit:
            return _Resolved(
                result="loss",
                result_ts=bar_ts_iso,
                result_price=sl,
                actual_pnl_dollars=expected_loss,
                actual_r_multiple=-1.0,
                bars_to_resolution=idx + 1,
            )

    # Walked to the end of available bars without a hit. The verdict
    # depends on whether the full max_hold window has actually elapsed
    # in wall-clock time, NOT just whether we ran out of fetched bars:
    #   - elapsed >= max_hold  → genuinely expired
    #   - elapsed <  max_hold  → still open (more bars will arrive;
    #     the next replay tick will re-evaluate)
    # The previous version unconditionally returned 'expired', which
    # prematurely marked trades that were still inside their hold
    # window — caught 2026-05-11 on the bitunix paper trades.
    max_hold = int(row.max_hold_seconds or 0)
    alert_dt = _parse_row_ts(row.ts)
    now = datetime.now(timezone.utc)
    elapsed = (now - alert_dt).total_seconds() if alert_dt else 0
    fully_elapsed = max_hold > 0 and elapsed >= max_hold

    if not bars:
        last_ts_iso = row.ts
        last_close = None
        bars_n = 0
    else:
        last_bar = bars[-1]
        last_ts_iso = (
            datetime.fromtimestamp(int(last_bar[0]) / 1000.0, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
        last_close = float(last_bar[4])
        bars_n = len(bars)

    if not fully_elapsed:
        # Still inside the hold window — leave the row at result=NULL
        # so we re-evaluate on the next tick (more bars will be live
        # at the venue by then).
        return _Resolved(
            result="still_open",
            result_ts=last_ts_iso,
            result_price=last_close,
            actual_pnl_dollars=None,
            actual_r_multiple=None,
            bars_to_resolution=None,
        )

    return _Resolved(
        result="expired",
        result_ts=last_ts_iso,
        result_price=last_close,
        actual_pnl_dollars=0.0,
        actual_r_multiple=0.0,
        bars_to_resolution=bars_n,
    )


def _parse_row_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# ── async core ─────────────────────────────────────────────────────────


async def _replay_tick_async(
    db_url: str,
    *,
    ohlcv_fetcher: OhlcvFetcher | None,
) -> dict:
    fetcher = ohlcv_fetcher or _default_router_fetcher

    # Mark pre-Phase-A rows first so we don't try to fetch bars for them.
    pre_phase_a_marked = mark_pre_phase_a_rows(db_url)

    pending = _load_pending(db_url)
    counts = {
        "scanned": len(pending),
        "resolved_win": 0,
        "resolved_loss": 0,
        "resolved_expired": 0,
        "marked_pre_phase_a": pre_phase_a_marked,
        "errors": 0,
    }

    for row in pending:
        try:
            since_ts_ms = _iso_to_ms(row.ts)
            max_hold = int(row.max_hold_seconds or 0)
            if max_hold <= 0:
                # No window configured — can't bound the fetch. Skip.
                counts["errors"] += 1
                continue
            bars_needed = max(1, max_hold // 60)  # 1m bars
            bars = await fetcher(row.symbol, "1m", since_ts_ms, bars_needed)
            verdict = _classify(row, bars)
            if verdict.result == "win":
                counts["resolved_win"] += 1
            elif verdict.result == "loss":
                counts["resolved_loss"] += 1
            elif verdict.result == "expired":
                counts["resolved_expired"] += 1
            elif verdict.result == "pre_phase_a":
                counts["marked_pre_phase_a"] += 1
            elif verdict.result == "still_open":
                # Inside max_hold window — leave row at result=NULL for
                # the next replay tick to re-evaluate. No DB write.
                counts.setdefault("still_open", 0)
                counts["still_open"] += 1
                continue

            _update_row(db_url, row.order_id, verdict)
        except Exception as e:
            log.exception("replay failed for order_id=%s: %s", row.order_id, e)
            counts["errors"] += 1

    return counts


async def _replay_loop(
    db_url: str,
    interval_sec: int,
    ohlcv_fetcher: OhlcvFetcher | None,
) -> None:
    log.info("paper_trade_replay loop online: interval=%ss", interval_sec)
    try:
        while True:
            try:
                counts = await _replay_tick_async(
                    db_url, ohlcv_fetcher=ohlcv_fetcher
                )
                # f-string (not %s) — RedactingFilter rewrites dict args
                # into their keys, producing a TypeError on % formatting.
                log.info(f"paper_trade_replay tick: {counts}")
            except Exception:
                log.exception("paper_trade_replay tick raised")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        log.info("paper_trade_replay loop cancelled.")
        raise


# ── OHLCV fetchers — venue-aware router ───────────────────────────────
#
# Different strategies trade on different venues, and replay bars MUST
# come from the same venue the order was placed against (otherwise the
# win/loss verdict reflects the wrong price tape):
#   Lord Otter / Market Cypher / Coinbase BTC Donchian → Coinbase BTC/USD
#   BitUnix Futures observer                          → BitUnix BTC/USDT.P
#
# Each concrete fetcher conforms to OhlcvFetcher
# (symbol, timeframe, since_ms, limit) → list[[ts_ms,o,h,l,c,v]].
# `_default_router_fetcher` picks the right one per call based on the
# symbol shape — perps end in `.P`, spot does not.


async def _coinbase_ccxt_fetcher(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """Coinbase spot OHLCV via ccxt public endpoint.

    Coinbase's fetch_ohlcv caps at ~300 bars per call, so we page if
    `limit` exceeds that. Cypher's 7-day window in 1m bars = 10080 bars
    → ~34 pages. Cheap, all read-only, no auth required.
    """
    import ccxt.async_support as ccxt_async
    exchange = ccxt_async.coinbase({"enableRateLimit": True})
    try:
        out: list[list[float]] = []
        page_size = 300
        cursor = since_ms
        remaining = limit
        while remaining > 0:
            this_page = min(page_size, remaining)
            page = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe,
                since=cursor, limit=this_page,
            )
            if not page:
                break
            out.extend(page)
            cursor = int(page[-1][0]) + _timeframe_ms(timeframe)
            remaining -= len(page)
            if len(page) < this_page:
                break  # ran out of data
        return out
    finally:
        await exchange.close()


async def _bitunix_kline_fetcher(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """BitUnix Futures OHLCV via the public kline endpoint (no auth).

    Same data source LiveBarCache uses for live ATR — keeps the
    replay's price path consistent with what the trade saw at
    placement. BitUnix accepts up to 1000 bars per call; for the
    typical bitunix_futures.max_hold_seconds=86400 (24h × 60 = 1440
    1m bars) we page in two requests.
    """
    import httpx
    bu_symbol = _to_bitunix_symbol(symbol)
    tf_ms = _timeframe_ms(timeframe)
    out: list[list[float]] = []
    cursor = since_ms
    remaining = limit
    page_size = 1000
    async with httpx.AsyncClient(base_url="https://fapi.bitunix.com", timeout=20.0) as client:
        while remaining > 0:
            this_page = min(page_size, remaining)
            end_ms = cursor + this_page * tf_ms
            r = await client.get(
                "/api/v1/futures/market/kline",
                params={
                    "symbol": bu_symbol,
                    "interval": timeframe,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": this_page,
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"bitunix kline err: code={data.get('code')} msg={data.get('msg')!r}"
                )
            page = data.get("data") or []
            if not page:
                break
            for row in page:
                out.append([
                    int(row["time"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    # BitUnix returns `baseVol` (USDT notional). Use it
                    # for parity with ccxt's volume convention.
                    float(row.get("baseVol") or 0.0),
                ])
            last_ts = max(int(row["time"]) for row in page)
            cursor = last_ts + tf_ms
            remaining -= len(page)
            if len(page) < this_page:
                break
    # Defensive: ensure chronological order (the replay classifier walks forward)
    out.sort(key=lambda r: r[0])
    return out


def _to_bitunix_symbol(symbol: str) -> str:
    """Normalize the order's symbol to BitUnix REST format (no slash, no .P).

    `BTC/USDT.P` → `BTCUSDT`. `BTCUSDT.P` → `BTCUSDT`. `BTCUSDT` → `BTCUSDT`.
    """
    s = symbol.replace("/", "").upper()
    if s.endswith(".P"):
        s = s[:-2]
    return s


def _is_bitunix_symbol(symbol: str) -> bool:
    """True iff the symbol is a BitUnix perp (ends in `.P`)."""
    return symbol.upper().endswith(".P")


async def _default_router_fetcher(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """Symbol-aware default fetcher. Routes BitUnix perps to BitUnix's
    native kline endpoint and everything else to Coinbase spot via ccxt."""
    if _is_bitunix_symbol(symbol):
        return await _bitunix_kline_fetcher(symbol, timeframe, since_ms, limit)
    return await _coinbase_ccxt_fetcher(symbol, timeframe, since_ms, limit)


def _timeframe_ms(tf: str) -> int:
    if tf.endswith("m"):
        return int(tf[:-1]) * 60_000
    if tf.endswith("h"):
        return int(tf[:-1]) * 3_600_000
    if tf.endswith("d"):
        return int(tf[:-1]) * 86_400_000
    return 60_000


# ── DB helpers ─────────────────────────────────────────────────────────


def _load_pending(db_url: str) -> list[_PendingRow]:
    with _db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT order_id, ts, strategy, symbol, side, qty, "
            "       stop_price, tp_price, tp_r_multiple, expected_loss, "
            "       expected_gain, max_hold_seconds "
            "FROM paper_trade_record WHERE result IS NULL "
            "ORDER BY ts ASC"
        ).fetchall()
    return [
        _PendingRow(
            order_id=r["order_id"], ts=r["ts"],
            strategy=r["strategy"], symbol=r["symbol"],
            side=r["side"], qty=r["qty"],
            stop_price=r["stop_price"], tp_price=r["tp_price"],
            tp_r_multiple=r["tp_r_multiple"],
            expected_loss=r["expected_loss"],
            expected_gain=r["expected_gain"],
            max_hold_seconds=r["max_hold_seconds"],
        ) for r in rows
    ]


def _update_row(db_url: str, order_id: str, v: _Resolved) -> None:
    with _db.connect(db_url) as conn:
        conn.execute(
            "UPDATE paper_trade_record SET "
            "  result=?, result_ts=?, result_price=?, "
            "  actual_pnl_dollars=?, actual_r_multiple=?, "
            "  bars_to_resolution=? "
            "WHERE order_id=?",
            (
                v.result, v.result_ts, v.result_price,
                v.actual_pnl_dollars, v.actual_r_multiple,
                v.bars_to_resolution, order_id,
            ),
        )


def _iso_to_ms(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
