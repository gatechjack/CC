set -u
ROOT=/home/azureuser/trading_corp
echo "### PHASE-6 LIVE PM DB LOCATE + ARMED BASELINE (READ-ONLY) $(date -u +%FT%TZ) ###"
PMPID=$(systemctl show prediction-markets-web -p MainPID --value 2>/dev/null)
echo "pm_web MainPID=$PMPID"
echo "-- /proc/$PMPID/cwd --"; readlink "/proc/$PMPID/cwd" 2>/dev/null || echo "(no cwd)"
echo "-- /proc/$PMPID/cmdline --"; tr '\0' ' ' < "/proc/$PMPID/cmdline" 2>/dev/null; echo
echo "-- pm_web ExecStart/WorkingDirectory --"; systemctl show prediction-markets-web -p ExecStart,WorkingDirectory 2>/dev/null
echo "-- PM_DB env in pm_web (path-ish vars) --"; tr '\0' '\n' < "/proc/$PMPID/environ" 2>/dev/null | grep -iE 'DB|PREDICTION|PM_' | head -20 || echo "(environ unreadable)"
echo "===ALL prediction_markets.db ON BOX (size-sorted, backups flagged)==="
find /home/azureuser -maxdepth 6 -name 'prediction_markets.db' 2>/dev/null | while read -r f; do
  sz=$(stat -c '%s' "$f" 2>/dev/null); mt=$(stat -c '%y' "$f" 2>/dev/null)
  flag=""; case "$f" in *bak*|*backup*|*_bak_*|*snapshot*) flag="[BACKUP?]";; esac
  printf "  %12s  %s  %s %s\n" "$sz" "$mt" "$f" "$flag"
done
echo "===END==="

# choose the live DB = a prediction_markets.db under the pm_web cwd, else largest non-backup
CWD=$(readlink "/proc/$PMPID/cwd" 2>/dev/null)
LIVE=""
if [ -n "$CWD" ] && [ -f "$CWD/prediction_markets.db" ]; then LIVE="$CWD/prediction_markets.db"; fi
if [ -z "$LIVE" ]; then
  LIVE=$(find /home/azureuser -maxdepth 6 -name 'prediction_markets.db' 2>/dev/null | grep -viE 'bak|backup|snapshot' | while read -r f; do echo "$(stat -c '%s' "$f") $f"; done | sort -rn | head -1 | cut -d' ' -f2-)
fi
echo "CHOSEN LIVE PMDB=$LIVE"
if [ -n "$LIVE" ]; then
  ls -la "$LIVE"* 2>/dev/null
  "$ROOT/venv/bin/python3" - "$LIVE" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
def q(sql,args=()):
    try: return list(c.execute(sql,args))
    except Exception as e: return [("ERR",str(e)[:140])]
print("schema head:", q("SELECT MAX(version) FROM schema_version"))
cols=[r[1] for r in c.execute("PRAGMA table_info(pm_subdivision)")]
print("pm_subdivision columns:", cols)
print("pm_subdivision total/active:", q("SELECT COUNT(*), SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) FROM pm_subdivision"))
for col in ('account','owner','owner_identity','venue','actor','sub_account'):
    if col in cols:
        print("pm_subdivision active by %s:"%col, q("SELECT %s, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY %s"%(col,col)))
        break
if 'category' in cols:
    print("pm_subdivision active by category:", q("SELECT category, COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY category ORDER BY category"))
print("pm_open_position count:", q("SELECT COUNT(*) FROM pm_open_position"))
ocols=[r[1] for r in c.execute("PRAGMA table_info(pm_subdivision_order)")]
print("pm_subdivision_order columns:", ocols)
print("pm_subdivision_order count:", q("SELECT COUNT(*) FROM pm_subdivision_order"))
tscol=None
for cand in ('placed_ts','created_ts','ts','order_ts','updated_ts','fill_ts','recorded_ts'):
    if cand in ocols: tscol=cand; break
print("pm_subdivision_order tscol=",tscol,"max=",q("SELECT MAX(%s) FROM pm_subdivision_order"%tscol) if tscol else "n/a")
if tscol:
    sel=",".join([x for x in ('id',tscol,'account','category','status','ticker','side','contracts') if x in ocols]) or tscol
    print("pm_subdivision_order last 6:")
    for r in q("SELECT %s FROM pm_subdivision_order ORDER BY %s DESC LIMIT 6"%(sel,tscol)): print("   ",r)
c.close()
PYEOF
fi
echo "### DONE ###"
