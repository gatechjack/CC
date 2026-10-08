PY=/home/azureuser/trading_corp/venv/bin/python
cd /home/azureuser/trading_corp || exit 2
PYTHONPATH=. "$PY" - <<'PY'
import sqlite3, json, time, datetime
from trading_corp.prediction_markets import leg_audit as LA
SINCE=int(datetime.datetime(2026,10,7,4,18,0,tzinfo=datetime.timezone.utc).timestamp())
NOW=int(time.time())
def fmt(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%m-%d %H:%M:%SZ')
    except Exception: return str(ts)
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
pm.row_factory=sqlite3.Row
JACK="atp bra bun boxing cfb cs2 epl f1 itf lal mex mlb mls nfl ucl ufc wnba wta".split()
KAREN="atp bra bun cfb cs2 epl itf lal mex mlb mls nfl ucl ufc wnba wta".split()
MARC="atp cfb cs2 itf lal mex mlb mls nfl ufc wnba".split()
TREY="atp cfb cs2 itf lal mex mlb mls nfl ufc wnba wta".split()
armed=set([("kalshi_jack",c) for c in JACK]+[("kalshi_karen",c) for c in KAREN]+[("kalshi_marc",c) for c in MARC]+[("kalshi_trey",c) for c in TREY])
print("SINCE", fmt(SINCE), "NOW", fmt(NOW), "armed subs:", len(armed))

print("\n==== A: ENTRY fills since arm, per account x status (is_exit=0, dry_run=0) ====")
for r in pm.execute("select account_id,outcome_status,count(*) n,sum(fill_count) fc from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by account_id,outcome_status order by account_id",(SINCE,)):
    print("  %-14s %-8s n=%s contracts=%s"%(r[0],r[1],r[2],r[3] or 0))

print("\n==== B: ENTRY fills since arm by (account,category) [filled only] ====")
rows=pm.execute("select account_id,category,count(*) n,sum(fill_count) fc,min(response_ts) first,max(response_ts) last from pm_subdivision_order where is_exit=0 and dry_run=0 and outcome_status='filled' and response_ts>=? group by account_id,category order by account_id,category",(SINCE,)).fetchall()
placed_pairs=set()
for r in rows:
    placed_pairs.add((r[0],r[1]))
    print("  %-14s %-7s nfilled=%s contracts=%s first=%s last=%s"%(r[0],r[1],r[2],r[3] or 0,fmt(r[4]),fmt(r[5])))

print("\n==== C: fills-per-hour (is_exit=0 filled) -- rate held or decayed ====")
for r in pm.execute("select strftime('%m-%d %H',response_ts,'unixepoch') hr, count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and outcome_status='filled' and response_ts>=? group by hr order by hr",(SINCE,)):
    print("   %sZ  %s"%(r[0],r[1]))

print("\n==== D: leg_audit verdict distribution, ALL is_exit=0 rows since arm ====")
dist={}
nonclean=[]
for r in pm.execute("select id,account_id,category,ticker,outcome_leg,outcome_status,signal_outcome,signal_slug,leg_audit,response_ts from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=?",(SINCE,)):
    st=LA.classify_leg_audit(r["leg_audit"])
    dist[st]=dist.get(st,0)+1
    if st in (LA.STATE_INVERSION, LA.STATE_SOFT, LA.STATE_UNEVALUATED):
        nonclean.append(dict(r))
print("  verdict states:", dist)
print("  NON-CLEAN leg_audit rows today (inversion/soft/uneval):", len(nonclean))
for d in nonclean:
    print("   *** id=%s %s/%s %s leg=%s %s whale=%r slug=%r audit=%r"%(d["id"],d["account_id"],d["category"],d["ticker"],d["outcome_leg"],d["outcome_status"],d["signal_outcome"],d["signal_slug"],d["leg_audit"]))

print("\n==== E: FIRST fill per (account,category) since arm + leg_audit (section 2) ====")
seen={}
for r in pm.execute("select account_id,category,ticker,outcome_leg,signal_outcome,leg_audit,response_ts from pm_subdivision_order where is_exit=0 and dry_run=0 and outcome_status='filled' and response_ts>=? order by response_ts",(SINCE,)):
    k=(r["account_id"],r["category"])
    if k in seen: continue
    seen[k]=True
    st=LA.classify_leg_audit(r["leg_audit"])
    print("   %-14s %-7s first=%s %s leg=%s whale=%r audit=%r [%s]"%(r["account_id"],r["category"],fmt(r["response_ts"]),(r["ticker"] or '')[:26],r["outcome_leg"],r["signal_outcome"],r["leg_audit"],st))

print("\n==== F: ARMED subs that placed NOTHING all day (no is_exit=0 row since arm) ====")
attempted=set((r[0],r[1]) for r in pm.execute("select distinct account_id,category from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=?",(SINCE,)))
silent=sorted(armed-attempted)
print("  armed-but-silent (%d of 57):"%len(silent))
for a,c in silent: print("    ",a,c)

print("\n==== G: P&L today (is_exit=1 closes since arm) ====")
for r in pm.execute("select account_id, count(*) n, sum(coalesce(realized_pnl,0)) pnl, sum(coalesce(fee,0)) fee, sum(case when realized_pnl is null then 1 else 0 end) null_pnl, coalesce(close_source,'NULL') cs from pm_subdivision_order where is_exit=1 and dry_run=0 and coalesce(settled_ts,response_ts)>=? group by account_id,cs order by account_id",(SINCE,)):
    print("  %-14s close_source=%-18s n=%s realized=$%.4f fee=$%.4f null_pnl=%s"%(r[0],r[5],r[1],r[2] or 0,r[3] or 0,r[4]))
print("  -- open-at-cost per account (filled entries w/ no is_exit=1 close for the cid) --")
q="""select o.account_id, count(*) nopen, round(sum(o.fill_count*case when o.outcome_leg='yes' then o.fill_price else (1.0-o.fill_price) end),2) cost
from pm_subdivision_order o where o.is_exit=0 and o.outcome_status='filled' and o.dry_run=0
and not exists(select 1 from pm_subdivision_order c where c.account_id=o.account_id and c.condition_id=o.condition_id and c.is_exit=1 and c.outcome_status='filled')
group by o.account_id order by o.account_id"""
for r in pm.execute(q): print("   %-14s open_legs=%s open_at_cost=$%s"%(r[0],r[1],r[2]))

print("\n==== H: exposure vs caps (config + today order counts) ====")
for r in pm.execute("select account_id,category,max_open_usd,daily_usd_cap,max_orders_per_day from pm_subdivision where (account_id,category) in (select account_id,category from pm_subdivision) and active=1 limit 1"):
    pass
print("  orders today per account (is_exit=0, since arm) vs account ceiling 50/cat*... :")
for r in pm.execute("select account_id,count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by account_id",(SINCE,)):
    print("   %-14s entries_today=%s"%(r[0],r[1]))
print("  per (account,category) entries today > 40 (near 50 ceiling)?:")
for r in pm.execute("select account_id,category,count(*) n from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by account_id,category having n>40 order by n desc",(SINCE,)):
    print("   NEAR-CEIL",r[0],r[1],r[2])

print("\n==== I: shard balances (latest) + pm_open_position freshness ====")
for a in ["kalshi_jack","kalshi_karen","kalshi_marc","kalshi_trey"]:
    r=pm.execute("select total_dollars,by_shard_json,snapshot_ts from pm_shard_balance_snapshot where account_id=? order by snapshot_ts desc limit 1",(a,)).fetchone()
    bd=json.loads(r[1]) if r and r[1] else {}
    print("  %-14s total=$%.2f s0=$%.2f s3=$%.2f (snap %s)"%(a,r[0] or 0,float(bd.get('0',0) or 0),float(bd.get('3',0) or 0),fmt(r[2])))
r=pm.execute("select max(refreshed_ts) from pm_open_position").fetchone()
print("  pm_open_position max refreshed_ts:", fmt(r[0]), "age %.1fh"%((NOW-int(r[0]))/3600.0))

print("\n==== J: shard-snapshot cadence per account today (union-across-cats liveness; gaps>=200s) ====")
for a in ["kalshi_jack","kalshi_karen","kalshi_marc","kalshi_trey"]:
    ts=[x[0] for x in pm.execute("select snapshot_ts from pm_shard_balance_snapshot where account_id=? and snapshot_ts>=? order by snapshot_ts",(a,SINCE))]
    mg=0; a1=a2=None
    for i in range(1,len(ts)):
        g=ts[i]-ts[i-1]
        if g>mg: mg=g;a1=ts[i-1];a2=ts[i]
    big=sum(1 for i in range(1,len(ts)) if ts[i]-ts[i-1]>=200)
    print("  %-14s snaps=%s maxgap=%.1fmin(%s->%s) gaps>=200s:%s"%(a,len(ts),mg/60.0,fmt(a1),fmt(a2),big))
print("\n==== DONE hc1 ====")
PY
