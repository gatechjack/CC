# RO FULL boot-verify after the entry-partialfix deploy+restart. Read-only.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = @'
cd /home/azureuser/trading_corp
echo "=== engine (expect new PID 809209, boot 05:16:42Z, active) ==="
systemctl show trading-corp -p MainPID -p ExecMainStartTimestamp -p NRestarts -p ActiveState -p SubState 2>&1
MP=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
ST=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null | sed 's/^[A-Za-z]* //')
echo "=== child proc (expect --live ... robinhood_mace = LIVE RH not paper) ==="
ps -o pid,cmd --ppid "$MP" 2>&1 | grep -v Xvfb | grep -iE "trading_corp --live" | head -2
echo "=== running 3 md5s (expect TARGET robinhood=1e6bbc1e rh_broker=bac4411a execution=e380c2f6) ==="
for f in trading_corp/brokers/robinhood.py trading_corp/mace/rh_broker.py trading_corp/mace/execution.py; do
  printf "%s  " "$(tr -d '\r' < "$f" | md5sum | cut -c1-8)"; echo "$f"
done
echo "=== config_hash (expect UNCHANGED 931a8214be50) ==="
python3 -c "import hashlib;print(hashlib.sha256(open('config/mace.yaml','rb').read()).hexdigest()[:12])"
echo "=== ALL DIVISIONS wired/online since boot (bitunix/PEAD/PMCC/PM/MACE+5 schedulers) ==="
timeout 45 journalctl -u trading-corp --no-pager --since "$ST" 2>&1 | grep -iE "observer wired|PEAD wired|PMCC scan scheduler online|PM LIVE DRIVER WIRED|MACE wired|MACE .* scheduler online|watchdog scheduler online|Donchian scheduler online|shard-snapshot writer WIRED" | head -25
echo "=== tracebacks / import/attr errors since boot (expect NONE) ==="
timeout 45 journalctl -u trading-corp --no-pager --since "$ST" 2>&1 | grep -iE "traceback|importerror|attributeerror|modulenotfound|RobinhoodComboPartial|failed to|could not import|Exception on boot" | head -25
echo "  (traceback line count:) $(timeout 45 journalctl -u trading-corp --no-pager --since "$ST" 2>&1 | grep -icE 'traceback')"
echo "=== HTTP ==="
echo "healthz: $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/healthz)  mace: $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/mace)"
echo DONE_SH
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
$py = @'
import sqlite3
DB="/home/azureuser/trading_corp/data/trading_corp.db"
con=sqlite3.connect("file:%s?mode=ro"%DB,uri=True); con.row_factory=sqlite3.Row
con.execute("PRAGMA query_only=ON")
print("=== MACE census (expect open 12 incl adopted XLE 11-27) ===")
print(" ", {s:con.execute("SELECT count(*) FROM mace_rung WHERE status=?",(s,)).fetchone()[0] for s in ("submitting","open","closing","closed","abandoned")})
print("  adopted XLE 11-27 present+open:", con.execute("SELECT status FROM mace_rung WHERE rung_id='mace-XLE-2026-11-27-63.5-62-73.5-75-20261016'").fetchone()[0])
print("=== entry_halt (expect halted:false = unchanged, not halted by deploy) ===")
r=con.execute("SELECT value_json FROM agent_state WHERE agent='robinhood_mace' AND key='entry_halt'").fetchone()
print("  ", (r[0] if r else "ABSENT"))
'@
$py | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | python3 -"
