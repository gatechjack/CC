# RO PRE-FLIGHT for the entry-partialfix deploy. Drift-gate (box==BASE), engine health, market-closed,
# divisions-idle. Read-only; changes nothing. ABORT the deploy if any gate fails.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = @'
cd /home/azureuser/trading_corp
echo "=== TIME (expect Tue ~01:xx ET, weekday, market CLOSED) ==="
echo "UTC: $(date -u '+%Y-%m-%d %H:%M:%S')  ET: $(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %a')"
echo "=== systemctl ==="
systemctl show trading-corp -p MainPID -p ExecMainStartTimestamp -p NRestarts -p ActiveState -p SubState 2>&1
echo "=== child proc (expect --live ... robinhood_mace in live-divisions = LIVE RH) ==="
MP=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
ps -o pid,ppid,etimes,cmd --ppid "$MP" 2>&1 | grep -v Xvfb | grep -iE "trading_corp|python" | head -3
echo "=== DRIFT GATE: 3 files CR-stripped md5 (expect BASE robinhood=2753939c rh_broker=c8aa5789 execution=71f1a828) ==="
for f in trading_corp/brokers/robinhood.py trading_corp/mace/rh_broker.py trading_corp/mace/execution.py; do
  printf "%s  " "$(tr -d '\r' < "$f" | md5sum | cut -c1-8)"; echo "$f"
done
echo "=== config_hash (expect 931a8214be50) ==="
python3 -c "import hashlib;print(hashlib.sha256(open('config/mace.yaml','rb').read()).hexdigest()[:12])"
echo "=== HTTP ==="
echo "healthz: $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/healthz)  mace: $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/mace)"
echo "=== journal last 3 min: any division actively placing/filling? (expect only idle/reconcile) ==="
timeout 40 journalctl -u trading-corp --no-pager --since "3 min ago" 2>&1 | grep -iE "plac|submit|fill|buy to open|sell to open|entry_start|exit_start" | tail -15
echo "  (count order-ish lines last 3 min:) $(timeout 40 journalctl -u trading-corp --no-pager --since '3 min ago' 2>&1 | grep -icE 'plac|submit|fill|buy to open|sell to open|entry_start|exit_start')"
echo "DONE_SH"
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"

$py = @'
import sqlite3, json
from datetime import datetime, timezone, timedelta
DB="/home/azureuser/trading_corp/data/trading_corp.db"
con=sqlite3.connect("file:%s?mode=ro"%DB,uri=True); con.row_factory=sqlite3.Row
con.execute("PRAGMA query_only=ON")
now=datetime.now(timezone.utc)
print("=== MACE census (submitting MUST be 0 = no half-placed entry; open expect 12) ===")
print(" ", {s:con.execute("SELECT count(*) FROM mace_rung WHERE status=?",(s,)).fetchone()[0] for s in ("submitting","open","closing","closed","abandoned")})
print("=== MACE heartbeat (expect stale -- market closed, no manage tick since ~15:55 ET) ===")
r=con.execute("SELECT value_json,updated_ts FROM agent_state WHERE agent='robinhood_mace' AND key='manage_tick_heartbeat'").fetchone()
print("  ", (dict(r) if r else "none"))
print("=== entry_halt latch (record pre-deploy state to restore after) ===")
r=con.execute("SELECT value_json,updated_ts FROM agent_state WHERE agent='robinhood_mace' AND key='entry_halt'").fetchone()
print("  ", (dict(r) if r else "ABSENT (=not halted)"))
print("=== last 20 audit_event rows (any actor) -- is anything writing right now? ===")
for x in con.execute("SELECT ts,actor,kind FROM audit_event ORDER BY ts DESC LIMIT 20").fetchall():
    print("  ", x["ts"], x["actor"], x["kind"])
cut=(now-timedelta(minutes=3)).isoformat()
n=con.execute("SELECT count(*) FROM audit_event WHERE ts>=? AND (kind LIKE '%fill%' OR kind LIKE '%place%' OR kind LIKE '%submit%' OR kind LIKE '%entry_start%' OR kind LIKE '%exit_start%')",(cut,)).fetchone()[0]
print("  order-ish audit rows last 3 min (expect 0):", n)
'@
$py | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | python3 -"
