#!/usr/bin/env bash
# READ-ONLY. Bitunix flat-confirm — restart-sequence STEP 2 (after halt, before
# the combined boot-smoke / restart). Exits non-zero (DO NOT RESTART) unless
# Bitunix is genuinely flat: 0 open live records AND reconciler mc=0/miss=0/orph=0.
set -uo pipefail
DB="${TC_DB:-/home/azureuser/trading_corp/data/trading_corp.db}"
CFG="${TC_CFG:-/home/azureuser/trading_corp/config/strategies.yaml}"
fail=0
echo "=== Bitunix flat-confirm (read-only) ==="
OL=$(sqlite3 "$DB" "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_futures' AND execution_mode='live' AND (result IS NULL OR result='open');")
echo "open live records (want 0): $OL"
[ "$OL" = "0" ] || { echo "  FAIL: $OL open live record(s)"; fail=1; }
TICK=$(sqlite3 "$DB" "SELECT payload_json FROM audit_event WHERE actor='bitunix_position_reconciler' AND kind='position_state_reconciled' ORDER BY id DESC LIMIT 1;")
echo "latest reconciler tick: $(printf '%s' "$TICK" | head -c 95)"
if printf '%s' "$TICK" | grep -q '"match_count": 0' \
   && printf '%s' "$TICK" | grep -q '"missing_on_broker_count": 0' \
   && printf '%s' "$TICK" | grep -q '"orphan_on_broker_count": 0'; then
  echo "  reconciler mc=0/miss=0/orph=0 OK"
else
  echo "  FAIL: reconciler not clean/flat"; fail=1
fi
echo "auto_execute (expect false once halted):"
awk '/^bitunix_futures:/{f=1} f&&/auto_execute:/{print "  "$0;exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"
[ "$fail" = "0" ] && echo "FLAT-CONFIRM PASS — safe to proceed to combined boot-smoke / restart" \
  || { echo "FLAT-CONFIRM FAIL — DO NOT RESTART"; exit 1; }
