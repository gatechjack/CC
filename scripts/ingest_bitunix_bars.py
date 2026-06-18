"""Native BitUnix OHLCV bar ingester -> data/btc_scalping.db (separate venue-tagged table).

Feeds REAL BitUnix-native bars (exported read-only from prod
`bitunix_bar_history`) into the backtest corpus as a SEPARATE, venue-tagged
table (`bars_3m_bitunix` by default) so the redeem-cap engine can run on
native data WITHOUT contaminating the frozen Bybit corpus (`bars_3m`).

Mirrors scripts/ingest_tv_export.py's design:
  - Idempotent at row level: ON CONFLICT(ts) DO UPDATE  (re-runnable / incremental;
    a re-extract with newer/repainted bars upserts cleanly).
  - Idempotent at file level: sha256 dedupe in `native_source_files`.
Differences vs the TV ingester:
  - OHLCV-only -- native klines carry NO signal columns (by design; the engine
    reads OHLCV from the DB and alerts from a separate JSON, see export_bitunix_alerts.py).
  - Every row is venue-tagged (`venue` column, default 'bitunix') so native and
    Bybit bars are never silently conflated.

SAFETY RAIL: writing a reserved Bybit corpus table name (bars_3m/bars_15m/...)
into the canonical corpus file `btc_scalping.db` is HARD-BLOCKED -- the Bybit
corpus stays frozen. A native-only *smoke* DB with a different filename MAY use
the `bars_3m` name (with --ensure-empty-15m) so the unmodified engine -- which
hardcodes the `bars_3m`/`bars_15m` table names -- can read native bars.

Input CSV columns (header required): ts,open,high,low,close,volume
  ts = Unix epoch SECONDS (bar-open). Produce via the read-only prod extract:
    scripts/native_etl/extract_bitunix_bars_3m.sql   (SELECT ts_ms/1000 AS ts, ...)

Usage:
  # durable artifact -- native table in the canonical corpus (Bybit bars_3m untouched):
  python scripts/ingest_bitunix_bars.py bars.csv --db /abs/data/btc_scalping.db --table bars_3m_bitunix
  # native-only smoke DB for the engine (aliases to bars_3m + empty bars_15m):
  python scripts/ingest_bitunix_bars.py bars.csv --db /abs/smoke.db --table bars_3m --ensure-empty-15m
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "btc_scalping.db"
CANONICAL_CORPUS_NAME = "btc_scalping.db"
RESERVED_BYBIT_TABLES = {
    "bars_1m", "bars_3m", "bars_5m", "bars_15m",
    "bars_30m", "bars_1h", "bars_4h", "bars_1d",
}
OHLCV = ("open", "high", "low", "close", "volume")


def _utc_iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_meta(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS native_source_files (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          filename      TEXT    NOT NULL,
          sha256        TEXT    NOT NULL UNIQUE,
          target_table  TEXT    NOT NULL,
          venue         TEXT    NOT NULL,
          ingested_at   TEXT    NOT NULL,
          row_count     INTEGER NOT NULL,
          ts_min        INTEGER NOT NULL,
          ts_max        INTEGER NOT NULL,
          rows_inserted INTEGER NOT NULL,
          rows_updated  INTEGER NOT NULL
        )
        """
    )


def _bars_ddl(table: str) -> str:
    return (
        f'CREATE TABLE IF NOT EXISTS "{table}" ('
        "ts INTEGER PRIMARY KEY, datetime_utc TEXT NOT NULL, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL, venue TEXT)"
    )


def _read_csv(csv_path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with csv_path.open(newline="") as f:
        rdr = csv.DictReader(f)
        missing = {"ts", *OHLCV} - set(rdr.fieldnames or [])
        if missing:
            raise ValueError(
                f"{csv_path.name} missing columns {sorted(missing)}; got {rdr.fieldnames}"
            )
        for r in rdr:
            ts = int(float(r["ts"]))
            rows.append((
                ts, _utc_iso(ts),
                float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), float(r["volume"]),
            ))
    return rows


