PY=/home/azureuser/trading_corp/venv/bin/python
"$PY" - <<'PY'
import sqlite3, json, time, datetime
NOW=int(time.time())
def fmt(ts):
    if ts in (None,''): return "None"
    try: return datetime.datetime.utcfromtimestamp(int(float(ts))).strftime('%Y-%m-%d %H:%M:%SZ')
    except Exception: return str(ts)
def age(ts):
    try: return "%.1fh" % ((NOW-int(float(ts)))/3600.0)
    except Exception: return "?"
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
lg=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
def cols(con,t):
    try: return [r[1] for r in con.execute("pragma table_info(%s)"%t)]
    except Exception as e: return ["ERR:%s"%e]
def S(name): print("\n===== %s =====" % name)
print("NOW", fmt(NOW), "epoch", NOW)

S("0 PRAGMA for uncertain tables")
for t in ("pm_subdivision_order","pm_closed_position","pm_open_position","pm_subdivision_attachment","pm_subdivision_attachment_event","pm_shard_balance_snapshot","schema_version","pm_subdivision"):
    print(t, "->", cols(pm,t))

S("A1 overall last ENTRY fill (is_exit=0, filled, dry_run=0)")
try:
    r=pm.execute("select max(response_ts) from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0").fetchone()
    print("last entry fill:", fmt(r[0]), "age", age(r[0]))
    r=pm.execute("select account_id,category,ticker,submitted_price,fill_count,response_ts from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 order by response_ts desc limit 1").fetchone()
    print("row:", r)
except Exception as e: print("ERR",e)

S("A2 last ENTRY fill per account")
try:
    for r in pm.execute("select account_id, max(response_ts) m, count(*) n from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 group by account_id order by account_id"):
        print("%-14s last=%s age=%s n_fills_alltime=%s"%(r[0],fmt(r[1]),age(r[1]),r[2]))
except Exception as e: print("ERR",e)

S("A3 last ENTRY fill per category")
try:
    for r in pm.execute("select category, max(response_ts) m, count(*) n from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 group by category order by m desc"):
        print("%-10s last=%s age=%s n_alltime=%s"%(r[0],fmt(r[1]),age(r[1]),r[2]))
except Exception as e: print("ERR",e)

S("A4 last ENTRY fill per (account,category) -- only where fills exist")
try:
    for r in pm.execute("select account_id,category, max(response_ts) m, count(*) n from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 group by account_id,category order by m desc"):
        print("%-14s %-10s last=%s age=%s n=%s"%(r[0],r[1],fmt(r[1+0] if False else r[2-0]) if False else fmt(r[2]),age(r[2]),r[3]))
except Exception as e: print("ERR",e)

S("A5 entry-fill counts by category, buckets 2d/3d/7d/14d/30d (dry_run=0)")
try:
    for d,lbl in ((2,"2d"),(3,"3d"),(7,"7d"),(14,"14d"),(30,"30d")):
        cut=NOW-d*86400
        rows=pm.execute("select category,count(*) from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 and response_ts>=? group by category order by 2 desc",(cut,)).fetchall()
        print(lbl, dict(rows), "TOTAL", sum(x[1] for x in rows))
except Exception as e: print("ERR",e)

S("A6 entry-fill counts by account, buckets (dry_run=0)")
try:
    for d,lbl in ((2,"2d"),(3,"3d"),(7,"7d"),(14,"14d"),(30,"30d")):
        cut=NOW-d*86400
        rows=pm.execute("select account_id,count(*) from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 and response_ts>=? group by account_id order by 2 desc",(cut,)).fetchall()
        print(lbl, dict(rows))
except Exception as e: print("ERR",e)

S("A7 most recent 25 ENTRY orders any status (dry_run=0)")
try:
    for r in pm.execute("select response_ts,submitted_ts,account_id,category,ticker,outcome_leg,submitted_count,submitted_price,outcome_status,substr(coalesce(error_detail,''),1,50) from pm_subdivision_order where is_exit=0 and dry_run=0 order by coalesce(response_ts,submitted_ts) desc limit 25"):
        print(fmt(r[0] or r[1]), "%-13s"%r[2], "%-8s"%r[3], (r[4] or '')[:24], r[5], "cnt",r[6],"px",r[7],"->",r[8], r[9])
except Exception as e: print("ERR",e)

