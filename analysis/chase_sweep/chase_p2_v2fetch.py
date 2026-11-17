"""Chase-rule section-3 probe 2 (READ-ONLY, stdlib): recover whale entry prices + first_entry_at from
Polymarket data-api v2 for every (wallet, condition_id) behind our historical copies.

Call budget: 400 GETs declared. Throttle 0.5s. HARD STOP on any 429 (save partial, exit 3).
Two passes per (wallet, cid-chunk): default status (OPEN+REDEEMABLE) and status=CLOSED.
Output: chase_v2_whale.json  {wallet|cid|oidx: {v2_avg, v2_first_entry_at, v2_total_size, v2_status}}
"""
import csv, json, sqlite3, sys, time, urllib.parse, urllib.request

SCR = r"C:\Users\AA Incorporado\cc-pm-backtest-2026-10-22-wt\scratch"
DB = (SCR + r"\prediction_markets.db").replace("\\", "/")
OUT = r"C:\Users\AAINCO~1\AppData\Local\Temp\claude\758809bcb234d7b1\43e4cf7d-4856-418a-a247-7081cd0839d4\scratchpad\chase_v2_whale.json"
BUDGET = 400
THROTTLE = 0.5
CHUNK = 20

con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
con.row_factory = sqlite3.Row
pairs = {}
for r in con.execute("SELECT DISTINCT wallet, condition_id, outcome_index FROM pm_subdivision_order "
                     "WHERE dry_run=0 AND is_exit=0 AND condition_id IS NOT NULL"):
    pairs.setdefault(str(r["wallet"]).lower(), set()).add(str(r["condition_id"]).lower())
con.close()
n_pairs = sum(len(v) for v in pairs.values())
print("wallets:", len(pairs), " distinct (wallet,cid) pairs:", n_pairs)

calls = 0
got = {}          # (wallet, cid_lower, oidx) -> row summary; keep BOTH outcome indices when present

def fetch(url):
    global calls
    if calls >= BUDGET:
        print("BUDGET EXHAUSTED at", calls, "calls -- stopping (partial saved)")
        save(); sys.exit(2)
    calls += 1
    req = urllib.request.Request(url, headers={"User-Agent": "tc-research/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("HTTP 429 at call", calls, "-- HARD STOP per rules (partial saved)")
            save(); sys.exit(3)
        print("HTTP", e.code, "at call", calls, url[:120])
        return None
    finally:
        time.sleep(THROTTLE)

def save():
    with open(OUT, "w") as fh:
        json.dump({"calls": calls, "pairs_requested": n_pairs,
                   "rows": {"|".join(str(x) for x in k): v for k, v in got.items()}}, fh)
    print("saved", len(got), "rows after", calls, "calls ->", OUT)

BASE = "https://data-api.polymarket.com/v2/positions"
for w, cids in sorted(pairs.items()):
    cl = sorted(cids)
    for i in range(0, len(cl), CHUNK):
        chunk = cl[i:i + CHUNK]
        for extra in ({}, {"status": "CLOSED"}):
            q = {"user": w, "condition": ",".join(chunk), "limit": "500"}
            q.update(extra)
            cursor = None
            while True:
                qq = dict(q)
                if cursor:
                    qq["cursor"] = cursor
                d = fetch(BASE + "?" + urllib.parse.urlencode(qq))
                if not d or not isinstance(d, dict):
                    break
                for row in d.get("data", []):
                    key = (str(row.get("proxy_wallet", "")).lower(),
                           str(row.get("condition_id", "")).lower(),
                           int(row.get("outcome_index") or 0))
                    # keep the row; if seen in both passes prefer the one with larger total_size (same row really)
                    got[key] = {
                        "v2_avg": row.get("avg_price"),
                        "v2_first_entry_at": row.get("first_entry_at"),
                        "v2_last_event_at": row.get("last_event_at"),
                        "v2_total_size": row.get("total_size"),
                        "v2_status": row.get("status"),
                        "outcome": row.get("outcome"),
                    }
                cursor = (d.get("pagination") or {}).get("next_cursor")
                if not cursor:
                    break
save()
print("DONE calls:", calls, " rows:", len(got))
