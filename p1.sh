#!/bin/bash
# Read-only prod probe #1 — fee-gate analysis scoping. SELECT-only, -readonly flag.
DB=/home/azureuser/trading_corp/data/trading_corp.db
echo "=== IDENTITY ==="
hostname; date -u +%Y-%m-%dT%H:%M:%SZ
echo "--- execution_mode (strategies.yaml) ---"
grep -n "execution_mode" /home/azureuser/trading_corp/config/strategies.yaml | head -5
echo "--- prod fee block (strategies.yaml 1343-1349) ---"
sed -n '1340,1350p' /home/azureuser/trading_corp/config/strategies.yaml
echo "--- tp1_min_profit_multiplier ---"
grep -n "tp1_min_profit_multiplier" /home/azureuser/trading_corp/config/strategies.yaml

run() { echo "--- $1 ---"; sqlite3 -readonly "$DB" "$2" 2>&1 || true; echo ""; }

echo ""
echo "=== TRADE_PLAN_DECISION SKIP-REASON COUNTS (2026-06-09 -> now) ==="
run "by skip_reason" \
"SELECT COALESCE(json_extract(payload_json,'\$.skip_reason'),'<fired>') sr, COUNT(*) n
 FROM audit_event WHERE kind='trade_plan_decision' AND ts>='2026-06-09'
 GROUP BY sr ORDER BY n DESC;"

run "fees_too_high by day + side" \
"SELECT substr(ts,1,10) d, json_extract(payload_json,'\$.score_side') side, COUNT(*) n
 FROM audit_event WHERE kind='trade_plan_decision' AND ts>='2026-06-09'
   AND json_extract(payload_json,'\$.skip_reason')='fees_too_high_for_risk'
 GROUP BY d, side ORDER BY d;"

run "sample 1 full fees_too_high payload" \
"SELECT ts, payload_json FROM audit_event WHERE kind='trade_plan_decision'
   AND json_extract(payload_json,'\$.skip_reason')='fees_too_high_for_risk'
   AND ts>='2026-06-09' ORDER BY ts LIMIT 1;"

echo "=== BAR_HISTORY COVERAGE ==="
run "timeframes present" \
"SELECT timeframe, COUNT(*) n, MIN(ts_ms) mn, MAX(ts_ms) mx FROM bitunix_bar_history GROUP BY timeframe;"

run "3m coverage since 2026-06-09 (ms 1780000000000~)" \
"SELECT COUNT(*) n_3m, MIN(ts_ms) mn, MAX(ts_ms) mx FROM bitunix_bar_history
 WHERE timeframe='3m' AND ts_ms>=1780000000000;"
echo "=== DONE P1 ==="