S("A8 outcome_status distribution ENTRY last 30d (dry_run=0) and all-time")
try:
    cut=NOW-30*86400
    print("last30d:", dict(pm.execute("select coalesce(outcome_status,'NULL'),count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by 1",(cut,)).fetchall()))
    print("alltime:", dict(pm.execute("select coalesce(outcome_status,'NULL'),count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 group by 1").fetchall()))
    print("dry_run=1 last7d entries:", pm.execute("select count(*) from pm_subdivision_order where is_exit=0 and dry_run=1 and coalesce(response_ts,submitted_ts)>=?",(NOW-7*86400,)).fetchone()[0])
except Exception as e: print("ERR",e)

S("B1 arm state (legacy agent_state pm_live arm:*)")
try:
    rows=lg.execute("select key,value_json,updated_ts from agent_state where agent='pm_live' and key like 'arm:%' order by key").fetchall()
    narmed=0; nnot=0; latched=[]
    notarmed=[]
    for k,v,u in rows:
        try: d=json.loads(v)
        except Exception: d={}
        a=d.get('armed'); l=d.get('latched'); at=d.get('auto_trigger')
        if a is True: narmed+=1
        else: nnot+=1; notarmed.append((k,a,l,at,d.get('reason'),u))
        if l: latched.append((k,a,l,at,d.get('reason'),u))
    print("total arm keys:", len(rows), "armed=True:", narmed, "armed!=True:", nnot)
    print("-- latched (latched truthy):", len(latched))
    for x in latched: print("  LATCH", x[0], "armed=",x[1],"latched=",x[2],"trigger=",x[3],"reason=",x[4],"upd=",x[5])
    print("-- armed!=True list:", len(notarmed))
    for x in notarmed: print("  NOTARM", x[0], "armed=",x[1],"latched=",x[2],"trigger=",x[3],"reason=",x[4],"upd=",x[5])
except Exception as e: print("ERR",e)

S("B2 other pm_live keys (non-arm)")
try:
    for r in lg.execute("select key,substr(value_json,1,100),updated_ts from agent_state where agent='pm_live' and key not like 'arm:%' order by key"):
        print(r[0], "|", r[1], "|", r[2])
except Exception as e: print("ERR",e)

S("B3 scalar/void/refund settlement defect check")
try:
    cc=cols(pm,"pm_closed_position")
    print("closed cols:", cc)
    mrcol=None
    for cand in ("market_result","result","resolution","outcome_result"):
        if cand in cc: mrcol=cand; break
    tcol="resolved_ts" if "resolved_ts" in cc else ("closed_ts" if "closed_ts" in cc else ("refreshed_ts" if "refreshed_ts" in cc else None))
    print("using market_result col:", mrcol, "time col:", tcol)
    if mrcol and tcol:
        cut=NOW-12*86400
        print("last12d by %s:"%mrcol, dict(pm.execute("select coalesce(%s,'NULL'),count(*) from pm_closed_position where %s>=? group by 1"%(mrcol,tcol),(cut,)).fetchall()))
        rows=pm.execute("select wallet,category,%s,%s,slug from pm_closed_position where %s>=? and lower(coalesce(%s,'')) in ('scalar','void','refund','voided','refunded') order by %s desc limit 20"%(mrcol,tcol,tcol,mrcol,tcol),(cut,)).fetchall()
        print("scalar/void/refund last12d:", len(rows))
        for r in rows: print("  ", r[0][:10],r[1],r[2],fmt(r[3]),(r[4] or '')[:40])
    print("pm_subdivision_order close_source last12d:", dict(pm.execute("select coalesce(close_source,'NULL'),count(*) from pm_subdivision_order where is_exit=1 and coalesce(response_ts,submitted_ts)>=? group by 1",(NOW-12*86400,)).fetchall()))
except Exception as e: print("ERR",e)

S("C1 pm_driver_task_heartbeat (per account)")
try:
    for r in pm.execute("select account_id,last_cycle_ts,updated_ts from pm_driver_task_heartbeat order by account_id"):
        print("%-14s last_cycle=%s age=%s upd=%s"%(r[0],fmt(r[1]),age(r[1]),fmt(r[2])))
except Exception as e: print("ERR",e)

S("C2 pm_driver_heartbeat all rows")
try:
    for r in pm.execute("select account_id,category,reached_ts,evaluated_ts,n_signals,placed,errors,ceiling_latched,state from pm_driver_heartbeat order by account_id,category"):
        print("%-13s %-9s reached=%s(%s) eval=%s(%s) sig=%s plc=%s err=%s ceil=%s st=%s"%(r[0],r[1],fmt(r[2]),age(r[2]),fmt(r[3]),age(r[3]),r[4],r[5],r[6],r[7],r[8]))
