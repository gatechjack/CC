#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

run "webhook_received sample (cypher)" \
"SELECT ts, substr(payload_json, 1, 800)
 FROM audit_event
 WHERE kind='webhook_received'
   AND json_extract(payload_json,'\$.strategy')='market_cypher'
 ORDER BY ts DESC LIMIT 1;"

run "webhook_received sample (otter)" \
"SELECT ts, substr(payload_json, 1, 800)
 FROM audit_event
 WHERE kind='webhook_received'
   AND json_extract(payload_json,'\$.strategy')='lord_otter'
 ORDER BY ts DESC LIMIT 1;"

run "trade_plan_decision sample — skip (fees_too_high)" \
"SELECT ts, substr(payload_json, 1, 1500)
 FROM audit_event
 WHERE kind='trade_plan_decision'
   AND json_extract(payload_json,'\$.skip_reason')='fees_too_high_for_risk'
 ORDER BY ts DESC LIMIT 1;"

run "trade_plan_decision sample — fire (should_trade=1)" \
"SELECT ts, substr(payload_json, 1, 1500)
 FROM audit_event
 WHERE kind='trade_plan_decision'
   AND json_extract(payload_json,'\$.should_trade')=1
 ORDER BY ts DESC LIMIT 2;"

run "bitunix_bar_history schema" "PRAGMA table_info(bitunix_bar_history);"

run "bitunix_bar_history sample (recent 3m)" \
"SELECT ts_ms, timeframe, open, high, low, close, volume
 FROM bitunix_bar_history WHERE timeframe='3m'
 ORDER BY ts_ms DESC LIMIT 3;"

run "bitunix_bar_history row count by timeframe" \
"SELECT timeframe, COUNT(*) n, MIN(ts_ms) min_ts, MAX(ts_ms) max_ts
 FROM bitunix_bar_history GROUP BY timeframe;"

run "intrinsic_side helper signal: bitunix_score_decided has trigger_signal — sample" \
"SELECT json_extract(payload_json,'\$.trigger_signal') sig,
        json_extract(payload_json,'\$.side') side,
        COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='2026-05-17T05:14:00+00:00'
 GROUP BY sig, side ORDER BY n DESC LIMIT 20;"

echo "=== DONE ==="
