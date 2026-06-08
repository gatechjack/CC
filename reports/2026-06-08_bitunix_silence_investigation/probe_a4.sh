#!/usr/bin/env bash
# Thread A Round 2, call 4 (az-split): H1a-vs-H1b.
#   A6  — regime + avg composite_score + avg H1/H4 ADX by day: is the window
#         consistently choppy/transitional (gate correct), or were there
#         STRONG_BEAR/high-ADX periods that should have traded but didn't (gate bug)?
#   A3c — gate verdict/reason: payload TAIL (the part call 3 capped off).
# READ-ONLY. SELECT only. No writes.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== A6: htf_gate_decision regime + avg composite_score + avg H1/H4 ADX, by day (06-02..now) ==="
sqlite3 -header -column "$DB" "SELECT DATE(ts) AS day, json_extract(payload_json,'\$.regime') AS regime, COUNT(*) AS n, ROUND(AVG(json_extract(payload_json,'\$.composite_score')),2) AS avg_score, ROUND(AVG(json_extract(payload_json,'\$.h1.adx')),1) AS h1_adx, ROUND(AVG(json_extract(payload_json,'\$.h4.adx')),1) AS h4_adx FROM audit_event WHERE kind='htf_gate_decision' AND ts>='2026-06-02T00:00:00+00:00' GROUP BY day, regime ORDER BY day, n DESC;"
echo

echo "=== A3c: gate verdict/reason — payload TAIL (chars 400-1200) of most-recent silent vs last-working 06-02 ==="
sqlite3 -line "$DB" "SELECT ts, substr(payload_json,400,800) AS payload_tail FROM audit_event WHERE kind='htf_gate_decision' AND ts IN ('2026-06-08T20:28:28+00:00','2026-06-02T22:15:01+00:00') ORDER BY ts DESC;"
echo "=== END call 4 (read-only) ==="
