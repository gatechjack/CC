#!/bin/bash
set -e
BASE=/home/azureuser/trading_corp
cd $BASE

echo "===== Finding python interpreter for the service ====="
# Read ExecStart from the unit
systemctl show trading-corp -p ExecStart --no-pager | head -2

echo ""
echo "===== Locating any python that knows about trading_corp ====="
ls -la /home/azureuser/trading_corp/venv/bin/python* 2>/dev/null | head -3 || true
ls -la /home/azureuser/trading_corp/.venv/bin/python* 2>/dev/null | head -3 || true
ls -la /opt/trading_corp/venv/bin/python* 2>/dev/null | head -3 || true
which python3
PY=$(systemctl show trading-corp -p ExecStart --no-pager | grep -oP 'argv\[\]=\K[^ ]+' | head -1)
echo "PY=$PY"

echo ""
echo "===== Import smoke test ====="
$PY -c "
from trading_corp.web import routes, data
from trading_corp.web.data import _query_pm_whales, _query_kalshi_watch_only_rows
import inspect
# Verify HX-Refresh is in the modified function source (within register())
src = inspect.getsource(routes.register)
assert 'HX-Refresh' in src, 'HX-Refresh header missing from register()'
print('imports green, HX-Refresh present in routes.register source')
"

echo ""
echo "===== Pre-restart PID ====="
systemctl show trading-corp -p MainPID --no-pager

echo ""
echo "===== Restart ====="
sudo systemctl restart trading-corp
sleep 4

echo ""
echo "===== Post-restart state ====="
systemctl is-active trading-corp
systemctl show trading-corp -p MainPID --no-pager

echo ""
echo "===== Final md5 ====="
md5sum trading_corp/web/routes.py trading_corp/web/data.py

echo "DEPLOY OK"
