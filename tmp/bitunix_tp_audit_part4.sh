#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

run "P2B: ALL position_sl_update audits ever — count + first 10" \
"SELECT COUNT(*) FROM audit_event WHERE kind='position_sl_update';"

run "P2B-rows: position_sl_update rows" \
"SELECT ts, substr(payload_json, 1, 400)
 FROM audit_event WHERE kind='position_sl_update'
 ORDER BY ts ASC LIMIT 20;"

run "P2C: position_sl_update by date" \
"SELECT substr(ts,1,10) day, COUNT(*) n
 FROM audit_event WHERE kind='position_sl_update'
 GROUP BY day ORDER BY day;"

run "P2D: bitunix_score_decided 5/19 with non-null order_id" \
"SELECT ts, json_extract(payload_json,'\$.order_id') order_id,
        json_extract(payload_json,'\$.tier') tier,
        json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.outcome') outcome
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts LIKE '2026-05-19%'
   AND json_extract(payload_json,'\$.order_id') IS NOT NULL;"

run "P2E: would_have_placed by date for bitunix_futures (post-5/17)" \
"SELECT substr(ts,1,10) day, COUNT(*) n
 FROM audit_event
 WHERE kind='would_have_placed'
   AND ts >= '2026-05-17T00:00:00+00:00'
   AND json_extract(payload_json,'\$.division')='bitunix_futures'
 GROUP BY day ORDER BY day;"

run "P2F: bitunix-strategy/division audit kinds in last 3 days" \
"SELECT kind, COUNT(*) n FROM audit_event
 WHERE ts >= '2026-05-18T00:00:00+00:00'
   AND (json_extract(payload_json,'\$.strategy')='bitunix_futures'
        OR json_extract(payload_json,'\$.division')='bitunix_futures')
 GROUP BY kind ORDER BY n DESC;"

run "P2G: paper_trade_record 5/19 across ALL divisions" \
"SELECT division, side, result,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v,
        ts
 FROM paper_trade_record
 WHERE ts LIKE '2026-05-19%'
 ORDER BY ts ASC LIMIT 50;"

run "P2G-counts: trades by division on 5/19" \
"SELECT division, COUNT(*) n, MIN(ts) first, MAX(ts) last
 FROM paper_trade_record
 WHERE ts LIKE '2026-05-19%'
 GROUP BY division ORDER BY n DESC;"

run "P2H: paper_trade_record where ts<5/19 but result_ts on 5/19 (resolved-on-5/19 trades)" \
"SELECT division, side, result, ts, result_ts,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v,
        bars_to_resolution
 FROM paper_trade_record
 WHERE result_ts LIKE '2026-05-19%'
 ORDER BY result_ts ASC LIMIT 50;"
