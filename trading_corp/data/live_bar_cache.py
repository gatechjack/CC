"""Live OHLCV bar cache for the BitUnix Phase 3.2a scalping pipeline.

In-memory cache of the most recent ~30-60 N-minute bars. Polled on a
periodic background task; consumed by `BitunixFuturesObserver` for
ATR-based stop sizing.

**Venue selection** (corrected 2026-05-10 after initial Coinbase mistake):
- `venue="bitunix"` (default for production) — direct REST call to
  BitUnix's public `/api/v1/futures/market/kline`. NO auth needed.
  Native 3m, 5m, 15m, etc. **This is the right venue** because we trade
  on BitUnix; matching the live data source to the trading venue
  eliminates cross-venue volatility-profile drift.
- `venue="bybit"` — via CCXT. Native 3m. Geo-blocked from US IPs by
  Cloudfront, so unusable from US-region Azure VMs. Kept as an option
  for non-US deploys.
- `venue="coinbase"` — via CCXT. Only supports {1m, 5m, 15m, 1h, 6h, 1d};
  no native 3m. Kept as fallback / for spot-market backtests.

Lifecycle:
    cache = LiveBarCache(symbol="BTC/USD", timeframe="3m", max_bars=60)
    await cache.refresh()                      # one-shot
    task = asyncio.create_task(
        cache.run_poll_loop(interval_s=60)
    )
    # ... at trigger time:
    atr = cache.get_atr(period=14)             # float | None
    last = cache.last_close()                  # float | None

Failures (network errors, ccxt issues) are logged and swallowed; the
cache returns whatever it last successfully fetched. Consumers MUST
handle `None` from `get_atr()` and fall back gracefully.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Bar:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


_BITUNIX_BASE_URL = "https://fapi.bitunix.com"


@dataclass
class LiveBarCache:
    """Bounded in-memory cache of recent OHLCV bars.

    Bars are stored chronologically (oldest first). On refresh, we
    replace the cache with the freshly fetched window, dropping any
    in-progress (partial) latest bar.
    """
    symbol: str = "BTCUSDT"               # BitUnix raw symbol; CCXT venues use unified
    timeframe: str = "3m"
    venue: str = "bitunix"                # bitunix (default) | bybit | coinbase
    max_bars: int = 60
    bars: list[Bar] = field(default_factory=list)
    last_refresh_ts: float | None = None     # monotonic seconds
    last_refresh_error: str | None = None
    last_refresh_count: int = 0

    @property
    def timeframe_seconds(self) -> int:
        m = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
             "1h": 3600, "4h": 14400, "1d": 86400}
        return m.get(self.timeframe, 180)

    async def refresh(self) -> int:
        """Fetch latest bars from venue. Returns count after refresh.

        Drops the in-progress (partial) latest bar. Errors are stored
        in `last_refresh_error`; bars are NOT cleared on error so we
        keep serving the most recent successful snapshot.
        """
        try:
            if self.venue == "bitunix":
                return await self._refresh_bitunix()
            return await self._refresh_ccxt()
        except Exception as e:
            self.last_refresh_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("LiveBarCache refresh failed: %s", self.last_refresh_error)
            return len(self.bars)

    async def _refresh_bitunix(self) -> int:
        """BitUnix public kline endpoint. No auth; native 3m support."""
        import httpx
        async with httpx.AsyncClient(base_url=_BITUNIX_BASE_URL, timeout=10.0) as client:
            r = await client.get(
                "/api/v1/futures/market/kline",
                params={
                    "symbol": self.symbol,
                    "interval": self.timeframe,
                    "limit": self.max_bars,
                },
            )
            r.raise_for_status()
            data = r.json()

        if data.get("code") != 0:
            self.last_refresh_error = (
                f"BitUnix code={data.get('code')} msg={data.get('msg')!r}"
            )[:200]
            return len(self.bars)

        raw = data.get("data") or []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        granularity_ms = self.timeframe_seconds * 1000
        out: list[Bar] = []
        for row in raw:
            ts_ms = int(row["time"])
            if ts_ms + granularity_ms > now_ms:
                continue        # drop in-progress bar
            out.append(Bar(
                ts_ms=ts_ms,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                # BitUnix's response has `quoteVol` (BTC qty) and `baseVol`
                # (USDT notional). Use baseVol (notional) for parity with
                # CCXT's "volume" field convention.
                volume=float(row.get("baseVol") or 0.0),
            ))
        out.sort(key=lambda b: b.ts_ms)
        if len(out) > self.max_bars:
            out = out[-self.max_bars:]

        if out:
            self.bars = out
            self.last_refresh_ts = asyncio.get_event_loop().time()
            self.last_refresh_error = None
            self.last_refresh_count = len(out)
        return len(self.bars)

    async def _refresh_ccxt(self) -> int:
        """Fallback path for CCXT venues (bybit, coinbase, ...)."""
        try:
            import ccxt.async_support as ccxt_async   # type: ignore
        except ImportError:
            self.last_refresh_error = "ccxt not installed"
            return len(self.bars)

        venue_cls = getattr(ccxt_async, self.venue, None)
        if venue_cls is None:
            self.last_refresh_error = f"ccxt venue {self.venue!r} not found"
            return len(self.bars)

        kwargs: dict = {"enableRateLimit": True}
        if self.venue == "bybit":
            # Force USDT-perpetual market type for Bybit
            kwargs["options"] = {"defaultType": "linear"}
        exchange = venue_cls(kwargs)

        try:
            raw = await exchange.fetch_ohlcv(
                self.symbol, timeframe=self.timeframe, limit=self.max_bars,
            )
        finally:
            try:
                await exchange.close()
            except Exception:
                pass

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        granularity_ms = self.timeframe_seconds * 1000
        out: list[Bar] = []
        for row in (raw or []):
            ts_ms, o, h, l, c, v = row
            if int(ts_ms) + granularity_ms > now_ms:
                continue
            out.append(Bar(
                ts_ms=int(ts_ms),
                open=float(o), high=float(h), low=float(l),
                close=float(c), volume=float(v),
            ))
        out.sort(key=lambda b: b.ts_ms)
        if len(out) > self.max_bars:
            out = out[-self.max_bars:]

        if out:
            self.bars = out
            self.last_refresh_ts = asyncio.get_event_loop().time()
            self.last_refresh_error = None
            self.last_refresh_count = len(out)
        return len(self.bars)

    async def run_poll_loop(self, interval_s: float = 60.0) -> None:
        """Periodic background task: refresh every `interval_s` seconds.

        Cancellable via task.cancel(); handles ccxt errors silently.
        Caller is responsible for awaiting / cleanup.
        """
        log.info(
            "LiveBarCache poll loop online (symbol=%s, tf=%s, venue=%s, interval=%ss)",
            self.symbol, self.timeframe, self.venue, interval_s,
        )
        try:
            while True:
                try:
                    await self.refresh()
                except Exception as e:  # belt + braces — refresh handles its own errors
                    log.warning("LiveBarCache poll: unexpected: %s", e)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            log.info("LiveBarCache poll loop cancelled")
            raise

    # ── computed indicators ──────────────────────────────────────────

    def last_close(self) -> float | None:
        return self.bars[-1].close if self.bars else None

    def get_atr(self, period: int = 14) -> float | None:
        """Average True Range over the last `period` bars.

        Uses Wilder's smoothing (the original ATR definition). Requires
        at least `period + 1` bars in cache; returns None if insufficient.

        TR(i) = max(high(i) - low(i),
                    abs(high(i) - close(i-1)),
                    abs(low(i)  - close(i-1)))
        """
        if len(self.bars) < period + 1:
            return None
        # True Range list of length len(bars)-1
        trs: list[float] = []
        for i in range(1, len(self.bars)):
            b = self.bars[i]
            prev_c = self.bars[i - 1].close
            tr = max(
                b.high - b.low,
                abs(b.high - prev_c),
                abs(b.low - prev_c),
            )
            trs.append(tr)

        if len(trs) < period:
            return None

        # Initial ATR = SMA of first `period` TRs
        atr = sum(trs[:period]) / period
        # Wilder's smoothing for the rest
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    # ── introspection ────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "venue": self.venue,
            "bars_cached": len(self.bars),
            "last_close": self.last_close(),
            "last_refresh_count": self.last_refresh_count,
            "last_refresh_error": self.last_refresh_error,
            "atr_14": self.get_atr(14),
        }
