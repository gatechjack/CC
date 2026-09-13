set -u
ROOT=/home/azureuser/trading_corp
echo "### LEGACY-PM MONEY-SAFETY READ (READ-ONLY) $(date -u +%FT%TZ) ###"
PY="$ROOT/venv/bin/python3"
"$PY" - "$ROOT/data/trading_corp.db" <<'PYEOF'
import sqlite3, sys, json, collections
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1], uri=True)
print("## poly_kalshi_order: classify last 400 by payload action/status/dry_run ##")
kinds=collections.Counter(); dry=collections.Counter(); realfills=0; sample=[]
for ts,pj in c.execute("SELECT ts,payload_json FROM audit_event WHERE actor='poly_kalshi_mlb' AND kind='poly_kalshi_order' ORDER BY ts DESC LIMIT 400"):
    try: p=json.loads(pj)
    except Exception: p={}
    act=p.get("action") or p.get("status") or p.get("result") or p.get("outcome") or p.get("event") or "?"
    kinds[act]+=1
    dr=p.get("dry_run")
    dry[str(dr)]+=1
    fc=p.get("fill_count") or p.get("filled") or p.get("count")
    if p.get("status")=="filled" or (isinstance(fc,(int,float)) and fc and not dr): realfills+=1
    if len(sample)<6: sample.append((ts, {k:p.get(k) for k in ("action","status","result","dry_run","fill_count","ticker","reason","side") if k in p}))
print("action/status histogram (last 400):", dict(kinds))
print("dry_run histogram:", dict(dry))
print("apparent real fills (status=filled or fill_count>0 & not dry):", realfills)
for s in sample: print("  sample:", s)
print()
print("## kalshi_round_trips schema + division/resolved breakdown ##")
cols=[r[1] for r in c.execute("PRAGMA table_info(kalshi_round_trips)")]
print("cols:", cols)
try:
    for r in c.execute("SELECT division, COUNT(*), SUM(CASE WHEN resolved_ts IS NULL THEN 1 ELSE 0 END) FROM kalshi_round_trips GROUP BY division"):
        print("  kalshi_rt division=%s total=%s unresolved=%s" % r)
except Exception as e: print("  division/resolved ERR", e)
print()
print("## polymarket_round_trips division/resolved ##")
try:
    for r in c.execute("SELECT division, COUNT(*), SUM(CASE WHEN resolved_ts IS NULL THEN 1 ELSE 0 END) FROM polymarket_round_trips GROUP BY division"):
        print("  poly_rt division=%s total=%s unresolved=%s" % r)
except Exception as e: print("  poly_rt ERR", e)
print()
print("## would_have_placed (paper) open exposure by actor (max ts) ##")
try:
    for r in c.execute("SELECT actor, COUNT(*), MAX(ts) FROM audit_event WHERE kind='would_have_placed' GROUP BY actor ORDER BY 2 DESC"):
        print("  whp actor=%s count=%s last=%s" % r)
except Exception as e: print("  whp ERR", e)
print()
print("## kalshi_copy_placed_live recency + any unresolved live copies ##")
try:
    for r in c.execute("SELECT MIN(ts),MAX(ts),COUNT(*) FROM audit_event WHERE actor='kalshi_copy_trader' AND kind='kalshi_copy_placed_live'"):
        print("  kalshi_copy_placed_live min/max/count:", r)
except Exception as e: print("  ERR", e)
c.close()
PYEOF
echo "### DONE ###"
