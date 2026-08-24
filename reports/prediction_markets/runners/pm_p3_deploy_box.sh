#!/usr/bin/env bash
# PM P3 DEPLOY (box-side, run as ROOT via az run-command). FAIL-CLOSED. Deploys ONLY the 11 PM artifacts
# to the DOUBLE path; manifest-assert (no engine file); chain-of-custody (box sha == local); import-resolution
# path proof BEFORE any overwrite/restart (GOTCHA-3); backup-before-overwrite; GOTCHA-2 owner/mode gate;
# restart pm_web ONLY if the gate passes; healthz; sync-names AS azureuser (GOTCHA-1); VERIFY THE RENDER
# (named + honest-missing); engine PID bracketed. Prints DEPLOY_VERDICT. Rolls back on any post-restart failure.
set -o pipefail
TS=$(date -u +%Y%m%d_%H%M%S)
H=/home/azureuser
ROOT=$H/trading_corp
DB=$ROOT/data/prediction_markets.db
STAGE=$H/pm_p3_deploy.tgz
REF=$H/pm_p3_deploy.sha256
STG=$H/pm_p3_deploy_stg
BK=$H/pm_p3_deploy_bak_$TS
VP=$ROOT/venv/bin/python
UNIT=prediction-markets-web.service
PORT=8081
PKG=$ROOT/trading_corp/prediction_markets
PMCLI=$ROOT/trading_corp/scripts/pm_cli.py

FILES="trading_corp/prediction_markets/positions.py trading_corp/prediction_markets/names.py trading_corp/prediction_markets/stats.py trading_corp/prediction_markets/web/app.py trading_corp/prediction_markets/web/static/pm.css trading_corp/prediction_markets/web/templates/pm_macros.html trading_corp/prediction_markets/web/templates/pm_whale.html trading_corp/prediction_markets/web/templates/pm_whale_overview.html trading_corp/prediction_markets/web/templates/partials/pm_position_rows.html trading_corp/prediction_markets/web/templates/partials/pm_scoreboard_table.html trading_corp/scripts/pm_cli.py"
MODIFIED="trading_corp/prediction_markets/stats.py trading_corp/prediction_markets/web/app.py trading_corp/prediction_markets/web/static/pm.css trading_corp/prediction_markets/web/templates/pm_macros.html trading_corp/prediction_markets/web/templates/partials/pm_scoreboard_table.html trading_corp/scripts/pm_cli.py"
NEW="trading_corp/prediction_markets/positions.py trading_corp/prediction_markets/names.py trading_corp/prediction_markets/web/templates/pm_whale.html trading_corp/prediction_markets/web/templates/pm_whale_overview.html trading_corp/prediction_markets/web/templates/partials/pm_position_rows.html"

rollback(){ for f in $MODIFIED; do [ -f "$BK/$f" ] && cp -p "$BK/$f" "$ROOT/$f"; done; for f in $NEW; do rm -f "$ROOT/$f"; done; }
abort(){ echo "DEPLOY_ABORT: $1"; echo "DEPLOY_VERDICT=ABORTED_PRE_RESTART (nothing restarted)"; rm -rf "$STG"; exit 2; }

echo "=== PM P3 DEPLOY (start) $TS ==="
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_PID_BEFORE=$PID0"
WHO=$(id -un); echo "running as: $WHO (expect root via az run-command)"

echo "=== [1] MANIFEST ASSERT (PM-only; ZERO engine files) ==="
[ -f "$STAGE" ] || abort "stage tarball missing: $STAGE"
[ -f "$REF" ] || abort "reference manifest missing: $REF"
MAN=$(tar -tzf "$STAGE" | grep -v '/$' | sort)
echo "$MAN"
LEAK=$(echo "$MAN" | grep -vE '^trading_corp/prediction_markets/|^trading_corp/scripts/pm_cli\.py$' || true)
[ -n "$LEAK" ] && abort "NON-PM/engine path in tar: $LEAK"
for f in $FILES; do echo "$MAN" | grep -qx "$f" || abort "expected file missing from tar: $f"; done
N=$(echo "$MAN" | grep -c .); [ "$N" = 11 ] || abort "tar has $N files, expected 11"
echo "MANIFEST_OK (11 PM files, no engine)"

echo "=== [2] STAGING EXTRACT + CHAIN OF CUSTODY (box sha == local ref) ==="
rm -rf "$STG"; mkdir -p "$STG"; tar -xzf "$STAGE" -C "$STG" || abort "extract failed"
COC_FAIL=0
while read -r ref rel; do
  [ -z "${ref:-}" ] && continue
  got=$(sha256sum "$STG/$rel" 2>/dev/null | awk '{print $1}')
  [ "$got" = "$ref" ] || { echo "COC_MISMATCH $rel ref=$ref got=$got"; COC_FAIL=1; }
