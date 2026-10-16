#!/usr/bin/env python3
# READ-ONLY post-write verify (run AFTER Jack executes mace_xle_reconcile_write.ps1).
# Confirms the XLE 10-30 60/59/70/71 rung is booked closed with the right P&L, is gone from
# the open census, and restates that the RH-session reconcile error is a SEPARATE live issue.
import sqlite3
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"
DB = ROOT + "/data/trading_corp.db"
RID = "mace-XLE-2026-10-30-60-59-70-71-20260917"


def conn_ro():
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON;")
    return c


def dump(c, sql, params=()):
    cur = c.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print("cols:", cols, "rows:", len(rows))
    for i, r in enumerate(rows):
        print("  [%d] %s" % (i, {k: r[k] for k in cols}))
    return rows


print("MACE XLE RECONCILE VERIFY RO ::", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
c = conn_ro()
print("\n-- the reconciled rung (expect status=closed, exit_reason=manual, exit_debit=0.22, realized set):")
dump(c, "SELECT rung_id,status,exit_ts,exit_reason,exit_debit,realized_pnl,credit_actual,contracts "
        "FROM mace_rung WHERE rung_id=?", (RID,))
print("\n-- census (the rung must NOT appear under open/closing):")
dump(c, "SELECT status, COUNT(*) n, GROUP_CONCAT(DISTINCT symbol) syms FROM mace_rung GROUP BY status")
print("\n-- any remaining open/closing XLE 10-30 rung (expect none for this condor):")
dump(c, "SELECT rung_id,status FROM mace_rung WHERE symbol='XLE' AND expiry='2026-10-30' AND status!='closed'")
print("\n-- XLE realized P&L total (closed rungs) after booking:")
dump(c, "SELECT COUNT(*) closed_xle, ROUND(SUM(realized_pnl),2) xle_realized_sum "
        "FROM mace_rung WHERE symbol='XLE' AND status='closed'")
c.close()
print("\nNOTE: 'reconcile open orders error' is an RH-session/login failure (get_all_open_option_orders")
print("      can only be called when logged in). It is SEPARATE from this rung and is NOT cleared by")
print("      this booking -- it clears only when the box Robinhood session re-authenticates.")
print("\n===== END verify =====")
