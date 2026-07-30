true
CFG=/home/azureuser/trading_corp/config/strategies.yaml
echo "=== yaml parses cleanly + feed_health present (running proc will hot-reload this) ==="
/home/azureuser/trading_corp/venv/bin/python -c "import yaml; d=yaml.safe_load(open('$CFG')); k=d.get('kalshi_copy_trader',{}); print('parse=OK'); print('feed_health=', k.get('feed_health')); print('auto_execute=', k.get('auto_execute')); print('autopause_mode=', k.get('autopause_mode'))" 2>&1
echo "=== running process alive? (old code, unchanged) ==="
systemctl show trading-corp.service -p MainPID -p ActiveState -p NRestarts 2>&1
echo "=== last 3 engine log lines (health) ==="
journalctl -u trading-corp.service -n 3 --no-pager 2>&1 | tail -3
echo "=== DONE postcheck ==="
