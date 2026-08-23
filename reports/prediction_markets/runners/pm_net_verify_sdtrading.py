#!/usr/bin/env python3
# pm_net_verify_sdtrading.py -- READ-ONLY §12 net-verify, PRIMARY target SDTrading (MLB, binary).
# FROM-SCRATCH reimplementation of the §3A scoreable predicate -- deliberately does NOT import
# trading_corp.prediction_markets.ingest -- to independently cross-check the DB's stored figures.
# Pulls /closed-positions, dedups by the PK (wallet,conditionId,outcomeIndex), reimplements clause (b)
# + event-group propagation + no-cost-basis quarantine (clause (a) is a FLAG, NOT excluded), filters MLB,
# and compares FULL net / SCOREABLE net / cost_basis / roi(cost) / roi(notional) / n_excluded to
# pm_category_stats. Reports DB figure, independent figure, delta. No DB writes.
import sqlite3
import json
import urllib.request
from collections import defaultdict

DATA = "https://data-api.polymarket.com"
SDT = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
DB = "data/prediction_markets.db"


def http(u):
    r = urllib.request.Request(u, headers={"User-Agent": "netverify/1.0"})
    with urllib.request.urlopen(r, timeout=40) as x:
        return json.loads(x.read().decode())


def pull_closed():
    rows, off = [], 0
    while off < 20000:
        p = http("%s/closed-positions?user=%s&limit=50&offset=%d" % (DATA, SDT, off))
        if not p:
            break
        rows.extend(p)
        if len(p) < 50:
            break
        off += 50
    return rows


def f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


raw = pull_closed()
# dedup by the PK (wallet, conditionId, outcomeIndex) exactly as INSERT OR REPLACE would (last wins)
by_pk = {}
for r in raw:
    pk = (SDT, str(r.get("conditionId") or ""), int(r.get("outcomeIndex") or 0))
    by_pk[pk] = r
rows = list(by_pk.values())

# FROM-SCRATCH predicate (NOT importing ingest): clause (b) zero-cost + event-group propagation +
# no-cost-basis; clause (a) is recorded as a flag only (NOT excluded from scoreable).
recs = []
for r in rows:
    tb = f(r.get("totalBought"))
    avg = f(r.get("avgPrice"))
    rp = f(r.get("realizedPnl"))
    es = (r.get("eventSlug") or "").strip()
    cb = tb * avg
    recs.append({"es": es, "tb": tb, "avg": avg, "rp": rp, "cb": cb,
                 "clause_b": (tb <= 0 and rp != 0), "ncb": (cb <= 0),
                 "mlb": (r.get("eventSlug") or r.get("slug") or "").lower().startswith("mlb")})
grp = defaultdict(list)
for x in recs:
    if x["es"]:
        grp[x["es"]].append(x)
for g in grp.values():
    if any(x["clause_b"] for x in g):
        for x in g:
            x["egp"] = True
for x in recs:
    x["suspect"] = x["clause_b"] or x.get("egp", False) or x["ncb"]

mlb = [x for x in recs if x["mlb"]]
mlb_sc = [x for x in mlb if not x["suspect"]]
ind_full_net = sum(x["rp"] for x in mlb)
ind_net = sum(x["rp"] for x in mlb_sc)
ind_cost = sum(x["cb"] for x in mlb_sc)
ind_tb = sum(x["tb"] for x in mlb_sc)
ind_n_res = len(mlb_sc)
ind_n_excl = sum(1 for x in mlb if x["suspect"])
ind_roi = (ind_net / ind_cost) if ind_cost > 0 else None
ind_roin = (ind_net / ind_tb) if ind_tb > 0 else None

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
db = c.execute("SELECT n_resolved, n_excluded, net_realized_pnl, cost_basis, total_bought, roi, roi_notional "
               "FROM pm_category_stats WHERE wallet=? AND category='mlb'", (SDT,)).fetchone()
c.close()


def line(label, dbv, indv):
    d = "n/a"
    if isinstance(dbv, (int, float)) and isinstance(indv, (int, float)):
        d = "%+.4f" % (dbv - indv)
    print("  %-22s DB=%-16s INDEP=%-16s delta=%s" % (label, dbv, indv, d))


print("=== SDTrading MLB net-verify (from-scratch predicate; NOT importing ingest) ===")
print("raw closed pulled:", len(raw), "| distinct PKs:", len(rows), "| MLB rows:", len(mlb), "| MLB scoreable:", len(mlb_sc))
if db is None:
    print("!! no pm_category_stats row for SDTrading/mlb -- DB not populated?")
else:
    line("n_resolved", db["n_resolved"], ind_n_res)
    line("n_excluded", db["n_excluded"], ind_n_excl)
    line("net_realized (scoreable)", round(db["net_realized_pnl"], 4), round(ind_net, 4))
    line("cost_basis (scoreable)", round(db["cost_basis"], 4), round(ind_cost, 4))
    line("total_bought (scoreable)", round(db["total_bought"], 4), round(ind_tb, 4))
    line("roi (cost)", round(db["roi"], 6) if db["roi"] is not None else None,
         round(ind_roi, 6) if ind_roi is not None else None)
    line("roi_notional", round(db["roi_notional"], 6) if db["roi_notional"] is not None else None,
         round(ind_roin, 6) if ind_roin is not None else None)
    net_ok = abs(db["net_realized_pnl"] - ind_net) < 0.01
    cost_ok = abs(db["cost_basis"] - ind_cost) < 0.01
    print("independent FULL net (ALL mlb rows, incl any suspect):", round(ind_full_net, 2))
    print("VERDICT: net match=%s  cost_basis match=%s  n_excluded match=%s"
          % (net_ok, cost_ok, db["n_excluded"] == ind_n_excl))
print("NET_VERIFY_DONE")
