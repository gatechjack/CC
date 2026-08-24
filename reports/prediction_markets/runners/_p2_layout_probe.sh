#!/usr/bin/env bash
# PM P2 CP2-Ph2 LAYOUT PROBE (READ-ONLY). My file-deploy landed at /home/azureuser/trading_corp/prediction_markets/
# (single) but the backup couldn't find the real stats.py/app.py there -> wrong path. Determine DEFINITIVELY where
# pm_web loads its modules from, confirm the running app + real files are untouched, and understand the deploy
# mechanism (git checkout vs file copy). Changes NOTHING.
echo "=== PM P2 CP2-Ph2 LAYOUT PROBE (read-only) ==="; date -u; echo "whoami=$(whoami)"
H="/home/azureuser"; ROOT="$H/trading_corp"

echo ""; echo "=== [1] deployed unit (WorkingDirectory / ExecStart / Environment) ==="
cat /etc/systemd/system/prediction-markets-web.service 2>&1 | grep -iE 'WorkingDirectory|ExecStart|Environment|PYTHONPATH|User='

echo ""; echo "=== [2] running pm_web process context ==="
PID=$(systemctl show -p MainPID --value prediction-markets-web.service 2>/dev/null); echo "pm_web MainPID=$PID"
echo "cwd:"; ls -l /proc/$PID/cwd 2>&1
echo "PYTHONPATH/PWD from environ:"; tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep -iE '^PYTHONPATH=|^PWD=' 2>&1

echo ""; echo "=== [3] DEFINITIVE import resolution (what pm_web ACTUALLY loads) ==="
cd "$ROOT" 2>/dev/null && venv/bin/python -c "import trading_corp.prediction_markets.web.app as a, trading_corp.prediction_markets.stats as s, trading_corp.prediction_markets.db as d, trading_corp.prediction_markets.category as c; print('web.app  ->', a.__file__); print('stats    ->', s.__file__); print('db       ->', d.__file__); print('category ->', c.__file__); print('has_scoreboard_flags ->', hasattr(s,'scoreboard_flags'))" 2>&1

echo ""; echo "=== [4] how is trading_corp on the path? (editable install / .pth / wheel) ==="
cd "$ROOT" 2>/dev/null && venv/bin/python -c "import trading_corp, os; print('trading_corp pkg __file__ ->', trading_corp.__file__); print('pkg dir ->', os.path.dirname(trading_corp.__file__))" 2>&1
ls -1 "$ROOT"/venv/lib/python*/site-packages/ 2>/dev/null | grep -iE 'trading|\.pth|\.egg' 2>&1
cat "$ROOT"/venv/lib/python*/site-packages/*.pth 2>/dev/null | head -5

echo ""; echo "=== [5] candidate paths on disk ==="
echo "-- DOUBLE  $ROOT/trading_corp/prediction_markets/ --"; ls -la "$ROOT/trading_corp/prediction_markets/" 2>&1 | head -20
echo "-- DOUBLE  $ROOT/trading_corp/prediction_markets/web/ --"; ls -la "$ROOT/trading_corp/prediction_markets/web/" 2>&1 | head -20
echo "-- SINGLE  $ROOT/prediction_markets/  (where my botched deploy landed) --"; ls -la "$ROOT/prediction_markets/" 2>&1 | head -20
echo "-- SINGLE  $ROOT/prediction_markets/web/ --"; ls -la "$ROOT/prediction_markets/web/" 2>&1 | head -20

echo ""; echo "=== [6] is the live tree a git checkout? (deploy mechanism) ==="
git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>&1
git -C "$ROOT" rev-parse HEAD 2>&1
git -C "$ROOT" status --porcelain 2>&1 | head -20
echo "(if the above shows a branch+SHA, the box tracks a git branch; my new SINGLE-path files would appear as untracked)"

echo ""; echo "=== [7] running pm_web STILL healthy (NOT restarted; botched deploy went to a different path) ==="
curl -s -o /tmp/pm_hz_probe.json -w "healthz HTTP %{http_code}\n" http://127.0.0.1:8081/healthz 2>&1; cat /tmp/pm_hz_probe.json 2>&1; echo; rm -f /tmp/pm_hz_probe.json

echo ""; echo "=== [8] engine unchanged ==="
systemctl show -p MainPID -p ActiveState trading-corp.service 2>&1
echo "=== LAYOUT PROBE (done) ==="
