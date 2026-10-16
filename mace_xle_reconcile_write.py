#!/usr/bin/env python3
# ===================================================================================
# RESERVED PROD-DB WRITE -- runs ONLY after Jack's explicit authorization.
# Books the MANUALLY-CLOSED XLE 2026-10-30 60/59/70/71 x2 rung as status=closed with
# correct history from Jack's 2026-10-13 manual fills. Guarded + idempotent + atomic backup.
# NO broker calls. See reports/mace/MACE_XLE_RECON_2026-10-15.md for the full arithmetic.
# ===================================================================================
import sqlite3, json, os, subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"
DB = ROOT + "/data/trading_corp.db"
RID = "mace-XLE-2026-10-30-60-59-70-71-20260917"

# --- BOOKING VALUES (recommended = MACE-formula-consistent; see report section P2) -----------
# Net close debit/contract from Jack's 4 fills: (0.08 buy 60P + 0.50 buy 70C) - (0.03 sell 59P
# + 0.33 sell 71C) = 0.58 - 0.36 = 0.22/contract = $44 total (2 contracts x 100).
EXIT_TS      = "2026-10-13T17:05:02+00:00"   # last of Jack's 4 manual fills (13:05:02 ET)
EXIT_REASON  = "manual"
EXIT_DEBIT   = 0.22                            # per-contract net close debit (same units as siblings)
REALIZED_PNL = 16.00                           # (credit_actual 0.30 - exit_debit 0.22) * 100 * 2
UPDATE_CREDIT_ACTUAL = None                    # leave entry credit as recorded (0.30). See ALT below.
# --- ALTERNATIVE (Jack's call): book RH's exact economic realized +18.00 ---------------------
#   Actual entry FILL credit was 0.31/ct ($62, order filled 0.01 better than its 0.30 limit).
#   To make Division PnL match RH exactly AND keep the row formula-consistent, set:
#       REALIZED_PNL = 18.00 ; UPDATE_CREDIT_ACTUAL = 0.31
#   (then realized == (0.31 - 0.22) * 100 * 2 == 18.00). Only change these two lines.
# --------------------------------------------------------------------------------------------

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
BK_DB  = "/home/azureuser/mace_xle_reconcile_backup_%s.db" % ts
BK_ROW = "/home/azureuser/mace_xle_reconcile_preimage_%s.json" % ts


def sh(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def keyvals(d, keys):
    return {k: d.get(k) for k in keys}


SHOW = ("rung_id", "status", "exit_ts", "exit_reason", "exit_debit", "realized_pnl",
        "credit_actual", "contracts")

print("== MACE XLE RECONCILE WRITE ==", ts)
print("DB :", DB)
print("RID:", RID)
print("SET: status=closed exit_ts=%s exit_reason=%s exit_debit=%s realized_pnl=%s credit_actual=%s"
      % (EXIT_TS, EXIT_REASON, EXIT_DEBIT, REALIZED_PNL,
         ("<unchanged>" if UPDATE_CREDIT_ACTUAL is None else UPDATE_CREDIT_ACTUAL)))

# 0) df-guard (backup lands on /home, same fs as DB)
dbsz = os.path.getsize(DB)
dfu = sh("df -Pk %s | tail -1" % os.path.dirname(DB)).stdout.split()
avail = (int(dfu[3]) * 1024) if len(dfu) >= 4 and dfu[3].isdigit() else 0
print("db_size=%d avail=%d" % (dbsz, avail))
if avail and avail < dbsz * 2:
    print("*** ABORT: <2x DB size free for backup ***"); raise SystemExit(2)

# 1) ATOMIC online backup (WAL-safe) BEFORE any write
src = sqlite3.connect(DB, timeout=60)
src.row_factory = sqlite3.Row
dst = sqlite3.connect(BK_DB)
with dst:
    src.backup(dst)
dst.close()
print("ATOMIC DB backup ->", BK_DB, "(size %d)" % os.path.getsize(BK_DB))

# 2) pre-image (read) + save to JSON
pre = src.execute("SELECT * FROM mace_rung WHERE rung_id=?", (RID,)).fetchone()
if pre is None:
    print("*** ABORT: rung not found ***"); src.close(); raise SystemExit(3)
pre_d = {k: pre[k] for k in pre.keys()}
with open(BK_ROW, "w") as f:
    json.dump(pre_d, f, indent=2, default=str)
print("pre-image row ->", BK_ROW)
print("PRE :", keyvals(pre_d, SHOW))

# 3) idempotency guard
if pre_d.get("status") == "closed":
    print(">>> IDEMPOTENT NO-OP: rung already status=closed. No write performed.")
    src.close(); raise SystemExit(0)

# 4) guarded UPDATE in a single transaction (WHERE ... AND status!='closed')
if UPDATE_CREDIT_ACTUAL is None:
    sql = ("UPDATE mace_rung SET status='closed', exit_ts=?, exit_reason=?, exit_debit=?, "
           "realized_pnl=? WHERE rung_id=? AND status!='closed'")
    params = (EXIT_TS, EXIT_REASON, EXIT_DEBIT, REALIZED_PNL, RID)
else:
    sql = ("UPDATE mace_rung SET status='closed', exit_ts=?, exit_reason=?, exit_debit=?, "
           "realized_pnl=?, credit_actual=? WHERE rung_id=? AND status!='closed'")
    params = (EXIT_TS, EXIT_REASON, EXIT_DEBIT, REALIZED_PNL, UPDATE_CREDIT_ACTUAL, RID)

cur = src.execute(sql, params)
print("rows updated:", cur.rowcount)
if cur.rowcount != 1:
    print("*** ROLLBACK: expected exactly 1 updated row, got %d ***" % cur.rowcount)
    src.rollback(); src.close(); raise SystemExit(4)
src.commit()

# 5) post-image verify
post = src.execute("SELECT * FROM mace_rung WHERE rung_id=?", (RID,)).fetchone()
post_d = {k: post[k] for k in post.keys()}
print("POST:", keyvals(post_d, SHOW))
assert post_d["status"] == "closed" and abs((post_d["realized_pnl"] or 0) - REALIZED_PNL) < 1e-6
print(">>> DONE. Rung booked CLOSED. Backups: DB=%s  row=%s" % (BK_DB, BK_ROW))
print(">>> NOTE: the 'reconcile open orders error' is an RH-session/login failure (separate);")
print(">>>       this write does NOT clear it -- it clears only the stuck-rung open/active display.")
src.close()
