"""TradingView CSV export ->SQLite ingester for the BTC scalping research DB.

Designed for the BitUnix-Phase-4 research pipeline: Bybit BTCUSDT.P bars
exported from TradingView with Otter + Vumanchu/Cypher + ATR + MACD +
Donchian + Bollinger Bands + CVD. Same indicator lineup across 1D / 4h /
3m timeframes; this script is the on-ramp into `data/btc_scalping.db`.

Design properties:
  - **Idempotent at file level**: re-running with an unchanged file is a
    no-op (sha256 match on `source_files`).
  - **Idempotent at row level**: re-running with an overlapping window
    UPSERTs by ts. Newer indicator values overwrite older ones for the
    same bar (TradingView occasionally repaints recent bars).
  - **Schema extension on the fly**: if a future export adds a new
    indicator column (e.g. you add Ichimoku to the chart later), we
    `ALTER TABLE ADD COLUMN` and back-fill nulls for older rows.
  - **Column-name sanitation**: TradingView header has duplicates
    (`Basis`, `Upper`, `Lower`, `EMA 8`), emojis, spaces, and a typo
    (`CVD (High` is missing its closing paren). Sanitized to
    snake_case; duplicates get numeric suffixes.

Usage:
    python scripts/ingest_tv_export.py <csv> [<csv> ...] [--db PATH] [--report]

Timeframe is auto-detected from the filename (`...1D...`, `...240...`,
`...3...`). Override with `--timeframe 1d|4h|3m`.

Bybit BTCUSDT.P is the canonical research source; data is read-only
for backtesting + signal-EDA work and does NOT feed `trading_corp.db`
or any prod runtime path.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "btc_scalping.db"

# Filename regex: TradingView exports look like
#   "BYBIT_BTCUSDT.P, 1D_64f1d.csv" / "..., 240_726fd.csv" / "..., 3_4ef61.csv"
# We pull the timeframe token from between the comma+space and the underscore.
_TF_FROM_FILENAME_RE = re.compile(r",\s*([0-9]+[A-Za-z]?)_[0-9a-f]+\.csv$")

_TIMEFRAME_ALIAS = {
    # TradingView token ->our canonical timeframe slug
    "1D": "1d",
    "D": "1d",
    "240": "4h",
    "60": "1h",
    "30": "30m",
    "15": "15m",
    "5": "5m",
    "3": "3m",
    "1": "1m",
}

_TABLE_FOR_TIMEFRAME = {
    "1d": "bars_1d",
    "4h": "bars_4h",
    "1h": "bars_1h",
    "30m": "bars_30m",
    "15m": "bars_15m",
    "5m": "bars_5m",
    "3m": "bars_3m",
    "1m": "bars_1m",
}

# ── Per-coin DB routing (Option A) ──────────────────────────────────────────
# One DB per coin: data/<coin>_scalping.db. The bars_<tf> tables are IDENTICAL in
# schema across coins (the coin lives in the DB filename, never the table name),
# so the p6 harness reads each coin's DB exactly as it reads BTC's. A coin guard
# (CSV symbol vs target-DB coin) prevents accidentally writing one coin's bars
# into another coin's DB (a ts-PK collision that would silently corrupt it).
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "PERP")


def _detect_coin_from_csv(filename: str) -> str | None:
    """TV export 'BYBIT_SOLUSDT.P, 15_<hash>.csv' -> 'sol'. None if unrecognized."""
    m = re.match(r"^[A-Za-z0-9]+_([A-Za-z0-9]+)\.P\s*,", filename)
    if not m:
        return None
    return _canon_coin(m.group(1))


def _canon_coin(token: str) -> str | None:
    """'SOLUSDT' / 'sol' / 'BTCUSDT.P' -> 'sol'/'btc'. Strips a quote suffix."""
    base = re.sub(r"\.P$", "", token.strip().upper())
    for q in _QUOTE_SUFFIXES:
        if base.endswith(q) and len(base) > len(q):
            base = base[: -len(q)]
            break
    return base.lower() or None


def _coin_from_db_path(db_path) -> str | None:
    """'data/sol_scalping.db' -> 'sol'. None if the name isn't <coin>_scalping.db."""
    m = re.match(r"([a-z0-9]+)_scalping\.db$", Path(db_path).name.lower())
    return m.group(1) if m else None


def _detect_timeframe(filename: str) -> str | None:
    m = _TF_FROM_FILENAME_RE.search(filename)
    if not m:
        return None
    tok = m.group(1).upper()
    return _TIMEFRAME_ALIAS.get(tok)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sanitize_one(name: str) -> str:
    """Lowercase + snake_case + strip non-ASCII (emojis, em-dashes)."""
    # Strip anything outside basic ASCII letters/digits/space/underscore/parens
    s = re.sub(r"[^A-Za-z0-9_ ()]+", "", name)
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"\s+", "_", s.strip())
    s = s.lower()
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "col"


