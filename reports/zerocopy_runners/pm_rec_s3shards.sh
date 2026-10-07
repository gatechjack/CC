PY=/home/azureuser/trading_corp/venv/bin/python
echo "=== STEP 3: shard balances per account per shard (latest snapshot, RO) ==="
"$PY" - <<'PY'
import sqlite3, json, time, datetime
NOW=int(time.time())
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
# armed category counts (from the 57 just armed)
ARMED={"kalshi_jack":18,"kalshi_karen":16,"kalshi_marc":11,"kalshi_trey":12}
def fmt(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%MZ')
    except Exception: return str(ts)
for acct in ["kalshi_jack","kalshi_karen","kalshi_marc","kalshi_trey"]:
    r=pm.execute("select snapshot_ts,total_dollars,has_breakdown,by_shard_json from pm_shard_balance_snapshot where account_id=? order by snapshot_ts desc limit 1",(acct,)).fetchone()
    if not r: print("%-14s NO snapshot"%acct); continue
    try: bd=json.loads(r[3])
    except Exception: bd={}
    s0=bd.get("0",0.0); s1=bd.get("1",0.0); s2=bd.get("2",0.0); s3=bd.get("3",0.0)
    fundable=float(s0 or 0)+float(s3 or 0)
    thin = "THIN" if fundable < 50.0 else ("modest" if fundable < 120.0 else "ok")
    print("%-14s snap=%s(%.1fh) total=$%.2f brk=%s  shard0=$%.2f shard1=$%.2f shard2=$%.2f shard3=$%.2f  fundable(0+3)=$%.2f armed_cats=%s -> %s"%(
        acct,fmt(r[0]),(NOW-int(r[0]))/3600.0,r[1] or 0.0,r[2],float(s0 or 0),float(s1 or 0),float(s2 or 0),float(s3 or 0),fundable,ARMED.get(acct,"?"),thin))
print("NOTE: gate 6b funds an order from the market's exchange_index shard; shards 1&2 are $0 (baseline). An")
print("underfunded account REJECTS pre-submit (no order row) -> presents as 'not trading' with nothing to grep.")
PY
echo "=== DONE s3 ==="
