# pm_legaudit_clear.ps1 - board-authorized 2026-10-07. Reclassifies the 2 venue-verified CS2 code_review rows
# (KXCS2GAME-26OCT070800M80TS-TS, TS=Team Spirit confirmed via Kalshi yes_sub_title) to 'ok:code_alias'
# so the "LEG AUDITS TO REVIEW" banner clears. GUARDED: fresh backup, scoped UPDATE, rowcount==2 or ROLLBACK,
# verify, engine PID/NRestarts unchanged. Writes ONLY prediction_markets.db; never legacy trading_corp.db; no restart.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$cmd = @'
PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
BK=/home/azureuser/pm_legaudit_clear_backup_$(date -u +%Y%m%dT%H%M%SZ).db
TICK=KXCS2GAME-26OCT070800M80TS-TS
echo "=== df / ==="; df -h / | tail -1
echo -n "engine BEFORE MainPID/NRestarts: "; systemctl show trading-corp -p MainPID -p NRestarts --value | tr "\n" " "; echo
cd "$ROOT" || exit 2
PYTHONPATH=. "$PY" - "$PMDB" "$BK" "$TICK" <<'PY'
import sys, os, sqlite3
from trading_corp.prediction_markets import leg_audit as LA
PMDB, BK, TICK = sys.argv[1], sys.argv[2], sys.argv[3]
NEW="ok:code_alias"
print("=== PRE-CHECK: ALL non-CLEAN leg_audit rows (RO) ===")
ro=sqlite3.connect("file:%s?mode=ro"%PMDB, uri=True); ro.row_factory=sqlite3.Row
nonclean=[]
for r in ro.execute("select id,account_id,category,ticker,outcome_leg,outcome_status,leg_audit from pm_subdivision_order where leg_audit is not null and leg_audit<>'' order by id"):
    st=LA.classify_leg_audit(r["leg_audit"])
    if st!=LA.STATE_CLEAN:
        nonclean.append(dict(r)); print("  id=%s %s/%s %s leg=%s %s state=%s audit=%r"%(r["id"],r["account_id"],r["category"],r["ticker"],r["outcome_leg"],r["outcome_status"],st,r["leg_audit"]))
print("TOTAL non-clean rows:",len(nonclean))
target=[d["id"] for d in nonclean if d["ticker"]==TICK and str(d["leg_audit"]).startswith("code_review")]
other=[d for d in nonclean if not (d["ticker"]==TICK and str(d["leg_audit"]).startswith("code_review"))]
print("target (CS2 ticker code_review) ids:",target)
if other:
    print("OTHER non-clean rows NOT cleared (reported, left as-is):")
    for d in other: print("   id=%s %s %s %r"%(d["id"],d["account_id"],d["ticker"],d["leg_audit"]))
ro.close()
if not target: print("ABORT: no CS2 code_review rows to clear."); sys.exit(3)
print("=== BACKUP (online, RO source) ===")
src=sqlite3.connect("file:%s?mode=ro"%PMDB, uri=True); dst=sqlite3.connect(BK)
try: src.backup(dst, pages=2000, sleep=0.05)
finally: dst.close(); src.close()
print("BACKUP:", BK, "size_bytes", os.path.getsize(BK))
print("=== WRITE: CS2 code_review -> %s (scoped to ticker; rowcount must be 2) ==="%NEW)
rw=sqlite3.connect(PMDB, timeout=30); rw.execute("pragma busy_timeout=30000")
cur=rw.execute("update pm_subdivision_order set leg_audit=? where ticker=? and leg_audit like 'code_review%'",(NEW,TICK))
n=cur.rowcount; print("rows updated:",n)
if n!=2: rw.rollback(); rw.close(); print("ABORT: expected exactly 2, got %s -> ROLLED BACK, no change."%n); sys.exit(4)
rw.commit(); print("COMMITTED")
print("=== VERIFY ===")
rw.row_factory=sqlite3.Row
for r in rw.execute("select id,ticker,outcome_leg,outcome_status,leg_audit from pm_subdivision_order where ticker=? order by id",(TICK,)):
    print("  id=%s %s leg=%s %s leg_audit=%r state=%s"%(r["id"],r["ticker"],r["outcome_leg"],r["outcome_status"],r["leg_audit"],LA.classify_leg_audit(r["leg_audit"])))
rem=0
for r in rw.execute("select leg_audit from pm_subdivision_order where leg_audit is not null and leg_audit<>''"):
    if LA.classify_leg_audit(r["leg_audit"])!=LA.STATE_CLEAN: rem+=1
print("non-clean leg_audit rows remaining (banner total):", rem)
print("quick_check:", rw.execute("pragma quick_check").fetchone()[0])
rw.close()
PY
echo "python rc=$?"
echo -n "engine AFTER MainPID/NRestarts: "; systemctl show trading-corp -p MainPID -p NRestarts --value | tr "\n" " "; echo
echo "=== DONE legaudit_clear ==="
'@
$cmd | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
