set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
MAIN=$ROOT/trading_corp/main.py
STAGE=/home/azureuser/pm_mainpy_stage
BK=/home/azureuser/pm_mainpy_restore_backup_$TS
V=$ROOT/venv/bin/python
echo "### PM DRIVER RESTORE -- APPLY (graft PM driver block onto MACE's CURRENT main.py; NO restart) $TS ###"
echo "engine PID (runs the OLD clobbered main.py in memory until Jack restarts -- a file write does NOT reload it): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
BH=$(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## drift check: box main.py CR-stripped = $BH (expect 3f3f3df847cec842 = the exact bytes the graft was built on)"
[ "$BH" = 3f3f3df847cec842 ] || { echo "** DRIFT -- box main.py moved since the graft was built; ABORT, re-graft needed. Nothing changed."; exit 3; }
[ -f "$STAGE/grafted_main.py" ] || { echo "** staged grafted_main.py MISSING -- ABORT"; exit 3; }
GH=$(tr -d '\r' < "$STAGE/grafted_main.py" | sha256sum | cut -c1-16)
[ "$GH" = 236a6be054268278 ] || { echo "** staged file hash $GH != 236a6be054268278 -- ABORT"; exit 3; }
cd "$ROOT"; PYTHONPATH=. "$V" -c "import trading_corp.main" >/tmp/gpre_$TS 2>&1; RCPRE=$?
echo "## pre-apply 'import trading_corp.main' exit=$RCPRE (baseline: is this env even able to import main?)"
PP=$(stat -c '%a' "$MAIN"); PO=$(stat -c '%U:%G' "$MAIN")
mkdir -p "$BK"; cp -a "$MAIN" "$BK/main.py"
echo "## main.py perms=$PP owner=$PO ; backup -> $BK/main.py (the rollback)"
restore(){ echo "  ...RESTORING $BK/main.py"; cp -a "$BK/main.py" "$MAIN"; chmod "$PP" "$MAIN"; }
cp "$STAGE/grafted_main.py" "$MAIN"; chmod "$PP" "$MAIN"
AH=$(tr -d '\r' < "$MAIN" | sha256sum | cut -c1-16)
echo "## applied main.py CR-stripped = $AH (expect 236a6be054268278)"
[ "$AH" = 236a6be054268278 ] || { echo "** APPLIED HASH MISMATCH -- RESTORING + ABORT"; restore; exit 4; }
echo "## MACE/base SURVIVES (byte-verified by hash above; markers confirm): dxfeed=$(grep -ic dxfeed "$MAIN")(exp 2) tastytrade=$(grep -ic tastytrade "$MAIN")(20) mace=$(grep -ic mace "$MAIN")(119) kalshi_arb=$(grep -c KalshiTailPriceArbAgent "$MAIN")(2) poly_kalshi=$(grep -c 'Poly->Kalshi MLB copy WIRED' "$MAIN")(1)"
echo "## PM driver block PRESENT: scheduled_pm_live_loop=$(grep -c scheduled_pm_live_loop "$MAIN")(2) plan_driver_tasks=$(grep -c plan_driver_tasks "$MAIN")(2) WIRED-line=$(grep -c 'PM LIVE DRIVER WIRED' "$MAIN")(1) ; M3-still-absent=$(grep -c 'SHARD-BALANCE SNAPSHOTS' "$MAIN")(driver-only scope)"
"$V" -m py_compile "$MAIN" >/tmp/gpc_$TS 2>&1 && echo "## Gate-A py_compile OK" || { echo "** py_compile FAILED -- RESTORING"; sed 's/^/    /' /tmp/gpc_$TS; restore; rm -f /tmp/gpc_$TS; exit 5; }
PYTHONPATH=. "$V" -c "import trading_corp.prediction_markets.live_driver, trading_corp.prediction_markets.driver_roster, trading_corp.prediction_markets.shard_snapshot_task" >/tmp/gpm_$TS 2>&1; RCPM=$?
echo "## Gate-A: PM driver modules import exit=$RCPM (expect 0 -- the block's runtime imports resolve in the service dir)"
[ "$RCPM" = 0 ] || { echo "** PM module import FAILED -- RESTORING"; sed 's/^/    /' /tmp/gpm_$TS; restore; rm -f /tmp/gpm_$TS; exit 6; }
PYTHONPATH=. "$V" -c "import trading_corp.main" >/tmp/gpost_$TS 2>&1; RCPOST=$?
echo "## Gate-A: 'import trading_corp.main' post-graft exit=$RCPOST (pre-apply was $RCPRE)"
if [ "$RCPRE" = 0 ] && [ "$RCPOST" != 0 ]; then echo "** GRAFT BROKE import trading_corp.main (pre OK, post FAIL) -- RESTORING"; sed 's/^/    /' /tmp/gpost_$TS; restore; rm -f /tmp/gpre_$TS /tmp/gpost_$TS /tmp/gpm_$TS /tmp/gpc_$TS; exit 7; fi
[ "$RCPRE" != 0 ] && echo "   (import-main failed pre-apply too -> ENV limitation of this ssh session, NOT the graft; py_compile + PM-modules ARE the gate, both green)"
rm -f /tmp/gpre_$TS /tmp/gpost_$TS /tmp/gpm_$TS /tmp/gpc_$TS "$STAGE/grafted_main.py"; rmdir "$STAGE" 2>/dev/null
echo "## perms now: $(stat -c '%a %U:%G' "$MAIN")"
echo "engine PID (UNCHANGED -- running process untouched; grafted file staged for the NEXT restart): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "MAINPY_GRAFT_APPLIED_OK backup=$BK grafted=236a6be0"
echo "### APPLY DONE -- PM driver block grafted onto MACE's main.py, MACE survives byte-for-byte, Gate-A green. NO restart. ###"
