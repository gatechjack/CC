#!/usr/bin/env bash
# READ-ONLY clean-flat + paper/live + staleness pre-flight. No writes (sqlite ro).
set -uo pipefail
cd /home/azureuser/trading_corp
DB="file:data/trading_corp.db?mode=ro"

echo "=== position schema ==="
sqlite3 "$DB" "PRAGMA table_info(position);"

echo "=== position: recent 12 rows ==="
sqlite3 -header -column "$DB" "SELECT * FROM position ORDER BY rowid DESC LIMIT 12;"

echo "=== proposed_order: recent 6 bitunix/BTC ==="
sqlite3 -header -column "$DB" "SELECT ts,strategy,symbol,side,qty,status,fill_ts,execution_mode FROM proposed_order WHERE strategy LIKE '%bitunix%' OR symbol LIKE '%BTC%' ORDER BY ts DESC LIMIT 6;"

echo "=== journal: broker paper/live init (1d) ==="
journalctl -u trading-corp --since "1 day ago" --no-pager 2>/dev/null | grep -iE "paper=|live broker|live=|bitunix.*broker|execution_mode" | tail -8

echo "=== journal: reconcile / halt / divergence / orphan (2h) ==="
journalctl -u trading-corp --since "2 hours ago" --no-pager 2>/dev/null | grep -iE "reconcil|halt|diverg|orphan|flat" | tail -15

echo "=== journal: staleness gate (1d) ==="
journalctl -u trading-corp --since "1 day ago" --no-pager 2>/dev/null | grep -iE "stale" | tail -6

echo "=== journal: bitunix fills/positions (6h) ==="
journalctl -u trading-corp --since "6 hours ago" --no-pager 2>/dev/null | grep -iE "bitunix.*(fill|position|entry|exit)|filled_qty|opened position" | tail -12
