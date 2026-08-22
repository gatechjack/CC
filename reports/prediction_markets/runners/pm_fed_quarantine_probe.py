#!/usr/bin/env python3
# pm_fed_quarantine_probe.py -- READ-ONLY reconciliation (Issue 1, 2026-08-22): does the
# S3A quarantine actually FIRE on Kickstand7 / pako Fed rows? Pulls live /closed-positions and
# runs the ACTUAL ingest path (tier-1 categorize -> cp_to_record [row invariant] -> tier-2 on
# suspect-unknowns -> apply_event_group_quarantine), then reports EXACT counts + pastes every
# firing row. No DB, no writes. Uses the committed ingest.py / category.py so this == what a
# Step-3 backfill would flag.
import asyncio
import json
import urllib.request
from collections import Counter

from trading_corp.prediction_markets import category, ingest
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

DATA = "https://data-api.polymarket.com"
NOW = 1_700_000_000
EPS_FLOOR = ingest.EPS_FLOOR
EPS_PCT = ingest.EPS_PCT

import sys

# Fed whales (Issue 1) + live MLB whales (Issue 2: SDTrading is the corrected net-verify primary).
_DEFAULT = [
    ("Kickstand7", "0xd1acd3925d895de9aec98ff95f3a30c5279d08d5"),   # Fed
    ("pako", "0x71edffd0d70a1da823ff07a3c6fc81457294d338"),          # Fed
    ("SDTrading", "0x16bb9951a36fce71e2ef57890b786145e0ba8492"),     # live MLB -> net-verify primary
    ("xifutloong3", "0x2dc13c6bda81b202281e796953a7323de675b33c"),   # live MLB
]
# optional argv override: name:0xaddr name:0xaddr ...
WALLETS = [tuple(a.split(":", 1)) for a in sys.argv[1:]] or _DEFAULT


def http(u):
    r = urllib.request.Request(u, headers={"User-Agent": "fed-quar/1.0"})
    with urllib.request.urlopen(r, timeout=30) as x:
        return json.loads(x.read().decode())


def pull_all(w):
    rows, off = [], 0
    while off < 8000:
        page = http("%s/closed-positions?user=%s&limit=50&offset=%d" % (DATA, w, off))
        if not page:
            break
        rows.extend(page)
        if len(page) < 50:
            break
        off += 50
    return rows


def clause_of(tb, rp):
    """Which S3A ROW clause fires (matches ingest.compute_row_suspect). '' = none."""
    tb = tb or 0.0
    rp = rp or 0.0
    if tb <= 0 and rp != 0:
        return "b:zero-cost-nonzero-realized"
    if rp < -(tb + max(EPS_FLOOR, EPS_PCT * tb)):
        return "a:loss-exceeds-cost"
    return ""


for name, w in WALLETS:
    raw = pull_all(w)
    cps = [ClosedPositionRow.from_api(r) for r in raw]
    records = []
    for cp in cps:
        cat, src = category.derive_category_from_slug(getattr(cp, "event_slug", ""), getattr(cp, "slug", ""))
        records.append(ingest.cp_to_record(cp, cat, src, NOW))

    # tier-2 ONLY on the suspect-flagged unknowns (small set) so a Fed row tier-1 missed can't hide
    row_susp = [r for r in records if r["pnl_suspect"]]
    unk_slugs = sorted({r["event_slug"] for r in row_susp if r["category"] == "unknown" and r["event_slug"]})
    if unk_slugs:
        t2 = asyncio.run(category.derive_categories_batch(unk_slugs))
        for r in records:
            if r["pnl_suspect"] and r["category"] == "unknown":
                c2, s2 = t2.get(r["event_slug"], ("unknown", "unknown"))
                if c2 != "unknown":
                    r["category"], r["category_source"] = c2, s2

    ingest.apply_event_group_quarantine(records)

    total = len(records)
    clause_a = sum(1 for r in records if clause_of(r["total_bought"], r["realized_pnl"]) == "a:loss-exceeds-cost")
    clause_b = sum(1 for r in records if clause_of(r["total_bought"], r["realized_pnl"]) == "b:zero-cost-nonzero-realized")
    row_inv = sum(1 for r in records if r["suspect_reason"] == "row_invariant")
    grp = sum(1 for r in records if r["suspect_reason"] == "event_group")
    susp = sum(1 for r in records if r["pnl_suspect"])
    cats = Counter(r["category"] for r in records)
    fed = [r for r in records if r["category"] == "fed"]
    fed_susp = [r for r in fed if r["pnl_suspect"]]
    fed_tb0 = sum(1 for r in fed if (r["total_bought"] or 0) <= 0)
    fed_score = sum(r["realized_pnl"] for r in fed if not r["pnl_suspect"])
    fed_excl = sum(r["realized_pnl"] for r in fed if r["pnl_suspect"])

    print("==================================================================")
    print("%s  %s" % (name, w))
    print("total closed positions           : %d" % total)
    print("category breakdown (tier-1)      : %s" % dict(cats))
    susp_by_cat = Counter(r["category"] for r in records if r["pnl_suspect"])
    print("per-category [total / suspect]   :")
    for c in sorted(cats, key=lambda k: -cats[k]):
        print("    %-10s %6d / %d" % (c, cats[c], susp_by_cat.get(c, 0)))
    print("ROW clause (a) loss>cost         : %d" % clause_a)
    print("ROW clause (b) zero-cost-nonzero : %d   <- the negRisk 'mirror leg' signature" % clause_b)
    print("SUSPECT total (post event-group) : %d  [row_invariant=%d, event_group=%d]" % (susp, row_inv, grp))
    print("FED rows                         : %d" % len(fed))
    print("FED suspect (quarantined)        : %d" % len(fed_susp))
    print("FED rows with total_bought<=0    : %d" % fed_tb0)
    print("FED scoreable realized sum       : %.2f" % fed_score)
    print("FED excluded  realized sum       : %.2f" % fed_excl)
    allsusp = [r for r in records if r["pnl_suspect"]]
    print("--- ALL suspect rows (cap 60 of %d) ---" % len(allsusp))
    for r in allsusp[:60]:
        print("  [%-28s] cat=%-8s tb=%10.2f rp=%12.2f reason=%-12s %s" % (
            (clause_of(r["total_bought"], r["realized_pnl"]) or "(propagated)"),
            r["category"], r["total_bought"] or 0.0, r["realized_pnl"] or 0.0,
            r["suspect_reason"], (r["event_slug"] or "")[:44]))
    print("--- sample FED rows (first 14: realized vs total_bought) ---")
    for r in fed[:14]:
        print("  tb=%10.2f rp=%12.2f won=%d %s" % (
            r["total_bought"] or 0.0, r["realized_pnl"] or 0.0, r["won"], (r["event_slug"] or "")[:44]))
print("PROBE DONE")
