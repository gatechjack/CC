#!/usr/bin/env bash
# secrev_batch2b_deploy.sh -- BATCH 2b deploy (runs as ROOT via az vm run-command). JACK RUNS (DEPLOY+RESTART reserved).
# pip install pyjwt 2.15.1 + urllib3 2.8.0 + pillow 12.3.0 into the prod venv; ONE engine restart.
# Gates: box==prod-live base, venv-at-baseline, quiet-60s, NEW-pip-conflict abort+auto-restore. cryptography/aiohttp/fastapi NOT touched.
set -u
exec > >(tee /home/azureuser/secrev_batch2b_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
BK=/home/azureuser/secrev_batch2b_backup_$TS
PIP="$ROOT/venv/bin/pip"
EXPECT_MAIN=6e52c9bd1a36eb1bc801c5772f4178c0   # prod-live f742c377 main.py (box base)
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"
mkdir -p "$BK"
echo "=== BATCH2B DEPLOY START $TS host=$(hostname) ==="

echo "[before] protected services:"
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null)"; done

# base gate: box on prod-live f742c377
BOXMAIN=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[base] box main.py md5=$BOXMAIN expect=$EXPECT_MAIN"
if [ "$BOXMAIN" != "$EXPECT_MAIN" ]; then echo "[ABORT] box not on prod-live f742c377 base; nothing changed"; exit 2; fi

# venv baseline gate
BL_JWT=$("$PIP" show pyjwt 2>/dev/null | awk '/^Version:/{print $2}')
BL_URL=$("$PIP" show urllib3 2>/dev/null | awk '/^Version:/{print $2}')
BL_PIL=$("$PIP" show pillow 2>/dev/null | awk '/^Version:/{print $2}')
echo "[base] venv now: pyjwt=$BL_JWT urllib3=$BL_URL pillow=$BL_PIL (expect 2.12.1 / 2.6.3 / 12.2.0)"
if [ "$BL_JWT" != "2.12.1" ] || [ "$BL_URL" != "2.6.3" ] || [ "$BL_PIL" != "12.2.0" ]; then echo "[ABORT] venv not at expected baseline; nothing changed"; exit 2; fi

# quiet-60s gate
PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then echo "[ABORT] order <60s ago -- not quiet; re-run in ~1 min"; exit 3; fi

# backup pip freeze + baseline pip-check
"$PIP" freeze > "$BK/pip_freeze.pre.txt" 2>/dev/null
"$PIP" check > "$BK/pipcheck.pre.txt" 2>&1 || true
echo "[backup] pip freeze -> $BK/pip_freeze.pre.txt ($(wc -l < "$BK/pip_freeze.pre.txt") pkgs); baseline pip-check captured"

# install the 3 (no deps -> no scope widen)
echo "[install] pyjwt==2.15.1 urllib3==2.8.0 pillow==12.3.0 ..."
"$PIP" install "pyjwt==2.15.1" "urllib3==2.8.0" "pillow==12.3.0" 2>&1 | tail -8

# verify versions + no NEW pip-check conflict + starlette/fastapi untouched
NJ=$("$PIP" show pyjwt 2>/dev/null | awk '/^Version:/{print $2}'); NU=$("$PIP" show urllib3 2>/dev/null | awk '/^Version:/{print $2}'); NPI=$("$PIP" show pillow 2>/dev/null | awk '/^Version:/{print $2}')
NS=$("$PIP" show starlette 2>/dev/null | awk '/^Version:/{print $2}'); NF=$("$PIP" show fastapi 2>/dev/null | awk '/^Version:/{print $2}')
echo "[verify] pyjwt=$NJ urllib3=$NU pillow=$NPI | starlette=$NS fastapi=$NF (must be 1.0.1 / 0.136.1)"
if [ "$NJ" != "2.15.1" ] || [ "$NU" != "2.8.0" ] || [ "$NPI" != "12.3.0" ] || [ "$NS" != "1.0.1" ] || [ "$NF" != "0.136.1" ]; then
  echo "[ABORT] versions off -- RESTORING from freeze"; "$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -3; exit 4; fi
"$PIP" check > "$BK/pipcheck.post.txt" 2>&1 || true
NEWC=$(comm -13 <(sort "$BK/pipcheck.pre.txt") <(sort "$BK/pipcheck.post.txt") | grep -viE '^No broken' || true)
if [ -n "$NEWC" ]; then echo "[ABORT] NEW pip-check conflict after install -- RESTORING:"; echo "$NEWC" | sed 's/^/    /'; "$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -3; exit 4; fi
echo "[verify] no NEW pip-check conflicts; versions correct"

# restart ONCE
OLDPID=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
echo "[restart] old PID=$OLDPID -- restarting trading-corp ONCE ..."
systemctl restart trading-corp
sleep 10
NEWPID=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
echo "[restart] new PID=$NEWPID active=$(systemctl is-active trading-corp) NRestarts=$(systemctl show trading-corp -p NRestarts --value 2>/dev/null)"
echo "=== BATCH2B DEPLOY DONE $TS -- backups in $BK. Verify: 5min tracebacks + divisions + broker auth + ss 127.0.0.1:8000. ==="
