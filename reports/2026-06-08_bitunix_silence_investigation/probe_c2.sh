#!/usr/bin/env bash
# Thread C call 2: recorded core fields + extra_json (sim_filled_legs / sim R) for the 3
# mismatched trades. order_id is the key (TEXT pk). READ-ONLY. extra_json printed LAST + capped.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db
W="order_id LIKE 'c8f25d17%' OR order_id LIKE 'ac5f9c59%' OR order_id LIKE 'c2eb7cda%'"

echo "=== C2a: recorded core fields (3 trades, by ts) ==="
sqlite3 -line "$DB" "SELECT substr(order_id,1,8) AS id8, ts, division, symbol, side, tier, entry_reference_price AS entry, stop_price AS stop, tp_price, tp_r_multiple AS plan_r, result, result_price, actual_pnl_dollars AS pnl, actual_r_multiple AS rec_r, bars_to_resolution AS bars FROM paper_trade_record WHERE $W ORDER BY ts;"
echo

echo "=== C2b: extra_json (capped 600c) — sim_filled_legs / sim R / tp_plan ==="
sqlite3 -line "$DB" "SELECT substr(order_id,1,8) AS id8, substr(extra_json,1,600) AS extra FROM paper_trade_record WHERE $W ORDER BY ts;"
echo "=== END call C2 (read-only) ==="
