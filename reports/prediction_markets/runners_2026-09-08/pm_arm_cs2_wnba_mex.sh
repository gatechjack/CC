set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
LEGACY=$ROOT/data/trading_corp.db
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo "### ARM cs2 + wnba + mex (WRITE) -- kalshi_jack + kalshi_karen = 6 subs -- $STAMP ###"
echo "### backup ~/pm_arm_backup_$STAMP.json ; refuses any LATCHED scope ; PRE/POST via read_arm_verdict ; NO restart ###"
echo
cd "$ROOT" && LEGACY_DB="$LEGACY" BK="$HOME/pm_arm_backup_$STAMP.json" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os,time,json
from trading_corp.prediction_markets import arm as ARM
LEG=os.environ["LEGACY_DB"]; BK=os.environ["BK"]
# arm the safe-to-fill ones (wnba: 0 open markets ; mex: whale empty) FIRST, then cs2 (the only "hot" one) LAST
TARGETS=[("kalshi_jack","wnba"),("kalshi_karen","wnba"),("kalshi_jack","mex"),("kalshi_karen","mex"),
         ("kalshi_jack","cs2"),("kalshi_karen","cs2")]

g=ARM.current_row(global_=True,legacy_db_path=LEG)
print("GLOBAL armed=%s latched=%s (per-sub arm becomes effective only if global armed)"%(bool(g and g.get("armed")),bool(g and g.get("latched"))))
pre={}
print("\n=== PRE ===")
for a,cat in TARGETS:
    r=ARM.current_row(a,cat,legacy_db_path=LEG); v=ARM.read_arm_verdict(a,cat,legacy_db_path=LEG)
    pre["%s/%s"%(a,cat)]=r
    print("   %-13s/%-4s effective_armed=%s latched=%s row=%s"%(a,cat,v.armed,bool(r and r.get("latched")),"present" if r else "ABSENT (never armed)"))
with open(BK,"w") as f: json.dump({k:(v if v else None) for k,v in pre.items()}, f)
print("   BACKUP: %s"%BK)

latched=[k for k,v in pre.items() if v and v.get("latched")]
if latched:
    print("\n*** ABORT: LATCHED scope(s) %s -- a human must ack (require_latch_clear); NOT arming. ***"%latched); raise SystemExit(1)

print("\n=== WRITE: arm() each (cold-start rows arm without a latch-clear flag) ===")
for a,cat in TARGETS:
    ARM.arm(a,cat,by="jack",source="cli",legacy_db_path=LEG)
    print("   armed %s/%s"%(a,cat))

print("\n=== POST-VERIFY ===")
allok=True
for a,cat in TARGETS:
    r=ARM.current_row(a,cat,legacy_db_path=LEG); v=ARM.read_arm_verdict(a,cat,legacy_db_path=LEG)
    ok=bool(v.armed and r and r.get("armed") and not r.get("latched"))
    allok=allok and ok
    print("   %-13s/%-4s effective_armed=%s row.armed=%s latched=%s -> %s"%(a,cat,v.armed,bool(r and r.get("armed")),bool(r and r.get("latched")),"OK" if ok else "*** NOT ARMED ***"))
g2=ARM.current_row(global_=True,legacy_db_path=LEG)
print("   GLOBAL still armed=%s latched=%s"%(bool(g2 and g2.get("armed")),bool(g2 and g2.get("latched"))))
print("\nRESULT: %s"%("OK -- all 6 subs ARMED + effective + no latch; global armed" if (allok and g2 and g2.get("armed")) else "*** CHECK FAILED -- inspect flags ***"))
print("NEXT: run cc/pm_fill_watch_ro.ps1 to read back the FIRST fill (jack/cs2 is the hot one). Wrong org/team/club/leg -> cc/pm_global_disarm.ps1 (FIRE FIRST).")
print("ROLLBACK per-sub: pm_cli live-disarm <account> <category> ; EMERGENCY ALL: cc/pm_global_disarm.ps1 ; backup %s"%BK)
PY
echo "### ARM DONE ###"
