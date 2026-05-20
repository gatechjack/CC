#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# ──────────────────────────────────────────────────────────────────
# Part 1 — Trades without prejudging the filter
# ──────────────────────────────────────────────────────────────────

run "P1.0: paper_trade_record full column list" "PRAGMA table_info(paper_trade_record);"

run "P1.1: ALL bitunix paper_trade_record since 5/18 — version tag check" \
"SELECT ts, side, result,
        json_extract(extra_json,'\$.tier') tier,
        json_extract(extra_json,'\$.tp_plan_version') v_version,
        json_extract(extra_json,'\$.tp_plan_version_str') v_str,
        json_extract(extra_json,'\$.plan_version') p_version,
        json_extract(extra_json,'\$.version') version,
        json_extract(extra_json,'\$.tp_plan') has_tp_plan_obj,
        json_extract(extra_json,'\$.redeemed') redeemed
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND ts >= '2026-05-18T00:00:00+00:00'
 ORDER BY ts ASC;"

run "P1.2: distinct version-ish keys in any bitunix extra_json (all-time)" \
"WITH keys AS (
  SELECT j.key AS k, COUNT(*) n
  FROM paper_trade_record, json_each(extra_json) j
  WHERE division='bitunix_futures'
  GROUP BY k
)
SELECT k, n FROM keys ORDER BY n DESC;"

run "P1.3: sample full extra_json from one 5/19 bitunix row" \
"SELECT ts, substr(extra_json, 1, 1500)
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND ts LIKE '2026-05-19%'
 ORDER BY ts ASC LIMIT 3;"

# ──────────────────────────────────────────────────────────────────
# Part 2 — Reconcile lifecycle audits without version filter
# ──────────────────────────────────────────────────────────────────

run "P2.1: position_sl_update — ALL since 5/18, no filter" \
"SELECT ts,
        json_extract(payload_json,'\$.source') source,
        json_extract(payload_json,'\$.transition') transition,
        json_extract(payload_json,'\$.symbol') symbol,
        json_extract(payload_json,'\$.division') division,
        json_extract(payload_json,'\$.from_sl') from_sl,
        json_extract(payload_json,'\$.to_sl') to_sl,
        json_extract(payload_json,'\$.trade_id') trade_id,
        json_extract(payload_json,'\$.tp_level') tp_level
 FROM audit_event
 WHERE kind='position_sl_update'
   AND ts >= '2026-05-18T00:00:00+00:00'
 ORDER BY ts ASC;"

run "P2.1b: position_sl_update — ALL kinds, total counts since 5/18" \
"SELECT COUNT(*) FROM audit_event
 WHERE kind='position_sl_update'
   AND ts >= '2026-05-18T00:00:00+00:00';"

run "P2.2: TP-hit-flavored audit kinds (search for tp/leg/exit/fill kinds)" \
"SELECT kind, COUNT(*) n FROM audit_event
 WHERE ts >= '2026-05-18T00:00:00+00:00'
   AND (kind LIKE '%tp%' OR kind LIKE '%leg%' OR kind LIKE '%exit%'
        OR kind LIKE '%fill%' OR kind LIKE '%close%' OR kind LIKE '%resolve%'
        OR kind LIKE '%reconcil%' OR kind LIKE '%position%' OR kind LIKE '%paper%')
 GROUP BY kind ORDER BY n DESC;"

run "P2.3: paper_trade_record ALL columns sample row (verify schema)" \
"SELECT * FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND ts LIKE '2026-05-19%'
 ORDER BY ts DESC LIMIT 1;"

run "P2.4: paper_trade_record latest 5/19 row full extra_json" \
"SELECT ts, side, result, extra_json
 FROM paper_trade_record
 WHERE division='bitunix_futures'
   AND ts LIKE '2026-05-19%'
 ORDER BY ts ASC;"

run "P2.5: would_have_placed audits since 5/18 with TP+SL details" \
"SELECT ts,
        json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.symbol') symbol,
        json_extract(payload_json,'\$.qty') qty,
        json_extract(payload_json,'\$.price') price,
        json_extract(payload_json,'\$.stop_loss') sl,
        json_extract(payload_json,'\$.take_profit') tp,
        json_extract(payload_json,'\$.extra.tp_plan') tp_plan,
        json_extract(payload_json,'\$.extra.tp_plan_version') tpv,
        json_extract(payload_json,'\$.division') division
 FROM audit_event
 WHERE kind='would_have_placed'
   AND ts >= '2026-05-18T00:00:00+00:00'
   AND json_extract(payload_json,'\$.division')='bitunix_futures'
 ORDER BY ts ASC;"

run "P2.6: bitunix_score_decided that FIRED (outcome=placed/order_id != null) since 5/18" \
"SELECT ts,
        json_extract(payload_json,'\$.side') side,
        json_extract(payload_json,'\$.tier') tier,
        json_extract(payload_json,'\$.outcome') outcome,
        json_extract(payload_json,'\$.order_id') order_id,
        json_extract(payload_json,'\$.note') note
 FROM audit_event
 WHERE kind='bitunix_score_decided'
   AND ts >= '2026-05-18T00:00:00+00:00'
   AND json_extract(payload_json,'\$.order_id') IS NOT NULL
 ORDER BY ts ASC;"

# ──────────────────────────────────────────────────────────────────
# Part 3 — reconciler source-of-truth check
# ──────────────────────────────────────────────────────────────────
echo "=== B: reconciler code on prod ==="
head -120 /home/azureuser/trading_corp/trading_corp/agents/divisions/bitunix_position_reconciler.py 2>&1 | head -120
echo "=== /B ==="

echo "=== C: paper_trade_replay v2 classification routine ==="
grep -n "v2\|tp_plan_version\|position_sl_update\|_classify_v2_multi_leg\|filled_legs\|current_sl" \
  /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py 2>&1 | head -60
echo "=== /C ==="

echo "=== DONE ==="