def sanitize_columns(raw: list[str]) -> list[str]:
    """Sanitize + disambiguate duplicate column names.

    Duplicates get numeric suffixes (`_1`, `_2`) starting at the second
    occurrence -- first occurrence keeps the bare name to minimize churn
    when columns later become unique-by-default.
    """
    cleaned = [_sanitize_one(n) for n in raw]
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cleaned:
        n = seen.get(c, 0)
        seen[c] = n + 1
        out.append(c if n == 0 else f"{c}_{n + 1}")
    return out


def _utc_iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")


def _ensure_meta_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS source_files (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          filename          TEXT    NOT NULL,
          sha256            TEXT    NOT NULL UNIQUE,
          timeframe         TEXT    NOT NULL,
          ingested_at       TEXT    NOT NULL,
          row_count         INTEGER NOT NULL,
          ts_min            INTEGER NOT NULL,
          ts_max            INTEGER NOT NULL,
          rows_inserted     INTEGER NOT NULL,
          rows_updated      INTEGER NOT NULL,
          new_columns_added TEXT
        )
        """
    )


def _ensure_bars_table(con: sqlite3.Connection, table: str, columns: list[str]) -> list[str]:
    """Create the bars table if missing; ALTER ADD COLUMN any new ones.

    Returns the list of column names that were newly added on this call.
    """
    existing = {
        row[1]
        for row in con.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if not existing:
        # Build CREATE TABLE. ts is PK, datetime_utc is convenience.
        col_defs = ["ts INTEGER PRIMARY KEY", "datetime_utc TEXT NOT NULL"]
        for c in columns:
            if c == "ts" or c == "datetime_utc":
                continue
            col_defs.append(f'"{c}" REAL')
        con.execute(f'CREATE TABLE "{table}" ({", ".join(col_defs)})')
        return []

    added: list[str] = []
    for c in columns:
        if c in existing or c in ("ts", "datetime_utc"):
            continue
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" REAL')
        added.append(c)
    return added


def _upsert_rows(
    con: sqlite3.Connection,
    table: str,
    df: pd.DataFrame,
) -> tuple[int, int]:
    """UPSERT rows keyed on `ts`. Returns (inserted, updated)."""
    cols = list(df.columns)
    placeholders = ", ".join(["?"] * len(cols))
    quoted_cols = ", ".join(f'"{c}"' for c in cols)
    update_clause = ", ".join(
        f'"{c}"=excluded."{c}"' for c in cols if c != "ts"
    )
    sql = (
        f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders}) '
        f"ON CONFLICT(ts) DO UPDATE SET {update_clause}"
    )

    # Pre-count which ts values already exist so we can split insert vs update.
    ts_values = df["ts"].astype(int).tolist()
    placeholders_ts = ",".join("?" * len(ts_values))
    existing_ts = {
        row[0]
        for row in con.execute(
            f'SELECT ts FROM "{table}" WHERE ts IN ({placeholders_ts})',
            ts_values,
        ).fetchall()
    }

    rows = [tuple(None if pd.isna(v) else v for v in r) for r in df.itertuples(index=False, name=None)]
    con.executemany(sql, rows)

    inserted = sum(1 for ts in ts_values if ts not in existing_ts)
    updated = len(ts_values) - inserted
    return inserted, updated


def ingest_file(
    con: sqlite3.Connection,
    csv_path: Path,
    timeframe_override: str | None = None,
) -> dict:
    """Ingest one TradingView CSV export. Returns a result dict for reporting."""
    filename = csv_path.name
    sha = _sha256_file(csv_path)

    # Dedup at file level
    existing = con.execute(
        "SELECT id, ingested_at FROM source_files WHERE sha256 = ?",
        (sha,),
    ).fetchone()
    if existing:
        return {
            "filename": filename,
            "status": "skipped_duplicate",
            "sha256": sha,
            "previously_ingested_at": existing[1],
        }

    tf = timeframe_override or _detect_timeframe(filename)
    if tf is None:
        raise ValueError(
            f"Could not detect timeframe from {filename!r}. "
            f"Pass --timeframe explicitly."
        )
    if tf not in _TABLE_FOR_TIMEFRAME:
        raise ValueError(f"Unsupported timeframe {tf!r} for {filename!r}.")

    table = _TABLE_FOR_TIMEFRAME[tf]

    # Read the raw header line ourselves so we can dedup duplicate names
    # before pandas does its `Basis.1`, `Basis.2` auto-rename (which our
    # regex would then collapse to `basis1`/`basis2`, losing the underscore
    # convention). Pass the deduped names via `names=` + `header=0`.
    with csv_path.open() as f:
        raw_cols = next(f).rstrip("\n\r").split(",")
    clean_cols = sanitize_columns(raw_cols)
    df_raw = pd.read_csv(csv_path, names=clean_cols, header=0)

    # `time` column is Unix seconds ->standardize as `ts`. Add convenience UTC ISO.
    if "time" not in clean_cols:
        raise ValueError(f"{filename!r} has no `time` column; got {raw_cols[:5]!r}...")
    # Guard the core OHLC schema so a malformed dump fails LOUDLY rather than
    # creating a table the p6 harness (SELECT ts,open,high,low,close) can't read.
    missing_ohlc = [c for c in ("open", "high", "low", "close") if c not in clean_cols]
    if missing_ohlc:
        raise ValueError(
            f"{filename!r} missing OHLC column(s) {missing_ohlc}; got {clean_cols[:8]}..."
        )
    df_raw = df_raw.rename(columns={"time": "ts"})
    df_raw["ts"] = df_raw["ts"].astype("int64")
    df_raw = df_raw.dropna(subset=["ts"])
    df_raw["datetime_utc"] = df_raw["ts"].apply(_utc_iso)

    # Reorder so ts/datetime_utc lead -- purely cosmetic for INSERT readability.
    lead = ["ts", "datetime_utc"]
    rest = [c for c in df_raw.columns if c not in lead]
    df = df_raw[lead + rest]

    new_cols = _ensure_bars_table(con, table, list(df.columns))
    inserted, updated = _upsert_rows(con, table, df)

    ts_min = int(df["ts"].min())
    ts_max = int(df["ts"].max())
    con.execute(
        """
        INSERT INTO source_files (
          filename, sha256, timeframe, ingested_at,
          row_count, ts_min, ts_max,
          rows_inserted, rows_updated, new_columns_added
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            sha,
            tf,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            len(df),
            ts_min,
            ts_max,
            inserted,
            updated,
            ",".join(new_cols) if new_cols else None,
        ),
    )
    return {
        "filename": filename,
        "status": "ingested",
        "timeframe": tf,
        "table": table,
        "row_count": len(df),
        "ts_min": ts_min,
        "ts_max": ts_max,
        "ts_min_iso": _utc_iso(ts_min),
        "ts_max_iso": _utc_iso(ts_max),
        "inserted": inserted,
        "updated": updated,
        "new_columns_added": new_cols,
        "raw_to_clean": dict(zip(raw_cols, clean_cols)),
    }


