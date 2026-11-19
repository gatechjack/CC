"""Guarded one-shot corrector: re-book closed MACE rungs at their ACTUAL fills.

PREPARED 2026-11-19 — DO NOT RUN without Jack. Operator-run only.

Input CSV (analysis_fix/mace_fill_corrections_2026-11-19.csv) was produced by the
read-only audit of every closed rung against its Robinhood order fills
(processed_premium / per-leg executions). Each row carries the OLD booked values
and the NEW actual values for credit_actual / exit_debit / realized_pnl.

Guards:
  * dry-run by default; writes require --apply.
  * backup FIRST: full `mace_rung` copied to mace_rung_backup_fill_<UTC> and the
    affected rows dumped to JSON beside the CSV.
  * idempotent: a row already at its NEW values is skipped; a row matching
    NEITHER old nor new values ABORTS the whole run (unexpected drift).
  * every UPDATE is pinned to the OLD values + status='closed' in the WHERE;
    changes() != 1 rolls back everything.
  * asserts the affected row count, prints per-row old -> new and PRE/POST totals.

Usage (on the box, engine state irrelevant — closed rows only):
  python3 scripts/mace_fill_corrections_runner.py \
      --db /home/azureuser/trading_corp/data/trading_corp.db \
      --csv analysis_fix/mace_fill_corrections_2026-11-19.csv          # dry-run
  ... --apply                                                          # write
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

EPS = 0.005


def feq(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < EPS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="write the corrections (default: dry-run, read-only)")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv, newline="", encoding="utf-8")))
    if not rows:
        print("csv empty — nothing to do")
        return 1

    uri = f"file:{args.db}?mode={'rw' if args.apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")   # engine may hold brief write locks

    def fetch(rid):
        return conn.execute(
            "SELECT status, credit_actual, exit_debit, realized_pnl "
            "FROM mace_rung WHERE rung_id=?", (rid,)).fetchone()

    pre_total = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung WHERE status='closed'"
    ).fetchone()[0]
    print(f"PRE  realized total (closed): {pre_total:.2f}")

    to_apply, already = [], []
    for r in rows:
        cur = fetch(r["rung_id"])
        if cur is None:
            print(f"ABORT: {r['rung_id']} not found"); return 2
        if cur["status"] != "closed":
            print(f"ABORT: {r['rung_id']} status={cur['status']} (expected closed)"); return 2
        if (feq(cur["credit_actual"], r["new_credit"])
                and feq(cur["exit_debit"], r["new_exit_debit"])
                and feq(cur["realized_pnl"], r["new_realized"])):
            already.append(r["rung_id"]); continue
        if not (feq(cur["credit_actual"], r["old_credit"])
                and feq(cur["exit_debit"], r["old_exit_debit"])
                and feq(cur["realized_pnl"], r["old_realized"])):
            print(f"ABORT: {r['rung_id']} matches neither old nor new values: "
                  f"db=({cur['credit_actual']},{cur['exit_debit']},{cur['realized_pnl']}) "
                  f"old=({r['old_credit']},{r['old_exit_debit']},{r['old_realized']})")
            return 2
        to_apply.append(r)

    assert len(to_apply) + len(already) == len(rows), (len(to_apply), len(already), len(rows))
    print(f"rows in csv: {len(rows)}  to apply: {len(to_apply)}  already applied: {len(already)}")
    for r in to_apply:
        print(f"  {r['rung_id']}: credit {r['old_credit']}->{r['new_credit']}  "
              f"exit {r['old_exit_debit']}->{r['new_exit_debit']}  "
              f"realized {r['old_realized']}->{r['new_realized']}")

    if not args.apply:
        print("DRY-RUN (no --apply): nothing written.")
        return 0
    if not to_apply:
        print("nothing to write.")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"mace_rung_backup_fill_{ts}"
    conn.execute(f"CREATE TABLE {backup} AS SELECT * FROM mace_rung")
    n_bak = conn.execute(f"SELECT count(*) FROM {backup}").fetchone()[0]
    dump_path = Path(args.csv).with_suffix(f".pre_{ts}.json")
    dump = [dict(fetch(r["rung_id"]), rung_id=r["rung_id"]) for r in to_apply]
    dump_path.write_text(json.dumps(dump, indent=1), encoding="utf-8")
    print(f"backup table {backup} ({n_bak} rows); affected-row dump {dump_path}")

    try:
        conn.execute("BEGIN")
        for r in to_apply:
            cur = conn.execute(
                "UPDATE mace_rung SET credit_actual=?, exit_debit=?, realized_pnl=? "
                "WHERE rung_id=? AND status='closed' "
                "AND abs(credit_actual - ?) < ? AND abs(exit_debit - ?) < ? "
                "AND abs(realized_pnl - ?) < ?",
                (float(r["new_credit"]), float(r["new_exit_debit"]), float(r["new_realized"]),
                 r["rung_id"], float(r["old_credit"]), EPS, float(r["old_exit_debit"]), EPS,
                 float(r["old_realized"]), EPS))
            if cur.rowcount != 1:
                raise RuntimeError(f"{r['rung_id']}: rowcount {cur.rowcount} != 1")
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001 — any miss rolls back everything
        conn.execute("ROLLBACK")
        print(f"ROLLED BACK: {exc}")
        return 3

    post_total = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung WHERE status='closed'"
    ).fetchone()[0]
    print(f"POST realized total (closed): {post_total:.2f}  "
          f"(delta {post_total - pre_total:+.2f}; applied {len(to_apply)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
