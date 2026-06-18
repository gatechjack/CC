#!/usr/bin/env bash
# READ-ONLY clean-flat airtight confirm. sqlite ro.
set -uo pipefail
cd /home/azureuser/trading_corp
DB="file:data/trading_corp.db?mode=ro"

echo "=== position total count (0 = flat) ==="
sqlite3 "$DB" "SELECT COUNT(*) FROM position;"
echo "=== any position rows ==="
sqlite3 -header -column "$DB" "SELECT * FROM position LIMIT 5;"

echo "=== last 8 bitunix_futures proposed_order ==="
sqlite3 -header -column "$DB" "SELECT ts,symbol,side,qty,status,fill_ts,execution_mode FROM proposed_order WHERE strategy='bitunix_futures' ORDER BY ts DESC LIMIT 8;"

echo "=== last bitunix reconcile lines (1d) ==="
journalctl -u trading-corp --since "1 day ago" --no-pager 2>/dev/null | grep -iE "bitunix_position_reconciler|reconcile_position_state" | tail -8

echo "=== broker paper/live at boot (1d) ==="
journalctl -u trading-corp --since "1 day ago" --no-pager 2>/dev/null | grep -iE "paper=False|paper=True|BitunixBroker|env_authorized|execution_mode.*:.*live|broker.*live" | tail -8
