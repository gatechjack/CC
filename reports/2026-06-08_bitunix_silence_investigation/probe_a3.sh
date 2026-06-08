#!/usr/bin/env bash
# Thread A Round 2, call 3 (az-split): HTF gate verdicts — distinguish
#   H1 (gate rejecting 100%) vs H2 (trade_plan formation broke).
# Compares current silent-day verdicts against the 06-02 working baseline.
# READ-ONLY. SELECT only. No writes.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== A3a: 4 MOST RECENT htf_gate_decision (raw payload capped 450c) — current verdict on silent days ==="
sqlite3 -line "$DB" "SELECT ts, substr(payload_json,1,450) AS payload FROM audit_event WHERE kind='htf_gate_decision' ORDER BY ts DESC LIMIT 4;"
echo

echo "=== A3b: 3 htf_gate_decision from WORKING day 06-02 (raw payload capped 450c) — contrast ==="
sqlite3 -line "$DB" "SELECT ts, substr(payload_json,1,450) AS payload FROM audit_event WHERE kind='htf_gate_decision' AND ts>='2026-06-02T00:00:00+00:00' AND ts<'2026-06-03T00:00:00+00:00' ORDER BY ts DESC LIMIT 3;"
echo "=== END call 3 (read-only) ==="
