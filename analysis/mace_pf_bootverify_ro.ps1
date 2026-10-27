# RO post-restart boot-verify for the entry-ladder orphan fix. Run AFTER restart_tc.ps1. Read-only.
# PASS = new PID, 3 md5s==TARGET, config_hash UNCHANGED 931a8214be50, 5 MACE schedulers online, 0 new
# tracebacks, healthz/mace 200.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = @'
cd /home/azureuser/trading_corp
echo "=== engine ==="
systemctl show trading-corp -p MainPID -p ExecMainStartTimestamp -p NRestarts -p ActiveState -p SubState 2>&1
echo "=== 3 grafted md5s (expect robinhood=1e6bbc1e rh_broker=bac4411a execution=e380c2f6) ==="
for f in trading_corp/brokers/robinhood.py trading_corp/mace/rh_broker.py trading_corp/mace/execution.py; do
  printf "%s  " "$(tr -d '\r' < "$f" | md5sum | cut -c1-8)"; echo "$f"
done
echo "=== config_hash (expect UNCHANGED 931a8214be50) ==="
python3 -c "import hashlib;print(hashlib.sha256(open('config/mace.yaml','rb').read()).hexdigest()[:12])"
echo "=== MACE wired + schedulers online (boot window) ==="
MP=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
ST=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null | sed 's/^[A-Za-z]* //')
timeout 45 journalctl -u trading-corp --no-pager --since "$ST" 2>&1 | grep -iE "Robinhood MACE wired|scheduler online|watchdog scheduler" | head -12
echo "=== tracebacks since boot (expect none) ==="
timeout 45 journalctl -u trading-corp --no-pager --since "$ST" 2>&1 | grep -iE "traceback|RobinhoodComboPartial|importerror|attributeerror" | head -20
echo "=== HTTP ==="
echo "healthz: $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/healthz)"
echo "mace   : $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/mace)"
echo DONE
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
