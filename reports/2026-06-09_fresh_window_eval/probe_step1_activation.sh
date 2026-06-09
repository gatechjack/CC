#!/usr/bin/env bash
# Step 1 — F-5 vol-classifier activation probe (fresh window, post-fix 7834375).
# Confirms, for the fresh paper window starting 2026-06-09T03:49:41Z:
#   (a) htf_gate_decision rows with atr_pct_d1 in [3,5) now classify
#       volatility_tier="high", size_multiplier=1.0, hard_zero_reason=null
#       (pre-fix they were "extreme" / 0.0 / "vol_tier_extreme");
#   (b) trade_plan_decision / would_have_placed firing has resumed
#       (first bitunix fires since 2026-06-02T22:15Z dormancy).
# Payload keys verified against bitunix_futures_observer.py:1102-1130 (top-level).
# actor='bitunix_futures' for htf_gate_decision / trade_plan_decision / would_have_placed.
# READ-ONLY. SELECT only. No writes.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db
W='2026-06-09T03:49:41+00:00'

echo "=== CONTEXT ==="
echo "host=$(hostname) now_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) window_start=$W"
if [ -f "$DB" ]; then echo "db=present size_bytes=$(stat -c %s "$DB")"; else echo "db=MISSING at $DB"; fi
echo

echo "=== S1a: ALL htf_gate_decision rows since window start (vol_tier vs ATR band) ==="
sqlite3 -header -column "$DB" "SELECT ts, json_extract(payload_json,'\$.volatility_tier') AS vol_tier, ROUND(json_extract(payload_json,'\$.atr_pct_d1'),3) AS atr_pct, json_extract(payload_json,'\$.size_multiplier') AS size_mult, json_extract(payload_json,'\$.hard_zero_reason') AS hard_zero, json_extract(payload_json,'\$.mode') AS mode FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$W' ORDER BY ts;"
echo

echo "=== S1b: ACTIVATION GATE — rows with ATR in [3,5): tier MUST be 'high', size_mult 1.0, hard_zero NULL ==="
sqlite3 -header -column "$DB" "SELECT ts, json_extract(payload_json,'\$.volatility_tier') AS vol_tier, ROUND(json_extract(payload_json,'\$.atr_pct_d1'),3) AS atr_pct, json_extract(payload_json,'\$.size_multiplier') AS size_mult, json_extract(payload_json,'\$.hard_zero_reason') AS hard_zero FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$W' AND json_extract(payload_json,'\$.atr_pct_d1')>=3.0 AND json_extract(payload_json,'\$.atr_pct_d1')<5.0 ORDER BY ts;"
echo

echo "=== S1b-FAIL: any ATR in [3,5) STILL classified 'extreme' / hard-zeroed (MUST be 0 rows) ==="
sqlite3 -header -column "$DB" "SELECT COUNT(*) AS still_extreme_under5 FROM audit_event WHERE kind='htf_gate_decision' AND ts>='$W' AND json_extract(payload_json,'\$.atr_pct_d1')>=3.0 AND json_extract(payload_json,'\$.atr_pct_d1')<5.0 AND (json_extract(payload_json,'\$.volatility_tier')='extreme' OR json_extract(payload_json,'\$.hard_zero_reason')='vol_tier_extreme');"
echo

echo "=== S1c: FIRING RESUMPTION — bitunix trade_plan_decision / would_have_placed since window start ==="
sqlite3 -header -column "$DB" "SELECT kind, COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM audit_event WHERE actor='bitunix_futures' AND kind IN ('trade_plan_decision','would_have_placed') AND ts>='$W' GROUP BY kind ORDER BY kind;"
echo

echo "=== S1d: PRIOR-FIRE BOUNDARY — last bitunix fire BEFORE window (expect ~2026-06-02T22:15Z) ==="
sqlite3 -header -column "$DB" "SELECT kind, MAX(ts) AS last_before_window FROM audit_event WHERE actor='bitunix_futures' AND kind IN ('trade_plan_decision','would_have_placed') AND ts<'$W' GROUP BY kind;"
echo "=== END Step 1 (read-only) ==="
