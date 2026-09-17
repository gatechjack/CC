set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
NEW=/tmp/live_driver_new.py
LD=trading_corp/prediction_markets/live_driver.py
LA=trading_corp/prediction_markets/leg_audit.py
LD_BASE=b9e67d1a7117191fa64b3e31290ec0f8; LD_TGT=15069d88dd4d69aea3df9aaece74bfdb
LA_TGT=61cfe84b094ab5b109452a18dc74f3bc
BK=/home/azureuser/pm_legaudit_engine_backup_$TS
m(){ md5sum "$1" | cut -d' ' -f1; }
echo "### PHASE 2 GRAFT (engine / live_driver.py; azureuser; base d9468361 -> target 9419c2d8) $TS ###"
echo "engine PID: $(systemctl show -p MainPID --value trading-corp 2>/dev/null) ; pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
[ -f "$NEW" ] || { echo "  ** delivered live_driver.py MISSING -- abort (0 changes)"; exit 2; }
la=$(m "$ROOT/$LA")
echo "  PRECONDITION pm_web-half: box leg_audit.py=$la  need=$LA_TGT"
[ "$la" = "$LA_TGT" ] || { echo "  ** STOP: PM_WEB HALF NOT DEPLOYED (leg_audit.py != target). HOLD -- do NOT graft or restart the engine until Phase 1 is grafted+restarted+verified. ABORT (0 changes)"; exit 3; }
# PROCESS-FRESHNESS (the gap: file-at-target is NOT proof pm_web is RUNNING that file). The pm_web
# restart must have happened AFTER leg_audit.py was written. Restart is confirmed ONLY by
# ActiveEnterTimestamp + PID, NEVER by an az/systemctl exit code (empty stdout is ambiguous -- a
# successful restart also prints nothing).
la_mtime=$(stat -c %Y "$ROOT/$LA" 2>/dev/null)
wpid=$(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)
wactive=$(systemctl show -p ActiveEnterTimestamp --value prediction-markets-web 2>/dev/null)
wepoch=$(date -d "$wactive" +%s 2>/dev/null)
echo "  PROCESS-FRESHNESS pm_web: PID=$wpid ActiveEnter=$wactive (epoch=${wepoch:-PARSE_FAIL}) vs leg_audit.py mtime=$la_mtime"
if [ -z "${wepoch:-}" ] || [ "$wepoch" -le "${la_mtime:-0}" ]; then
  echo "  ** STOP: pm_web is NOT running the deployed leg_audit.py (ActiveEnter <= file mtime => the classifier restart did not take). HOLD -- restart pm_web onto the new classifier first and confirm by PID change + ActiveEnter postdating the graft. ABORT (0 changes)"; exit 8
fi
echo "  -> process-freshness OK: pm_web restarted AFTER leg_audit.py was deployed (running the new classifier)"
bx=$(m "$ROOT/$LD"); st=$(m "$NEW")
echo "  gate LD: box=$bx base=$LD_BASE staged=$st tgt=$LD_TGT"
[ "$bx" = "$LD_BASE" ] || { echo "  ** BOX != BASE (live_driver.py drifted from d9468361) -- ABORT (0 changes)"; exit 4; }
[ "$st" = "$LD_TGT" ] || { echo "  ** STAGED != TARGET (bad delivery) -- ABORT (0 changes)"; exit 5; }
mkdir -p "$BK/$(dirname "$LD")"; cp "$ROOT/$LD" "$BK/$LD"
rm -f "$ROOT/$LD"; cp "$NEW" "$ROOT/$LD"
n=$(m "$ROOT/$LD")
[ "$n" = "$LD_TGT" ] || { echo "  ** POST-COPY mismatch ($n) -- ROLLING BACK"; cp "$BK/$LD" "$ROOT/$LD"; echo "  rolled back from $BK"; exit 6; }
cd "$ROOT" && "$V" -m py_compile "$LD" && echo "  py_compile OK" || { echo "  ** py_compile FAILED -- ROLLING BACK"; cp "$BK/$LD" "$ROOT/$LD"; echo "  rolled back"; exit 7; }
rm -f "$NEW"
echo "  APPLIED live_driver.py -> $n ; backup=$BK ; engine PID UNCHANGED until restart: $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "### PHASE 2 GRAFT DONE. NEXT (timed clear of the 9:30 ET open): restart_tc.ps1 (bounces ALL 31 subs / every division). Then boot-verify + FF push. ###"
echo "### STOP CONDITION: if this graft landed but pm_web is NOT confirmed on the new classifier, HOLD -- do NOT run restart_tc.ps1; restore live_driver.py from $BK (engine has not restarted, so box returns to base = prod-live d9468361, no divergence). ###"
