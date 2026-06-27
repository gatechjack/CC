"""Bitunix futures public WebSocket kline feed → pushes CLOSED bars into LiveBarCache.

Piece 3 (2026-06-27) — ws-primary / REST-fallback hybrid. ONE persistent ws
connection (wss://fapi.bitunix.com/public/) subscribes to ``market_kline_<tf>``
for every ``LiveBarCache`` it is given and maintains each cache's ``.bars`` in
real time. The cache's own ``refresh()`` short-circuits to the ws-maintained
bars while the ws is fresh (no REST call) and falls back to the REST kline poll
when the ws goes stale — so SFP + every other consumer is UNCHANGED (they still
call ``refresh()`` and read ``.bars``), and the recurring REST poll drains to
~0 while the ws is healthy.

Protocol (empirically captured from prod 2026-06-27, egress 168.62.60.79):
    connect ack : {"op":"connect","data":{"result":true}}
    subscribe   : {"op":"subscribe","args":[{"symbol":"BTCUSDT","ch":"market_kline_3min"}]}
    kline push  : {"ch":"market_kline_3min","symbol":"BTCUSDT","ts":<push_ms>,
                   "data":{"o","c","h","l","b","q"}}   # running in-progress candle, ~2s
    ping/pong   : send {"op":"ping","ping":<unix_s>} -> {"op":"ping","pong":..,"ping":..}

There is NO confirm/closed flag in the push. A bar is treated as CLOSED when the
push timestamp's interval-bucket advances: ``bucket = (ts // interval_ms) * interval_ms``;
when a push lands in a NEW bucket, the PREVIOUS bucket's accumulated OHLC is
emitted as a closed Bar (ts_ms = the bucket start, exactly like the REST bars).
The few seconds of tail between the last push of a bucket and the rollover are
immaterial for SFP's bar-structure logic; the REST fallback corrects any gap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from trading_corp.data.live_bar_cache import Bar, LiveBarCache

log = logging.getLogger(__name__)

WS_URI = "wss://fapi.bitunix.com/public/"

# LiveBarCache.timeframe -> Bitunix ws channel name.
_TF_TO_CH = {
    "1m": "market_kline_1min",
    "3m": "market_kline_3min",
    "5m": "market_kline_5min",
    "15m": "market_kline_15min",
    "30m": "market_kline_30min",
    "1h": "market_kline_60min",
    "4h": "market_kline_4h",
    "1d": "market_kline_1day",
}
_CH_TO_TF = {v: k for k, v in _TF_TO_CH.items()}


@dataclass
class _Accum:
    """Running OHLC for the current (symbol, ch) interval bucket."""
    bucket_ms: int = -1
    o: float = 0.0
    h: float = 0.0
    low: float = 0.0
    c: float = 0.0
    v: float = 0.0


class BitunixWsFeed:
    """Maintains a set of LiveBarCache `.bars` from one Bitunix public ws."""

    def __init__(self, caches, *, ping_interval_s: float = 15.0,
                 open_timeout_s: float = 15.0):
        self._caches: dict[tuple[str, str], LiveBarCache] = {}
        self._subs: list[dict] = []
        for c in caches:
            if getattr(c, "venue", "bitunix") != "bitunix":
                continue
            ch = _TF_TO_CH.get(c.timeframe)
            if ch is None:
                log.warning("ws feed: unsupported timeframe %r for %s; skipping",
                            c.timeframe, c.symbol)
                continue
            self._caches[(c.symbol, c.timeframe)] = c
            self._subs.append({"symbol": c.symbol, "ch": ch})
        self._accum: dict[tuple[str, str], _Accum] = {}
        self._ping_interval_s = ping_interval_s
        self._open_timeout_s = open_timeout_s
        self._connected = False
        self._msgs = 0
        self._bars_emitted = 0

    def mark_caches_ws_enabled(self) -> None:
        """Flip every wired cache to ws-primary (refresh() short-circuits while fresh)."""
        for c in self._caches.values():
            c.ws_enabled = True

    @property
    def sub_count(self) -> int:
        return len(self._subs)

    def status(self) -> dict:
        return {
            "connected": self._connected,
            "subs": len(self._subs),
            "msgs": self._msgs,
            "bars_emitted": self._bars_emitted,
        }

    async def run(self) -> None:
        """Connect / subscribe / receive, reconnecting with backoff. Runs until cancelled."""
        from websockets.asyncio.client import connect

        if not self._subs:
            log.warning("bitunix ws feed: no subscribable caches; not starting")
            return

        backoff = 1.0
        while True:
            try:
                async with connect(WS_URI, ping_interval=None,
                                   open_timeout=self._open_timeout_s) as ws:
                    self._connected = True
                    backoff = 1.0
                    log.info("bitunix ws feed connected (%d channels)", len(self._subs))
                    await ws.send(json.dumps({"op": "subscribe", "args": self._subs}))
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        async for raw in ws:
                            self._on_message(raw)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass
            except asyncio.CancelledError:
                log.info("bitunix ws feed cancelled")
                raise
            except Exception as e:  # noqa: BLE001 — keep the feed alive across any ws error
                log.warning("bitunix ws feed disconnected: %s; reconnect in %.1fs",
                            e, backoff)
            finally:
                self._connected = False
                # Drop accumulators so a pre-gap bucket can't bridge the outage;
                # the REST fallback (cache.refresh) keeps .bars current meanwhile.
                self._accum.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)

    async def _ping_loop(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(self._ping_interval_s)
                await ws.send(json.dumps({"op": "ping", "ping": int(time.time())}))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("bitunix ws ping loop ended: %s", e)

    def _on_message(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            return
        if not isinstance(msg, dict) or "op" in msg:
            return  # connect ack / pong / other control frame
        ch = msg.get("ch")
        symbol = msg.get("symbol")
        d = msg.get("data")
        if not ch or not symbol or not isinstance(d, dict):
            return
        tf = _CH_TO_TF.get(ch)
        if tf is None:
            return
        cache = self._caches.get((symbol, tf))
        if cache is None:
            return
        try:
            push_ms = int(msg.get("ts") or 0)
            o = float(d["o"]); h = float(d["h"]); low = float(d["l"]); c = float(d["c"])
            v = float(d.get("b") or 0.0)
        except (KeyError, ValueError, TypeError):
            return
        interval_ms = cache.timeframe_seconds * 1000
        if interval_ms <= 0 or push_ms <= 0:
            return

        self._msgs += 1
        cache.note_ws_alive()
        bucket = (push_ms // interval_ms) * interval_ms
        akey = (symbol, ch)
        acc = self._accum.get(akey)

        if acc is None or acc.bucket_ms < 0:
            self._accum[akey] = _Accum(bucket_ms=bucket, o=o, h=h, low=low, c=c, v=v)
            return
        if bucket == acc.bucket_ms:
            acc.h = max(acc.h, h)
            acc.low = min(acc.low, low)
            acc.c = c
            acc.v = v
            return
        if bucket > acc.bucket_ms:
            # bucket rolled over -> previous bucket is CLOSED; emit it.
            closed = Bar(ts_ms=acc.bucket_ms, open=acc.o, high=acc.h,
                         low=acc.low, close=acc.c, volume=acc.v)
            try:
                cache.append_ws_bar(closed)
                self._bars_emitted += 1
            except Exception as e:  # noqa: BLE001
                log.warning("bitunix ws append_ws_bar failed (%s %s): %s",
                            symbol, tf, e)
            self._accum[akey] = _Accum(bucket_ms=bucket, o=o, h=h, low=low, c=c, v=v)
        # bucket < acc.bucket_ms : stale/out-of-order push -> ignore
