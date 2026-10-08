PY=/home/azureuser/trading_corp/venv/bin/python
echo "=== engine state ==="
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveEnterTimestamp -p SubState 2>&1
echo "=== arm/latch state (legacy agent_state RO) ==="
"$PY" - <<'PY'
import sqlite3, json
lg=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
rows=lg.execute("select key,value_json from agent_state where agent='pm_live' and key like 'arm:%'").fetchall()
narm=nlatch=0; latched=[]
g=None
for k,v in rows:
    d=json.loads(v) if v else {}
    if k=='arm:global': g=(d.get('armed'),d.get('latched')); continue
    if d.get('armed') is True: narm+=1
    if d.get('latched'): nlatch+=1; latched.append((k,d.get('auto_trigger')))
print("arm:global armed/latched =", g)
print("per-sub: armed=True", narm, " latched=True", nlatch)
for k,t in latched[:10]: print("   LATCHED", k, t)
PY
echo "=== recent ENTRY activity (pm DB RO): last fill overall + last 2h entries by account ==="
"$PY" - <<'PY'
import sqlite3, time, datetime
NOW=int(time.time())
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
def fmt(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%SZ')
    except Exception: return str(ts)
r=pm.execute("select max(response_ts) from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0").fetchone()
print("last ENTRY fill overall:", fmt(r[0]), "age %.1fh"%((NOW-int(r[0]))/3600.0) if r[0] else "-")
print("entries (is_exit=0, dry_run=0) last 2h by account x status:")
cut=NOW-2*3600
for r in pm.execute("select account_id,outcome_status,count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by account_id,outcome_status order by account_id",(cut,)):
    print("   ",r[0],r[1],r[2])
print("task heartbeat (driver alive):")
for r in pm.execute("select account_id,last_cycle_ts from pm_driver_task_heartbeat order by account_id"):
    print("   %-14s last_cycle=%s age=%.1fmin"%(r[0],fmt(r[1]),(NOW-int(r[1]))/60.0))
PY
echo "=== DONE restart_check ==="
