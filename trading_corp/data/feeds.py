"""Real-time + snapshot price feeds. Phase 2 ships:

  - yfinance fallback for stocks (snapshot, polling)
  - ccxt sandbox poll for crypto (snapshot)
  - asyncio.Queue aggregator for downstream strategy/bot subscribers
  - Stale-feed watchdog skeleton (active in Phase 3 when bots subscribe)

Phase 3 swaps polling for native WebSockets (ccxt.pro / native exchange WS).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass
class Tick:
    symbol: str
    price: float
    ts: float  # epoch seconds (UTC)
    venue: str


class FeedAggregator:
    """Holds a ring buffer per symbol + an asyncio.Queue for subscribers."""

    def __init__(self, ringbuffer_size: int = 256) -> None:
        self._buffers: dict[str, deque[Tick]] = {}
        self._ringbuffer_size = ringbuffer_size
        self.queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=10_000)
        self._last_seen: dict[str, float] = {}

    def latest(self, symbol: str) -> Tick | None:
        buf = self._buffers.get(symbol)
        return buf[-1] if buf else None

    def history(self, symbol: str) -> list[Tick]:
        return list(self._buffers.get(symbol, []))

    async def push(self, tick: Tick) -> None:
        buf = self._buffers.setdefault(symbol := tick.symbol, deque(maxlen=self._ringbuffer_size))
        buf.append(tick)
        self._last_seen[symbol] = tick.ts
        try:
            self.queue.put_nowait(tick)
        except asyncio.QueueFull:
            log.warning("FeedAggregator queue full; dropping tick for %s", symbol)

    def stale(self, symbol: str, max_age_s: float) -> bool:
        last = self._last_seen.get(symbol)
        if last is None:
            return True
        return (time.time() - last) > max_age_s


async def yfinance_poll(
    symbols: Iterable[str],
    aggregator: FeedAggregator,
    interval_s: float = 5.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Polling stub for stocks. Imports yfinance lazily so it is optional."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        log.warning("yfinance not installed; stock feed disabled.")
        return

    syms = list(symbols)
    log.info("yfinance polling started for %s every %.1fs", syms, interval_s)
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            tickers = yf.Tickers(" ".join(syms))
            for s in syms:
                info = tickers.tickers[s].fast_info
                last = float(info.last_price) if info.last_price else 0.0
                if last > 0:
                    await aggregator.push(Tick(s, last, time.time(), "yfinance"))
        except Exception as e:  # network errors, rate limits, holiday no-data
            log.warning("yfinance poll error: %s", e)
        await asyncio.sleep(interval_s)


async def ccxt_poll(
    pairs: Iterable[str],
    aggregator: FeedAggregator,
    venue: str = "coinbase",
    interval_s: float = 2.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Polling stub for crypto via ccxt. Phase 3 replaces with ccxt.pro WS."""
    try:
        import ccxt.async_support as ccxt  # type: ignore
    except ImportError:
        log.warning("ccxt not installed; crypto feed disabled.")
        return

    cls = getattr(ccxt, venue, None)
    if cls is None:
        log.error("ccxt venue '%s' not found", venue)
        return
    ex = cls({"enableRateLimit": True})
    pairs = list(pairs)
    log.info("ccxt polling started for %s on %s every %.1fs", pairs, venue, interval_s)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            for p in pairs:
                try:
                    t = await ex.fetch_ticker(p)
                    last = float(t.get("last") or t.get("close") or 0.0)
                    if last > 0:
                        await aggregator.push(Tick(p, last, time.time(), venue))
                except Exception as e:
                    log.warning("ccxt poll error for %s: %s", p, e)
            await asyncio.sleep(interval_s)
    finally:
        await ex.close()


async def staleness_watchdog(
    aggregator: FeedAggregator,
    symbols: Iterable[str],
    max_age_s: float = 60.0,
    on_stale: callable | None = None,
    interval_s: float = 5.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """If any feed goes stale beyond max_age_s, invoke on_stale(symbol)."""
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        for s in symbols:
            if aggregator.stale(s, max_age_s):
                log.warning("Feed stale for %s (>%ss)", s, max_age_s)
                if on_stale is not None:
                    on_stale(s)
        await asyncio.sleep(interval_s)
