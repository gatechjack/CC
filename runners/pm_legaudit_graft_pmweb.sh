set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
NEW=/tmp/leg_audit_new.py
LA=trading_corp/prediction_markets/leg_audit.py
LA_BASE=ec005466f1d18c364ca2597e185c855f; LA_TGT=61cfe84b094ab5b109452a18dc74f3bc
BK=/home/azureuser/pm_legaudit_pmweb_backup_$TS
m(){ md5sum "$1" | cut -d' ' -f1; }
echo "### PHASE 1 GRAFT (pm_web / leg_audit.py; azureuser; base d9468361 -> target 9419c2d8) $TS ###"
echo "engine PID: $(systemctl show -p MainPID --value trading-corp 2>/dev/null) ; pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
[ -f "$NEW" ] || { echo "  ** delivered leg_audit.py MISSING -- abort (0 changes)"; exit 2; }
bx=$(m "$ROOT/$LA"); st=$(m "$NEW")
echo "  gate LA: box=$bx base=$LA_BASE staged=$st tgt=$LA_TGT"
[ "$bx" = "$LA_BASE" ] || { echo "  ** BOX != BASE (leg_audit.py drifted from d9468361) -- ABORT (0 changes)"; exit 3; }
[ "$st" = "$LA_TGT" ] || { echo "  ** STAGED != TARGET (bad delivery) -- ABORT (0 changes)"; exit 4; }
mkdir -p "$BK/$(dirname "$LA")"; cp "$ROOT/$LA" "$BK/$LA"
rm -f "$ROOT/$LA"; cp "$NEW" "$ROOT/$LA"
n=$(m "$ROOT/$LA")
[ "$n" = "$LA_TGT" ] || { echo "  ** POST-COPY mismatch ($n) -- ROLLING BACK"; cp "$BK/$LA" "$ROOT/$LA"; echo "  rolled back from $BK"; exit 5; }
cd "$ROOT" && "$V" -m py_compile "$LA" && echo "  py_compile OK" || { echo "  ** py_compile FAILED -- ROLLING BACK"; cp "$BK/$LA" "$ROOT/$LA"; echo "  rolled back"; exit 6; }
rm -f "$NEW"
echo "  APPLIED leg_audit.py -> $n ; backup=$BK ; engine PID UNCHANGED: $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "### PHASE 1 GRAFT DONE. NEXT (this authorization): restart_pmweb.ps1 (pm_web ONLY; engine + 31 subs untouched), then Phase-1 verify. The ENGINE graft is Phase 2 = a SEPARATE authorization. ###"
