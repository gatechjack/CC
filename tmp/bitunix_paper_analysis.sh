#!/bin/bash
set -e
DB=/home/azureuser/trading_corp/data/trading_corp.db
SINCE="${1:-2026-05-17T05:14:00+00:00}"

echo "=== DB: $DB ==="
echo "=== Since: $SINCE  (trade-plan v2 flip @ 2026-05-17 05:14 UTC) ==="
echo "=== Now: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo ""

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2"
  echo ""
}

run "audit_event schema" ".schema audit_event"

run "Q0: all bitunix-relevant audit kinds, total counts (all-time)" \
"SELECT kind, COUNT(*) AS n
 FROM audit_event
 WHERE kind LIKE 'bitunix%' OR kind LIKE 'pa_%' OR kind LIKE 'htf_%'
    OR kind LIKE 'trade_plan%' OR kind LIKE 'position_sl%'
    OR kind = 'would_have_placed' OR kind = 'webhook_received'
    OR kind = 'risk_decision'
 GROUP BY kind ORDER BY n DESC;"

run "Q0b: same since SINCE, filtered to bitunix_futures where division is in payload" \
"SELECT kind, COUNT(*) AS n
 FROM audit_event
 WHERE ts >= '$SINCE'
   AND (kind LIKE 'bitunix%' OR kind LIKE 'pa_validation%' OR kind LIKE 'htf_gate%'
        OR kind LIKE 'trade_plan%' OR kind LIKE 'position_sl%'
        OR (kind IN ('would_have_placed','webhook_received','risk_decision')
            AND json_extract(payload_json,'\$.division')='bitunix_futures'))
 GROUP BY kind ORDER BY n DESC;"

run "Q1a: webhook_received for bitunix_futures, by strategy, since SINCE" \
"SELECT json_extract(payload_json,'\$.strategy') AS strategy, COUNT(*) n
 FROM audit_event
 WHERE kind='webhook_received' AND ts>='$SINCE'
   AND json_extract(payload_json,'\$.division')='bitunix_futures'
 GROUP BY strategy ORDER BY n DESC;"

run "Q1b: bitunix_score_decided — tier distribution since SINCE" \
"SELECT json_extract(payload_json,'\$.tier') AS tier, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$SINCE'
 GROUP BY tier ORDER BY n DESC;"

run "Q1c: bitunix_score_decided — trigger_source distribution since SINCE" \
"SELECT json_extract(payload_json,'\$.trigger_source') AS source, COUNT(*) n
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$SINCE'
 GROUP BY source ORDER BY n DESC;"

run "Q1d: bitunix_score_decided — side x tier since SINCE" \
"SELECT json_extract(payload_json,'\$.side') AS side,
        json_extract(payload_json,'\$.tier') AS tier,
        COUNT(*) n,
        ROUND(AVG(CAST(json_extract(payload_json,'\$.net_score') AS INT)), 2) avg_net
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$SINCE'
 GROUP BY side, tier ORDER BY side, tier;"

run "Q2a: PA validation decisions since SINCE" \
"SELECT json_extract(payload_json,'\$.decision') d,
        json_extract(payload_json,'\$.mode') mode,
        COUNT(*) n
 FROM audit_event
 WHERE kind='pa_validation_decision' AND ts>='$SINCE'
 GROUP BY d, mode ORDER BY n DESC;"

run "Q2b: PA rejects — failed validators since SINCE (json_each over failed[])" \
"SELECT j.value AS validator, COUNT(*) n
 FROM audit_event ae,
      json_each(ae.payload_json, '\$.failed') j
 WHERE ae.kind='pa_validation_decision' AND ae.ts>='$SINCE'
   AND json_extract(ae.payload_json,'\$.decision')='reject'
 GROUP BY validator ORDER BY n DESC;"

run "Q2c: PA redeem outcomes since SINCE" \
"SELECT json_extract(payload_json,'\$.outcome') outcome, COUNT(*) n
 FROM audit_event
 WHERE kind='pa_validation_redeem' AND ts>='$SINCE'
 GROUP BY outcome ORDER BY n DESC;"

run "Q2d: PA expired-waits since SINCE" \
"SELECT json_extract(payload_json,'\$.reason') reason, COUNT(*) n
 FROM audit_event
 WHERE kind='pa_validation_expired' AND ts>='$SINCE'
 GROUP BY reason ORDER BY n DESC;"

run "Q2e: HTF gate — size_multiplier distribution since SINCE" \
"SELECT CAST(json_extract(payload_json,'\$.size_multiplier') AS REAL) mult, COUNT(*) n
 FROM audit_event
 WHERE kind='htf_gate_decision' AND ts>='$SINCE'
 GROUP BY mult ORDER BY mult;"

