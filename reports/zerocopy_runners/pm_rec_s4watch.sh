PY=/home/azureuser/trading_corp/venv/bin/python
SNAP=/tmp/pmsnap_$$.py
cat > "$SNAP" <<'PY'
import sqlite3, sys, time, datetime
SINCE=int(sys.argv[1]); NOW=int(time.time())
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
def fmt(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%H:%M:%SZ')
    except Exception: return str(ts)
# new ENTRY orders (is_exit=0) since arm
rows=pm.execute("select account_id,category,ticker,outcome_leg,submitted_price,fill_count,outcome_status,coalesce(response_ts,submitted_ts) ts,substr(coalesce(leg_audit,''),1,40) from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? order by ts",(SINCE,)).fetchall()
print("  new ENTRY orders since arm:",len(rows))
byacct={}
for r in rows:
    byacct.setdefault(r[0],{}).setdefault(r[6] or 'NULL',0)
    byacct[r[0]][r[6] or 'NULL']+=1
    flag=" <<< BOXING/F1 FIRST-FILL (fire-first watch)" if r[1] in ('boxing','f1') else ""
    print("    %s %-13s %-7s %-26s %s px=%s cnt=%s -> %s legaudit=%s%s"%(fmt(r[7]),r[0],r[1],(r[2] or '')[:26],r[3],r[4],r[5],r[6],r[8],flag))
for a,d in sorted(byacct.items()): print("    %s: %s"%(a,d))
# heartbeat per armed account (union across cats): sig/placed/errors
print("  heartbeat per account (sum sig/placed/errors, newest eval age):")
for r in pm.execute("select account_id, sum(coalesce(n_signals,0)), sum(coalesce(placed,0)), sum(coalesce(errors,0)), max(evaluated_ts) from pm_driver_heartbeat group by account_id order by account_id"):
    print("    %-14s sig=%s placed=%s errors=%s eval_age=%.1fmin"%(r[0],r[1],r[2],r[3],(NOW-int(r[4] or NOW))/60.0))
PY
SINCE=$(date -u -d '2026-10-07 04:15:00' +%s)
echo "WATCH since $SINCE (2026-10-07 04:15:00Z); engine cycle ~5-11min; 10 samples x 150s (~25min)"
for i in $(seq 1 10); do
  echo "==== sample $i $(date -u +%H:%M:%SZ) ===="
  "$PY" "$SNAP" "$SINCE"
  if [ "$i" -lt 10 ]; then sleep 150; fi
done
rm -f "$SNAP"
echo "=== FINAL per-account entry-status tally since arm ==="
"$PY" - "$SINCE" <<'PY'
import sqlite3, sys
SINCE=int(sys.argv[1])
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
print("entries by account x status:")
for r in pm.execute("select account_id,outcome_status,count(*) from pm_subdivision_order where is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=? group by account_id,outcome_status order by account_id",(SINCE,)):
    print("   ",r[0],r[1],r[2])
print("first FILLED entry per account since arm:")
for r in pm.execute("select account_id,min(coalesce(response_ts,submitted_ts)) from pm_subdivision_order where is_exit=0 and dry_run=0 and outcome_status='filled' and coalesce(response_ts,submitted_ts)>=? group by account_id",(SINCE,)):
    print("   ",r[0],r[1])
print("boxing/f1 entries since arm:", pm.execute("select count(*) from pm_subdivision_order where category in ('boxing','f1') and is_exit=0 and dry_run=0 and coalesce(response_ts,submitted_ts)>=?",(SINCE,)).fetchone()[0])
PY
echo "=== DONE s4 ==="
