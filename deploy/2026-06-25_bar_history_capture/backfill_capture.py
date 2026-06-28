"""One-time, API-ONLY backfill of SOL/ETH/XRP 15m+3m bars into
bitunix_bar_history in the TRADING db (data/trading_corp.db) via Bitunix
public REST klines (the SAME endpoint LiveBarCache uses) -- NOT TradingView,
NOT btc_scalping.db.

Run AFTER the schema migration + engine restart:

    cd ~/trading_corp
    python deploy/2026-06-25_bar_history_capture/backfill_capture.py

Idempotent: INSERT OR IGNORE -- safe to re-run. Matches BTC depth per
timeframe so the new coins have the same historical reach. Reports actual
depth achieved per coin/TF.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timezone

from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher

DB_PATH = "data/trading_corp.db"

COINS = ("SOLUSDT", "ETHUSDT", "XRPUSDT")
TIMEFRAMES = ("15m", "3m")

# kline row indices (confirmed from _bitunix_kline_fetcher source):
# [0]=ts_ms  [1]=open  [2]=high  [3]=low  [4]=close  [5]=volume(baseVol)
_I_TS = 0
_I_OPEN = 1
_I_HIGH = 2
_I_LOW = 3
_I_CLOSE = 4
_I_VOLUME = 5


def _db_execute_with_retry(conn: sqlite3.Connection, sql: str, params=()):
    """Execute a single SQL statement with db-lock retry (5 attempts)."""
    for attempt in range(5):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                if attempt < 4:
                    time.sleep(0.5 * (attempt + 1))
                    continue
            raise


def _executemany_with_retry(conn: sqlite3.Connection, sql: str, rows: list):
    """executemany with db-lock retry (5 attempts)."""
    for attempt in range(5):
        try:
            conn.executemany(sql, rows)
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                if attempt < 4:
                    time.sleep(0.5 * (attempt + 1))
                    continue
            raise


async def main() -> None:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row

        for tf in TIMEFRAMES:
            # Determine BTC anchor depth for this timeframe.
            row = _db_execute_with_retry(
                conn,
                "SELECT COUNT(*) AS n, MIN(ts_ms) AS oldest_ts "
                "FROM bitunix_bar_history WHERE symbol='BTCUSDT' AND timeframe=?",
                (tf,),
            ).fetchone()
            btc_count = row["n"] if row else 0
            btc_oldest_ts = row["oldest_ts"] if row else None

            if btc_count == 0 or btc_oldest_ts is None:
                print(f"BTCUSDT {tf}: no BTC bars in DB -- skipping all coins for this TF")
                continue

            for wire in COINS:
                # Count rows before insert.
                before = _db_execute_with_retry(
                    conn,
                    "SELECT COUNT(*) AS n FROM bitunix_bar_history "
                    "WHERE symbol=? AND timeframe=?",
                    (wire, tf),
                ).fetchone()["n"]

                print(
                    f"{wire} {tf}: fetching up to {btc_count} bars "
                    f"since {btc_oldest_ts} ..."
                )
                klines = await _bitunix_kline_fetcher(
                    wire, tf, since_ms=btc_oldest_ts, limit=btc_count
                )

                if not klines:
                    print(f"{wire} {tf}: BTC_target={btc_count} fetched=0 inserted=0 total={before}")
                    continue

                insert_rows = [
                    (
                        wire,
                        int(k[_I_TS]),
                        tf,
                        float(k[_I_OPEN]),
                        float(k[_I_HIGH]),
                        float(k[_I_LOW]),
                        float(k[_I_CLOSE]),
                        float(k[_I_VOLUME]),
                        now_iso,
                    )
                    for k in klines
                ]

                _executemany_with_retry(
                    conn,
                    "INSERT OR IGNORE INTO bitunix_bar_history "
                    "(symbol, ts_ms, timeframe, open, high, low, close, volume, inserted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    insert_rows,
                )
                conn.commit()

                after = _db_execute_with_retry(
                    conn,
                    "SELECT COUNT(*) AS n FROM bitunix_bar_history "
                    "WHERE symbol=? AND timeframe=?",
                    (wire, tf),
                ).fetchone()["n"]

                print(
                    f"{wire} {tf}: BTC_target={btc_count} "
                    f"fetched={len(klines)} "
                    f"inserted={after - before} "
                    f"total={after}"
                )


if __name__ == "__main__":
    asyncio.run(main())
