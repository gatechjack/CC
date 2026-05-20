#!/bin/bash
set -e
BASE=/home/azureuser/trading_corp
cd $BASE
PY=/home/azureuser/trading_corp/venv/bin/python

echo "===== Import smoke test ====="
$PY -c "
from trading_corp.web import routes, data
from trading_corp.web.data import _query_pm_whales, _query_kalshi_watch_only_rows
import inspect
src = inspect.getsource(routes.register)
assert 'HX-Refresh' in src, 'HX-Refresh header missing from register()'
src2 = inspect.getsource(_query_pm_whales)
assert 'selected-placeholder' in src2 or 'selected-placeholder failed' in src2, 'selected-placeholder block missing'
src3 = inspect.getsource(_query_kalshi_watch_only_rows)
assert 'watch_only_whales' in src3 and 'watch_only_stats' in src3, 'watch_only_whales source/enrich missing'
print('imports green, all three fixes present in source')
"

echo ""
echo "===== Pre-restart PID ====="
systemctl show trading-corp -p MainPID --no-pager

echo ""
echo "===== Restart ====="
sudo systemctl restart trading-corp
sleep 5

echo ""
echo "===== Post-restart state ====="
systemctl is-active trading-corp
systemctl show trading-corp -p MainPID --no-pager

echo ""
echo "===== Final md5 ====="
md5sum trading_corp/web/routes.py trading_corp/web/data.py

echo ""
echo "===== Tail journal ====="
journalctl -u trading-corp -n 12 --no-pager 2>&1 | tail -12

echo "DEPLOY OK"
