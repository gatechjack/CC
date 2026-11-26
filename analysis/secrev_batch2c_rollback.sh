#!/usr/bin/env bash
# secrev_batch2c_rollback.sh -- manual rollback for a 2c batch AFTER a successful deploy.
# STAGED -- DO NOT RUN unless rolling back. DEPLOY+RESTART reserved for Jack.
# Usage: secrev_batch2c_rollback.sh /home/azureuser/secrev_batch2c<1|2>_backup_<TS>
# Restores the exact pre-deploy venv from the batch's pip_freeze.pre.txt, then restarts the 4 app services.
set -u
exec > >(tee /home/azureuser/secrev_batch2c_rollback_lastrun.log) 2>&1
BK="${1:?usage: secrev_batch2c_rollback.sh <backup_dir>}"
ROOT=/home/azureuser/trading_corp
PIP="$ROOT/venv/bin/pip"
RESTART="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder"
FREEZE="$BK/pip_freeze.pre.txt"
echo "=== BATCH2C ROLLBACK $(date -u +%Y%m%dT%H%M%SZ) from $BK ==="
[ -r "$FREEZE" ] || { echo "[ABORT] no $FREEZE"; exit 2; }
echo "[restore] pip install -r $FREEZE ($(wc -l < "$FREEZE") pkgs) ..."
"$PIP" install -r "$FREEZE" 2>&1 | tail -8
echo "[verify] key versions after restore:"
for p in cryptography msal aiohttp yarl fastapi starlette; do echo "  $p=$("$PIP" show $p 2>/dev/null | awk '/^Version:/{print $2}')"; done
"$PIP" check 2>&1 | tail -3
for u in $RESTART; do echo "[restart] $u ..."; systemctl restart $u; sleep 6; echo "  $u active=$(systemctl is-active $u) PID=$(systemctl show $u -p MainPID --value)"; done
echo "=== ROLLBACK DONE -- verify /healthz 200 + brokers + tracebacks=0 ==="
