#!/usr/bin/env bash
# Fee-model verification probe. READ-ONLY. Confirm deployed fees == local; find any recorded fee.
DB=/home/azureuser/trading_corp/data/trading_corp.db
CFG=/home/azureuser/trading_corp/config/strategies.yaml
echo "===PROD_FEES_BLOCK (deployed strategies.yaml)==="
grep -n -E "taker_pct|maker_pct|slippage_pct|entry_is_taker|tp_is_maker|Experience Card|VIP" "$CFG"
echo "===PTR_FEE_LIKE_COLUMNS==="
sqlite3 -readonly "$DB" "SELECT name FROM pragma_table_info('paper_trade_record') WHERE name LIKE '%fee%';"
echo "===PTR_EXTRA_JSON_SAMPLE (first post-fix taken)==="
sqlite3 -readonly "$DB" "SELECT order_id, extra_json FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09T03:49:41+00:00' ORDER BY ts LIMIT 1;"
echo "===PTR_ACTUAL_PNL_VS_R (do dollars carry fees? compare to R)==="
sqlite3 -readonly -csv "$DB" "SELECT substr(order_id,1,8) oid, actual_pnl_dollars, actual_r_multiple, entry_reference_price, stop_price FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09T03:49:41+00:00' ORDER BY ts LIMIT 4;"
echo "===END==="
