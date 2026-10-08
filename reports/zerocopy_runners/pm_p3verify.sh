PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
OLDPID=664274
echo "=== wait for NEW boot (was PID $OLDPID, settlement.py mtime 2026-10-08 11:49:34Z) ==="
NEWPID=""; AE=""
for i in $(seq 1 30); do
  pid=$(systemctl show trading-corp -p MainPID --value)
  sub=$(systemctl show trading-corp -p SubState --value)
  if [ "$pid" != "$OLDPID" ] && [ "$pid" != "0" ] && [ "$sub" = "running" ]; then NEWPID=$pid; break; fi
  sleep 12
done
if [ -z "$NEWPID" ]; then echo "NEW BOOT NOT DETECTED after wait (pid still $pid, sub=$sub) -- STOP, do not conclude"; exit 7; fi
echo "new boot detected: MainPID=$NEWPID (waited ~$((i*12))s); letting boot-reconcile + first cycle settle (40s)"; sleep 40
echo "=== 3.2 systemd ==="; systemctl show trading-corp -p MainPID -p NRestarts -p ActiveEnterTimestamp -p SubState 2>&1
echo "=== box settlement.py csha (expect TARGET a0eb5a437166b875) + mtime ==="
tr -d '\r' < "$ROOT/trading_corp/prediction_markets/settlement.py" | sha256sum | cut -c1-16
stat -c '%y' "$ROOT/trading_corp/prediction_markets/settlement.py"
echo "=== boot-reconcile (all 4; expect reconciled=True latched=False) + divisions + any NON-STANDARD/Traceback ==="
journalctl -u trading-corp --since "2026-10-08 11:49:00" --no-pager -o short-iso 2>&1 | grep -iE "boot-reconcile account=|MACE wired|pmcc approval reconcile loop starting|PEAD.*reconciler online|bitunix position-state reconciler .*startup|NON-STANDARD market_result|Traceback" | tail -40
echo "=== 3.2 arm state (RO; expect 57 armed, global armed, 0 latched) ==="
PYTHONPATH=. "$PY" - <<'PY'
import sqlite3, json
lg=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
rows=lg.execute("select key,value_json from agent_state where agent='pm_live' and key like 'arm:%'").fetchall()
g=None;narm=nlat=0
for k,v in rows:
    d=json.loads(v) if v else {}
    if k=='arm:global': g=(d.get('armed'),d.get('latched')); continue
    if d.get('armed') is True: narm+=1
    if d.get('latched'): nlat+=1
print("arm:global (armed,latched)=",g," per-sub armed=",narm," latched=",nlat)
PY
echo "=== 3.3 first-scan vs prediction: new-branch close rows since restart (expect 0) + fills ==="
PYTHONPATH=. "$PY" - <<'PY'
import sqlite3, time
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
cut=int(time.time())-1500   # ~last 25 min (covers apply+restart)
print("is_exit=1 closes last 25min by close_source:", dict(pm.execute("select coalesce(close_source,'NULL'),count(*) from pm_subdivision_order where is_exit=1 and coalesce(settled_ts,response_ts)>=? group by 1",(cut,)).fetchall()))
nb=pm.execute("select count(*) from pm_subdivision_order where is_exit=1 and close_source is not null and close_source not in ('settlement','settlement_void','opposed') and coalesce(settled_ts,response_ts)>=?",(cut,)).fetchone()[0]
print("NEW-BRANCH rows (settlement_scalar/settlement_unknown/other non-standard) since restart:", nb, "(PREDICTION: 0)")
print("entries (is_exit=0) last 25min by account (fills still flowing):", dict(pm.execute("select account_id,count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by 1",(cut,)).fetchall()))
print("total pm_subdivision_order rows now:", pm.execute("select count(*) from pm_subdivision_order").fetchone()[0])
PY
echo "=== DONE p3verify ==="
