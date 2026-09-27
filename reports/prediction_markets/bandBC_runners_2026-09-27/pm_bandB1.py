import sqlite3
import json
import time
import sys
PMDB = "/home/azureuser/trading_corp/data/prediction_markets.db"
TARGETS = [("kalshi_jack", "itf"), ("kalshi_karen", "itf"), ("kalshi_marc", "itf"), ("kalshi_trey", "itf")]


def abort(msg):
    print("ABORT: " + msg)
    sys.exit(3)


conn = sqlite3.connect(PMDB, timeout=10)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=8000")

before = {(r["account_id"], r["category"]): dict(r) for r in conn.execute("SELECT * FROM pm_subdivision").fetchall()}
print("read %d rows BEFORE" % len(before))

# drift-gate: the 4 targets must exist and their cap must be NULL right now
for k in TARGETS:
    if k not in before:
        conn.close(); abort("target missing: %s" % (k,))
    if before[k]["per_order_usd_cap"] is not None:
        conn.close(); abort("DRIFT: %s per_order_usd_cap is not NULL (=%s) -- refusing to write" % (k, before[k]["per_order_usd_cap"]))
print("drift-gate OK: all 4 ITF targets present with per_order_usd_cap IS NULL")

now = int(time.time())
backup = "/home/azureuser/pm_itfcap_backup_%d.json" % now
with open(backup, "w") as f:
    f.write(json.dumps({("%s|%s" % k): v for k, v in before.items()}, default=str, indent=0))
print("backup written: %s (%d rows)" % (backup, len(before)))

cur = conn.execute(
    "UPDATE pm_subdivision SET per_order_usd_cap=50.0, updated_ts=? "
    "WHERE category='itf' AND per_order_usd_cap IS NULL "
    "AND account_id IN ('kalshi_jack','kalshi_karen','kalshi_marc','kalshi_trey')", (now,))
if cur.rowcount != 4:
    conn.rollback(); conn.close(); abort("rowcount=%d != 4 -- rolled back" % cur.rowcount)
conn.commit()
print("UPDATE committed: rowcount=%d" % cur.rowcount)

after = {(r["account_id"], r["category"]): dict(r) for r in conn.execute("SELECT * FROM pm_subdivision").fetchall()}
conn.close()

changed = []
for k in before:
    for col in before[k]:
        if str(before[k][col]) != str(after[k].get(col)):
            changed.append((k, col, before[k][col], after[k].get(col)))
print("=== changed cells (expect exactly the 4 targets x {per_order_usd_cap, updated_ts}) ===")
for (k, col, b, a) in changed:
    print("   %s.%s : %s -> %s" % (k, col, b, a))

only_targets_capts = all(k in TARGETS and col in ("per_order_usd_cap", "updated_ts") for (k, col, _, _) in changed)
caps_now_50 = all(after[k]["per_order_usd_cap"] == 50.0 for k in TARGETS)
others_cap_unchanged = all(after[k]["per_order_usd_cap"] == before[k]["per_order_usd_cap"] for k in before if k not in TARGETS)
n_cap_changes = sum(1 for (_, col, _, _) in changed if col == "per_order_usd_cap")
print("=== assertions ===")
print("   only 4 targets & only cap/updated_ts changed : %s" % only_targets_capts)
print("   all 4 target caps == 50.0                    : %s" % caps_now_50)
print("   all 86 other caps unchanged                  : %s" % others_cap_unchanged)
print("   number of cap cells changed (expect 4)       : %d" % n_cap_changes)
print("   VERDICT: %s" % ("PASS" if (only_targets_capts and caps_now_50 and others_cap_unchanged and n_cap_changes == 4) else "FAIL -- INVESTIGATE"))

# belt-and-suspenders: the 4 subs' config now reads 50.0 through the engine's own loader
try:
    import os
    os.chdir("/home/azureuser/trading_corp")
    sys.path.insert(0, "/home/azureuser/trading_corp")
    from trading_corp.prediction_markets.execution import sub_config_from_row
    print("=== sub_config_from_row POST-check (engine loader) ===")
    for k in TARGETS:
        sc = sub_config_from_row(after[k])
        print("   %s -> per_order_usd_cap=%s" % (k, getattr(sc, "per_order_usd_cap", "n/a")))
except Exception as e:
    print("   sub_config_from_row check SKIPPED (%s: %s) -- row re-read above is authoritative" % (type(e).__name__, e))