done < "$REF"
[ "$COC_FAIL" = 1 ] && abort "chain-of-custody mismatch (box != local)"
echo "CHAIN_OF_CUSTODY_OK (all 11 box sha256 == local)"

echo "=== [3] IMPORT-RESOLUTION PATH PROOF (GOTCHA-3 double path; BEFORE any overwrite) ==="
RES=$(cd "$ROOT" && PYTHONPATH="$ROOT" "$VP" -c 'import trading_corp.prediction_markets.stats as s; print(s.__file__)' 2>&1) || abort "import-resolution failed: $RES"
echo "runtime resolves stats.py -> $RES"
[ "$RES" = "$PKG/stats.py" ] || abort "runtime path $RES != expected DOUBLE path $PKG/stats.py"
[ -f "$PKG/stats.py" ] || abort "target $PKG/stats.py missing (wrong path)"
echo "PATH_PROOF_OK ($PKG)"

echo "=== [4] BACKUP-BEFORE-OVERWRITE (MODIFIED must pre-exist; absence = wrong path) ==="
mkdir -p "$BK"
for f in $MODIFIED; do
  t="$ROOT/$f"; [ -f "$t" ] || abort "MODIFIED target missing (wrong path?): $t"
  mkdir -p "$BK/$(dirname "$f")"; cp -p "$t" "$BK/$f" || abort "backup failed: $t"
done
echo "BACKUP_OK -> $BK"

echo "=== [5] DEPLOY FILES (copy staged -> DOUBLE path) ==="
for f in $FILES; do mkdir -p "$ROOT/$(dirname "$f")"; cp -p "$STG/$f" "$ROOT/$f" || abort "copy failed: $f"; done
echo "DEPLOYED 11 files"

echo "=== [6] CHOWN + MODES + GOTCHA-2 GATE (fail -> rollback, NO restart) ==="
chown -R azureuser:azureuser "$PKG" || abort "chown PKG failed"
chown azureuser:azureuser "$PMCLI" || abort "chown pm_cli failed"
find "$PKG" -type d -exec chmod 755 {} +
find "$PKG" -type f -exec chmod 644 {} +
chmod 644 "$PMCLI"
BAD_OWN=$( { find "$PKG" \( ! -user azureuser -o ! -group azureuser \); find "$PMCLI" \( ! -user azureuser -o ! -group azureuser \); } 2>/dev/null )
BAD_DIR=$(find "$PKG" -type d ! -perm 755 2>/dev/null)
WW_FILE=$( { find "$PKG" -type f -perm -0002; find "$PMCLI" -perm -0002; } 2>/dev/null )
echo "gate bad_owner=[$(echo $BAD_OWN)]"
echo "gate non755_dir=[$(echo $BAD_DIR)]"
echo "gate world_writable=[$(echo $WW_FILE)]"
if [ -n "$BAD_OWN$BAD_DIR$WW_FILE" ]; then rollback; abort "GOTCHA-2 gate FAILED (see above); rolled back, pm_web NOT restarted"; fi
echo "GATE_PASS (all azureuser, dirs 755, no world-writable file)"

echo "=== [7] RESTART pm_web ONLY (gate passed; must not touch trading-corp.service) ==="
if ! systemctl restart "$UNIT"; then rollback; systemctl restart "$UNIT" || true; echo "DEPLOY_VERDICT=FAIL_RESTART_ROLLED_BACK"; rm -rf "$STG"; exit 4; fi
sleep 2
if ! systemctl is-active "$UNIT" | grep -qx active; then rollback; systemctl restart "$UNIT" || true; echo "DEPLOY_VERDICT=FAIL_UNIT_INACTIVE_ROLLED_BACK"; rm -rf "$STG"; exit 4; fi
echo "RESTART_OK ($UNIT active)"

echo "=== [8] HEALTHZ (200 + schema 4) ==="
HZ=$(curl -fsS "http://127.0.0.1:$PORT/healthz" 2>&1)
if [ $? -ne 0 ]; then rollback; systemctl restart "$UNIT" || true; echo "HEALTHZ_FAIL: $HZ"; echo "DEPLOY_VERDICT=FAIL_HEALTHZ_ROLLED_BACK"; rm -rf "$STG"; exit 4; fi
echo "healthz: $HZ"
echo "$HZ" | grep -qE '"pm_db_schema_version": ?4' || echo "WARN: healthz schema_version != 4"
echo "HEALTHZ_OK"

