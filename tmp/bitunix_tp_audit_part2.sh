#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# Full extra_json of the 2 v2 trades
run "P2A: full extra_json of the 2 5/18 v2 trades (with order_id)" \
"SELECT order_id, ts, result, result_ts, result_price, actual_r_multiple, bars_to_resolution, extra_json
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts LIKE '2026-05-18T1%'
 ORDER BY ts ASC;"

# All position_sl_update rows EVER for bitunix
run "P2B: ALL position_sl_update audits ever (no division filter; show payload)" \
"SELECT ts, substr(payload_json, 1, 600)
 FROM audit_event WHERE kind='position_sl_update'
 ORDER BY ts ASC LIMIT 50;"

# Position_sl_update count by date
run "P2C: position_sl_update counts by date" \
"SELECT substr(ts,1,10) day, COUNT(*) n
 FROM audit_event WHERE kind='position_sl_update'
 GROUP BY day ORDER BY day;"

# Any bitunix-related activity 5/19
run "P2D: any bitunix_score_decided rows on 5/19 with order_id IS NOT NULL" \
"SELECT ts, json_extract(payload_json,'\$.order_id') order_id,
        json_extract(payload_json,'\$.tier') tier,
        json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.outcome') outcome
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts LIKE '2026-05-19%'
   AND json_extract(payload_json,'\$.order_id') IS NOT NULL;"

run "P2E: would_have_placed for bitunix_futures since 5/18 (full date list)" \
"SELECT ts, json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.symbol') symbol,
        json_extract(payload_json,'\$.qty') qty,
        json_extract(payload_json,'\$.price') price,
        json_extract(payload_json,'\$.division') division
 FROM audit_event
 WHERE kind='would_have_placed'
   AND ts >= '2026-05-18T00:00:00+00:00'
   AND json_extract(payload_json,'\$.division')='bitunix_futures'
 ORDER BY ts ASC;"

# Look for OTHER audit kinds that might log TP progression
run "P2F: any audit kinds active in last 3 days with 'bitunix' in payload?" \
"SELECT kind, COUNT(*) n FROM audit_event
 WHERE ts >= '2026-05-18T00:00:00+00:00'
   AND (json_extract(payload_json,'\$.strategy')='bitunix_futures'
        OR json_extract(payload_json,'\$.division')='bitunix_futures')
 GROUP BY kind ORDER BY n DESC;"

# Other divisions on 5/19 — did the user maybe observe Donchian or Otter trades?
run "P2G: paper_trade_record on 5/19 across ALL divisions" \
"SELECT division, side, result,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v,
        ts
 FROM paper_trade_record
 WHERE ts LIKE '2026-05-19%'
 ORDER BY ts ASC LIMIT 50;"

# Source code: line ~741 routing condition
echo "=== B: paper_trade_replay routing condition (line 735-770) ==="
sed -n '735,775p' /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py 2>&1
echo "=== /B ==="
echo ""

# Source code: _classify_v2_multi_leg signature + the SL-update emission
echo "=== C: paper_trade_replay _classify_v2_multi_leg head (line 401-460) ==="
sed -n '401,470p' /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py 2>&1
echo "=== /C ==="

echo "=== DONE PART 2 ==="
