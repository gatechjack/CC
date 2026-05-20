#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
SINCE="2026-05-17T05:14:00+00:00"

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

run "schema: paper_trade_record FULL" \
"SELECT sql FROM sqlite_master WHERE type='table' AND name='paper_trade_record';"

run "paper_trade_record columns" "PRAGMA table_info(paper_trade_record);"

run "paper_trade_record sample row (one bitunix)" \
"SELECT * FROM paper_trade_record WHERE division='bitunix_futures' ORDER BY ts DESC LIMIT 1;"

run "Q3b SINCE: result distribution" \
"SELECT result, COUNT(*) n
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts>='$SINCE'
 GROUP BY result ORDER BY n DESC;"

run "Q3c SINCE: by tier x result" \
"SELECT json_extract(extra_json,'\$.tier') tier, result, COUNT(*) n
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts>='$SINCE'
 GROUP BY tier, result;"

run "Q3e SINCE: trade-plan-v2 subset" \
"SELECT COUNT(*) n, result
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND json_extract(extra_json,'\$.tp_plan_version')='v2'
 GROUP BY result;"

run "Q7: full chronology of bitunix paper trades" \
"SELECT ts, side, result,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v,
        json_extract(extra_json,'\$.redeemed') rd
 FROM paper_trade_record
 WHERE division='bitunix_futures'
 ORDER BY ts ASC;"

run "Q4a-fix: score_path payload sample (one PREMIUM row)" \
"SELECT substr(payload_json, 1, 1200)
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND json_extract(payload_json,'\$.tier')='PREMIUM'
 ORDER BY ts DESC LIMIT 1;"

run "Q4a-fix: score_path is a list-of-strings? try json_each on contributing_factors" \
"SELECT j.value AS factor, COUNT(*) n
 FROM audit_event ae, json_each(ae.payload_json, '\$.contributing_factors') j
 WHERE ae.kind='bitunix_score_decided' AND ae.ts>='$SINCE'
   AND json_extract(ae.payload_json,'\$.tier') IN ('PREMIUM','STANDARD')
 GROUP BY factor ORDER BY n DESC LIMIT 30;"

echo "=== DONE ==="
