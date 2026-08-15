#!/bin/bash
set -u
echo "ET now (server): $(TZ=America/New_York date +'%Y-%m-%d %H:%M:%S %Z')"
now=$(TZ=America/New_York date +%H%M)
if [ "$now" -ge 1540 ] && [ "$now" -le 1558 ]; then echo "REFUSE: inside 15:40-15:58 ET forbidden window"; exit 9; fi
OLDPID=$(systemctl show trading-corp.service -p MainPID --value)
echo "old MainPID=$OLDPID"
echo "=== restarting trading-corp.service ==="
systemctl restart trading-corp.service
for i in $(seq 1 60); do
  sleep 5
  st=$(systemctl show trading-corp.service -p ActiveState --value)
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ 2>/dev/null || echo 000)
  if [ "$st" = "active" ] && [ "$code" = "200" ]; then echo "up after ~$((i*5))s (web HTTP $code)"; break; fi
done
echo "=== unit state ==="
systemctl show trading-corp.service -p MainPID -p ActiveState -p SubState -p NRestarts -p ExecMainStartTimestamp
BOOT=$(systemctl show trading-corp.service -p ExecMainStartTimestamp --value | sed 's/^[A-Za-z][A-Za-z][A-Za-z] //')
echo "=== Traceback/Exception in boot log? (since $BOOT) ==="
journalctl -u trading-corp.service --since "$BOOT" --no-pager 2>&1 | grep -E 'Traceback|Exception' | tail -20
echo "(blank above = no tracebacks)"
echo "=== MACE wired line (config_hash) ==="
journalctl -u trading-corp.service --since "$BOOT" --no-pager 2>&1 | grep -iE 'MACE wired|config_hash' | tail -3
echo "=== done ==="
