"""BitUnix OHLCV bar archiver — persistent record of every bar the
HTF caches see (PR 5a).

Decoupled from `LiveBarCache`: the cache stays a pure in-memory ring
buffer (used by other divisions too — Coinbase Donchian, etc.). This
archiver is BitUnix-specific: it polls a list of caches, finds bars
that haven't been persisted yet, and writes them to
`bitunix_bar_history`.

Why decouple:
  - LiveBarCache is reused. Adding a SQL write to its `refresh()`
    would couple non-BitUnix consumers to BitUnix-specific persistence.
  - Cache lifecycle is "fast forward-only" (drop on max_bars overflow);
    persistence wants append-only with no drops. Different semantics.
  - The archiver owns its own poll loop so the cache's poll cadence
    (60s for 3m, 5min for 1h, etc.) is independent of how often we
    write to disk.

Storage estimate (BTCUSDT, 4 timeframes):
  3m  → 480 bars/day × 365 = 175k rows/year
  1h  → 24  bars/day × 365 =   8.8k rows/year
  4h  →  6  bars/day × 365 =   2.2k rows/year
  1d  →  1  bar/day  × 365 =     365 rows/year
  Total: ~186k rows/year. SQLite handles this trivially.

Idempotence: PRIMARY KEY (ts_ms, timeframe) + INSERT OR IGNORE means
re-running the archiver against the same cache contents is a no-op.
The archiver also tracks the highest-seen ts_ms per cache to skip the
SQL round-trip for known-old bars on each tick.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading_corp.data.live_bar_cache import LiveBarCache
from trading_corp.persistence import db

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


BAR_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_bar_history (
    symbol       TEXT NOT NULL,
    ts_ms        INTEGER NOT NULL,
    timeframe    TEXT NOT NULL,
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    volume       REAL NOT NULL,
    inserted_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, ts_ms, timeframe)
);
"""

# 2026-06-25 multi-coin migration: PK is now (symbol, ts_ms, timeframe) so
# SOL/ETH/XRP bars no longer collide with BTC on the same (ts_ms, timeframe).
BAR_HISTORY_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS bitunix_bar_history_sym_tf_ts_idx "
    "ON bitunix_bar_history(symbol, timeframe, ts_ms)"
)


@dataclass
class BitUnixBarArchiver:
    """Polls a list of `LiveBarCache` instances and archives every new
    closed bar to `bitunix_bar_history`.

    The first archive pass against a cache writes everything currently
    in `cache.bars`. Subsequent passes only write bars whose ts_ms is
    strictly greater than the highest-seen ts_ms for that (cache.symbol,
    cache.timeframe) tuple — cheap upper-bound check before the SQL.
    INSERT OR IGNORE makes the SQL itself idempotent as a backstop.
    """
    db_url: str
    caches: tuple[LiveBarCache, ...]
    _high_water_mark: dict[tuple[str, str], int] = field(default_factory=dict)
    _schema_ready: bool = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(BAR_HISTORY_DDL)
                conn.execute(BAR_HISTORY_INDEX_DDL)
            self._schema_ready = True
        except Exception as e:
            log.warning("bitunix_bar_archiver: schema init failed: %s", e)

    def archive_once(self) -> int:
        """One archive pass across all caches. Returns the count of
        rows actually written across all caches.

        Sync (no awaits) — the SQL is fast and we don't want to risk
        re-entrancy with the cache poll loops modifying `cache.bars`
        mid-iteration. Snapshot the bars list locally before iterating.
        """
        self._ensure_schema()
        if not self._schema_ready:
            return 0

        total_written = 0
        now = _utc_now_iso()

        try:
            with db.connect(self.db_url) as conn:
                for cache in self.caches:
                    key = (cache.symbol, cache.timeframe)
                    high = self._high_water_mark.get(key, -1)
                    # Snapshot bars list to a local — refresh() may
                    # mutate the underlying list while we iterate.
                    snapshot = list(cache.bars)
                    new_bars = [b for b in snapshot if b.ts_ms > high]
                    if not new_bars:
                        continue
                    rows = [
                        (
                            cache.symbol, b.ts_ms, cache.timeframe,
                            b.open, b.high, b.low, b.close, b.volume,
                            now,
                        )
                        for b in new_bars
                    ]
                    conn.executemany(
                        "INSERT OR IGNORE INTO bitunix_bar_history "
                        "(symbol, ts_ms, timeframe, open, high, low, close, volume, inserted_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                    self._high_water_mark[key] = max(b.ts_ms for b in new_bars)
                    total_written += len(rows)
        except Exception as e:
            log.warning("bitunix_bar_archiver: archive_once failed: %s", e)
            return total_written

        if total_written > 0:
            log.debug(
                "bitunix_bar_archiver: wrote %d new bar(s) across %d cache(s)",
                total_written, len(self.caches),
            )
        return total_written

    async def run_loop(self, interval_s: float = 60.0) -> None:
        """Background task: archive every `interval_s` seconds. Mirrors
        the LiveBarCache poll cadence so we capture each new bar within
        ~one cache-poll-tick of when it appeared."""
        log.info(
            "bitunix_bar_archiver online (caches=%d, interval=%ss)",
            len(self.caches), interval_s,
        )
        try:
            while True:
                try:
                    self.archive_once()
                except Exception as e:
                    log.warning("bitunix_bar_archiver: loop tick raised: %s", e)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            log.info("bitunix_bar_archiver: loop cancelled")
            raise
