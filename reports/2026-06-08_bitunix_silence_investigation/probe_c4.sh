#!/usr/bin/env bash
# Thread C call 4: confirm RECORDED fill sets (filled_legs / current_sl) for all 3 trades.
# NOTE: extra_json has NO stored sim — "sim R" is a dashboard re-computation (see code-read).
# READ-ONLY.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db
W="order_id LIKE 'c8f25d17%' OR order_id LIKE 'ac5f9c59%' OR order_id LIKE 'c2eb7cda%'"

echo "=== C4: recorded fill sets per trade (filled_legs / current_sl / result_ts) ==="
sqlite3 -line "$DB" "SELECT substr(order_id,1,8) AS id8, ts, result, result_ts, bars_to_resolution AS bars, actual_r_multiple AS rec_r, json_extract(extra_json,'\$.tp_r_multiple') AS plan_r, json_extract(extra_json,'\$.filled_legs') AS filled_legs, json_extract(extra_json,'\$.current_sl') AS current_sl FROM paper_trade_record WHERE $W ORDER BY ts;"
echo "=== END call C4 (read-only) ==="
