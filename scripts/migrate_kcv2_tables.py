"""Migration: create the 4 dedicated kalshi_crypto_v2 observer tables.

Operator-run at deploy (writes prod data/trading_corp.db). Idempotent
(IF NOT EXISTS). READ the DDL below before running. Raw-quote storage only;
no derived implieds. Join on market_ticker + cycle_id (never timestamp
tolerance). Conditions baked in: kcv2_quotes.band_pct (near-money band per row)
and kcv2_signals.computed_bar_ts_ms (bar the SFP state was computed from).

Usage:  run_capped python scripts/migrate_kcv2_tables.py [DB_PATH]
Default DB_PATH = $TRADING_CORP_DB_URL or data/trading_corp.db (sqlite path).
"""
from __future__ import annotations

import os
import sqlite3
import sys

DDL = """
CREATE TABLE IF NOT EXISTS kcv2_index_ticks (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms             INTEGER NOT NULL,     -- observer sample time (epoch ms)
  cycle_id          INTEGER NOT NULL,
  index_id          TEXT    NOT NULL,     -- BRTI / ETHUSD_RTI / SOLUSD_RTI / XRPUSD_RTI
  asset             TEXT    NOT NULL,     -- BTC/ETH/SOL/XRP
  value             REAL,                 -- raw cfbenchmarks index value
  avg60_value       REAL,                 -- trailing 60s average (settlement TWAP)
  avg60_window_size INTEGER,              -- readings in the 60s window (warms 0->60)
  received_at_ms    INTEGER               -- upstream received_at (epoch ms)
);
CREATE INDEX IF NOT EXISTS kcv2_index_ticks_ts       ON kcv2_index_ticks(ts_ms);
CREATE INDEX IF NOT EXISTS kcv2_index_ticks_asset_ts ON kcv2_index_ticks(asset, ts_ms);

CREATE TABLE IF NOT EXISTS kcv2_quotes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms         INTEGER NOT NULL,
  cycle_id      INTEGER NOT NULL,
  asset         TEXT    NOT NULL,
  cadence       TEXT    NOT NULL,         -- '15m' | 'hourly_ladder' | 'hourly_dir'
  series        TEXT    NOT NULL,
  event_ticker  TEXT,
  market_ticker TEXT    NOT NULL,
  floor_strike  REAL,
  index_value   REAL,                     -- index at sample (for moneyness)
  moneyness     REAL,                     -- (floor_strike - index_value)/index_value
  band_pct      REAL    NOT NULL,         -- near-money band cutoff USED this row (COND 1)
  yes_bid       REAL, yes_ask REAL, no_bid REAL, no_ask REAL,   -- RAW dollars 0-1
  last_price    REAL, volume REAL, open_interest REAL,
  status        TEXT,
  sum_to_1_ok   INTEGER NOT NULL          -- 1 if yes_ask+no_ask in [0.5,1.5] else 0 (LIVE)
);
CREATE INDEX IF NOT EXISTS kcv2_quotes_ts    ON kcv2_quotes(ts_ms);
CREATE INDEX IF NOT EXISTS kcv2_quotes_mkt   ON kcv2_quotes(market_ticker, ts_ms);
CREATE INDEX IF NOT EXISTS kcv2_quotes_cycle ON kcv2_quotes(cycle_id);

CREATE TABLE IF NOT EXISTS kcv2_signals (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms              INTEGER NOT NULL,    -- observer sample time
  cycle_id           INTEGER NOT NULL,
  asset              TEXT    NOT NULL,
  sfp_mode           TEXT,                -- REAL | CONSIDERABLE
  bos_tf             TEXT,                -- 15m
  state              TEXT    NOT NULL,    -- ARMED | CONFIRMED | NONE
  swept_swing_level  REAL, swept_low REAL, bos_ref_high REAL,
  computed_bar_ts_ms INTEGER              -- bar ts the SFP state was computed from (COND 2)
);
CREATE INDEX IF NOT EXISTS kcv2_signals_ts       ON kcv2_signals(ts_ms);
CREATE INDEX IF NOT EXISTS kcv2_signals_asset_ts ON kcv2_signals(asset, ts_ms);

CREATE TABLE IF NOT EXISTS kcv2_heartbeat (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms              INTEGER NOT NULL,
  cycle_id           INTEGER NOT NULL,
  rows_index         INTEGER, rows_quotes INTEGER, rows_signals INTEGER,
  n_markets_active   INTEGER,
  index_ws_connected INTEGER,
  alarm              INTEGER NOT NULL,    -- 1 if a category wrote 0 rows this cycle
  note               TEXT
);
CREATE INDEX IF NOT EXISTS kcv2_heartbeat_ts ON kcv2_heartbeat(ts_ms);
"""

KCV2_TABLES = ("kcv2_index_ticks", "kcv2_quotes", "kcv2_signals", "kcv2_heartbeat")


def resolve_db(argv: list[str]) -> str:
    if len(argv) > 1:
        return argv[1]
    url = os.getenv("TRADING_CORP_DB_URL", "data/trading_corp.db")
    return url.replace("sqlite:///", "").replace("sqlite://", "")


def main() -> int:
    db = resolve_db(sys.argv)
    print(f"DB: {db}")
    conn = sqlite3.connect(db)
    try:
        before = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.executescript(DDL)
        conn.commit()
        after = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        created = sorted(set(KCV2_TABLES) & (after - before))
        present = sorted(set(KCV2_TABLES) & after)
        print(f"created this run: {created or '(none — already present)'}")
        print(f"kcv2 tables present: {present}")
        for t in KCV2_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:20} rows={n}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