run "Q2f: HTF gate — hard-zero reasons since SINCE" \
"SELECT json_extract(payload_json,'\$.hard_zero_reason') reason, COUNT(*) n
 FROM audit_event
 WHERE kind='htf_gate_decision' AND ts>='$SINCE'
   AND json_extract(payload_json,'\$.hard_zero_reason') IS NOT NULL
 GROUP BY reason ORDER BY n DESC;"

run "Q2g: HTF gate — regime distribution since SINCE" \
"SELECT json_extract(payload_json,'\$.regime') regime, COUNT(*) n
 FROM audit_event
 WHERE kind='htf_gate_decision' AND ts>='$SINCE'
 GROUP BY regime ORDER BY n DESC;"

run "Q2h: trade_plan_decision since SINCE — should_trade x skip_reason" \
"SELECT json_extract(payload_json,'\$.should_trade') should_trade,
        json_extract(payload_json,'\$.skip_reason') skip_reason,
        json_extract(payload_json,'\$.sl_method') sl_method,
        COUNT(*) n
 FROM audit_event
 WHERE kind='trade_plan_decision' AND ts>='$SINCE'
 GROUP BY should_trade, skip_reason, sl_method ORDER BY n DESC;"

run "Q3a: would_have_placed for bitunix_futures since SINCE" \
"SELECT COUNT(*) total,
        SUM(CASE WHEN json_extract(payload_json,'\$.side')='buy' THEN 1 ELSE 0 END) buys,
        SUM(CASE WHEN json_extract(payload_json,'\$.side')='sell' THEN 1 ELSE 0 END) sells
 FROM audit_event
 WHERE kind='would_have_placed' AND ts>='$SINCE'
   AND json_extract(payload_json,'\$.division')='bitunix_futures';"

run "Q3b: paper_trade_record for bitunix_futures since SINCE — result distribution" \
"SELECT result, COUNT(*) n,
        ROUND(AVG(realized_r),3) avg_r,
        ROUND(SUM(realized_r),3) total_r
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND opened_at>='$SINCE'
 GROUP BY result ORDER BY n DESC;"

run "Q3c: paper_trade_record bitunix_futures — by tier x result" \
"SELECT json_extract(extra_json,'\$.tier') tier,
        result, COUNT(*) n,
        ROUND(AVG(realized_r),3) avg_r
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND opened_at>='$SINCE'
 GROUP BY tier, result ORDER BY tier, result;"

run "Q3d: paper_trade_record — redeemed-fire outcome since SINCE" \
"SELECT json_extract(extra_json,'\$.redeemed') redeemed,
        result, COUNT(*) n,
        ROUND(AVG(realized_r),3) avg_r
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND opened_at>='$SINCE'
 GROUP BY redeemed, result ORDER BY redeemed, n DESC;"

run "Q3e: trade-plan-v2 paper trades (tp_plan_version='v2') — counts" \
"SELECT COUNT(*) n_v2_trades,
        SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins,
        SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) losses,
        SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) open
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND json_extract(extra_json,'\$.tp_plan_version')='v2'
   AND opened_at>='$SINCE';"

run "Q3f: position_sl_update counts since SINCE — lifecycle events" \
"SELECT json_extract(payload_json,'\$.source') source,
        json_extract(payload_json,'\$.transition') transition,
        COUNT(*) n
 FROM audit_event
 WHERE kind='position_sl_update' AND ts>='$SINCE'
 GROUP BY source, transition ORDER BY n DESC;"

run "Q4a: per-factor contribution from bitunix_score_decided.score_path (PREMIUM/STANDARD fires)" \
"SELECT j.value AS factor, COUNT(*) n
 FROM audit_event ae,
      json_each(ae.payload_json, '\$.score_path') j
 WHERE ae.kind='bitunix_score_decided' AND ae.ts>='$SINCE'
   AND json_extract(ae.payload_json,'\$.tier') IN ('PREMIUM','STANDARD')
 GROUP BY factor ORDER BY n DESC LIMIT 30;"

run "Q4b: last 10 score_decided rows — ts, side, tier, net_score" \
"SELECT ts,
        json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.tier') tier,
        json_extract(payload_json,'\$.net_score') net,
        json_extract(payload_json,'\$.trigger_source') src
 FROM audit_event
 WHERE kind='bitunix_score_decided' AND ts>='$SINCE'
 ORDER BY ts DESC LIMIT 10;"

run "Q4c: boot wiring sanity — most recent service-start marker" \
"SELECT ts, kind, substr(payload_json, 1, 200)
 FROM audit_event
 WHERE kind IN ('service_started','boot_complete','bitunix_observer_wiring')
   OR (kind='log_event' AND payload_json LIKE '%BitUnix observer wiring%')
 ORDER BY ts DESC LIMIT 3;"

echo "=== ANALYSIS COMPLETE ==="
