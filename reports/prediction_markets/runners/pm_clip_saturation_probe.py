#!/usr/bin/env python3
# pm_clip_saturation_probe.py -- READ-ONLY (Item 2): measure the COST-based ROI distribution and
# _edge_factor clip saturation across the 12 roster whales, per (wallet, category). Uses the ACTUAL
# ingest path (clause-(b) quarantine + event-group propagation + cost_basis = total_bought*avg_price),
# so the ROI equals what the rollup would store. MEASUREMENT ONLY -- does NOT change clip bounds.
# No DB, no writes.
import asyncio
from collections import defaultdict

from trading_corp.prediction_markets import category, ingest, rosters
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient

LEGACY = "/home/azureuser/trading_corp/data/trading_corp.db"
CEIL, FLOOR = 2.0, -0.5     # _edge_factor = 1.0 + clip(roi, -0.5, +2.0)  (kalshi_whale_stats._edge_factor)
MIN_RESOLVED = 10           # stats.DEFAULT_MIN_RESOLVED -> pairs that actually get SCORED/ranked
NOW = 1_700_000_000


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


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


async def main():
    roster = rosters.load_seed_roster(legacy_db_path=LEGACY)
    print("roster size = %d  (min_resolved for scoring = %d; clip [%.1f, %.1f])" % (len(roster), MIN_RESOLVED, FLOOR, CEIL))
    pairs = []            # (name, category, n_scoreable, cost_roi, notional_roi)
    zero_cost_scoreable = 0
    async with PolymarketDataAPIClient() as c:
        for entry in roster:
            w = entry["wallet"]
            nm = (entry.get("user_name") or w[:10])
            try:
                cps = await pull_closed(c, w)
            except Exception as e:
                print("pull err %s: %s" % (nm, str(e)[:60]))
                continue
            recs = []
            for cp in cps:
                cat, src = category.derive_category_from_slug(getattr(cp, "event_slug", ""), getattr(cp, "slug", ""))
                recs.append(ingest.cp_to_record(cp, cat, src, NOW))
            ingest.apply_event_group_quarantine(recs)
            bycat = defaultdict(lambda: {"n": 0, "net": 0.0, "cb": 0.0, "tb": 0.0})
            for r in recs:
                if r["pnl_suspect"]:
                    continue                                  # scoreable rows only
                d = bycat[r["category"]]
                d["n"] += 1
                d["net"] += (r["realized_pnl"] or 0.0)
                d["cb"] += (r["cost_basis"] or 0.0)
                d["tb"] += (r["total_bought"] or 0.0)
                if (r["cost_basis"] or 0.0) <= 0:
                    zero_cost_scoreable += 1
            for cat, d in bycat.items():
                croi = (d["net"] / d["cb"]) if d["cb"] > 0 else None
                nroi = (d["net"] / d["tb"]) if d["tb"] > 0 else None
                pairs.append((nm, cat, d["n"], croi, nroi))

    def pin(croi):
        if croi is None:
            return " "
        if croi >= CEIL:
            return "CEIL"
        if croi <= FLOOR:
            return "FLR"
        return " "

    print("\n=== ALL (wallet, category) pairs -- cost-ROI vs notional-ROI (SCORED = n>=%d) ===" % MIN_RESOLVED)
    print("  %-13s %-8s %5s %10s %10s %6s %s" % ("wallet", "cat", "n", "cost_roi%", "notl_roi%", "pin", "scored"))
    for nm, cat, n, croi, nroi in sorted(pairs, key=lambda x: (x[3] is None, -(x[3] or -9e9))):
        print("  %-13s %-8s %5d %10s %10s %6s %s" % (
            nm[:13], cat[:8], n,
            ("%+.1f" % (croi * 100)) if croi is not None else "n/a",
            ("%+.1f" % (nroi * 100)) if nroi is not None else "n/a",
            pin(croi), ("SCORED" if n >= MIN_RESOLVED else "")))

    scored = [(nm, cat, n, croi) for (nm, cat, n, croi, nroi) in pairs if n >= MIN_RESOLVED and croi is not None]
    crois = [croi for (_, _, _, croi) in scored]
    ceil_hits = [(nm, cat, croi) for (nm, cat, n, croi) in scored if croi >= CEIL]
    floor_hits = [(nm, cat, croi) for (nm, cat, n, croi) in scored if croi <= FLOOR]
    by_cat_ceil = defaultdict(int)
    for nm, cat, croi in ceil_hits:
        by_cat_ceil[cat] += 1

    print("\n=== SATURATION (scored pairs only, n>=%d) ===" % MIN_RESOLVED)
    print("scored pairs: %d" % len(scored))
    if crois:
        print("cost_roi distribution: min=%+.1f%%  median=%+.1f%%  max=%+.1f%%"
              % (min(crois) * 100, _median(crois) * 100, max(crois) * 100))
    print("pinned at CEILING (cost_roi >= %.1f = +200%%): %d" % (CEIL, len(ceil_hits)))
    for nm, cat, croi in ceil_hits:
        print("    CEIL  %-13s %-8s cost_roi=%+.1f%%" % (nm[:13], cat, croi * 100))
    print("pinned at FLOOR (cost_roi <= %.1f = -50%%): %d" % (FLOOR, len(floor_hits)))
    for nm, cat, croi in floor_hits:
        print("    FLR   %-13s %-8s cost_roi=%+.1f%%" % (nm[:13], cat, croi * 100))
    multi = {cat: k for cat, k in by_cat_ceil.items() if k >= 2}
    print("categories with MULTIPLE scored pairs at the ceiling (discrimination lost): %s" % (multi or "NONE"))
    print("scoreable rows with cost_basis<=0 (avg_price<=0/NULL; the guard concern): %d" % zero_cost_scoreable)
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
