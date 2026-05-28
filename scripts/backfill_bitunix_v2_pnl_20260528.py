"""One-shot backfill for bitunix paper_trade_record rows where
actual_pnl_dollars=0 because the v2 builder oversight left
expected_gain_if_tp_hit absent (see runbooks/deploy_log.md
2026-05-28 entry).

Eligible rows:
  division = 'bitunix_futures'
  result = 'win'
  actual_pnl_dollars = 0
  expected_gain IS NULL
  actual_r_multiple > 0       (positive partial-win R; loss/expired excluded)

Backfill value (v2 invariant: actual_pnl = max_dollar_risk * actual_r,
where max_dollar_risk = -expected_loss):
  actual_pnl_dollars = -expected_loss * actual_r_multiple

Prints BEFORE rows, performs the UPDATE in a single transaction, prints
AFTER rows. Read-only first run with --dry-run.

Usage:
  python scripts/backfill_bitunix_v2_pnl_20260528.py <db_path> [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys


SELECT_SQL = """
SELECT
    substr(order_id, 1, 12)              AS oid,
    substr(ts, 1, 19)                    AS ts,
    result,
    expected_loss,
    actual_r_multiple,
    actual_pnl_dollars
FROM paper_trade_record
WHERE division = 'bitunix_futures'
  AND result = 'win'
  AND actual_pnl_dollars = 0
  AND expected_gain IS NULL
  AND actual_r_multiple > 0
ORDER BY ts;
"""

UPDATE_SQL = """
UPDATE paper_trade_record
SET actual_pnl_dollars = (-expected_loss) * actual_r_multiple
WHERE division = 'bitunix_futures'
  AND result = 'win'
  AND actual_pnl_dollars = 0
  AND expected_gain IS NULL
  AND actual_r_multiple > 0;
"""


def _print_rows(rows: list[tuple], header: str) -> None:
    print(f"--- {header} ({len(rows)} rows) ---")
    for r in rows:
        oid, ts, result, eloss, ar, apnl = r
        print(
            f"{oid} | {ts} | {result} | el={eloss:.6f} | "
            f"aR={ar:.4f} | aPnL={apnl:.6f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    try:
        before = conn.execute(SELECT_SQL).fetchall()
        _print_rows(before, "BEFORE")
        if not before:
            print("No eligible rows; nothing to backfill.")
            return 0

        if args.dry_run:
            print(f"\n--- DRY-RUN: would UPDATE {len(before)} rows ---")
            for oid, _ts, _r, eloss, ar, _ap in before:
                projected = -eloss * ar
                print(f"  {oid}  -> actual_pnl_dollars = {projected:.6f}")
            return 0

        cur = conn.execute(UPDATE_SQL)
        conn.commit()
        rowcount = cur.rowcount

        after = conn.execute(SELECT_SQL).fetchall()
        _print_rows(after, "AFTER (should be 0 — backfill complete)")

        # Re-select the now-backfilled rows by relaxing the actual_pnl=0
        # predicate but keeping the rest.
        verify_sql = """
        SELECT
            substr(order_id, 1, 12),
            substr(ts, 1, 19),
            result,
            expected_loss,
            actual_r_multiple,
            actual_pnl_dollars
        FROM paper_trade_record
        WHERE division = 'bitunix_futures'
          AND result = 'win'
          AND expected_gain IS NULL
          AND actual_r_multiple > 0
        ORDER BY ts;
        """
        verified = conn.execute(verify_sql).fetchall()
        _print_rows(verified, f"VERIFIED (rowcount from UPDATE = {rowcount})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
