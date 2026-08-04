#!/usr/bin/env bash
# READ-ONLY post-restart safety verification (gates AUTO-ROLLBACK).
set +e
APPROOT=/home/azureuser/trading_corp; DB="$APPROOT/data/trading_corp.db"; cd "$APPROOT"
sleep 50   # + ~20s already elapsed => ~70s uptime, enough to expose a delayed crash-loop
AET=$(systemctl show -p ActiveEnterTimestamp --value trading-corp)
echo "ENGINE MainPID=$(systemctl show -p MainPID --value trading-corp) active=$(systemctl is-active trading-corp) sub=$(systemctl show -p SubState --value trading-corp) nrestarts=$(systemctl show -p NRestarts --value trading-corp)"
echo "ActiveEnter=$AET  UPTIME_s=$(( $(date +%s) - $(date -d "$AET" +%s) ))"
echo "TRACEBACKS_since_restart=$(journalctl -u trading-corp --since "$AET" --no-pager 2>/dev/null | grep -ciE 'Traceback|CRITICAL ')"
echo "ORDER_EMITS_on_boot=$(journalctl -u trading-corp --since "$AET" --no-pager 2>/dev/null | grep -ciE 'placed order|order placed|submitting live order|_place_option_order|place_combo')"
echo "healthz=$(curl -s -m 8 http://127.0.0.1:8000/healthz 2>&1)"
echo "PENDING_ORDERS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM pending_order WHERE state='pending';" 2>&1)"
echo "PMCC_POSITION_ROWS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM position WHERE account='robinhood_pmcc';" 2>&1) (pre-flight=18)"
echo "HALT_STANDBY_KEYS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM agent_state WHERE key LIKE '%halt%' OR key LIKE '%standby%' OR key LIKE '%pause%';" 2>&1)"
echo "PEAD_dial_override=$(sqlite3 "$DB" "SELECT substr(value_json,1,50) FROM agent_state WHERE key LIKE '%max_concurrent_override%';" 2>&1)"
echo "md5_live_==target?:"
for f in config/strategies.yaml trading_corp/agents/divisions/pmcc_robinhood.py trading_corp/web/pmcc_pricing.py trading_corp/web/routes.py; do echo "  $(tr -d '\r'<"$f"|md5sum|cut -c1-12) $f"; done
echo "SFP_BOARD (SOL should be REAL in-trade, TP id 4659275805879952845):"
curl -s -m 10 http://127.0.0.1:8000/sfp/partials/state-board 2>&1 | sed 's/<[^>]*>/ /g' | tr -s ' \n' ' ' | grep -oE "(BTC|ETH|SOL|XRP) live[^Z]{0,90}" | head -8
echo "SFP_RECON_active=$(curl -s -m 10 http://127.0.0.1:8000/sfp/partials/recon 2>&1 | grep -ociE 'TIER A|BOS-confirm')"
echo "END_VERIFY"
