#!/usr/bin/env bash
# secrev_batch2c1_deploy.sh -- BATCH 2c-1 (SAFE combo: aiohttp + fastapi group).
# STAGED -- DO NOT RUN. DEPLOY+RESTART reserved for Jack (az vm run-command, market-closed).
# Upgrades, in the SHARED prod venv:
#     aiohttp 3.13.5->3.14.5   yarl 1.23.0->1.25.1   (CVE-2026-69244/128124/128128; transitive via ccxt)
#     fastapi 0.136.1->0.165.0 starlette 1.0.1->1.10.0 (+ opentelemetry-api NEW) (GHSA-f56c quadratic-param DoS)
# starlette 1.10.0 >= 1.0.1  => BadHost KEV CVE-2026-48710 STAYS fixed.
# Restarts all 4 app services (shared venv -> avoid mixed in-memory/on-disk state).
# Gates: box==prod-live 110d735c base, venv-at-baseline, quiet-60s, versions+NO-new-pip-conflict abort+auto-restore.
set -u
exec > >(tee /home/azureuser/secrev_batch2c1_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
BK=/home/azureuser/secrev_batch2c1_backup_$TS
PIP="$ROOT/venv/bin/pip"
EXPECT_MAIN=6e52c9bd1a36eb1bc801c5772f4178c0   # prod-live 110d735c main.py (box base; unchanged since f742c377)
RESTART="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder"
PROTECTED="$RESTART caddy authelia"
mkdir -p "$BK"
echo "=== BATCH2C-1 DEPLOY START $TS host=$(hostname) ==="

echo "[before] protected services:"
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done

# base gate: box on prod-live 110d735c
BOXMAIN=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[base] box main.py md5=$BOXMAIN expect=$EXPECT_MAIN"
[ "$BOXMAIN" = "$EXPECT_MAIN" ] || { echo "[ABORT] box not on prod-live 110d735c base; nothing changed"; exit 2; }

# venv baseline gate (must be the pre-2c1 versions)
g(){ "$PIP" show "$1" 2>/dev/null | awk '/^Version:/{print $2}'; }
BA=$(g aiohttp); BY=$(g yarl); BF=$(g fastapi); BS=$(g starlette); BO=$(g opentelemetry-api)
echo "[base] venv now: aiohttp=$BA yarl=$BY fastapi=$BF starlette=$BS opentelemetry-api=${BO:-absent}"
if [ "$BA" != "3.13.5" ] || [ "$BY" != "1.23.0" ] || [ "$BF" != "0.136.1" ] || [ "$BS" != "1.0.1" ]; then
  echo "[ABORT] venv not at expected 2c-1 baseline; nothing changed"; exit 2; fi

# quiet-60s gate (no real PM order in the last 60s)
PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then echo "[ABORT] order <60s ago -- not quiet"; exit 3; fi

# backup + baseline pip-check
"$PIP" freeze > "$BK/pip_freeze.pre.txt" 2>/dev/null
"$PIP" check > "$BK/pipcheck.pre.txt" 2>&1 || true
echo "[backup] pip freeze -> $BK/pip_freeze.pre.txt ($(wc -l < "$BK/pip_freeze.pre.txt") pkgs)"

# install (fastapi 0.165.0 pulls opentelemetry-api automatically)
echo "[install] aiohttp==3.14.5 yarl==1.25.1 fastapi==0.165.0 starlette==1.10.0 ..."
"$PIP" install "aiohttp==3.14.5" "yarl==1.25.1" "fastapi==0.165.0" "starlette==1.10.0" 2>&1 | tail -10

# verify target versions + crypto/msal UNTOUCHED + 2b pins UNTOUCHED
NA=$(g aiohttp); NY=$(g yarl); NF=$(g fastapi); NS=$(g starlette); NO=$(g opentelemetry-api)
NC=$(g cryptography); NM=$(g msal); NJ=$(g pyjwt); NU=$(g urllib3); NPI=$(g pillow)
echo "[verify] aiohttp=$NA yarl=$NY fastapi=$NF starlette=$NS otel=$NO | UNTOUCHED crypto=$NC msal=$NM pyjwt=$NJ urllib3=$NU pillow=$NPI"
if [ "$NA" != "3.14.5" ] || [ "$NY" != "1.25.1" ] || [ "$NF" != "0.165.0" ] || [ "$NS" != "1.10.0" ] \
   || [ "$NC" != "47.0.0" ] || [ "$NM" != "1.36.0" ] || [ "$NJ" != "2.15.1" ] || [ "$NU" != "2.8.0" ] || [ "$NPI" != "12.3.0" ]; then
  echo "[ABORT] versions off / collateral change -- RESTORING"; "$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -3; exit 4; fi
"$PIP" check > "$BK/pipcheck.post.txt" 2>&1 || true
NEWC=$(comm -13 <(sort "$BK/pipcheck.pre.txt") <(sort "$BK/pipcheck.post.txt") | grep -viE '^No broken' || true)
if [ -n "$NEWC" ]; then echo "[ABORT] NEW pip-check conflict -- RESTORING:"; echo "$NEWC" | sed 's/^/    /'; "$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -3; exit 4; fi
echo "[verify] versions correct; no NEW pip-check conflict"

# restart the 4 app services ONCE each (shared venv)
for u in $RESTART; do
  OLD=$(systemctl show $u -p MainPID --value 2>/dev/null)
  echo "[restart] $u old PID=$OLD ..."; systemctl restart $u; sleep 6
  echo "[restart] $u new PID=$(systemctl show $u -p MainPID --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"
done
echo "=== BATCH2C-1 DEPLOY DONE $TS -- backups in $BK ==="
echo "VERIFY: /healthz 200 (8000 & 8081); dashboard 302 via Authelia; webhook GET->405; 5-min tracebacks=0;"
echo "        ccxt marks flowing (engine) + Coinbase ccxt.pro WS reconnect (market-context-recorder); PM/SFP boot clean."
