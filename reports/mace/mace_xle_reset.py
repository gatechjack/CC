#!/usr/bin/env python3
# XLE 2026-10-30 rung RESET: CLOSING -> open (RESERVED prod-DB write; run ONLY under Jack
# authorization, AFTER the mark-guard is deployed + boot-verified). Guarded + idempotent +
# backed up. The position was never closed (0 mace_exit_fill; empty-response blocked all
# attempts) so 'open' is the TRUE state. exit_ts/reason/debit/realized stay NULL.
import sqlite3, json
from datetime import datetime, timezone

DB = "/home/azureuser/trading_corp/data/trading_corp.db"
RID = "mace-XLE-2026-10-30-60-59-70-71-20260917"
SIB = "mace-XLE-2026-10-16-60-59-70-71-20260901"   # identical-strike shorter-dated sibling (sanity)
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
BK = "/home/azureuser/mace_xle_reset_backup_%s.json" % TS


def main():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row

    row = c.execute("SELECT * FROM mace_rung WHERE rung_id=?", (RID,)).fetchone()
    if row is None:
        print("ABORT: rung %s not found" % RID); return 2
    d = {k: row[k] for k in row.keys()}
    with open(BK, "w") as fh:
        fh.write(json.dumps(d, default=str))
    print("BACKUP written:", BK)
    print("PRE row: status=%s exit_ts=%s exit_reason=%s exit_debit=%s realized_pnl=%s contracts=%s" % (
        d.get("status"), d.get("exit_ts"), d.get("exit_reason"), d.get("exit_debit"),
        d.get("realized_pnl"), d.get("contracts")))

    # RO mark-state awareness (frozen while CLOSING) + sibling (should be the guard's arbitrage bound)
    for rid in (RID, SIB):
        lr = c.execute("SELECT rung_id,mark,spot,ts FROM mace_rung_live WHERE rung_id=?", (rid,)).fetchone()
        print("  mace_rung_live:", {k: lr[k] for k in lr.keys()} if lr else None)

    # GUARDS: only reset a genuinely-stuck CLOSING rung with NO booked exit (no half-set fields).
    if d.get("status") != "closing":
        print("ABORT: status is %r, not 'closing' (nothing to reset / already handled)" % d.get("status")); return 3
    if any(d.get(k) is not None for k in ("exit_ts", "exit_reason", "exit_debit", "realized_pnl")):
        print("ABORT: exit fields are set -> a real close was booked; do NOT reopen"); return 3

    cur = c.execute("UPDATE mace_rung SET status='open' WHERE rung_id=? AND status='closing'", (RID,))
    c.commit()
    print("UPDATE rows affected:", cur.rowcount)

    row2 = c.execute("SELECT status,exit_ts,exit_reason,exit_debit,realized_pnl FROM mace_rung "
                     "WHERE rung_id=?", (RID,)).fetchone()
    post = {k: row2[k] for k in row2.keys()}
    print("POST row:", post)
    ok = (post["status"] == "open" and post["exit_ts"] is None and post["exit_reason"] is None
          and post["exit_debit"] is None and post["realized_pnl"] is None)
    print("VERDICT:", "RESET OK - rung is clean-open (record preserved; guard holds if RH still bad)"
          if ok else "*** POST-STATE UNEXPECTED - inspect (backup at %s) ***" % BK)
    c.close()
    return 0 if ok else 4


raise SystemExit(main())
