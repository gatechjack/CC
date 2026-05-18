"""Load Bitunix 1m OHLCV JSON cache into `bars_1m` table of btc_scalping.db.

Used by the v1.1 v3-addendum backtest (Branch A: 1m trade-resolution
disambiguation of the bar-fidelity hypothesis). The 1m bars come from
Bitunix native REST (via scripts/fetch_bitunix_5m_history.py with
--interval 1m), NOT from a TradingView export of Bybit — so this table's
source venue differs from bars_3m / bars_15m (which were ingested from
a TV CSV of BYBIT_BTCUSDT.P via scripts/ingest_tv_export.py).

Bitunix vs Bybit basis is generally small for BTCUSDT.P but not zero;
this lineage is recorded in the v3 report addendum.

Schema is a strict OHLCV subset (7 columns) — none of the 90 indicator
columns that bars_3m / bars_15m carry. Future indicator augmentation of
bars_1m would need its own ingestion pipeline.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "btc_scalping.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS bars_1m (
    ts INTEGER PRIMARY KEY,
    datetime_utc TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL
)
"""


def ingest(cache_path: Path, db_path: Path) -> tuple[int, int, int]:
    """UPSERT rows from cache JSON into bars_1m. Returns
    (rows_in_cache, rows_in_db_for_window, rows_inserted_or_updated)."""
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    rows: list[tuple] = []
    for b in cache:
        ts_dt = datetime.fromisoformat(b["ts"])
        ts_s = int(ts_dt.timestamp())
        rows.append((
            ts_s,
            ts_dt.astimezone(timezone.utc).isoformat(),
            float(b["open"]), float(b["high"]), float(b["low"]),
            float(b["close"]), float(b.get("volume", 0.0)),
        ))
    con = sqlite3.connect(db_path)
    try:
        con.execute(SCHEMA)
        cur = con.cursor()
        cur.executemany(
            "INSERT INTO bars_1m (ts, datetime_utc, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(ts) DO UPDATE SET "
            "  datetime_utc=excluded.datetime_utc, open=excluded.open, "
            "  high=excluded.high, low=excluded.low, close=excluded.close, "
            "  volume=excluded.volume",
            rows,
        )
        con.commit()
        n_total = cur.execute("SELECT COUNT(*) FROM bars_1m").fetchone()[0]
        if rows:
            mn, mx = rows[0][0], rows[-1][0]
            n_window = cur.execute(
                "SELECT COUNT(*) FROM bars_1m WHERE ts >= ? AND ts <= ?",
                (mn, mx),
            ).fetchone()[0]
        else:
            n_window = 0
    finally:
        con.close()
    return len(rows), n_window, n_total


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache", required=True,
                   help="Path to cache_ohlcv_bitunix_1m_*.json")
    p.add_argument("--db", default=str(DEFAULT_DB))
    args = p.parse_args()
    cache_path = Path(args.cache)
    db_path = Path(args.db)
    if not cache_path.exists():
        raise SystemExit(f"cache not found: {cache_path}")
    n_in, n_win, n_total = ingest(cache_path, db_path)
    print(f"Ingested {n_in} rows from {cache_path.name}")
    print(f"bars_1m has {n_win} rows in [min,max] of cache; "
          f"{n_total} rows total")


if __name__ == "__main__":
    main()
