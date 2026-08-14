#!/usr/bin/env bash
# READ-ONLY — the critical gate values only (small output so az doesn't truncate).
set +e
APPROOT=$(systemctl show -p WorkingDirectory --value trading-corp 2>/dev/null)
[ -d "$APPROOT" ] || APPROOT=/home/azureuser/trading_corp
DB="$APPROOT/data/trading_corp.db"
echo "APPROOT=$APPROOT"
echo "ENGINE: $(systemctl show trading-corp -p MainPID,NRestarts,ActiveState,SubState,ActiveEnterTimestamp --value 2>&1 | tr '\n' ' ')"
echo "TRACEBACKS_last200=$(journalctl -u trading-corp -n 200 --no-pager 2>/dev/null | grep -ciE 'Traceback|CRITICAL ')"
echo "PENDING_ORDERS_pending=$(sqlite3 "$DB" "SELECT COUNT(*) FROM pending_order WHERE state='pending';" 2>&1)"
echo "PENDING_ORDERS_by_state=[$(sqlite3 "$DB" "SELECT state||':'||COUNT(*) FROM pending_order GROUP BY state;" 2>&1 | tr '\n' ',')]"
echo "POSITION_ROWS:"
sqlite3 "$DB" "SELECT '  '||account||' | '||symbol||' | qty='||qty||' | avg='||avg_price FROM position ORDER BY account,symbol;" 2>&1
echo "HALT_OVERRIDE_KEYS:"
sqlite3 "$DB" "SELECT '  '||agent||' / '||key||' = '||substr(replace(value_json,char(10),' '),1,80) FROM agent_state WHERE key LIKE '%halt%' OR key LIKE '%standby%' OR key LIKE '%pause%' OR key LIKE '%max_concurrent_override%' ORDER BY agent,key;" 2>&1
echo "END"
