#!/bin/bash
# Read-only data pull for fee-gate replay. SELECT-only, -readonly.
DB=/home/azureuser/trading_corp/data/trading_corp.db
Q="json_extract(payload_json,"
P='$.'

echo "===VAL==="
# Fired rows (should_trade=1): validate build_trade_plan(stored inputs)==stored plan.
sqlite3 -readonly -separator ',' "$DB" "
SELECT ts,
  json_extract(payload_json,'\$.entry'),
  json_extract(payload_json,'\$.score_side'),
  json_extract(payload_json,'\$.inputs.atr_used'),
  json_extract(payload_json,'\$.inputs.swing_low'),
  json_extract(payload_json,'\$.inputs.swing_high'),
  json_extract(payload_json,'\$.inputs.resistance'),
  json_extract(payload_json,'\$.inputs.support'),
  json_extract(payload_json,'\$.stop_loss'),
  json_extract(payload_json,'\$.tp1'),
  json_extract(payload_json,'\$.tp2'),
  json_extract(payload_json,'\$.tp3'),
  json_extract(payload_json,'\$.sl_method'),
  json_extract(payload_json,'\$.tp2_method')
FROM audit_event
WHERE kind='trade_plan_decision' AND ts>='2026-06-09'
  AND json_extract(payload_json,'\$.should_trade')=1
ORDER BY ts;"

echo "===DECL==="
# Fee-declined rows: ts, entry, side, atr_used, swl, swh, res, sup
sqlite3 -readonly -separator ',' "$DB" "
SELECT ts,
  json_extract(payload_json,'\$.entry'),
  json_extract(payload_json,'\$.score_side'),
  json_extract(payload_json,'\$.inputs.atr_used'),
  json_extract(payload_json,'\$.inputs.swing_low'),
  json_extract(payload_json,'\$.inputs.swing_high'),
  json_extract(payload_json,'\$.inputs.resistance'),
  json_extract(payload_json,'\$.inputs.support'),
  json_extract(payload_json,'\$.score_tier'),
  json_extract(payload_json,'\$.trigger_signal')
FROM audit_event
WHERE kind='trade_plan_decision' AND ts>='2026-06-09'
  AND json_extract(payload_json,'\$.skip_reason')='fees_too_high_for_risk'
ORDER BY ts;"

echo "===BARS3M==="
sqlite3 -readonly -separator ',' "$DB" "
SELECT ts_ms, open, high, low, close FROM bitunix_bar_history
WHERE timeframe='3m' AND ts_ms>=1780000000000 ORDER BY ts_ms;"
echo "===END==="
