#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
YAML=/home/azureuser/trading_corp/config/strategies.yaml

V2_FLIP="2026-05-17T05:14:00+00:00"
H2_LIVE="2026-05-16T19:21:00+00:00"
PRE_H2_START="2026-05-10T00:00:00+00:00"

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

echo "=== A: strategies.yaml bitunix_futures.scoring.factors (full block) ==="
sed -n '/^bitunix_futures:/,/^[a-zA-Z]/{
  /^bitunix_futures:/p
  /^  /p
}' "$YAML" | sed -n '/^  scoring:/,/^  [a-z]/p' | head -350
echo "=== /A ==="
echo ""

run "Step1a-post: webhook signal counts by strategy + signal since v2 flip" \
"SELECT json_extract(payload_json,'\$.strategy') strategy,
        json_extract(payload_json,'\$.signal') signal,
        COUNT(*) n
 FROM audit_event
 WHERE kind='webhook_received' AND ts>='$V2_FLIP'
   AND json_extract(payload_json,'\$.strategy') IN ('market_cypher','lord_otter')
 GROUP BY strategy, signal
 ORDER BY n DESC;"

run "Step1a-pre: webhook signal counts in PRE-H2 window (5/10 -> 5/16 19:21)" \
"SELECT json_extract(payload_json,'\$.strategy') strategy,
        json_extract(payload_json,'\$.signal') signal,
        COUNT(*) n
 FROM audit_event
 WHERE kind='webhook_received'
   AND ts>='$PRE_H2_START' AND ts<'$H2_LIVE'
   AND json_extract(payload_json,'\$.strategy') IN ('market_cypher','lord_otter')
 GROUP BY strategy, signal
 ORDER BY n DESC;"

run "Step1c: bitunix_signal_ledger schema" \
"SELECT sql FROM sqlite_master WHERE type='table' AND name='bitunix_signal_ledger';"

run "Step1c: ledger column list" "PRAGMA table_info(bitunix_signal_ledger);"

run "Step1d-pre-H2: score_decided FRESH side mix 5/10 -> 5/16 19:21" \
"SELECT json_extract(payload_json,'\$.side') side, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts>='$PRE_H2_START' AND ts<'$H2_LIVE'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY side ORDER BY n DESC;"

run "Step1d-h2-pre-v2: score_decided FRESH side mix 5/16 19:21 -> 5/17 05:14" \
"SELECT json_extract(payload_json,'\$.side') side, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts>='$H2_LIVE' AND ts<'$V2_FLIP'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY side ORDER BY n DESC;"

run "Step1d-post-v2: score_decided FRESH side mix 5/17 05:14 -> now" \
"SELECT json_extract(payload_json,'\$.side') side, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$V2_FLIP'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY side ORDER BY n DESC;"

run "Step1d-PREMIUM-tier across windows" \
"SELECT
  CASE WHEN ts<'$H2_LIVE' THEN 'PRE_H2'
       WHEN ts<'$V2_FLIP' THEN 'H2_PRE_V2'
       ELSE 'POST_V2' END AS window,
  json_extract(payload_json,'\$.side') side,
  COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts>='$PRE_H2_START'
   AND json_extract(payload_json,'\$.tier')='PREMIUM'
   AND json_extract(payload_json,'\$.trigger_source') IN ('market_cypher','lord_otter')
 GROUP BY window, side
 ORDER BY window, n DESC;"

echo "=== PART 1 DONE ==="
