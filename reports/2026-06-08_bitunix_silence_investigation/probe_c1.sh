#!/usr/bin/env bash
# Thread C call 1: schema discovery for paper_trade_record — find the id column + recorded/sim
# R / leg / extra fields, so C2 can pull the 3 mismatched trades precisely. READ-ONLY.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== C1a: paper_trade_record full schema ==="
sqlite3 -header -column "$DB" "PRAGMA table_info(paper_trade_record);"
echo

echo "=== C1b: relevant columns (id / sim / record / R / pnl / leg / extra) ==="
sqlite3 "$DB" "SELECT name, type FROM pragma_table_info('paper_trade_record') WHERE name LIKE '%id%' OR name LIKE '%sim%' OR name LIKE '%record%' OR name LIKE '%_r%' OR name LIKE '%pnl%' OR name LIKE '%leg%' OR name LIKE '%extra%' OR name LIKE '%fill%';"
echo

echo "=== C1c: row count + division for the 3 target IDs (via id prefix) ==="
sqlite3 -header -column "$DB" "SELECT substr(id,1,8) AS id8, division, ts FROM paper_trade_record WHERE id LIKE 'c8f25d17%' OR id LIKE 'ac5f9c59%' OR id LIKE 'c2eb7cda%' ORDER BY ts;" 2>&1 | head -8
echo "  ^ if 'no such column: id' above, C2 will use the id column from C1a instead"
echo "=== END call C1 (read-only) ==="
