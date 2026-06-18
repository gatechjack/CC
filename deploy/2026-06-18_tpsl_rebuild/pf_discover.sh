#!/usr/bin/env bash
# READ-ONLY pre-flight discovery: journal access + DB position/order schema.
# No writes. sqlite opened in mode=ro.
set -uo pipefail
cd /home/azureuser/trading_corp
DB="file:data/trading_corp.db?mode=ro"

echo "=== journalctl read access test ==="
journalctl -u trading-corp --no-pager -n 2 2>&1 | head -3

echo "=== candidate tables ==="
sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%position%' OR name LIKE '%order%' OR name LIKE '%trade%' OR name LIKE '%reconcil%' OR name LIKE '%fill%') ORDER BY name;"

echo "=== proposed_order columns ==="
sqlite3 "$DB" "PRAGMA table_info(proposed_order);" 2>/dev/null | head -40