except Exception as e: print("ERR",e)

S("C3 per-account UNION-across-categories newest heartbeat (stall instrument)")
try:
    for r in pm.execute("select account_id, max(reached_ts) mr, max(evaluated_ts) me, sum(n_signals) s, sum(placed) p, sum(errors) e, count(*) ncat from pm_driver_heartbeat group by account_id order by account_id"):
        print("%-14s newest_reached=%s(%s) newest_eval=%s(%s) sum_sig=%s sum_plc=%s sum_err=%s ncats=%s"%(r[0],fmt(r[1]),age(r[1]),fmt(r[2]),age(r[2]),r[3],r[4],r[5],r[6]))
except Exception as e: print("ERR",e)

S("D1 active attachments")
try:
    print("active total:", pm.execute("select count(*) from pm_subdivision_attachment where active=1").fetchone()[0])
    print("distinct wallets active:", pm.execute("select count(distinct wallet) from pm_subdivision_attachment where active=1").fetchone()[0])
    print("per category:", dict(pm.execute("select category,count(*) from pm_subdivision_attachment where active=1 group by category order by 2 desc").fetchall()))
    print("per account:", dict(pm.execute("select account_id,count(*) from pm_subdivision_attachment where active=1 group by account_id").fetchall()))
except Exception as e: print("ERR",e)

S("D2 attachment events since 2026-09-27")
try:
    ac=cols(pm,"pm_subdivision_attachment_event"); print("event cols:", ac)
    cut=int(datetime.datetime(2026,9,27).timestamp())
    tcol="event_ts" if "event_ts" in ac else ("ts" if "ts" in ac else ("created_ts" if "created_ts" in ac else None))
    if tcol:
        for r in pm.execute("select * from pm_subdivision_attachment_event where %s>=? order by %s desc limit 40"%(tcol,tcol),(cut,)):
            print(r)
except Exception as e: print("ERR",e)

S("D3 pm_open_position freshness + attached-whale open positions by category")
try:
    r=pm.execute("select max(refreshed_ts) from pm_open_position").fetchone()
    print("max refreshed_ts:", fmt(r[0]), "age", age(r[0]))
    for d,lbl in ((1,"24h"),(3,"3d")):
        print("open positions refreshed within",lbl,":", pm.execute("select count(*) from pm_open_position where refreshed_ts>=?",(NOW-d*86400,)).fetchone()[0])
    print("-- current open positions held BY ACTIVE-ATTACHED whales, per category:")
    q="select p.category,count(*),max(p.refreshed_ts) from pm_open_position p join pm_subdivision_attachment a on a.wallet=p.wallet and a.active=1 group by p.category order by 2 desc"
    for r in pm.execute(q):
        print("  %-10s open=%s maxref=%s(%s)"%(r[0],r[1],fmt(r[2]),age(r[2])))
except Exception as e: print("ERR",e)

S("D5 category mix of ENTRY fills 30d vs 90d (dry_run=0)")
try:
    for d,lbl in ((30,"30d"),(90,"90d")):
        cut=NOW-d*86400
        print(lbl, dict(pm.execute("select category,count(*) from pm_subdivision_order where is_exit=0 and outcome_status='filled' and dry_run=0 and response_ts>=? group by category order by 2 desc",(cut,)).fetchall()))
except Exception as e: print("ERR",e)

S("E1 pm_subdivision config (gate 2) -- all rows, flag eff ceiling < 0.99")
try:
    hdr="acct category act mode contracts fixed cap daily maxopen maxord slip liqr eff_ceil FLAG"
    print(hdr)
    for r in pm.execute("select account_id,category,active,sizing_mode,contracts,fixed_stake_usd,per_order_usd_cap,daily_usd_cap,max_open_usd,max_orders_per_day,max_slippage_cents,liquidity_ratio from pm_subdivision order by account_id,category"):
        acct,cat,act,mode,contracts,fixed,cap,daily,mo,mord,slip,liqr=r
        eff=None; flag=""
        if mode=='contracts' and contracts and cap:
            eff=cap/float(contracts)
            if eff<0.99: flag="<<< GATE2-TRAP"
        print("%-13s %-9s a=%s %-9s c=%s fx=%s cap=%s dly=%s mo=%s ord=%s sl=%s lq=%s eff=%s %s"%(acct,cat,act,mode,contracts,fixed,cap,daily,mo,mord,slip,liqr, ("%.3f"%eff if eff is not None else "-"), flag))