# ---- artifacts DEPLOYED + pm_web healthy. This is the deploy verdict. sync-names/render below are report-only. ----
echo "DEPLOY_VERDICT=OK"

echo "=== [9] SYNC-NAMES as azureuser (GOTCHA-1: runuser; LOAD-BEARING, whales_with_user_name=0 today) ==="
BEFORE=$(runuser -u azureuser -- "$VP" -c "import sqlite3;c=sqlite3.connect('file:$DB?mode=ro',uri=True);print(c.execute('SELECT COUNT(*) FROM pm_whale WHERE user_name IS NOT NULL AND LENGTH(user_name)>0').fetchone()[0]);c.close()" 2>&1)
echo "whales_with_user_name BEFORE=$BEFORE"
SN=$(runuser -u azureuser -- bash -c "cd $ROOT && PYTHONPATH=$ROOT $VP $PMCLI sync-names" 2>&1); echo "sync-names output: $SN"
AFTER=$(runuser -u azureuser -- "$VP" -c "import sqlite3;c=sqlite3.connect('file:$DB?mode=ro',uri=True);print(c.execute('SELECT COUNT(*) FROM pm_whale WHERE user_name IS NOT NULL AND LENGTH(user_name)>0').fetchone()[0]);c.close()" 2>&1)
echo "whales_with_user_name AFTER=$AFTER"
if [ "${AFTER:-0}" -gt 0 ] 2>/dev/null; then echo "SYNC_NAMES_OK ($BEFORE -> $AFTER)"; else echo "SYNC_NAMES_WARN: 0 names populated (roster carries no names for tracked whales?) -- REPORT, do NOT fix mid-deploy"; fi

echo "=== [10] VERIFY RENDER (the page, not just the write; loopback curl) ==="
HTML=$(curl -fsS "http://127.0.0.1:$PORT/?min_resolved=1" 2>&1) || echo "RENDER_CURL_FAIL: $HTML"
KNOWN=$(echo "$HTML" | grep -oE 'Kickstand7|BetMechanic|SDTrading|Kh4mz4t|xifutloong3|pako|FordBronco|AIisTheNewWD|MadeiraIsland|STC14|000why000|kutsumiakia|evanng' | head -1)
if [ -n "$KNOWN" ]; then echo "RENDER_NAME_OK (found display name '$KNOWN' in scoreboard HTML)"; else echo "RENDER_NAME_WARN: no known display name in HTML -- names not rendering; REPORT (code IS deployed, this is a defect to report not fix mid-deploy)"; fi
UNW=$(runuser -u azureuser -- "$VP" -c "import sqlite3;c=sqlite3.connect('file:$DB?mode=ro',uri=True);r=c.execute('SELECT s.wallet,s.category FROM pm_category_stats s JOIN pm_whale w ON s.wallet=w.wallet WHERE (w.user_name IS NULL OR LENGTH(w.user_name)=0) AND s.n_resolved>=1 LIMIT 1').fetchone();print((r[0]+chr(32)+r[1]) if r else '');c.close()" 2>&1)
if [ -n "$UNW" ]; then
  set -- $UNW; UW="$1"; UC="$2"
  WH=$(curl -fsS "http://127.0.0.1:$PORT/whale/$UW/$UC" 2>&1)
  if echo "$WH" | grep -q 'pm-waddr-only' && echo "$WH" | grep -q "$UW" && echo "$WH" | grep -q 'no display name'; then echo "RENDER_NONAME_OK (unnamed whale ${UW:0:10} renders its WALLET + 'no display name', no placeholder)"; else echo "RENDER_NONAME_WARN: unnamed whale did not render its wallet cleanly -- REPORT"; fi
else
  echo "RENDER_NONAME_NA (all shown whales are named; honest-missing path proven by fixture test_null_user_name_renders_wallet)"
fi

echo "=== [11] ENGINE AFTER + CLEANUP ==="
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_PID_AFTER=$PID1 BEFORE=$PID0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED=INVESTIGATE"; fi
rm -rf "$STG"; rm -f "$STAGE" "$REF"
echo "BACKUP_KEPT_AT=$BK"
echo "ROLLBACK_IF_NEEDED: for f in \$(...); do cp -p $BK/<file> $ROOT/<file>; done; rm new files; systemctl restart $UNIT"
echo "=== PM P3 DEPLOY (done) ==="
