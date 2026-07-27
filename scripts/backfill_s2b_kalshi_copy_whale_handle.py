#!/usr/bin/env python3
"""S2 fix (b) — one-time backfill: add structured `whale_handle` to extra_json
on pre-fix live kalshi_copy_trading round-trips (parsed from the free-text
rationale "copy entry: @HANDLE opened ...").

Why: autopause keys on json_extract(extra_json,'$.whale_handle') in
kalshi_round_trips. Before the S2 (b) resolver fix, settlement-path rows never
got a structured whale_handle (it lived only in extra_json.rationale), so
autopause was blind to all live history. The resolver fix covers rows going
FORWARD; this script covers the ~15 pre-fix live rows so autopause has history
immediately rather than blind for weeks.

SAFE BY DEFAULT: dry-run unless --apply is passed. Idempotent: only touches rows
where whale_handle is currently absent AND a handle is parseable from rationale.
Read-committed, short transaction, busy_timeout — safe against the live writer.

Usage (on prod):
  python3 scripts/backfill_s2b_kalshi_copy_whale_handle.py            # dry-run
  python3 scripts/backfill_s2b_kalshi_copy_whale_handle.py --apply    # commit
  (optional) --db /home/azureuser/trading_corp/data/trading_corp.db
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys

DEFAULT_DB = "/home/azureuser/trading_corp/data/trading_corp.db"
LIVE_EPOCH = "2026-07-01"  # kalshi_copy live go-live; scope backfill to live rows only
_HANDLE_RE = re.compile(r"copy entry:\s*@([^\s]+)\s+opened")


def parse_handle(rationale: str | None) -> str | None:
    if not rationale:
        return None
    m = _HANDLE_RE.search(rationale)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")

    rows = conn.execute(
        "SELECT id, ticker, entry_ts, extra_json FROM kalshi_round_trips "
        "WHERE division='kalshi_copy_trading' AND entry_ts>=? "
        "  AND (json_extract(extra_json,'$.whale_handle') IS NULL)",
        (LIVE_EPOCH,),
    ).fetchall()

    planned, skipped = [], []
    for r in rows:
        try:
            ej = json.loads(r["extra_json"]) if r["extra_json"] else {}
        except Exception:
            ej = {}
        handle = parse_handle(ej.get("rationale"))
        if not handle:
            skipped.append((r["id"], r["ticker"], "no-handle-in-rationale"))
            continue
        ej["whale_handle"] = handle
        planned.append((r["id"], r["ticker"], r["entry_ts"][:16], handle, json.dumps(ej, default=str)))

    print(f"candidate rows (live, handle-less): {len(rows)}")
    print(f"parseable (will backfill): {len(planned)} | unparseable (skipped): {len(skipped)}")
    for pid, tk, ts, h, _ in planned:
        print(f"  id={pid} {ts} {tk} -> whale_handle={h}")
    for sid, tk, why in skipped:
        print(f"  SKIP id={sid} {tk} ({why})")

    if not args.apply:
        print("\nDRY-RUN — no changes written. Re-run with --apply to commit.")
        conn.close()
        return 0

    n = 0
    try:
        for pid, _tk, _ts, _h, new_ej in planned:
            conn.execute("UPDATE kalshi_round_trips SET extra_json=? WHERE id=?", (new_ej, pid))
            n += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"ERROR — rolled back: {e!r}")
        conn.close()
        return 1
    print(f"\nAPPLIED — {n} rows updated with structured whale_handle.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
