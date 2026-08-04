#!/usr/bin/env bash
# READ-ONLY pre-flight snapshot for the PMCC price-fix deploy. No writes, no restart.
set +e
echo "===PREFLIGHT_BEGIN==="
date -u +"utc=%Y-%m-%dT%H:%M:%SZ"

APPROOT=$(systemctl show -p WorkingDirectory --value trading-corp 2>/dev/null)
[ -d "$APPROOT" ] || APPROOT=/home/azureuser/trading_corp
echo "APPROOT=$APPROOT"

echo "---ENGINE---"
systemctl show trading-corp -p MainPID,NRestarts,ActiveState,SubState,ActiveEnterTimestamp 2>&1
PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
echo "cwd=$(readlink -f /proc/$PID/cwd 2>/dev/null)"
echo "recent_tracebacks_last200:"
journalctl -u trading-corp -n 200 --no-pager 2>/dev/null | grep -ciE "Traceback|CRITICAL|Error:"

DB="$APPROOT/data/trading_corp.db"
echo "---DB=$DB exists=$([ -f "$DB" ] && echo yes || echo NO)---"
echo "pending_order_by_state:"
sqlite3 "$DB" "SELECT state, COUNT(*) FROM pending_order GROUP BY state;" 2>&1
echo "pending_order_PENDING_count:"
sqlite3 "$DB" "SELECT COUNT(*) FROM pending_order WHERE state='pending';" 2>&1
echo "position_table:"
sqlite3 "$DB" "SELECT account, symbol, qty, avg_price FROM position ORDER BY account, symbol;" 2>&1
echo "agent_state_halt_standby_override_keys:"
sqlite3 "$DB" "SELECT agent, key, substr(replace(value_json,char(10),' '),1,140) FROM agent_state WHERE key LIKE '%halt%' OR key LIKE '%standby%' OR key LIKE '%pause%' OR key LIKE '%override%' OR key LIKE '%auto_exec%' ORDER BY agent, key;" 2>&1

echo "---CONFIG auto_execute (per division, from strategies.yaml)---"
grep -nE "^[a-z_]+:|auto_execute:" "$APPROOT/config/strategies.yaml" 2>/dev/null | grep -B1 "auto_execute:" | head -40

echo "---HTTP :8000---"
echo "healthz:"; curl -s -m 8 http://127.0.0.1:8000/healthz 2>&1 | head -c 500; echo
echo "sfp_state_board (bitunix — CRITICAL):"; curl -s -m 10 http://127.0.0.1:8000/sfp/partials/state-board 2>&1 | tr -d '\n' | sed 's/<[^>]*>/ /g' | tr -s ' ' | head -c 2500; echo
echo "sfp_recon:"; curl -s -m 10 http://127.0.0.1:8000/sfp/partials/recon 2>&1 | tr -d '\n' | sed 's/<[^>]*>/ /g' | tr -s ' ' | head -c 1200; echo
echo "pmcc_division_legcount:"; curl -s -m 12 http://127.0.0.1:8000/division/robinhood_pmcc 2>&1 | grep -oiE "pmcc|leg|C[0-9]{2,}" | wc -l

echo "---GATE-A md5 (LF-normalized) of the 4 runtime files on PROD---"
for f in config/strategies.yaml trading_corp/agents/divisions/pmcc_robinhood.py trading_corp/web/pmcc_pricing.py trading_corp/web/routes.py; do
  if [ -f "$APPROOT/$f" ]; then hh=$(tr -d '\r' < "$APPROOT/$f" | md5sum | cut -c1-12); else hh=ABSENT; fi
  echo "$hh  $f"
done
echo "===PREFLIGHT_END==="
