#!/usr/bin/env bash
# secrev_batch2c2_deploy.sh -- BATCH 2c-2 (ISOLATED: cryptography auth-path group).
# STAGED -- DO NOT RUN. DEPLOY+RESTART reserved for Jack (az vm run-command, market-closed).
# Run ONLY after 2c-1 is stable. Isolated because it bumps msal (the Key Vault / managed-identity
# auth path) which has NO PC test coverage -> keep it a standalone batch for clean rollback attribution.
# Upgrades, in the SHARED prod venv:
#     cryptography 47.0.0->50.0.2   (CVE-2026-69248 x509 name-constraint bypass; NOT reachable here --
#                                    app never calls cryptography.x509.verification; TLS via stdlib ssl.
#                                    Patched as defense-in-depth.)
#     msal         1.36.0 ->1.39.0  (REQUIRED companion: msal 1.36.0 pins cryptography<49.)
# azure-identity / azure-core / azure-keyvault-secrets UNCHANGED (verified compatible via dry-run).
# Restarts all 4 app services (shared venv; cryptography reaches all via ccxt + azure-identity + pykalshi).
set -u
exec > >(tee /home/azureuser/secrev_batch2c2_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
BK=/home/azureuser/secrev_batch2c2_backup_$TS
PIP="$ROOT/venv/bin/pip"
EXPECT_MAIN=6e52c9bd1a36eb1bc801c5772f4178c0
RESTART="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder"
PROTECTED="$RESTART caddy authelia"
mkdir -p "$BK"
echo "=== BATCH2C-2 DEPLOY START $TS host=$(hostname) ==="
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done

BOXMAIN=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[base] box main.py md5=$BOXMAIN expect=$EXPECT_MAIN"
[ "$BOXMAIN" = "$EXPECT_MAIN" ] || { echo "[ABORT] box not on prod-live base; nothing changed"; exit 2; }

g(){ "$PIP" show "$1" 2>/dev/null | awk '/^Version:/{print $2}'; }
BC=$(g cryptography); BM=$(g msal)
echo "[base] venv now: cryptography=$BC msal=$BM (expect 47.0.0 / 1.36.0)"
if [ "$BC" != "47.0.0" ] || [ "$BM" != "1.36.0" ]; then echo "[ABORT] venv not at expected 2c-2 baseline"; exit 2; fi

PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then echo "[ABORT] order <60s ago"; exit 3; fi

"$PIP" freeze > "$BK/pip_freeze.pre.txt" 2>/dev/null
"$PIP" check > "$BK/pipcheck.pre.txt" 2>&1 || true
echo "[backup] pip freeze -> $BK/pip_freeze.pre.txt ($(wc -l < "$BK/pip_freeze.pre.txt") pkgs)"

echo "[install] cryptography==50.0.2 msal==1.39.0 ..."
"$PIP" install "cryptography==50.0.2" "msal==1.39.0" 2>&1 | tail -10

NC=$(g cryptography); NM=$(g msal); NAI=$(g azure-identity); NACO=$(g azure-core); NKV=$(g azure-keyvault-secrets)
echo "[verify] cryptography=$NC msal=$NM | UNTOUCHED azure-identity=$NAI azure-core=$NACO azure-keyvault-secrets=$NKV"
if [ "$NC" != "50.0.2" ] || [ "$NM" != "1.39.0" ] || [ "$NAI" != "1.25.3" ] || [ "$NACO" != "1.39.0" ] || [ "$NKV" != "4.11.0" ]; then
  echo "[ABORT] versions off / collateral azure change -- RESTORING"; "$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -3; exit 4; fi
"$PIP" check > "$BK/pipcheck.post.txt" 2>&1 || true
NEWC=$(comm -13 <(sort "$BK/pipcheck.pre.txt") <(sort "$BK/pipcheck.post.txt") | grep -viE '^No broken' || true)
if [ -n "$NEWC" ]; then echo "[ABORT] NEW pip-check conflict -- RESTORING:"; echo "$NEWC" | sed 's/^/    /'; "$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -3; exit 4; fi
echo "[verify] versions correct; no NEW pip-check conflict"

for u in $RESTART; do
  OLD=$(systemctl show $u -p MainPID --value 2>/dev/null)
  echo "[restart] $u old PID=$OLD ..."; systemctl restart $u; sleep 6
  echo "[restart] $u new PID=$(systemctl show $u -p MainPID --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"
done
echo "=== BATCH2C-2 DEPLOY DONE $TS -- backups in $BK ==="
echo "VERIFY (CRITICAL -- no PC coverage of this path): Key Vault secret-fetch SUCCESS burst at boot"
echo "  (azure http_logging_policy 'Response status: 200' x~hundreds; the single benign credential-chain 401 is OK);"
echo "  broker auth across RH + Bitunix + Kalshi (sign via cryptography); NO msal/auth errors; 5-min tracebacks=0."
