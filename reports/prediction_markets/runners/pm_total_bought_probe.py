#!/usr/bin/env python3
# pm_total_bought_probe.py -- READ-ONLY (Task 1): is /closed-positions total_bought a trustworthy ROI
# denominator? For each clause-(a)-FIRING row and a NON-FIRING control, reconstruct the position from
# /activity (sum actual BUY fills on that conditionId) and compare to total_bought. THE CONTROL IS THE
# POINT: if total_bought is understated only on firing rows -> bounded blast radius; if understated
# universally -> every ROI is inflated. Also reconstructs evanng's UFC slice (open item, one-root-cause
# test). Reuses PolymarketDataAPIClient (no hand-rolled HTTP). No DB, no writes.
import asyncio
from collections import defaultdict

from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient

WALLETS = [
    ("SDTrading", "0x16bb9951a36fce71e2ef57890b786145e0ba8492", None),      # MLB
    ("xifutloong3", "0x2dc13c6bda81b202281e796953a7323de675b33c", None),    # MLB
    ("evanng", "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618", "ufc-"),       # UFC open item
]


def clause_a(tb, rp):
    tb = tb or 0.0
    rp = rp or 0.0
    if tb <= 0 and rp != 0:
        return False   # that is clause (b), not (a)
    return rp < -(tb + max(1.0, 0.01 * tb))


def act_amt(a):
    v = getattr(a, "usdc_size", None)
    if not v:
        v = (getattr(a, "size", 0) or 0) * (getattr(a, "price", 0) or 0)
    return v or 0.0


async def pull_closed(c, w):
    rows, off = [], 0
    while off < 8000:
        p = await c.fetch_closed_positions(w, limit=50, offset=off)
        if not p:
            break
        rows.extend(p)
        if len(p) < 50:
            break
        off += 50
    return rows


async def pull_activity(c, w, cap=10000):
    rows, off, trunc = [], 0, False
    while off < cap:
        p = await c.fetch_activity(w, limit=500, offset=off)
        if not p:
            break
        rows.extend(p)
        if len(p) < 500:
            break
        off += 500
    else:
        trunc = True
    return rows, trunc


def _fmt(v, w=11, d=2):
    return ("%*.*f" % (w, d, v)) if v is not None else ("%*s" % (w, "n/a"))


