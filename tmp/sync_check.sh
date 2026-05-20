#!/bin/bash
BASE=/home/azureuser/trading_corp
for f in trading_corp/web/routes.py trading_corp/web/data.py trading_corp/web/templates/prediction_markets_dashboard.html tests/test_promote_demote_fixes.py tests/test_pmcc_logic.py; do
  md5=$(md5sum "$BASE/$f" 2>/dev/null | awk '{print $1}')
  echo "$f : $md5"
done

echo ""
echo "===== Service health ====="
systemctl is-active trading-corp
systemctl show trading-corp -p MainPID --no-pager

echo ""
echo "===== Timer state ====="
systemctl list-timers trading-corp-pm-watchlist-deep.timer --no-pager 2>&1 | grep -v "^$" | head -3
