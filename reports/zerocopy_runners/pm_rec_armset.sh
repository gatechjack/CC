PY=/home/azureuser/trading_corp/venv/bin/python
echo "===== A: boxing/f1 attachment RE-CHECK (premise conflict: Jack says zero whales) ====="
"$PY" - <<'PY'
import sqlite3, datetime
def fmt(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%MZ')
    except Exception: return str(ts)
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
print("-- current ACTIVE attachments for category in (boxing,f1) --")
for r in pm.execute("select account_id,category,wallet,active,added_ts,removed_ts from pm_subdivision_attachment where category in ('boxing','f1') order by category,active desc,added_ts"):
    print("   %-13s %-7s active=%s added=%s removed=%s wallet=%s"%(r[0],r[1],r[3],fmt(r[4]),(fmt(r[5]) if r[5] else '-'),r[2][:14]))
print("-- attachment EVENTS for boxing/f1 --")
for r in pm.execute("select ts,account_id,category,action,wallet from pm_subdivision_attachment_event where category in ('boxing','f1') order by ts"):
    print("   %s %-13s %-7s %-7s %s"%(fmt(r[0]),r[1],r[2],r[3],r[4][:14]))
PY
echo "===== B: FULL 92-SUB MAP (attachment / CURRENT heartbeat / pre-latch placements) ====="
"$PY" - <<'PY'
import sqlite3, datetime
LATCH=int(datetime.datetime(2026,10,6,2,57,1,tzinfo=datetime.timezone.utc).timestamp())
W7=LATCH-7*86400
def fmt(ts):
    if not ts: return "-"
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%m-%d %H:%MZ')
    except Exception: return str(ts)
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
pm.row_factory=sqlite3.Row
subs=[(r[0],r[1]) for r in pm.execute("select account_id,category from pm_subdivision where active=1 order by account_id,category")]
att={}
for r in pm.execute("select account_id,category,count(*) n from pm_subdivision_attachment where active=1 group by account_id,category"):
    att[(r[0],r[1])]=r[2]
hb={}
for r in pm.execute("select account_id,category,reached_ts,n_signals,placed,state from pm_driver_heartbeat"):
    hb[(r[0],r[1])]={"reached":r[2],"sig":r[3],"placed":r[4],"state":r[5]}
def entries(aid,cat,lo,hi):
    return pm.execute("select count(*) from pm_subdivision_order where account_id=? and category=? and is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? and coalesce(response_ts,submitted_ts)<?",(aid,cat,lo,hi)).fetchone()[0]
print("LATCH epoch",LATCH,"(2026-10-06 02:57:01Z); pre-latch 7d window from",fmt(W7))
print("%-13s %-8s %5s %-16s %5s %5s %6s %6s  %s"%("account","category","att","hb_state","sig","plc","e7dPL","eEver","VERDICT"))
c_armed=c_ind=0
rows=[]
for aid,cat in subs:
    a=att.get((aid,cat),0)
    h=hb.get((aid,cat))
    hs=h["state"] if h else "NONE"
    sig=h["sig"] if h else "-"; plc=h["placed"] if h else "-"
    e7=entries(aid,cat,W7,LATCH)
    eever=pm.execute("select count(*) from pm_subdivision_order where account_id=? and category=? and is_exit=0 and dry_run=0",(aid,cat)).fetchone()[0]
    if e7>0:
        verdict="ARMED (placed %d entries in 7d pre-latch)"%e7; c_armed+=1
    else:
        verdict="INDETERMINATE"; c_ind+=1
    rows.append((aid,cat,a,hs,sig,plc,e7,eever,verdict))
    print("%-13s %-8s %5s %-16s %5s %5s %6s %6s  %s"%(aid,cat,a,hs,sig,plc,e7,eever,verdict))
print("---- counts: ARMED(placed 7d pre-latch)=%s  INDETERMINATE=%s  total=%s ----"%(c_armed,c_ind,len(subs)))
print("has-heartbeat (driven/attached) subs:",len(hb),"| active-attachment subs:",sum(1 for s in subs if att.get(s,0)>0))
print("ARMED-but-NO-heartbeat (placed pre-latch yet no current beat):")
for aid,cat,a,hs,sig,plc,e7,eever,v in rows:
    if e7>0 and (aid,cat) not in hb: print("   ",aid,cat,"e7d",e7,"att",a)
print("ATTACHED/driven but NO pre-latch placement (INDETERMINATE -- armed-no-fill OR disarmed):")
n=0
for aid,cat,a,hs,sig,plc,e7,eever,v in rows:
    if e7==0 and (aid,cat) in hb: print("   ",aid,cat,"hb",hs,"sig",sig,"att",a); n+=1
print("   (",n,"such subs )")
PY
echo "===== C: STEP-3 journal pre-latch arm enumeration search (2026-10-06 00:30..02:57) ====="
journalctl -u trading-corp --since "2026-10-06 00:30:00" --until "2026-10-06 02:57:00" --no-pager -o short-iso 2>&1 | grep -iE "disarm_blocked|read_arm_verdict|not armed|arm:kalshi|armed roster|skip:disarm|[0-9]+ armed|disarmed" | head -25 || echo "(no arm-enumeration lines in window)"
echo "===== DONE armset ====="
