PY=/home/azureuser/trading_corp/venv/bin/python
"$PY" - <<'PY'
import sqlite3, datetime, time
NOW=int(time.time())
def fmt(ts):
    if ts in (None,''): return "None"
    try: return datetime.datetime.utcfromtimestamp(int(float(ts))).strftime('%Y-%m-%d %H:%M:%SZ')
    except Exception: return str(ts)
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)

print("===== S1 pm_shard_balance_snapshot continuity (driver-alive instrument) =====")
# per-account snapshots per day last 8 days
print("-- snapshots per day per account (last 8d) --")
cut=NOW-8*86400
rows=pm.execute("select account_id, strftime('%Y-%m-%d', snapshot_ts,'unixepoch') d, count(*) from pm_shard_balance_snapshot where snapshot_ts>=? group by account_id,d order by account_id,d",(cut,)).fetchall()
for r in rows: print("   %-14s %s n=%s"%(r[0],r[1],r[2]))
print("-- LARGEST inter-snapshot gap per account in last 8d --")
for acct in [x[0] for x in pm.execute("select distinct account_id from pm_shard_balance_snapshot")]:
    ts=[r[0] for r in pm.execute("select snapshot_ts from pm_shard_balance_snapshot where account_id=? and snapshot_ts>=? order by snapshot_ts",(acct,cut))]
    mg=0; a=b=None
    for i in range(1,len(ts)):
        g=ts[i]-ts[i-1]
        if g>mg: mg=g; a=ts[i-1]; b=ts[i]
    print("   %-14s n=%s maxgap=%.1f min  from %s -> %s"%(acct,len(ts),mg/60.0,fmt(a),fmt(b)))
print("-- snapshots straddling the Phase-1 window (2026-10-02 18:00 .. 2026-10-06 03:30), jack, sampled --")
lo=int(datetime.datetime(2026,10,2,18,0).timestamp()); hi=int(datetime.datetime(2026,10,6,3,30).timestamp())
ts=[r[0] for r in pm.execute("select snapshot_ts from pm_shard_balance_snapshot where account_id='kalshi_jack' and snapshot_ts>=? and snapshot_ts<=? order by snapshot_ts",(lo,hi))]
print("   jack snapshots in window:", len(ts), "first", fmt(ts[0]) if ts else None, "last", fmt(ts[-1]) if ts else None)
# print any gap > 20min in window
prev=None
for t in ts:
    if prev is not None and (t-prev)>1200:
        print("     GAP %.1f min: %s -> %s"%((t-prev)/60.0, fmt(prev), fmt(t)))
    prev=t

print("\n===== S2 pm_subdivision_order WRITE timeline (entries vs exits) per day last 8d =====")
for r in pm.execute("select strftime('%Y-%m-%d', coalesce(response_ts,submitted_ts),'unixepoch') d, is_exit, outcome_status, count(*) from pm_subdivision_order where dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by d,is_exit,outcome_status order by d,is_exit",(NOW-8*86400,)):
    print("   %s exit=%s %-9s n=%s"%(r[0],r[1],r[2],r[3]))

print("\n===== S3 approx OPEN exposure per account (gate-6 proxy, journal-based) =====")
# open = entry-filled rows whose condition_id has no is_exit=1 filled close
q="""
select o.account_id,
       count(*) n_open_legs,
       round(sum(o.fill_count * case when o.outcome_leg='yes' then o.fill_price else (1.0-o.fill_price) end),2) approx_open_usd
from pm_subdivision_order o
where o.is_exit=0 and o.outcome_status='filled' and o.dry_run=0
  and not exists (select 1 from pm_subdivision_order c
                  where c.account_id=o.account_id and c.condition_id=o.condition_id
                        and c.is_exit=1 and c.outcome_status='filled')
group by o.account_id order by o.account_id
"""
try:
    for r in pm.execute(q):
        print("   %-14s open_legs=%s approx_open_usd=$%s  (max_open_usd cap=350)"%(r[0],r[1],r[2]))
except Exception as e: print("   ERR",e)
print("   NOTE: journal-based approximation; gate 6 uses LIVE venue exposure, not this. Treat as indicative only.")

print("\n===== S4 last entry vs last exit write per account =====")
for r in pm.execute("select account_id, max(case when is_exit=0 then coalesce(response_ts,submitted_ts) end) last_entry, max(case when is_exit=1 then coalesce(response_ts,submitted_ts) end) last_exit from pm_subdivision_order where dry_run=0 group by account_id"):
    print("   %-14s last_entry=%s  last_exit(settlement/close)=%s"%(r[0],fmt(r[1]),fmt(r[2])))
print("DONE diag3-db")
PY
echo "===== S5 journal: PM-driver activity DURING Phase 1 (2026-10-02 20:00 .. 2026-10-06 02:55) ====="
journalctl -u trading-corp --since "2026-10-02 20:00:00" --until "2026-10-06 02:55:00" --no-pager -o short-iso 2>&1 | grep -iE 'pm_live_driver|prediction_markets|pm_driver' > /tmp/pmj_$$.txt 2>&1
echo "PM-driver matched lines in Phase-1 window:"; wc -l /tmp/pmj_$$.txt
echo "-- first 5 --"; head -5 /tmp/pmj_$$.txt
echo "-- last 5 --"; tail -5 /tmp/pmj_$$.txt
echo "-- hourly histogram of PM-driver log lines (Phase 1) --"; awk '{print substr($1,1,13)}' /tmp/pmj_$$.txt | uniq -c | head -90
rm -f /tmp/pmj_$$.txt
echo "===== S6 the 10-04 01:12 traceback context ====="
journalctl -u trading-corp --since "2026-10-04 01:11:50" --until "2026-10-04 01:13:30" --no-pager -o short-iso 2>&1 | head -40
echo "===== DONE diag3 ====="
