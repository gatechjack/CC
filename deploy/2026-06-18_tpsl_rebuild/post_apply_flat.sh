#!/usr/bin/env bash
# READ-ONLY post-apply verify + pre-restart flat re-confirm. sqlite ro.
set -uo pipefail
cd /home/azureuser/trading_corp
DB="file:data/trading_corp.db?mode=ro"
BAK=".bak-pre-tpsl-rebuild-2026-06-18"

echo "=== installed 3 files md5 (== target 74aa1b42/19da15ff/707c6828) ==="
md5sum trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py
echo "=== backups present ==="
ls -1 trading_corp/brokers/bitunix.py$BAK trading_corp/agents/divisions/bitunix_futures_observer.py$BAK trading_corp/agents/divisions/bitunix_position_reconciler.py$BAK
echo "=== main.py/db.py md5 (must == f16e9c24 / a2c2ff46) ==="
md5sum trading_corp/main.py trading_corp/persistence/db.py
echo "=== FLAT re-confirm: position count (0=flat) ==="
sqlite3 "$DB" "SELECT COUNT(*) FROM position;"
echo "=== last 3 bitunix_futures orders ==="
sqlite3 -header -column "$DB" "SELECT ts,symbol,side,qty,status,fill_ts FROM proposed_order WHERE strategy='bitunix_futures' ORDER BY ts DESC LIMIT 3;"
echo "=== bitunix activity last 3h (fills/exits/halt/reconcile/orphan/diverg) ==="
journalctl -u trading-corp --since "3 hours ago" --no-pager 2>/dev/null | grep -iE "bitunix.*(fill|exit|position)|reconcile_position_state|_halt_new_orders|orphan|diverg" | tail -10
echo "=== engine PID/state ==="
systemctl show trading-corp -p MainPID -p ActiveState -p SubState
