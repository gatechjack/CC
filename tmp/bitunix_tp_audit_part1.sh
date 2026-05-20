#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# Part 1.0 — schema
run "P1.0: paper_trade_record full column list" "PRAGMA table_info(paper_trade_record);"

# Part 1.1 — ALL bitunix rows since 5/18 + version-key forensics
run "P1.1: ALL bitunix paper_trade_record since 5/18, version-key forensics" \
"SELECT ts, side, result,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v_version,
        json_extract(extra_json,'\$.plan_version') p_version,
        json_extract(extra_json,'\$.version') version,
        CASE WHEN json_extract(extra_json,'\$.tp_plan') IS NOT NULL THEN 'yes' ELSE 'no' END has_tp_plan,
        json_extract(extra_json,'\$.tp_plan.version') tp_inner_v,
        json_extract(extra_json,'\$.redeemed') redeemed
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND ts >= '2026-05-18T00:00:00+00:00'
 ORDER BY ts ASC;"

# Part 1.2 — distinct top-level extra_json keys
run "P1.2: distinct top-level extra_json keys on bitunix rows since 5/18" \
"WITH e AS (
  SELECT extra_json FROM paper_trade_record
  WHERE division='bitunix_futures' AND ts >= '2026-05-18T00:00:00+00:00'
)
SELECT j.key AS k, COUNT(*) n
FROM e, json_each(e.extra_json) j
GROUP BY k ORDER BY n DESC;"

# Part 1.3 — sample full extra_json
run "P1.3: full extra_json from each distinct 5/19 trade" \
"SELECT ts, extra_json
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts LIKE '2026-05-19%'
 ORDER BY ts ASC;"

echo "=== DONE PART 1 ==="
