set -u
ROOT=/home/azureuser/trading_corp
echo "### PHASE-6 BASELINE-2: LIVE PM DB (via pm_web open fds) + PLACING-GATE SIGNAL (READ-ONLY) $(date -u +%FT%TZ) ###"

PMPID=$(systemctl show prediction-markets-web -p MainPID --value 2>/dev/null)
echo "pm_web MainPID=$PMPID"
echo "===LIVE_PM_DB_PATH (from /proc/$PMPID/fd)==="
PMDB=""
if [ -n "$PMPID" ] && [ -d "/proc/$PMPID/fd" ]; then
  for l in $(ls -l /proc/$PMPID/fd 2>/dev/null | grep -oE '/[^ ]*prediction_markets\.db' | sort -u); do
    echo "  open-fd DB candidate: $l"
    case "$l" in *-wal|*-shm) ;; *) [ -z "$PMDB" ] && PMDB="$l" ;; esac
  done
fi
[ -z "$PMDB" ] && PMDB="$(ls -l /proc/$PMPID/fd 2>/dev/null | grep -oE '/[^ ]*prediction_markets\.db' | grep -vE '\-wal$|\-shm$' | head -1)"
echo "LIVE PMDB=$PMDB"
[ -n "$PMDB" ] && ls -la "$PMDB" 2>/dev/null
echo "===LIVE_PM_DB_PATH_END==="

if [ -n "$PMDB" ]; then
  "$ROOT/venv/bin/python3" - "$PMDB" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
def q(sql,args=()):
    try: return list(c.execute(sql,args))
    except Exception as e: return [("ERR",str(e)[:140])]
print("schema head:", q("SELECT MAX(version) FROM schema_version"))
print("pm_subdivision columns:", [r[1] for r in c.execute("PRAGMA table_info(pm_subdivision)")])
print("pm_subdivision total/active:", q("SELECT COUNT(*), SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) FROM pm_subdivision"))
# armed breakdown - try owner/account-like columns
for col in ('account','owner','owner_identity','venue','actor'):
    r=q("SELECT %s, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY %s"%(col,col))
    if r and r[0][0] != "ERR":
        print("pm_subdivision active by %s:"%col, r); break
print("pm_subdivision active by category:", q("SELECT category, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY category ORDER BY category"))
print()
print("pm_open_position columns:", [r[1] for r in c.execute("PRAGMA table_info(pm_open_position)")])
print("pm_open_position count:", q("SELECT COUNT(*) FROM pm_open_position"))
print()
print("pm_subdivision_order columns:", [r[1] for r in c.execute("PRAGMA table_info(pm_subdivision_order)")])
print("pm_subdivision_order count:", q("SELECT COUNT(*) FROM pm_subdivision_order"))
# most-recent order rows (find a ts-ish column dynamically)
cols=[r[1] for r in c.execute("PRAGMA table_info(pm_subdivision_order)")]
tscol=None
for cand in ('placed_ts','created_ts','ts','order_ts','updated_ts','fill_ts','recorded_ts'):
    if cand in cols: tscol=cand; break
print("pm_subdivision_order tscol=",tscol)
if tscol:
    print("pm_subdivision_order max ts:", q("SELECT MAX(%s) FROM pm_subdivision_order"%tscol))
    print("pm_subdivision_order last 8 (ts-desc):")
    sel=",".join([x for x in ('id',tscol,'account','category','status','ticker','side','contracts') if x in cols])
    for r in q("SELECT %s FROM pm_subdivision_order ORDER BY %s DESC LIMIT 8"%(sel,tscol)):
        print("   ",r)
c.close()
PYEOF
fi

echo "===ENGINE_LIVE_PM_ACTOR (trading_corp.db, pm_live / live_driver / kalshi_jack|karen)==="
if [ -f "$ROOT/data/trading_corp.db" ]; then
  "$ROOT/venv/bin/python3" - "$ROOT/data/trading_corp.db" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
try:
    print("actors matching pm_live/live/kalshi_jack/kalshi_karen (actor, count, max ts):")
    for r in c.execute("SELECT actor, COUNT(*), MAX(ts) FROM audit_event WHERE actor LIKE '%pm_live%' OR actor LIKE '%live_driver%' OR actor LIKE '%kalshi_jack%' OR actor LIKE '%kalshi_karen%' OR actor LIKE '%pm_web%' GROUP BY actor ORDER BY MAX(ts) DESC LIMIT 20"):
        print("  ",r)
    print("distinct kinds for poly_kalshi_mlb last 5 (retiring loop -- expect to STOP post-restart):")
    for r in c.execute("SELECT kind, COUNT(*), MAX(ts) FROM audit_event WHERE actor='poly_kalshi_mlb' GROUP BY kind ORDER BY MAX(ts) DESC LIMIT 5"):
        print("  ",r)
except Exception as e: print("  ERR",str(e)[:160])
c.close()
PYEOF
fi
echo "===ENGINE_LIVE_PM_ACTOR_END==="

echo "===JOURNALCTL READABILITY (gate candidate)==="
journalctl -u trading-corp -n 3 --no-pager 2>&1 | head -6 || echo "journalctl not readable by azureuser"
echo "===JOURNALCTL_END==="
echo "### DONE ###"
