# RO boot-verify after the durability-fix graft + restart. Read-only.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = @'
echo "NOW_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=== systemctl (NEW pid/boot/restarts) ==="
systemctl show trading-corp -p MainPID -p ActiveEnterTimestamp -p NRestarts -p SubState -p ActiveState 2>&1
MP=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
ps -o pid,etimes,lstart,cmd --ppid "$MP" 2>&1 | grep -i trading_corp | head -2
echo "=== grafted 5 files CR-md5 (must == TARGET: config 4a47188b/execution 71f1a828/manager 0116e860/loops 765e3360/main f486de42) ==="
cd /home/azureuser/trading_corp
for f in trading_corp/mace/config.py trading_corp/mace/execution.py trading_corp/mace/manager.py trading_corp/mace/loops.py trading_corp/main.py; do
  printf "%s  %s\n" "$(tr -d '\r' < $f | md5sum | cut -c1-8)" "$f"
done
python3 -c "import hashlib;print('config_hash', hashlib.sha256(open('config/mace.yaml','rb').read()).hexdigest()[:12])"
echo "=== journald since boot: MACE schedulers online + tracebacks ==="
SINCE="2026-10-23 20:09:00"
for pat in "MACE daily-slots scheduler online" "MACE manage scheduler online" "MACE reconcile scheduler online" "MACE weekly-calendar scheduler online" "MACE watchdog scheduler online"; do
  n=$(journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | grep -c "$pat")
  printf "  %-42s %s\n" "$pat" "$n"
done
echo -n "  Traceback lines since boot: "; journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | grep -c "Traceback"
echo "  --- last 6 mace. log lines ---"
journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | grep -iE "mace\.|MACE " | tail -6
echo "DONE"
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
