#!/bin/bash
# Read-only data pull for entry-timing counterfactual. SELECT-only, -readonly.
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "===FIRES==="
# order_id, fire_ts, signal, side, entry(signal px), stop, result, result_px, actual_r,
# bars_to_res, redeemed, bars_waited, secs_waited, original_cached_at, filled_legs, exec_mode
sqlite3 -readonly -separator '|' "$DB" "
SELECT order_id, ts, source_signal, side, entry_reference_price, stop_price,
  COALESCE(result,''), COALESCE(result_price,''), COALESCE(actual_r_multiple,''),
  COALESCE(bars_to_resolution,''),
  COALESCE(json_extract(extra_json,'\$.redeemed'),0),
  COALESCE(json_extract(extra_json,'\$.bars_waited'),''),
  COALESCE(json_extract(extra_json,'\$.seconds_waited'),''),
  COALESCE(json_extract(extra_json,'\$.original_cached_at'),''),
  COALESCE(json_extract(extra_json,'\$.filled_legs'),'[]'),
  COALESCE(json_extract(extra_json,'\$.execution_mode'),'paper')
FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09'
ORDER BY ts;"

echo "===PLAN==="
# fired trade_plan_decision rows -> full 3-leg geometry (join to FIRES by signal+nearest ts)
sqlite3 -readonly -separator '|' "$DB" "
SELECT ts, json_extract(payload_json,'\$.trigger_signal'),
  json_extract(payload_json,'\$.entry'), json_extract(payload_json,'\$.score_side'),
  json_extract(payload_json,'\$.inputs.atr_used'),
  json_extract(payload_json,'\$.inputs.swing_low'), json_extract(payload_json,'\$.inputs.swing_high'),
  json_extract(payload_json,'\$.inputs.resistance'), json_extract(payload_json,'\$.inputs.support'),
  json_extract(payload_json,'\$.stop_loss'), json_extract(payload_json,'\$.tp1'),
  json_extract(payload_json,'\$.tp2'), json_extract(payload_json,'\$.tp3'),
  json_extract(payload_json,'\$.sl_method'), json_extract(payload_json,'\$.tp2_method')
FROM audit_event WHERE kind='trade_plan_decision' AND ts>='2026-06-09'
  AND json_extract(payload_json,'\$.should_trade')=1
ORDER BY ts;"

echo "===BARS3M==="
sqlite3 -readonly -separator ',' "$DB" "
SELECT ts_ms, open, high, low, close FROM bitunix_bar_history
WHERE timeframe='3m' AND ts_ms>=1779900000000 ORDER BY ts_ms;"
echo "===END==="
