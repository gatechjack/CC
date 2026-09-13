set -u
ROOT=/home/azureuser/trading_corp
echo "### PHASE-6 DO-NO-HARM BASELINE (READ-ONLY) $(date -u +%FT%TZ) ###"

echo "===SERVICES==="
for svc in trading-corp prediction-markets-web sfp-card-watcher; do
  echo "-- $svc --"
  systemctl show "$svc" -p Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp,NRestarts 2>/dev/null
done
echo "===SERVICES_END==="

echo "===KCV2_OBSERVER (Jack's; must NOT be touched by this phase)==="
echo "-- PID 679 (memory-recorded) --"
ps -p 679 -o pid,ppid,etime,cmd 2>/dev/null || echo "PID 679 NOT RUNNING (may have rotated)"
echo "-- pattern search (crypto_v2 / kcv2 / crypto_v2_observer) --"
pgrep -af 'crypto_v2|kcv2|crypto_v2_observer' 2>/dev/null || echo "(no pattern match)"
echo "===KCV2_OBSERVER_END==="

echo "===PM_DB_DISCOVERY==="
PMDB=""
for c in /home/azureuser/prediction_markets/prediction_markets.db /home/azureuser/pm_web/prediction_markets.db /home/azureuser/prediction_markets/data/prediction_markets.db /home/azureuser/trading_corp/prediction_markets.db; do
  [ -f "$c" ] && PMDB="$c" && break
done
if [ -z "$PMDB" ]; then
  PMDB="$(find /home/azureuser -maxdepth 5 -name 'prediction_markets.db' 2>/dev/null | head -1)"
fi
echo "PMDB=$PMDB"
if [ -n "$PMDB" ]; then
  ls -la "$PMDB" 2>/dev/null
  "$ROOT/venv/bin/python3" - "$PMDB" <<'PYEOF'
import sqlite3,sys,re
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
def q(sql,args=()):
    try: return list(c.execute(sql,args))
    except Exception as e: return [("ERR",str(e)[:120])]
tbls=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("tables(%d):"%len(tbls),tbls)
print("schema head:", q("SELECT MAX(version) FROM schema_version"))
print("pm_subdivision total/active:", q("SELECT COUNT(*), SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) FROM pm_subdivision"))
print("pm_subdivision active by account:", q("SELECT account, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY account"))
print("pm_subdivision active (account/category):", q("SELECT account, category, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY account, category ORDER BY account, category"))
for t in tbls:
    if re.search(r'journal|fill|order|position|copy|trade',t,re.I):
        cols=[r[1] for r in c.execute("PRAGMA table_info(%s)"%t)]
        tscol=None
        for cand in ('ts','created_ts','placed_ts','fill_ts','updated_ts','event_ts','order_ts','recorded_ts'):
            if cand in cols: tscol=cand; break
        cnt=q("SELECT COUNT(*) FROM %s"%t)
        mx=q("SELECT MAX(%s) FROM %s"%(tscol,t)) if tscol else [("no-ts-col",)]
        print("  table=%s count=%s tscol=%s max=%s"%(t,cnt,tscol,mx))
c.close()
PYEOF
fi
echo "===PM_DB_DISCOVERY_END==="

echo "===ENGINE SURVIVOR ACTIVITY (trading_corp.db, most-recent actors)==="
if [ -f "$ROOT/data/trading_corp.db" ]; then
  "$ROOT/venv/bin/python3" - "$ROOT/data/trading_corp.db" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
try:
    print("audit_event: top 25 actors by most-recent ts (actor, count, max ts):")
    for r in c.execute("SELECT actor, COUNT(*), MAX(ts) FROM audit_event GROUP BY actor ORDER BY MAX(ts) DESC LIMIT 25"):
        print("  ",r)
except Exception as e: print("  ERR",str(e)[:160])
c.close()
PYEOF
else
  echo "(engine trading_corp.db not found at $ROOT/data/trading_corp.db)"
fi
echo "===ENGINE_SURVIVOR_ACTIVITY_END==="
echo "### DONE ###"
