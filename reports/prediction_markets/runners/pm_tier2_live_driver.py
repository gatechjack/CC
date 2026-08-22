#!/usr/bin/env python3
# pm_tier2_live_driver.py -- READ-ONLY: run the ACTUAL tier-2 (category.derive_categories_batch,
# live gamma /events default fetch) against real tier-1-UNKNOWN eventSlugs collected from wallets.
# Answers Task 1(e): does tier-2 resolve the unknown tail, and to what? Also a live tail-resolution
# rate (beyond the offline fixtures). No writes. Uses the committed category.py.
import asyncio
import json
import urllib.request

from trading_corp.prediction_markets import category

DATA = "https://data-api.polymarket.com"


def http(u):
    r = urllib.request.Request(u, headers={"User-Agent": "tier2-live/1.0"})
    with urllib.request.urlopen(r, timeout=30) as x:
        return json.loads(x.read().decode())


WALLETS = [
    ("evanng", "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618"),
    ("pako", "0x71edffd0d70a1da823ff07a3c6fc81457294d338"),
    ("d1k21", "0x71ed0bc95433cdf1be29f43219725fce9addd9eb"),
]

unk = set()
for nm, w in WALLETS:
    for off in range(0, 500, 50):
        try:
            rows = http("%s/closed-positions?user=%s&limit=50&offset=%d" % (DATA, w, off))
        except Exception as e:
            print("pull err", str(e)[:60])
            break
        if not rows:
            break
        for r in rows:
            es = r.get("eventSlug") or ""
            c, _ = category.derive_category_from_slug(es, r.get("slug"))
            if c == category.CATEGORY_UNKNOWN and es:
                unk.add(es)
        if len(rows) < 50:
            break

named = ["2026-nba-champion"]  # the NBA-futures tail Jack named (knicks row's eventSlug)
targets = named + [e for e in sorted(unk) if e not in named]
print("tier-1 UNKNOWN distinct eventSlugs collected: %d" % len(targets))

res = asyncio.run(category.derive_categories_batch(targets))
resolved = sum(1 for v in res.values() if v[0] != category.CATEGORY_UNKNOWN)
print("tier-2 RESOLVED %d / %d (%.0f%%)" % (resolved, len(targets), 100.0 * resolved / max(1, len(targets))))
print("--- per-slug tier-2 (category, category_source) ---")
for es in targets[:45]:
    print("  %-52s -> %s" % (es[:52], res.get(es)))
print("DRIVER DONE")