except Exception as e: print("ERR",e)

S("E1b cap + contracts distribution")
try:
    print("per_order_usd_cap dist:", dict(pm.execute("select per_order_usd_cap,count(*) from pm_subdivision group by 1 order by 1").fetchall()))
    print("contracts dist:", dict(pm.execute("select contracts,count(*) from pm_subdivision group by 1 order by 1").fetchall()))
    print("sizing_mode dist:", dict(pm.execute("select sizing_mode,count(*) from pm_subdivision group by 1").fetchall()))
    print("active dist:", dict(pm.execute("select active,count(*) from pm_subdivision group by 1").fetchall()))
    print("ITF rows:")
    for r in pm.execute("select account_id,category,active,sizing_mode,contracts,per_order_usd_cap from pm_subdivision where category='itf' order by account_id"):
        print("  ",r)
except Exception as e: print("ERR",e)

S("E2 shard balances latest snapshot per account (gate 6b)")
try:
    for acct in [x[0] for x in pm.execute("select distinct account_id from pm_shard_balance_snapshot")]:
        r=pm.execute("select snapshot_ts,total_dollars,has_breakdown,by_shard_json from pm_shard_balance_snapshot where account_id=? order by snapshot_ts desc limit 1",(acct,)).fetchone()
        shards=""
        try:
            bd=json.loads(r[3]); shards=json.dumps(bd)
        except Exception: shards=str(r[3])[:120]
        print("%-14s snap=%s(%s) total=$%.2f brk=%s shards=%s"%(acct,fmt(r[0]),age(r[0]),r[1] or 0.0,r[2],shards))
except Exception as e: print("ERR",e)

S("E3 market_types tokens per sub (prop/inning detection)")
try:
    print("market_types distinct values:")
    for r in pm.execute("select market_types,count(*) from pm_subdivision group by market_types order by 2 desc"):
        print("  n=%s : %s"%(r[1], r[0]))
except Exception as e: print("ERR",e)

S("F1 last POST attempt (dry_run=0, outcome_status NOT NULL) + recent failures")
try:
    r=pm.execute("select response_ts,submitted_ts,account_id,category,ticker,outcome_status,substr(coalesce(error_detail,''),1,60) from pm_subdivision_order where dry_run=0 and outcome_status is not null order by coalesce(response_ts,submitted_ts) desc limit 1").fetchone()
    print("last POST-with-response:", fmt(r[0] or r[1]),"age",age(r[0] or r[1]),"acct",r[2],"cat",r[3],"stat",r[5],"err",r[6])
    cut=NOW-14*86400
    print("entry non-fill statuses last14d:", dict(pm.execute("select outcome_status,count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and outcome_status in ('rejected','error','no_fill') and coalesce(response_ts,submitted_ts)>=? group by 1",(cut,)).fetchall()))
    for r in pm.execute("select coalesce(response_ts,submitted_ts),account_id,category,ticker,outcome_status,substr(coalesce(error_detail,''),1,70) from pm_subdivision_order where is_exit=0 and dry_run=0 and outcome_status in ('rejected','error','no_fill') and coalesce(response_ts,submitted_ts)>=? order by 1 desc limit 20",(cut,)):
        print("  ",fmt(r[0]),r[1],r[2],(r[3] or '')[:22],r[4],r[5])
except Exception as e: print("ERR",e)

S("G1 schema_version + new subs since 09-27")
try:
    print("schema_version cols:", cols(pm,"schema_version"))
    try: print("max version:", pm.execute("select max(version) from schema_version").fetchone()[0])
    except Exception as e: print("maxver ERR",e)
    sc=cols(pm,"pm_subdivision")
    if "created_ts" in sc:
        cut=int(datetime.datetime(2026,9,27).timestamp())
        print("subs created since 2026-09-27:")
        for r in pm.execute("select account_id,category,active,created_ts from pm_subdivision where created_ts>=? order by created_ts",(cut,)):
            print("  ",r[0],r[1],"active=",r[2],fmt(r[3]))
    print("total subs:", pm.execute("select count(*) from pm_subdivision").fetchone()[0])
except Exception as e: print("ERR",e)
print("\n===== DONE diag1 =====")
PY
