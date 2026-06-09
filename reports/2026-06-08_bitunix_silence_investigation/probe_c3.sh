#!/usr/bin/env bash
# Thread C call 3: reveal extra_json structure (sim_filled_legs / sim_r key names + values) via
# json_each, + extra_json length per trade. order_id is the key. READ-ONLY.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db
W="order_id LIKE 'c8f25d17%' OR order_id LIKE 'ac5f9c59%' OR order_id LIKE 'c2eb7cda%'"

echo "=== C3a: extra_json top-level keys + value previews (c2eb7cda — biggest delta) ==="
sqlite3 -line "$DB" "SELECT je.key AS k, substr(je.value,1,100) AS v FROM paper_trade_record p, json_each(p.extra_json) je WHERE p.order_id LIKE 'c2eb7cda%';"
echo

echo "=== C3b: extra_json length per trade (how much is past the C2 600c cap?) ==="
sqlite3 -column "$DB" "SELECT substr(order_id,1,8) AS id8, length(extra_json) AS len FROM paper_trade_record WHERE $W ORDER BY ts;"
echo "=== END call C3 (read-only) ==="
