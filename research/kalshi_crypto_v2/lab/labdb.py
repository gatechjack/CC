"""Lab sqlite (SEPARATE from prod trading_corp.db) for S3-S5 historical modeling.
Raw-storage tables + features/labels/results/coverage. Gitignored. Idempotent DDL.
"""
from __future__ import annotations

import os
import sqlite3

LAB_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcv2_lab.db")

DDL = """
-- raw bars (store raw; derive at analysis time)
CREATE TABLE IF NOT EXISTS lab_bars_coinbase (
  asset TEXT, ts_ms INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY(asset, ts_ms));
CREATE TABLE IF NOT EXISTS lab_bars_binance (
  asset TEXT, ts_ms INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY(asset, ts_ms));
-- coinalyze long: metric in {price_o/h/l/c,vol,buy_vol,buy_trades,trades,oi_*,
--   funding_*,liq_long,liq_short,ls_ratio,long_pct,short_pct,...}. `interval`
--   dimension added S3: Coinalyze retains 1min only ~2d, 5min ~8d, 15min ~22d,
--   1hour = full period, so metrics are stored per available granularity.
CREATE TABLE IF NOT EXISTS lab_coinalyze (
  asset TEXT, ts_ms INTEGER, interval TEXT, metric TEXT, value REAL,
  PRIMARY KEY(asset, ts_ms, interval, metric));
-- raw kalshi market metadata (census + drives candle pull + S4-time labels)
CREATE TABLE IF NOT EXISTS lab_kalshi_markets (
  series TEXT, market_ticker TEXT PRIMARY KEY, event_ticker TEXT, asset TEXT,
  kind TEXT, market_type TEXT, strike_type TEXT,
  floor_strike REAL, cap_strike REAL,
  open_ts INTEGER, close_ts INTEGER, expiration_ts INTEGER,
  result TEXT, settlement_value REAL, last_price REAL, status TEXT,
  candles_pulled INTEGER DEFAULT 0);
-- ladder snapshot: all strikes of a SAMPLED hourly event at window-open (S5
-- Breeden-Litzenberger density source). ref_ts = the event open reference.
CREATE TABLE IF NOT EXISTS lab_kalshi_ladder_snap (
  asset TEXT, series TEXT, event_ticker TEXT, market_ticker TEXT,
  floor_strike REAL, cap_strike REAL, ref_ts INTEGER, snap_ts INTEGER,
  yes_bid REAL, yes_ask REAL, price_mean REAL, volume REAL, open_interest REAL,
  result TEXT, settlement_value REAL,
  PRIMARY KEY(market_ticker, ref_ts));
-- per-settled-market kalshi 1m candles (raw yes bid/ask OHLC + traded-price
-- OHLC + price mean/vol/oi). price_{open,high,low,close} = actual TRADED prints
-- (added for the EV forensic 2026-08-02; earlier builds stored only price_mean).
CREATE TABLE IF NOT EXISTS lab_kalshi_candles (
  series TEXT, market_ticker TEXT, end_period_ts INTEGER,
  yes_bid_open REAL, yes_bid_high REAL, yes_bid_low REAL, yes_bid_close REAL,
  yes_ask_open REAL, yes_ask_high REAL, yes_ask_low REAL, yes_ask_close REAL,
  price_open REAL, price_high REAL, price_low REAL, price_close REAL,
  price_mean REAL, volume REAL, open_interest REAL,
  PRIMARY KEY(market_ticker, end_period_ts));
-- engineered features (long)
CREATE TABLE IF NOT EXISTS lab_features (
  asset TEXT, window_ts INTEGER, feature TEXT, value REAL,
  PRIMARY KEY(asset, window_ts, feature));
-- labels under the S1-confirmed settlement rule
CREATE TABLE IF NOT EXISTS lab_labels (
  asset TEXT, series TEXT, window_ts INTEGER, market_ticker TEXT,
  strike REAL, settle REAL, result INTEGER, move_pct REAL, flat_flag INTEGER,
  PRIMARY KEY(asset, series, window_ts));
-- model/baseline results (dual EV, calibration inputs, kelly)
CREATE TABLE IF NOT EXISTS lab_results (
  model_version TEXT, split TEXT, asset TEXT, window_ts INTEGER, side TEXT,
  model_p REAL, market_p REAL, outcome INTEGER,
  ev_taker REAL, ev_maker REAL, maker_filled INTEGER, kelly_frac REAL,
  PRIMARY KEY(model_version, split, asset, window_ts));
CREATE TABLE IF NOT EXISTS lab_coverage (
  source TEXT, asset TEXT, rows INTEGER, min_ts INTEGER, max_ts INTEGER,
  gap_count INTEGER, note TEXT, PRIMARY KEY(source, asset));
CREATE INDEX IF NOT EXISTS lab_features_ts ON lab_features(asset, window_ts);
CREATE INDEX IF NOT EXISTS lab_labels_ts ON lab_labels(asset, window_ts);
"""


def connect(path: str = LAB_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")   # tolerate a concurrent writer
    return conn


def migrate(path: str = LAB_DB) -> list[str]:
    conn = connect(path)
    try:
        # S3 migration: lab_coinalyze gained an `interval` PK column. The old
        # schema table is empty pre-S3, so drop+recreate is lossless.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_coinalyze)")]
        if cols and "interval" not in cols:
            n = conn.execute("SELECT COUNT(*) FROM lab_coinalyze").fetchone()[0]
            if n == 0:
                conn.execute("DROP TABLE lab_coinalyze")
            else:
                raise RuntimeError(
                    f"lab_coinalyze has {n} rows on the pre-interval schema; "
                    "manual migration required (refusing to drop data)")
        conn.executescript(DDL)
        # EV-forensic migration (2026-08-02): traded-price OHLC columns are
        # additive; ADD COLUMN on an existing table is lossless (old rows get
        # NULL until the market is re-pulled). Idempotent.
        have = [r[1] for r in conn.execute("PRAGMA table_info(lab_kalshi_candles)")]
        for col in ("price_open", "price_high", "price_low", "price_close"):
            if col not in have:
                conn.execute(f"ALTER TABLE lab_kalshi_candles ADD COLUMN {col} REAL")
        conn.commit()
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lab_%' ORDER BY name")]
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"lab db: {LAB_DB}")
    print("tables:", migrate())
