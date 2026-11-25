#!/usr/bin/env bash
# secrev_batch2_rollback.sh -- BATCH 2 ROLLBACK (runs as ROOT via az vm run-command). Run only if needed.
# Restores main.py (bind 0.0.0.0) + starlette 1.0.0 + restarts trading-corp ONCE. python3.12 debs left (backward-compatible).
set -u
BK=${1:-}
[ -z "$BK" ] && BK=$(ls -dt /home/azureuser/secrev_batch2_backup_* 2>/dev/null | head -1)
ROOT=/home/azureuser/trading_corp
echo "=== BATCH2 ROLLBACK from $BK ==="
if [ -z "$BK" ] || [ ! -d "$BK" ]; then echo "no backup dir -- cannot roll back"; exit 1; fi
cp -a "$BK/main.py.bak" "$ROOT/trading_corp/main.py"
echo "[rollback] main.py restored md5=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)"
"$ROOT/venv/bin/pip" install "starlette==1.0.0" 2>&1 | tail -4
echo "[rollback] starlette now: $("$ROOT/venv/bin/pip" show starlette 2>/dev/null | awk '/^Version:/{print $2}')"
systemctl restart trading-corp
sleep 10
echo "[rollback] trading-corp PID=$(systemctl show trading-corp -p MainPID --value) active=$(systemctl is-active trading-corp)"
echo "=== BATCH2 ROLLBACK DONE ==="
