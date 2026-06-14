#!/bin/bash
# Read-only probe #3 — per-stage latency confirmation. SELECT-only, -readonly.
DB=/home/azureuser/trading_corp/data/trading_corp.db
run() { echo "--- $1 ---"; sqlite3 -readonly "$DB" "$2" 2>&1 || true; echo ""; }

echo "=== NON-REDEEM fire chain (cvd_bear_flip ~06-09 04:57, tight 2min window) ==="
run "audit chain" \
"SELECT substr(ts,12,12) t, kind FROM audit_event
 WHERE json_extract(payload_json,'\$.trigger_signal')='cvd_bear_flip'
   AND ts>='2026-06-09T04:56:00' AND ts<='2026-06-09T04:58:30'
   AND kind IN ('bitunix_score_decided','pa_validation_decision','pa_validation_redeem','htf_gate_decision','trade_plan_decision')
 ORDER BY ts;"

echo "=== REDEEM fire chain (mc_b_sell_circle_div, cached 05:42:03 -> fired 05:58:38) ==="
run "audit chain (reject@cache .. pass@fire)" \
"SELECT substr(ts,12,12) t, kind,
   COALESCE(json_extract(payload_json,'\$.decision'),json_extract(payload_json,'\$.skip_reason'),'') detail
 FROM audit_event
 WHERE json_extract(payload_json,'\$.trigger_signal')='mc_b_sell_circle_div'
   AND ts>='2026-06-09T05:41:30' AND ts<='2026-06-09T05:59:30'
   AND kind IN ('bitunix_score_decided','pa_validation_decision','pa_validation_redeem','htf_gate_decision','trade_plan_decision')
 ORDER BY ts;"

echo "=== redeem-wait consistency: seconds_waited vs (fire_ts - original_cached_at) ==="
run "sample 5 redeem fires" \
"SELECT substr(ts,12,8) fire_t, json_extract(extra_json,'\$.seconds_waited') secs,
   json_extract(extra_json,'\$.bars_waited') bars, substr(json_extract(extra_json,'\$.original_cached_at'),12,8) cached_t
 FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-09'
   AND json_extract(extra_json,'\$.redeemed')=1 AND json_extract(extra_json,'\$.bars_waited')>=1
 ORDER BY ts LIMIT 5;"
echo "=== DONE P3 ==="