def print_data_quality_report(con: sqlite3.Connection) -> None:
    print("\n" + "=" * 76)
    print("DATA QUALITY REPORT -- data/btc_scalping.db")
    print("=" * 76)

    tables = [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bars_%'"
        ).fetchall()
    ]
    if not tables:
        print("(no bars tables yet)")
        return

    for t in sorted(tables):
        n_rows = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        if n_rows == 0:
            print(f"\n[{t}] empty")
            continue
        ts_min, ts_max = con.execute(
            f'SELECT MIN(ts), MAX(ts) FROM "{t}"'
        ).fetchone()
        cols = [
            row[1]
            for row in con.execute(f"PRAGMA table_info({t})").fetchall()
        ]
        n_cols = len(cols)

        print(f"\n[{t}]  rows={n_rows:>6,}  cols={n_cols}  "
              f"range={_utc_iso(ts_min)} ->{_utc_iso(ts_max)}")

        # NaN distribution per non-OHLCV column -- surface columns with > 50% null.
        nan_summary = []
        for c in cols:
            if c in ("ts", "datetime_utc", "open", "high", "low", "close", "volume"):
                continue
            null_count = con.execute(
                f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NULL'
            ).fetchone()[0]
            pct = 100.0 * null_count / n_rows
            nan_summary.append((c, null_count, pct))

        # Sort by null %, descending. Print top 10 (most-sparse signal columns).
        nan_summary.sort(key=lambda x: -x[2])
        sparsest = [r for r in nan_summary if r[2] > 50.0]
        if sparsest:
            print(f"  sparse columns (>50% NULL -- typical for divergence/signal flags):")
            for c, n, pct in sparsest[:10]:
                print(f"    {c:<40s} {n:>6,} null ({pct:5.1f}%)")
            if len(sparsest) > 10:
                print(f"    ... and {len(sparsest) - 10} more sparse columns")

        # Bar-cadence audit: detect gaps. Expected stride from table name.
        stride_seconds = {
            "bars_1m": 60,
            "bars_3m": 180,
            "bars_5m": 300,
            "bars_15m": 900,
            "bars_1h": 3600,
            "bars_4h": 14400,
            "bars_1d": 86400,
        }.get(t)
        if stride_seconds:
            ts_list = [
                row[0]
                for row in con.execute(
                    f'SELECT ts FROM "{t}" ORDER BY ts'
                ).fetchall()
            ]
            gaps = []
            for i in range(1, len(ts_list)):
                delta = ts_list[i] - ts_list[i - 1]
                if delta != stride_seconds:
                    gaps.append((ts_list[i - 1], ts_list[i], delta))
            if gaps:
                print(f"  cadence: {len(gaps)} gap(s) vs expected {stride_seconds}s stride")
                for a, b, d in gaps[:5]:
                    n_missing = (d // stride_seconds) - 1
                    print(f"    {_utc_iso(a)} ->{_utc_iso(b)}  "
                          f"({d}s, ~{n_missing} bars missing)")
                if len(gaps) > 5:
                    print(f"    ... and {len(gaps) - 5} more gaps")
            else:
                print(f"  cadence: clean -- no gaps at expected {stride_seconds}s stride")

    # source_files summary
    print("\n[source_files]")
    rows = con.execute(
        "SELECT filename, timeframe, row_count, rows_inserted, rows_updated, "
        "ingested_at, new_columns_added FROM source_files ORDER BY id"
    ).fetchall()
    for r in rows:
        new_cols = f"  +cols: {r[6]}" if r[6] else ""
        print(f"  {r[0]}  tf={r[1]}  rows={r[2]:>5}  "
              f"+ins={r[3]:>5}  +upd={r[4]:>5}  at={r[5]}{new_cols}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("csv", nargs="+", help="One or more TradingView CSV exports")
    parser.add_argument("--symbol", default=None,
                        help="Coin slug (btc|sol|eth|xrp) -> data/<coin>_scalping.db "
                             "when --db is omitted (Option A: one DB per coin)")
    parser.add_argument("--db", default=None,
                        help="SQLite DB path. Overrides --symbol. Default when neither "
                             f"--db nor --symbol is given: {DEFAULT_DB}")
    parser.add_argument("--timeframe", default=None,
                        help="Override timeframe detection (1d|4h|3m|...)")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the CSV<->DB coin-mismatch guard (use with care)")
    parser.add_argument("--report", action="store_true",
                        help="Print data quality report after ingestion")
    args = parser.parse_args(argv)

    # Resolve the target DB (Option A — one DB per coin). Explicit --db wins;
    # else --symbol -> data/<coin>_scalping.db; else the BTC default (back-compat).
    if args.db:
        db_path = Path(args.db)
    elif args.symbol:
        coin = _canon_coin(args.symbol) or args.symbol.lower()
        db_path = REPO_ROOT / "data" / f"{coin}_scalping.db"
    else:
        db_path = DEFAULT_DB
    target_coin = _coin_from_db_path(db_path)

    # Pre-flight BEFORE opening/creating the DB, so a refused run leaves NO file
    # behind: every CSV must exist and its coin must match the target DB's coin
    # (so a SOL dump can never even create, let alone corrupt, btc_scalping.db).
    for csv_arg in args.csv:
        csv_path = Path(csv_arg)
        if not csv_path.exists():
            print(f"!! NOT FOUND: {csv_arg}", file=sys.stderr)
            return 1
        csv_coin = _detect_coin_from_csv(csv_path.name)
        if target_coin and csv_coin and target_coin != csv_coin and not args.force:
            print(f"!! REFUSED: {csv_path.name} is {csv_coin.upper()} but target DB is "
                  f"{target_coin.upper()} ({db_path}). Use --symbol {csv_coin} "
                  f"(or --db data/{csv_coin}_scalping.db), or --force to override.",
                  file=sys.stderr)
            return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        _ensure_meta_table(con)

        for csv_arg in args.csv:
            csv_path = Path(csv_arg)
            try:
                result = ingest_file(con, csv_path, args.timeframe)
            except Exception as e:
                con.rollback()
                print(f"!! FAILED: {csv_path.name}: {e}", file=sys.stderr)
                return 1
            con.commit()
            if result["status"] == "skipped_duplicate":
                print(f"  [skip] {result['filename']}  (sha256 matches "
                      f"prior ingest at {result['previously_ingested_at']})")
            else:
                print(
                    f"  [ok]   {result['filename']}  ->{result['table']}  "
                    f"rows={result['row_count']}  +ins={result['inserted']}  "
                    f"+upd={result['updated']}  "
                    f"range={result['ts_min_iso']} ->{result['ts_max_iso']}"
                )
                if result["new_columns_added"]:
                    n = len(result["new_columns_added"])
                    sample = ", ".join(result["new_columns_added"][:5])
                    more = f" (+{n - 5} more)" if n > 5 else ""
                    print(f"           +cols ({n}): {sample}{more}")

        if args.report:
            print_data_quality_report(con)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
