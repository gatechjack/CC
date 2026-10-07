PY=/home/azureuser/trading_corp/venv/bin/python
echo "=== A) triggering UFC position in order journal ==="
"$PY" - <<'PY'
import sqlite3, datetime
def fmt(ts):
    if ts in (None,''): return "None"
    try: return datetime.datetime.utcfromtimestamp(int(float(ts))).strftime('%Y-%m-%d %H:%M:%SZ')
    except Exception: return str(ts)
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
print("-- pm_subdivision_order rows for KXUFCFIGHT-26SEP29BULVIS-VIS --")
cids=set()
for r in pm.execute("select account_id,is_exit,outcome_leg,outcome_status,submitted_count,fill_count,close_source,submitted_ts,response_ts,settled_ts,realized_pnl,condition_id,signal_slug from pm_subdivision_order where ticker like 'KXUFCFIGHT-26SEP29BULVIS%' order by account_id,submitted_ts"):
    cids.add(r[11])
    print("%-13s exit=%s leg=%s st=%-8s cnt=%s fill=%s close=%s sub=%s resp=%s settled=%s pnl=%s"%(r[0],r[1],r[2],r[3],r[4],r[5],r[6],fmt(r[7]),fmt(r[8]),fmt(r[9]),r[10]))
    print("    cid=%s slug=%s"%(r[11], r[12]))
print("-- net per account (entries filled minus exits) --")
for r in pm.execute("select account_id, sum(case when is_exit=0 and outcome_status='filled' then fill_count else 0 end) ent, sum(case when is_exit=1 and outcome_status='filled' then fill_count else 0 end) ex from pm_subdivision_order where ticker like 'KXUFCFIGHT-26SEP29BULVIS%' group by account_id"):
    print("   ",r[0],"entry_filled=",r[1],"exit_filled=",r[2],"net_open=",(r[1] or 0)-(r[2] or 0))
print("-- closed_position match for these condition_ids --")
for cid in cids:
    for r in pm.execute("select wallet,category,outcome,won,realized_pnl,cur_price,resolved_ts,end_date,title from pm_closed_position where condition_id=? limit 5",(cid,)):
        print("   ",cid[:12],"won=",r[3],"cur_price=",r[5],"resolved=",r[6],"end=",r[7],"title=",(r[8] or '')[:40])
print("-- any order rows with is_exit=1 close_source for these cids --")
for cid in cids:
    n=pm.execute("select count(*) from pm_subdivision_order where condition_id=? and is_exit=1",(cid,)).fetchone()[0]
    print("   ",cid[:12],"is_exit=1 rows:",n)
PY
echo "=== B) journal single-scan grep (since 2026-10-02 12:00) ==="
J=/tmp/tcj_$$.txt
journalctl -u trading-corp --since "2026-10-02 12:00:00" --no-pager -o short-iso 2>&1 | grep -iE 'reconcile|latch|disarm|mismatch|journal_only|BULVIS|KXUFCFIGHT|scalar|refund|[^a-z]void|Traceback|CRITICAL|boot-reconcile|auto_trigger' > "$J" 2>&1
echo "matched lines:"; wc -l "$J"; echo "file size:"; ls -la "$J"
echo "--- first 8 matched ---"; head -8 "$J"
echo "--- reconcile/latch/mismatch (first 25) ---"; grep -iE 'reconcile|latch|mismatch|journal_only|disarm' "$J" | head -25
echo "--- reconcile/latch/mismatch (LAST 10) ---"; grep -iE 'reconcile|latch|mismatch|journal_only|disarm' "$J" | tail -10
echo "--- BULVIS / KXUFCFIGHT ---"; grep -iE 'BULVIS|KXUFCFIGHT' "$J" | head -20
echo "--- scalar/void/refund ---"; grep -iE 'scalar|refund|[^a-z]void' "$J" | head -20
echo "--- Traceback/CRITICAL ---"; grep -iE 'Traceback|CRITICAL' "$J" | head -20
rm -f "$J"
echo "=== C) distinct reconcile-mismatch timestamps (coarse) ==="
journalctl -u trading-corp --since "2026-10-02 00:00:00" --no-pager -o short-iso 2>&1 | grep -iE 'boot-reconcile|reconcile mismatch|1 ticker' | awk '{print $1}' | sort | uniq -c | head -40
echo "=== DONE diag2 ==="
