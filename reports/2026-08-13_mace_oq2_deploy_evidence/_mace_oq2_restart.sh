#!/bin/bash
# MACE OQ-2 RESTART runner. Guarded: refuses inside 15:35-16:00 ET (restart
# there runs daily-slots catch-up, which can PLACE). Boot takes ~2.5 min.
set -u
et=$(TZ=America/New_York date +%H%M)
if [ "$et" -ge 1535 ] && [ "$et" -le 1600 ]; then
  echo "RESTART RESULT: REFUSED - now $et ET, inside 15:35-16:00 guard"
  exit 1
fi
echo "restarting trading-corp (boot ~2.5 min: bitunix seeds 1500 closes before web :8000)"
systemctl restart trading-corp
sleep 155
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts
code=000
for i in 1 2 3 4 5 6; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo 000)
  [ "$code" = "200" ] && break
  sleep 20
done
echo "web home HTTP $code"
T=$(systemctl show trading-corp -p ActiveEnterTimestamp --value | cut -d" " -f2-3)
echo "tracebacks since boot: $(journalctl -u trading-corp --since "$T" --no-pager | grep -c Traceback)"
echo ""
echo "RESTART RESULT: DONE - now run mace_oq2_verify.ps1"
