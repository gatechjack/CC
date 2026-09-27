import sqlite3
import hashlib
import json
PMDB = "/home/azureuser/trading_corp/data/prediction_markets.db"
c = sqlite3.connect("file:%s?mode=ro" % PMDB, uri=True)
c.row_factory = sqlite3.Row


def show(t, sql, a=()):
    print("--- " + t)
    rows = c.execute(sql, a).fetchall()
    if not rows:
        print("   (no rows)")
    for r in rows:
        print("   " + " | ".join("%s=%s" % (k, r[k]) for k in r.keys()))


print("=== pm_subdivision columns (locate the 'contracts' value) ===")
for r in c.execute("PRAGMA table_info(pm_subdivision)").fetchall():
    print("   col %s %s" % (r["name"], r["type"]))
print("=== the 4 ITF rows, ALL columns (BEFORE) ===")
show("itf rows", "SELECT * FROM pm_subdivision WHERE category='itf' ORDER BY account_id")
print("=== all-90 caps snapshot hash (byte-unchanged baseline for the 86) ===")
rows = c.execute("SELECT account_id, category, per_order_usd_cap FROM pm_subdivision ORDER BY account_id, category").fetchall()
blob = json.dumps([[r["account_id"], r["category"], r["per_order_usd_cap"]] for r in rows])
print("   caps_sha16=%s n=%d" % (hashlib.sha256(blob.encode()).hexdigest()[:16], len(rows)))
print("=== full-row snapshot hash EXCLUDING per_order_usd_cap (must be identical after write) ===")
allrows = c.execute("SELECT * FROM pm_subdivision ORDER BY account_id, category").fetchall()
cols = [k for k in allrows[0].keys() if k != "per_order_usd_cap"] if allrows else []
blob2 = json.dumps([[str(r[k]) for k in cols] for r in allrows])
print("   nonecap_sha16=%s cols=%d rows=%d" % (hashlib.sha256(blob2.encode()).hexdigest()[:16], len(cols), len(allrows)))
print("=== inning_winner grammar slug count (precise GLOB -inning-[1-9]-winner-{away|home|draw}) ===")
show("closed distinct grammar slugs", "SELECT COUNT(DISTINCT slug) n FROM pm_closed_position WHERE slug GLOB '*-inning-[1-9]-winner-away' OR slug GLOB '*-inning-[1-9]-winner-home' OR slug GLOB '*-inning-[1-9]-winner-draw'")
show("open distinct grammar slugs", "SELECT COUNT(DISTINCT slug) n FROM pm_open_position WHERE slug GLOB '*-inning-[1-9]-winner-away' OR slug GLOB '*-inning-[1-9]-winner-home' OR slug GLOB '*-inning-[1-9]-winner-draw'")
show("distinct grammar slugs (list)", "SELECT DISTINCT slug FROM pm_closed_position WHERE slug GLOB '*-inning-[1-9]-winner-away' OR slug GLOB '*-inning-[1-9]-winner-home' OR slug GLOB '*-inning-[1-9]-winner-draw' ORDER BY slug LIMIT 40")
show("distinct side breakdown", "SELECT CASE WHEN slug GLOB '*-winner-away' THEN 'away' WHEN slug GLOB '*-winner-home' THEN 'home' WHEN slug GLOB '*-winner-draw' THEN 'draw' END side, COUNT(DISTINCT slug) n FROM pm_closed_position WHERE slug GLOB '*-inning-[1-9]-winner-away' OR slug GLOB '*-inning-[1-9]-winner-home' OR slug GLOB '*-inning-[1-9]-winner-draw' GROUP BY side")
c.close()
