#!/usr/bin/env bash
# Step 2 — fresh-window evaluation probe (RUN ONLY AFTER Step 1 activation PASSES).
# Fire rate, outcomes, classifier sanity, anomaly scan over the fresh paper window
# starting 2026-06-09T03:49:41Z. READ-ONLY. SELECT only. No writes.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db
W='2026-06-09T03:49:41+00:00'

echo "=== CONTEXT ==="
echo "host=$(hostname) now_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) window_start=$W"
echo

echo "=== S2a: FIRE RATE — bitunix paper fires per day (paper_trade_record). Anchor: pre-bug 06-02 fired 9x ==="
sqlite3 -header -column "$DB" "SELECT DATE(ts) AS day, COUNT(*) AS fires FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='$W' GROUP BY day ORDER BY day;"
echo

echo "=== S2b: OUTCOMES — result distribution + avg R + sum PnL (NULL result = still open/unresolved) ==="
sqlite3 -header -column "$DB" "SELECT COALESCE(result,'(unresolved)') AS result, COUNT(*) AS n, ROUND(AVG(actual_r_multiple),3) AS avg_r, ROUND(SUM(actual_pnl_dollars),2) AS sum_pnl FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='$W' GROUP BY result ORDER BY n DESC;"
echo

echo "=== S2c: CLASSIFIER SANITY — vol_tier distribution + ATR range by day ==="
sqlite3 -header -column "$DB" "SELECT DATE(ts) AS day, json_extract(payload_json,'\$.volatility_tier') AS vol_tier, COUNT(*) AS n, ROUND(MIN(json_extract(payload_json,'\$.atr_pct_d1')),2) AS atr_min, ROUND(MAX(json_extract(payload_json,'\$.atr_pct_d1')),2) AS atr_max FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$W' GROUP BY day, vol_tier ORDER BY day, n DESC;"
echo

echo "=== S2c-FAIL: tier<->ATR band violations (high MUST be [3,5); extreme MUST be >=5) — expect 0 ==="
sqlite3 -header -column "$DB" "SELECT COUNT(*) AS band_violations FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$W' AND ((json_extract(payload_json,'\$.volatility_tier')='extreme' AND json_extract(payload_json,'\$.atr_pct_d1')<5.0) OR (json_extract(payload_json,'\$.volatility_tier')='high' AND (json_extract(payload_json,'\$.atr_pct_d1')<3.0 OR json_extract(payload_json,'\$.atr_pct_d1')>=5.0)));"
echo

echo "=== S2d: HARD-STOP — Phase 3 live-mode primitives firing in PAPER (A5; MUST be 0 rows) ==="
sqlite3 -header -column "$DB" "SELECT kind, COUNT(*) AS count, MIN(ts) AS first, MAX(ts) AS last FROM audit_event WHERE ts>='$W' AND (kind LIKE 'live_exit_order_%' OR kind LIKE 'position_state_%' OR kind LIKE 'restart_resume_%' OR kind IN ('exit_outcome_recorded','orphan_broker_position_on_restart')) GROUP BY kind;"
echo

echo "=== S2e: ANOMALY — agent_error rows since window start, by day+actor ==="
sqlite3 -header -column "$DB" "SELECT DATE(ts) AS day, actor, COUNT(*) AS n FROM audit_event WHERE kind='agent_error' AND ts>='$W' GROUP BY day, actor ORDER BY n DESC LIMIT 40;"
echo

echo "=== S2f: ANOMALY — reconciler-mismatch / divergence kinds since window start ==="
sqlite3 -header -column "$DB" "SELECT kind, COUNT(*) AS n, MAX(ts) AS last FROM audit_event WHERE ts>='$W' AND (kind LIKE '%reconcil%' OR kind LIKE '%mismatch%' OR kind LIKE '%divergence%') GROUP BY kind ORDER BY n DESC;"
echo "=== END Step 2 (read-only) ==="