async def analyze(c, name, w, focus):
    closed = await pull_closed(c, w)
    acts, trunc = await pull_activity(c, w)
    agg = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "redeem": 0.0, "nbuy": 0})
    for a in acts:
        cid = getattr(a, "condition_id", "") or ""
        t = getattr(a, "type", "")
        s = getattr(a, "side", "")
        m = act_amt(a)
        d = agg[cid]
        if t == "TRADE" and s == "BUY":
            d["buy"] += m
            d["nbuy"] += 1
        elif t == "TRADE" and s == "SELL":
            d["sell"] += m
        elif t == "REDEEM":
            d["redeem"] += m

    def view(r):
        cid = r.condition_id
        d = agg.get(cid)
        has = d is not None
        tb = r.total_bought or 0.0
        rp = r.realized_pnl or 0.0
        avg = (getattr(r, "avg_price", 0.0) or 0.0)
        cost = tb * avg                        # HYPOTHESIS: real USDC cost = shares(=tb) * avg_price
        buy = d["buy"] if has else None
        # gap of the COST proxy (tb*avg) vs actual activity BUY -- ~0 confirms tb=notional, cost=tb*avg
        cgap = (100.0 * (cost - buy) / buy) if (has and buy) else None
        areal = (d["sell"] + d["redeem"] - d["buy"]) if has else None
        return {"cid": cid, "slug": (r.event_slug or r.slug or ""), "tb": tb, "rp": rp, "avg": avg,
                "cost": cost, "buy": buy, "nbuy": (d["nbuy"] if has else 0), "cgap": cgap,
                "areal": areal, "has": has, "fire": clause_a(tb, rp)}

    views = [view(r) for r in closed]
    if focus:
        views = [v for v in views if v["slug"].startswith(focus)]
    n_has = sum(1 for v in views if v["has"])
    firing = [v for v in views if v["fire"]]
    ctrl_loss = [v for v in views if (not v["fire"]) and v["has"] and v["tb"] > 0 and v["rp"] < 0]
    ctrl_win = [v for v in views if (not v["fire"]) and v["has"] and v["tb"] > 0 and v["rp"] > 0]

    print("=" * 78)
    print("%s  %s   focus=%s" % (name, w, focus or "(all)"))
    print("closed rows=%d  activity rows=%d  truncated=%s  cids-with-activity=%d/%d"
          % (len(closed), len(acts), trunc, n_has, len(views)))
    hdr = "  %-12s %-24s %11s %5s %11s %11s %7s %11s" % (
        "cid", "slug", "total_bght", "avg", "cost=tbXavg", "act_BUY", "c-b%", "row_realiz")

    def dump(rows, label, cap):
        print("-- %s (%d) --  [KEY: cost=tb*avg should ~= act_BUY (c-b%% ~0) if total_bought is NOTIONAL]" % (label, len(rows)))
        if rows:
            print(hdr)
        for v in rows[:cap]:
            print("  %-12s %-24s %s %5s %s %s %7s %s" % (
                v["cid"][:12], (v["slug"] or "")[:24], _fmt(v["tb"]),
                (("%.3f" % v["avg"]) if v["avg"] else "  -  "), _fmt(v["cost"]), _fmt(v["buy"]),
                (("%+.1f" % v["cgap"]) if v["cgap"] is not None else "n/a"), _fmt(v["rp"])))

    dump(firing, "CLAUSE-(A) FIRING rows", 12)
    dump(ctrl_loss, "CONTROL: non-firing LOSSES (tb>0, rp<0)", 8)
    dump(ctrl_win, "CONTROL: non-firing WINS (tb>0, rp>0)", 6)

    def slice_gap(rows):
        rr = [v for v in rows if v["has"] and v["tb"] > 0 and v["buy"]]
        stb = sum(v["tb"] for v in rr)
        scost = sum(v["cost"] for v in rr)
        sbuy = sum(v["buy"] for v in rr)
        g_tb = (100.0 * (sbuy - stb) / stb) if stb > 0 else None       # act_BUY vs total_bought
        g_cost = (100.0 * (scost - sbuy) / sbuy) if sbuy > 0 else None  # cost(tb*avg) vs act_BUY -> ~0 confirms notional
        return stb, scost, sbuy, g_tb, g_cost, len(rr)

    for lbl, rows in (("ALL matched", views), ("FIRING", firing),
                      ("CONTROL-loss", ctrl_loss), ("CONTROL-win", ctrl_win)):
        stb, scost, sbuy, g_tb, g_cost, n = slice_gap(rows)
        print("SLICE %-13s n=%-4d tb=%12.2f  cost(tbXavg)=%12.2f  act_BUY=%12.2f  buy-vs-tb%%=%s  cost-vs-buy%%=%s"
              % (lbl, n, stb, scost, sbuy,
                 ("%+.1f" % g_tb) if g_tb is not None else "n/a",
                 ("%+.1f" % g_cost) if g_cost is not None else "n/a"))
    if focus == "ufc-":
        rr = [v for v in views if v["has"]]
        print("evanng UFC: SUM row_realized=%+.2f  SUM act_realized(S+R-B)=%+.2f  (Probe-A: closed +9778.97 / act +1141.72 / scout -13706.51)"
              % (sum(v["rp"] for v in views), sum(v["areal"] for v in rr)))


async def main():
    async with PolymarketDataAPIClient() as c:
        for name, w, focus in WALLETS:
            try:
                await analyze(c, name, w, focus)
            except Exception as e:
                import traceback
                print("ANALYZE ERROR %s: %r" % (name, e))
                traceback.print_exc()
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
