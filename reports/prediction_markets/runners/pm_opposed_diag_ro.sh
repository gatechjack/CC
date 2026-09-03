set -u
ROOT=/home/azureuser/trading_corp
PM=$ROOT/data/prediction_markets.db
V=$ROOT/venv/bin/python
CID="0x0f589076dd946f3c73af64a6ee6b8b3f776505b23fefbe220dde7f92f007aad1"
date -u +"### OPPOSED-GUARD NON-CONVERGENCE DIAG (READ-ONLY, mode=ro) %Y-%m-%dT%H:%M:%SZ ###"
PM="$PM" CID="$CID" "$V" - <<'PY'
import os, sqlite3, time
def ts(x):
    try:
        x=int(x); return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x)) if x else "None"
    except Exception: return str(x)
pm=sqlite3.connect("file:%s?mode=ro"%os.path.abspath(os.environ["PM"]),uri=True); pm.row_factory=sqlite3.Row
CID=os.environ["CID"]
print("### [1] account_opposed_cids MEMORY (DISTINCT condition_id WHERE close_source='opposed') for kalshi_jack/mlb ###")
rows=pm.execute("SELECT DISTINCT condition_id FROM pm_subdivision_order WHERE account_id='kalshi_jack' AND category='mlb' AND close_source='opposed' AND condition_id IS NOT NULL").fetchall()
print("  opposed-memory cid count =", len(rows))
for r in rows: print("    mem:", r["condition_id"])
print("  >>> target cid IN opposed-memory? ->", CID in {r["condition_id"] for r in rows},
      " (False => the memory NEVER recorded it => Jack's defect: memory keyed on resolution not decision)")
print()
print("### [2] ALL pm_subdivision_order rows for the contested cid (kalshi_jack) ###")
q=pm.execute("SELECT id,category,ticker,outcome_index,outcome_leg,is_exit,close_source,fill_count,fill_price,outcome_status,wallet,response_ts,settled_ts,won,realized_pnl FROM pm_subdivision_order WHERE account_id='kalshi_jack' AND condition_id=? ORDER BY id",(CID,)).fetchall()
print("  rows for cid:", len(q))
for r in q:
    print("   id=%s cat=%s tk=%s oidx=%s leg=%s is_exit=%s src=%s n=%s px=%s st=%s w=%s resp=%s settled=%s won=%s pnl=%s"%(
        r["id"],r["category"],r["ticker"],r["outcome_index"],r["outcome_leg"],r["is_exit"],r["close_source"],r["fill_count"],r["fill_price"],r["outcome_status"],(r["wallet"] or "")[:10],ts(r["response_ts"]),ts(r["settled_ts"]),r["won"],r["realized_pnl"]))
print()
print("### [3] NET-OPEN by outcome_index for the cid (account_held_outcomes view: net>0 = a held side) ###")
n=pm.execute("SELECT outcome_index, COALESCE(SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE -COALESCE(fill_count,0) END),0) net FROM pm_subdivision_order WHERE account_id='kalshi_jack' AND condition_id=? AND dry_run=0 AND outcome_status='filled' GROUP BY outcome_index",(CID,)).fetchall()
for r in n: print("   oidx=%s net_open=%s"%(r["outcome_index"], r["net"]))
print("   (during the 23:10-03:16 loop the held side had net>0; a settlement is_exit row nets it flat AFTER the game)")
print()
print("### [4] total close_source='opposed' rows EVER (both accounts, all categories) ###")
for aid in ("kalshi_jack","kalshi_karen"):
    c=pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE account_id=? AND close_source='opposed'",(aid,)).fetchone()[0]
    print("   %-13s opposed rows = %s"%(aid,c))
print()
print("### [5] any OTHER cid with entries on BOTH outcome_index (a held/contested shape) for jack/mlb ###")
b=pm.execute("SELECT condition_id, COUNT(DISTINCT outcome_index) noi, GROUP_CONCAT(DISTINCT outcome_index) ois FROM pm_subdivision_order WHERE account_id='kalshi_jack' AND category='mlb' AND is_exit=0 AND dry_run=0 AND outcome_status='filled' AND condition_id IS NOT NULL GROUP BY condition_id HAVING noi>=2").fetchall()
print("  cids we entered on >=2 outcomes:", len(b))
for r in b: print("    ", r["condition_id"], "outcome_idx=", r["ois"])
PY
echo "### DONE ###"
