#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# The full extra_json (output is huge — paginate via row index)
run "P2A: full extra_json of 5/18 v2 trade #1" \
"SELECT order_id, ts, result, result_ts, result_price,
        actual_r_multiple, actual_pnl_dollars, bars_to_resolution,
        stop_price, tp_price, extra_json
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts='2026-05-18T16:24:02+00:00';"

run "P2A: full extra_json of 5/18 v2 trade #2" \
"SELECT order_id, ts, result, result_ts, result_price,
        actual_r_multiple, actual_pnl_dollars, bars_to_resolution,
        stop_price, tp_price, extra_json
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts='2026-05-18T18:30:05+00:00';"
