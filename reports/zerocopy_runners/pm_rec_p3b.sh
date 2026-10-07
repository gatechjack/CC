PY=/home/azureuser/trading_corp/venv/bin/python
echo "=== DECISION AID (RO): per-sub live-entry activity -- NOT the armed set, evidence for Jack to specify it ==="
"$PY" - <<'PY'
import sqlite3, time
NOW=int(time.time())
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
subs=[(r[0],r[1]) for r in pm.execute("select account_id,category from pm_subdivision where active=1 order by account_id,category")]
def stat(aid,cat):
    row=pm.execute("""select max(case when is_exit=0 then coalesce(response_ts,submitted_ts) end),
      sum(case when is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? then 1 else 0 end),
      sum(case when is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? then 1 else 0 end),
      sum(case when is_exit=0 and dry_run=0 then 1 else 0 end)
      from pm_subdivision_order where account_id=? and category=?""",
      (NOW-14*86400, NOW-90*86400, aid, cat)).fetchone()
    return row
print("%-14s %-9s %-10s %6s %6s %7s"%("account","category","last_entry","e14d","e90d","e_all"))
import datetime
n_traded=0
for aid,cat in subs:
    le,e14,e90,eall=stat(aid,cat)
    lestr="-" if not le else datetime.datetime.utcfromtimestamp(int(le)).strftime('%Y-%m-%d')
    if (eall or 0)>0: n_traded+=1
    print("%-14s %-9s %-10s %6s %6s %7s"%(aid,cat,lestr,e14 or 0,e90 or 0,eall or 0))
print("total active subs:",len(subs),"| subs that EVER placed a live entry:",n_traded)
print("subs that placed a live entry in last 90d:", sum(1 for aid,cat in subs if (stat(aid,cat)[2] or 0)>0))
PY
echo "=== DONE p3b ==="
