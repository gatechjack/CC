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
-- coinalyze long: metric in {cvd_buy,cvd_sell,oi,funding,liq_long,liq_short,ls_ratio,...}
CREATE TABLE IF NOT EXISTS lab_coinalyze (
  asset TEXT, ts_ms INTEGER, metric TEXT, value REAL,
  PRIMARY KEY(asset, ts_ms, metric));
-- per-settled-market kalshi 1m candles (raw yes bid/ask OHLC + price/vol/oi)
CREATE TABLE IF NOT EXISTS lab_kalshi_candles (
  series TEXT, market_ticker TEXT, end_period_ts INTEGER,
  yes_bid_open REAL, yes_bid_high REAL, yes_bid_low REAL, yes_bid_close REAL,
  yes_ask_open REAL, yes_ask_high REAL, yes_ask_low REAL, yes_ask_close REAL,
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
    return conn


def migrate(path: str = LAB_DB) -> list[str]:
    conn = connect(path)
    try:
        conn.executescript(DDL)
        conn.commit()
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lab_%' ORDER BY name")]
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"lab db: {LAB_DB}")
    print("tables:", migrate())
