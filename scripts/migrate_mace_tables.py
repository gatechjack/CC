#!/usr/bin/env python3
"""MACE migration: create the net-new MACE tables/columns on a target DB.

Plan: planning/mace_v1_plan.md § DB / Phase 1. The tables (`mace_rung`,
`mace_equity_snapshot`, `mace_iv_history`, `economic_event`) live in the
canonical `persistence/db.py` SCHEMA as `CREATE TABLE IF NOT EXISTS`, so
this migration simply invokes `db.init_db()` — the SAME idempotent DDL the
engine runs at every boot (no divergence, single source of truth) — then
verifies the expected tables + indexes + the idempotent-re-seed UNIQUE
landed, and reports. Running it twice is a no-op.

Rollback: none needed — the tables are inert with no reader/writer until the
MACE division ships. Dropping them is safe if ever desired.

Checkpoint-1 procedure runs this against a COPY of the prod DB (NOT live prod):
    python scripts/migrate_mace_tables.py --db sqlite:///data/prod_copy.db
Read-only pre-check (no writes, safe on any DB incl. live):
    python scripts/migrate_mace_tables.py --db sqlite:///data/trading_corp.db --verify-only

Usage:
    python scripts/migrate_mace_tables.py
    python scripts/migrate_mace_tables.py --db sqlite:///data/trading_corp.db
    python scripts/migrate_mace_tables.py --verify-only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trading_corp.persistence import db as _db

MACE_TABLES = (
    "mace_rung",
    "mace_equity_snapshot",
    "mace_iv_history",
    "mace_rung_live",       # UI rebuild 2026-08-14 — volatile per-rung live-state snapshot
    "economic_event",
)
# Columns added by _maybe_add_column (idempotent ALTER, not in CREATE TABLE) that
# the verify must also confirm on an upgraded prod DB.
MACE_COLUMNS = (
    ("mace_rung", "entry_atm_iv"),   # A3 — durable per-rung entry ATM IV
)
MACE_INDEXES = (
    "ix_mace_rung_symbol_status",
    "ix_mace_rung_symbol_week",
    "ix_mace_rung_status",
    "ix_mace_rung_exit",
    "ix_economic_event_date",
)


def _present(conn: sqlite3.Connection, kind: str, names: tuple[str, ...]) -> dict[str, bool]:
    have = {
        r[0]
        for r in conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='{kind}'"
        ).fetchall()
    }
    return {n: (n in have) for n in names}


def _columns_present(conn: sqlite3.Connection) -> dict[str, bool]:
    """Confirm _maybe_add_column migrations landed (upgraded prod DBs)."""
    out: dict[str, bool] = {}
    for table, col in MACE_COLUMNS:
        try:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            cols = set()
        out[f"{table}.{col}"] = col in cols
    return out


def _economic_event_has_unique(conn: sqlite3.Connection) -> bool:
    """The idempotent-re-seed UNIQUE(event_type,event_date,symbol_scope) is
    load-bearing (weekly re-seeds must not duplicate). SQLite implements it as
    an auto-index with origin 'u'."""
    try:
        return any(
            row[3] == "u"  # (seq, name, unique, origin, partial)
            for row in conn.execute("PRAGMA index_list(economic_event)")
        )
    except sqlite3.OperationalError:
        return False  # table absent


def verify(db_url: str) -> bool:
    path = _db.resolve_db_path(db_url)
    if not path.exists():
        print(f"[verify] DB does not exist: {path}")
        return False
    conn = sqlite3.connect(path)
    try:
        tabs = _present(conn, "table", MACE_TABLES)
        idxs = _present(conn, "index", MACE_INDEXES)
        cols = _columns_present(conn)
        uniq = _economic_event_has_unique(conn)
    finally:
        conn.close()

    print(f"[verify] DB: {path}")
    for name, ok in tabs.items():
        print(f"  table  {'OK ' if ok else 'MISS'}  {name}")
    for name, ok in idxs.items():
        print(f"  index  {'OK ' if ok else 'MISS'}  {name}")
    for name, ok in cols.items():
        print(f"  column {'OK ' if ok else 'MISS'}  {name}")
    print(f"  unique {'OK ' if uniq else 'MISS'}  economic_event(event_type,event_date,symbol_scope)")

    all_ok = all(tabs.values()) and all(idxs.values()) and all(cols.values()) and uniq
    print(f"[verify] {'PASS' if all_ok else 'FAIL'} — "
          f"{sum(tabs.values())}/{len(MACE_TABLES)} tables, "
          f"{sum(idxs.values())}/{len(MACE_INDEXES)} indexes, "
          f"{sum(cols.values())}/{len(MACE_COLUMNS)} columns, "
          f"unique={'yes' if uniq else 'no'}")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="sqlite:///data/trading_corp.db",
                    help="target DB URL or path (default: sqlite:///data/trading_corp.db)")
    ap.add_argument("--verify-only", action="store_true",
                    help="read-only: check tables/indexes without creating anything")
    args = ap.parse_args()

    if args.verify_only:
        return 0 if verify(args.db) else 1

    path = _db.resolve_db_path(args.db)
    print(f"[migrate] init_db (idempotent SCHEMA) on: {path}")
    _db.init_db(args.db)
    ok = verify(args.db)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
