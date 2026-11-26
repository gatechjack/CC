#!/usr/bin/env bash
# secrev_batch2b_rollback.sh -- BATCH 2b ROLLBACK (ROOT via az vm run-command). Run only if needed.
# Reinstalls the backed-up pre-change pip freeze + restarts trading-corp ONCE.
set -u
BK=${1:-}
[ -z "$BK" ] && BK=$(ls -dt /home/azureuser/secrev_batch2b_backup_* 2>/dev/null | head -1)
ROOT=/home/azureuser/trading_corp
PIP="$ROOT/venv/bin/pip"
echo "=== BATCH2B ROLLBACK from $BK ==="
if [ -z "$BK" ] || [ ! -f "$BK/pip_freeze.pre.txt" ]; then echo "no backup freeze found -- cannot roll back"; exit 1; fi
"$PIP" install -r "$BK/pip_freeze.pre.txt" 2>&1 | tail -5
echo "[rollback] pyjwt=$("$PIP" show pyjwt 2>/dev/null|awk '/^Version:/{print $2}') urllib3=$("$PIP" show urllib3 2>/dev/null|awk '/^Version:/{print $2}') pillow=$("$PIP" show pillow 2>/dev/null|awk '/^Version:/{print $2}')"
systemctl restart trading-corp
sleep 10
echo "[rollback] trading-corp PID=$(systemctl show trading-corp -p MainPID --value) active=$(systemctl is-active trading-corp)"
echo "=== BATCH2B ROLLBACK DONE ==="
