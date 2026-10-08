PY=/home/azureuser/trading_corp/venv/bin/python
echo "=== ENGINE ==="; systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState -p ActiveEnterTimestamp 2>&1
echo "=== PM_WEB ==="; systemctl show prediction-markets-web -p MainPID -p NRestarts -p ActiveState -p ActiveEnterTimestamp 2>&1
echo "=== SCHEMA HEAD + ARM STATE ==="
"$PY" - <<'PY'
import sqlite3, json
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
try: print("schema head (max version):", pm.execute("select max(version) from schema_version").fetchone()[0])
except Exception as e: print("schema err",e)
lg=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
rows=lg.execute("select key,value_json from agent_state where agent='pm_live' and key like 'arm:%'").fetchall()
g=None; narm=nlatch=0
for k,v in rows:
    d=json.loads(v) if v else {}
    if k=='arm:global': g=(d.get('armed'),d.get('latched')); continue
    if d.get('armed') is True: narm+=1
    if d.get('latched'): nlatch+=1
print("arm:global (armed,latched) =", g, "| per-sub armed=True:", narm, "| latched:", nlatch, "| total subs:", len(rows)-1)
PY
echo "=== SHARD BALANCES (latest per account) ==="
"$PY" - <<'PY'
import sqlite3, json
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
for a in ["kalshi_jack","kalshi_karen","kalshi_marc","kalshi_trey"]:
    r=pm.execute("select total_dollars,by_shard_json from pm_shard_balance_snapshot where account_id=? order by snapshot_ts desc limit 1",(a,)).fetchone()
    bd=json.loads(r[1]) if r and r[1] else {}
    print("  %-14s total=$%.2f  s0=$%.2f s3=$%.2f"%(a, (r[0] or 0.0), float(bd.get('0',0) or 0), float(bd.get('3',0) or 0)))
PY
echo "=== SCRATCH: my /tmp intermediates (should be none) ==="
ls -1 /tmp 2>/dev/null | grep -iE '^(tcj_|pmj_|pmj2_|pmsnap_)' || echo "(none -- clean)"
echo "=== BACKUPS KEPT (present + sizes) ==="
ls -l /home/azureuser/pm_recovery_backup_20261007T032558Z.db /home/azureuser/pm_legaudit_clear_backup_20261008T000933Z.db /home/azureuser/pm_itfcap_backup_1790547283.json 2>&1
echo "=== DF / ==="; df -h / | tail -2
echo "=== DONE wrap_state ==="
