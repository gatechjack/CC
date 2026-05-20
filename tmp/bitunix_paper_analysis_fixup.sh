#!/bin/bash
# Fixup: drop set -e so one failure doesn't abort, get schema first.
DB=/home/azureuser/trading_corp/data/trading_corp.db
SINCE="${1:-2026-05-17T05:14:00+00:00}"

echo "=== Now: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo ""

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

run "schema: paper_trade_record" ".schema paper_trade_record"

run "schema: position" ".schema position"

run "schema: proposed_order" ".schema proposed_order"

run "Q3-fix: paper_trade_record columns inspect (one row)" \
"SELECT * FROM paper_trade_record WHERE division='bitunix_futures'
 ORDER BY opened_at DESC LIMIT 1;"

# Q3b retry without realized_r — generic result distribution
run "Q3b': result distribution since SINCE" \
"SELECT result, COUNT(*) n
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND opened_at>='$SINCE'
 GROUP BY result ORDER BY n DESC;"

# Q3b'': all-time bitunix paper_trade_record (catches anything before SINCE)
run "Q3b'': result distribution ALL-TIME" \
"SELECT result, COUNT(*) n
 FROM paper_trade_record
 WHERE division='bitunix_futures'
 GROUP BY result ORDER BY n DESC;"

run "Q3c': by tier via extra_json" \
"SELECT json_extract(extra_json,'\$.tier') tier,
        result, COUNT(*) n
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND opened_at>='$SINCE'
 GROUP BY tier, result;"

run "Q3d': redeemed-fire outcome via extra_json" \
"SELECT json_extract(extra_json,'\$.redeemed') redeemed,
        result, COUNT(*) n
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND opened_at>='$SINCE'
 GROUP BY redeemed, result;"

run "Q3e': trade-plan-v2 paper trades" \
"SELECT COUNT(*) n_v2_trades,
        SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins,
        SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) losses,
        SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) open
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND json_extract(extra_json,'\$.tp_plan_version')='v2'
   AND opened_at>='$SINCE';"

run "Q3f: position_sl_update counts since SINCE" \
"SELECT json_extract(payload_json,'\$.source') source,
        json_extract(payload_json,'\$.transition') transition,
        COUNT(*) n
 FROM audit_event
 WHERE kind='position_sl_update' AND ts>='$SINCE'
 GROUP BY source, transition ORDER BY n DESC;"

run "Q3g: position_sl_update — all-time, any" \
"SELECT json_extract(payload_json,'\$.source') source,
        json_extract(payload_json,'\$.transition') transition,
        COUNT(*) n
 FROM audit_event
 WHERE kind='position_sl_update'
 GROUP BY source, transition ORDER BY n DESC;"

run "Q4a: per-factor contribution on PREMIUM/STANDARD fires" \
"SELECT j.value AS factor, COUNT(*) n
 FROM audit_event ae,
      json_each(ae.payload_json, '\$.score_path') j
 WHERE ae.kind='bitunix_score_decided' AND ae.ts>='$SINCE'
   AND json_extract(ae.payload_json,'\$.tier') IN ('PREMIUM','STANDARD')
 GROUP BY factor ORDER BY n DESC LIMIT 40;"

run "Q4b: last 10 score_decided" \
"SELECT ts,
        json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.tier') tier,
        json_extract(payload_json,'\$.net_score') net,
        json_extract(payload_json,'\$.trigger_source') src
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$SINCE'
 ORDER BY ts DESC LIMIT 10;"

run "Q4c: webhook_received bitunix-related (Otter+Cypher webhooks fan into bitunix observer)" \
"SELECT json_extract(payload_json,'\$.strategy') strategy,
        json_extract(payload_json,'\$.division') division,
        COUNT(*) n
 FROM audit_event
 WHERE kind='webhook_received' AND ts>='$SINCE'
 GROUP BY strategy, division ORDER BY n DESC LIMIT 20;"

run "Q5: PA pass details — what survived the gate" \
"SELECT ts, json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.trigger_source') src,
        json_extract(payload_json,'\$.decision') d,
        json_extract(payload_json,'\$.tier') tier
 FROM audit_event
 WHERE kind='pa_validation_decision' AND ts>='$SINCE'
   AND json_extract(payload_json,'\$.decision')='pass'
 ORDER BY ts DESC LIMIT 20;"

run "Q6: trade_plan_decision detail — last 11" \
"SELECT ts,
        json_extract(payload_json,'\$.should_trade') st,
        json_extract(payload_json,'\$.skip_reason') skip,
        json_extract(payload_json,'\$.tier') tier,
        json_extract(payload_json,'\$.sl_method') sl,
        json_extract(payload_json,'\$.tp_plan_version') v,
        json_extract(payload_json,'\$.fee_floor_pct') fee,
        json_extract(payload_json,'\$.tp2_r') tp2r
 FROM audit_event
 WHERE kind='trade_plan_decision' AND ts>='$SINCE'
 ORDER BY ts ASC;"

run "Q7: paper_trade_record ALL bitunix rows — full chronology" \
"SELECT opened_at, side, result,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v,
        json_extract(extra_json,'\$.redeemed') rd
 FROM paper_trade_record
 WHERE division='bitunix_futures'
 ORDER BY opened_at ASC;"

echo "=== FIXUP COMPLETE ==="
