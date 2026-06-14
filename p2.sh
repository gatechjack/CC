#!/bin/bash
# Read-only probe #2 — entry-timing scoping. SELECT-only, -readonly.
DB=/home/azureuser/trading_corp/data/trading_corp.db
run() { echo "--- $1 ---"; sqlite3 -readonly "$DB" "$2" 2>&1 || true; echo ""; }
echo "=== IDENTITY ==="; hostname; date -u +%Y-%m-%dT%H:%M:%SZ

echo "=== FIRES over window (paper_trade_record, bitunix_futures, >=2026-06-09) ==="
run "by execution_mode + result" \
"SELECT COALESCE(json_extract(extra_json,'\$.execution_mode'),'paper') mode, result, COUNT(*) n
 FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09'
 GROUP BY mode, result ORDER BY mode, n DESC;"

run "redeem fraction + bars_waited dist" \
"SELECT COALESCE(json_extract(extra_json,'\$.redeemed'),'(none)') redeemed,
        json_extract(extra_json,'\$.bars_waited') bars_waited,
        json_extract(extra_json,'\$.seconds_waited') secs_waited, COUNT(*) n
 FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09'
 GROUP BY redeemed, bars_waited ORDER BY n DESC;"

run "tp_plan_version + filled_legs dist" \
"SELECT json_extract(extra_json,'\$.tp_plan_version') v,
        COALESCE(json_extract(extra_json,'\$.filled_legs'),'(null)') legs, result, COUNT(*) n
 FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09'
 GROUP BY v, legs, result ORDER BY n DESC;"

run "sample REDEEM extra_json (1)" \
"SELECT ts, side, entry_price, result, extra_json FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts>='2026-06-09'
   AND json_extract(extra_json,'\$.redeemed') IS NOT NULL
 ORDER BY ts LIMIT 1;"

run "sample NON-redeem extra_json (1)" \
"SELECT ts, side, entry_price, result, extra_json FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts>='2026-06-09'
   AND json_extract(extra_json,'\$.redeemed') IS NULL
 ORDER BY ts DESC LIMIT 1;"

run "paper_trade_record columns" "PRAGMA table_info(paper_trade_record);"
echo "=== DONE P2 ==="