def ingest(con: sqlite3.Connection, csv_path: Path, table: str, venue: str) -> dict:
    sha = _sha256_file(csv_path)
    dup = con.execute(
        "SELECT ingested_at FROM native_source_files WHERE sha256 = ?", (sha,)
    ).fetchone()
    if dup:
        return {"status": "skipped_duplicate", "previously_ingested_at": dup[0]}

    rows = _read_csv(csv_path)
    if not rows:
        raise ValueError(f"{csv_path.name} has no data rows")

    con.execute(_bars_ddl(table))
    ts_values = [r[0] for r in rows]
    qmarks = ",".join("?" * len(ts_values))
    existing_ts = {
        r[0] for r in con.execute(
            f'SELECT ts FROM "{table}" WHERE ts IN ({qmarks})', ts_values
        ).fetchall()
    }
    sql = (
        f'INSERT INTO "{table}" (ts, datetime_utc, open, high, low, close, volume, venue) '
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ts) DO UPDATE SET "
        "datetime_utc=excluded.datetime_utc, open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, volume=excluded.volume, venue=excluded.venue"
    )
    con.executemany(sql, [(*r, venue) for r in rows])

    inserted = sum(1 for ts in ts_values if ts not in existing_ts)
    updated = len(ts_values) - inserted
    ts_min, ts_max = min(ts_values), max(ts_values)
    con.execute(
        """
        INSERT INTO native_source_files
          (filename, sha256, target_table, venue, ingested_at,
           row_count, ts_min, ts_max, rows_inserted, rows_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (csv_path.name, sha, table, venue,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         len(rows), ts_min, ts_max, inserted, updated),
    )
    return {
        "status": "ingested", "table": table, "row_count": len(rows),
        "inserted": inserted, "updated": updated,
        "ts_min_iso": _utc_iso(ts_min), "ts_max_iso": _utc_iso(ts_max),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("csv", help="Native BitUnix bars CSV (ts,open,high,low,close,volume; ts=epoch s)")
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite DB path (default: {DEFAULT_DB})")
    p.add_argument("--table", default="bars_3m_bitunix", help="Target table (default: bars_3m_bitunix)")
    p.add_argument("--venue", default="bitunix", help="Venue tag for every row (default: bitunix)")
    p.add_argument("--ensure-empty-15m", action="store_true",
                   help="Also create an empty bars_15m so the unmodified engine (which loads "
                        "bars_15m unconditionally) can read a native-only smoke DB.")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    # SAFETY RAIL: never write a Bybit corpus table into the canonical corpus.
    if db_path.name == CANONICAL_CORPUS_NAME and args.table in RESERVED_BYBIT_TABLES:
        print(
            f"!! REFUSED: writing reserved table '{args.table}' into the canonical corpus "
            f"'{db_path.name}' is blocked (the Bybit corpus stays frozen). Use a separate "
            f"native table (e.g. bars_3m_bitunix) or a different DB file (smoke DB).",
            file=sys.stderr,
        )
        return 2

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"!! NOT FOUND: {csv_path}", file=sys.stderr)
        return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        _ensure_meta(con)
        if args.ensure_empty_15m:
            con.execute(_bars_ddl("bars_15m"))
        try:
            res = ingest(con, csv_path, args.table, args.venue)
        except Exception as e:
            con.rollback()
            print(f"!! FAILED: {csv_path.name}: {e}", file=sys.stderr)
            return 1
        con.commit()

    if res["status"] == "skipped_duplicate":
        print(f"  [skip] {csv_path.name} (sha256 matches prior ingest at "
              f"{res['previously_ingested_at']})")
    else:
        print(f"  [ok]   {csv_path.name} -> {res['table']}  rows={res['row_count']} "
              f"+ins={res['inserted']} +upd={res['updated']}  "
              f"range={res['ts_min_iso']} -> {res['ts_max_iso']}  venue={args.venue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
