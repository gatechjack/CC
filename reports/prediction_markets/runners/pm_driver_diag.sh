set -u
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp
MAIN=$PKG/main.py
PMDB=$ROOT/data/prediction_markets.db
LDB=$ROOT/data/trading_corp.db
V=$ROOT/venv/bin/python
echo "### PM DRIVER DIAGNOSTIC (READ-ONLY; touches NOTHING -- files/hashes/mode=ro reads/journal) $(date -u +%Y%m%dT%H%M%SZ) ###"
echo
echo "## [1] ** WHICH main.py IS ON THE BOX -- hash + wiring presence (the decisive check):"
BH=$(tr -d '\r' < "$MAIN" 2>/dev/null | sha256sum | cut -c1-16)
echo "  box main.py CR-stripped sha256(16) = $BH"
echo "  roster-carrying tip (multicat bba046e8f1ce9801): $([ "$BH" = bba046e8f1ce9801 ] && echo MATCH-block-present || echo DIFFERS-investigate)"
echo "  driver-spawn markers in the box main.py (line:match):"
grep -nE 'scheduled_pm_live_loop|driver_roster|plan_driver_tasks|PM LIVE DRIVER WIRED|pm_live_driver' "$MAIN" 2>/dev/null | sed 's/^/    /' | head -14
NMARK=$(grep -cE 'scheduled_pm_live_loop|plan_driver_tasks' "$MAIN" 2>/dev/null)
echo "  -> driver-spawn marker count = $NMARK (0 = the wiring block is GONE from this main.py)"
echo
echo "## [2] the 4 wiring log branches since the restart (which one fired? 0-of-all = block never ran):"
ST="2026-09-05 23:28:58"
for pat in 'PM LIVE DRIVER WIRED' 'idle (0 tasks)' 'enabled=false . not wired' 'PM live driver wiring FAILED'; do
  n=$(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -cF "$pat" 2>/dev/null)
  echo "    '$pat' : $n"
done
echo "  any 'PM live driver' line since restart:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'PM live driver|PM LIVE DRIVER' | grep -ivE 'IBIT|Candle' | sed 's/^/    /' | tail -6
echo
echo "## [3] config: pm_live_driver.enabled in the LIVE box config/strategies.yaml:"
CFG=$ROOT/config/strategies.yaml; [ -f "$CFG" ] || CFG=$PKG/config/strategies.yaml
$V - <<PY 2>/dev/null || echo "  (config read failed at $CFG)"
import yaml
d=(yaml.safe_load(open("$CFG")) or {}).get("pm_live_driver") or {}
print("  config file: $CFG")
print("  pm_live_driver.enabled =", d.get("enabled"), " (keys:", list(d.keys()), ")")
PY
echo
echo "## [4] roster READ-ONLY (mode=ro): active_driver_subdivisions + plan_driver_tasks -> does it PLAN tasks now?"
cd "$ROOT"
PYTHONPATH="$ROOT" "$V" - <<PY 2>&1 | sed 's/^/  /'
import sqlite3
try:
    from trading_corp.prediction_markets import driver_roster
except Exception as e:
    print("import driver_roster FAILED:", repr(e)[:160]); raise SystemExit
c=sqlite3.connect("file:$PMDB?mode=ro", uri=True); c.row_factory=sqlite3.Row
rows=driver_roster.active_driver_subdivisions(c)
print("active_driver_subdivisions -> %d row(s):" % len(rows))
for r in rows:
    d=dict(r); print("   ", {k:d.get(k) for k in ('account_id','category','wallet','n_attached') if k in d} or d)
accts={r['account_id'] for r in rows if 'account_id' in r.keys()}
spawn,skips=driver_roster.plan_driver_tasks(rows, accts)
print("plan_driver_tasks(all-accts-have-keys) -> %d TASK(s), %d SKIP(s)" % (len(spawn),len(skips)))
for s in spawn: sd=dict(s) if not isinstance(s,dict) else s; print("   TASK:", {k:sd.get(k) for k in ('account_id','categories') if k in sd} or sd)
for s in skips: print("   SKIP:", s)
PY
echo
echo "## [5] attachments (mode=ro) -- the 8 sub-divisions still active-attached?"
$V - <<PY 2>/dev/null || echo "  (attachment read failed)"
import sqlite3
c=sqlite3.connect("file:$PMDB?mode=ro", uri=True); c.row_factory=sqlite3.Row
try:
    rows=c.execute("SELECT account_id,category,COUNT(*) n FROM pm_subdivision_attachment WHERE active=1 GROUP BY account_id,category ORDER BY account_id,category").fetchall()
    print("  active attachments by (account,category):")
    for r in rows: print("    %s/%s : %d whale(s)" % (r['account_id'],r['category'],r['n']))
    print("  -> %d attached sub-divisions (expect 8)" % len(rows))
except Exception as e:
    print("  pm_subdivision_attachment query failed:", repr(e)[:120])
PY
echo
echo "## [6] BLAST RADIUS (mode=ro) -- open positions + anything placed since restart:"
$V - <<PY 2>/dev/null || echo "  (blast-radius read failed)"
import sqlite3
c=sqlite3.connect("file:$PMDB?mode=ro", uri=True); c.row_factory=sqlite3.Row
cols=[r[1] for r in c.execute("PRAGMA table_info(pm_subdivision_order)").fetchall()]
tsc=[x for x in ('created_ts','ts','placed_ts','updated_ts','filled_ts') if x in cols]
tsc=tsc[0] if tsc else None
mx=c.execute("SELECT MAX(id) FROM pm_subdivision_order").fetchone()[0]
tot=c.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0]
print("  pm_subdivision_order: %d rows, max id=%s (ts col=%s)" % (tot,mx,tsc))
print("  last 6 orders:")
for r in c.execute("SELECT * FROM pm_subdivision_order ORDER BY id DESC LIMIT 6").fetchall():
    d=dict(r); print("    id=%s acct=%s cat=%s status=%s ts=%s" % (d.get('id'),d.get('account_id'),d.get('category'),d.get('outcome_status') or d.get('status'),d.get(tsc) if tsc else '?'))
# placed since restart (best-effort): count rows with ts after the restart epoch
import calendar,time
re_ep=calendar.timegm(time.strptime("2026-09-05 23:28:58","%Y-%m-%d %H:%M:%S"))
if tsc:
    try:
        n=c.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE CAST(%s AS INTEGER) > ?" % tsc,(re_ep,)).fetchone()[0]
        print("  orders with %s > restart(%d) = %d  (0 = nothing placed since the restart)" % (tsc,re_ep,n))
    except Exception as e: print("  since-restart count failed:", repr(e)[:100])
PY
echo
echo "## [7] arm rows re-confirm (mode=ro) -- armed + timestamps unchanged:"
$V - <<PY 2>/dev/null || echo "  (arm read failed)"
import sqlite3,json
c=sqlite3.connect("file:$LDB?mode=ro", uri=True)
rows=c.execute("SELECT key,value_json FROM agent_state WHERE key LIKE 'arm:%' ORDER BY key").fetchall()
armed=sum(1 for k,v in rows if (json.loads(v).get('armed') is True))
print("  %d arm rows, %d armed=True (expect 9/9); latched any: %s" % (len(rows),armed,any(json.loads(v).get('latched') for k,v in rows)))
PY
echo "### DIAGNOSTIC DONE ###"
