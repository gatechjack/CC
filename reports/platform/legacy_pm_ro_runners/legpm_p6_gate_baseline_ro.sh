set -u
ROOT=/home/azureuser/trading_corp
LIVE=/home/azureuser/trading_corp/data/prediction_markets.db
echo "### PHASE-6 GATE BASELINE: pm_subdivision_order placement cadence (READ-ONLY) $(date -u +%FT%TZ) ###"
NOW=$(date -u +%s)
echo "now_epoch=$NOW"
"$ROOT/venv/bin/python3" - "$LIVE" "$NOW" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True); now=int(sys.argv[2])
def q(sql,args=()):
    try: return list(c.execute(sql,args))
    except Exception as e: return [("ERR",str(e)[:140])]
print("active by account_id:", q("SELECT account_id, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY account_id"))
print("order MAX submitted_ts / response_ts / settled_ts:",
      q("SELECT MAX(submitted_ts), MAX(response_ts), MAX(settled_ts) FROM pm_subdivision_order"))
print("orders total / dry_run=0 (real) / dry_run=1:",
      q("SELECT COUNT(*), SUM(CASE WHEN dry_run=0 THEN 1 ELSE 0 END), SUM(CASE WHEN dry_run=1 THEN 1 ELSE 0 END) FROM pm_subdivision_order"))
for w,sec in (("1h",3600),("6h",21600),("24h",86400),("72h",259200)):
    print("orders submitted in last %s:"%w,
          q("SELECT COUNT(*), SUM(CASE WHEN dry_run=0 THEN 1 ELSE 0 END) FROM pm_subdivision_order WHERE submitted_ts>=?", (now-sec,)))
print("last 8 orders (submitted_ts desc): id, submitted_ts, account_id, category, order_side, dry_run, fill_count, broker_order_id, outcome_status")
for r in q("SELECT id, submitted_ts, account_id, category, order_side, dry_run, fill_count, broker_order_id, outcome_status FROM pm_subdivision_order ORDER BY submitted_ts DESC LIMIT 8"):
    print("   ",r)
# gap analysis: median-ish spacing of last 30 real orders
rows=q("SELECT submitted_ts FROM pm_subdivision_order WHERE dry_run=0 AND submitted_ts IS NOT NULL ORDER BY submitted_ts DESC LIMIT 30")
ts=[r[0] for r in rows if isinstance(r[0],(int,float))]
if len(ts)>2:
    gaps=sorted(ts[i]-ts[i+1] for i in range(len(ts)-1))
    print("real-order inter-arrival gaps (sec) over last %d: min=%d median=%d max=%d"%(len(ts),gaps[0],gaps[len(gaps)//2],gaps[-1]))
    print("newest real order age (sec):", now-ts[0])
c.close()
PYEOF
echo "### DONE ###"
