set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
STG=/tmp/legaudit_graft_stage
TAR=/tmp/legaudit_graft.tgz
BK=/home/azureuser/pm_legaudit_graft_backup_$TS
LD=trading_corp/prediction_markets/live_driver.py
LA=trading_corp/prediction_markets/leg_audit.py
LD_BASE=b9e67d1a7117191fa64b3e31290ec0f8; LD_TGT=15069d88dd4d69aea3df9aaece74bfdb
LA_BASE=ec005466f1d18c364ca2597e185c855f; LA_TGT=61cfe84b094ab5b109452a18dc74f3bc
m(){ md5sum "$1" | cut -d' ' -f1; }
echo "### LEG-AUDIT CODE-ALIAS GRAFT (2 files; azureuser; base d9468361 -> target 9419c2d8) $TS ###"
echo "engine PID: $(systemctl show -p MainPID --value trading-corp 2>/dev/null) ; pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
[ -f "$TAR" ] || { echo "  ** staging tar MISSING -- abort (0 changes)"; exit 2; }
rm -rf "$STG"; mkdir -p "$STG"; tar xzf "$TAR" -C "$STG"
bx_ld=$(m "$ROOT/$LD"); bx_la=$(m "$ROOT/$LA"); st_ld=$(m "$STG/$LD"); st_la=$(m "$STG/$LA")
echo "  gate LD: box=$bx_ld base=$LD_BASE stg=$st_ld tgt=$LD_TGT"
echo "  gate LA: box=$bx_la base=$LA_BASE stg=$st_la tgt=$LA_TGT"
[ "$bx_ld" = "$LD_BASE" ] && [ "$bx_la" = "$LA_BASE" ] || { echo "  ** BOX != BASE (drifted from d9468361) -- ABORT (0 changes)"; exit 3; }
[ "$st_ld" = "$LD_TGT" ] && [ "$st_la" = "$LA_TGT" ] || { echo "  ** STAGING != TARGET (bad tar) -- ABORT (0 changes)"; exit 4; }
mkdir -p "$BK/$(dirname "$LD")"; cp "$ROOT/$LD" "$BK/$LD"; cp "$ROOT/$LA" "$BK/$LA"
rm -f "$ROOT/$LD"; cp "$STG/$LD" "$ROOT/$LD"
rm -f "$ROOT/$LA"; cp "$STG/$LA" "$ROOT/$LA"
n_ld=$(m "$ROOT/$LD"); n_la=$(m "$ROOT/$LA")
if [ "$n_ld" != "$LD_TGT" ] || [ "$n_la" != "$LA_TGT" ]; then
  echo "  ** POST-COPY mismatch (LD=$n_ld LA=$n_la) -- ROLLING BACK ALL"; cp "$BK/$LD" "$ROOT/$LD"; cp "$BK/$LA" "$ROOT/$LA"; echo "  rolled back from $BK"; exit 5
fi
cd "$ROOT" && "$V" -m py_compile "$LD" "$LA" && echo "  py_compile OK" || { echo "  ** py_compile FAILED -- ROLLING BACK ALL"; cp "$BK/$LD" "$ROOT/$LD"; cp "$BK/$LA" "$ROOT/$LA"; echo "  rolled back from $BK"; exit 6; }
rm -rf "$STG" "$TAR"
echo "  APPLIED OK: LD=$n_ld LA=$n_la ; backup=$BK"
echo "  engine PID: $(systemctl show -p MainPID --value trading-corp 2>/dev/null) (UNCHANGED -- restart is a SEPARATE Jack step)"
echo "### GRAFT DONE. RESTART ORDER: (1) restart_pmweb.ps1  (2) restart_tc.ps1 (bounces ALL divisions). Then FF: git push origin cs2-legaudit-lg-luminosity-2026-09-17:prod-live ###"
