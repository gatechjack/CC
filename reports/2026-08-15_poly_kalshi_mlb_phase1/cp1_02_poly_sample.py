#!/usr/bin/env python3
"""CP1 step 2 — study REAL Poly MLB market shapes on a whale's activity. READ-ONLY.

Pulls SDTrading's recent activity and buckets rows by slug/title shape so the
parser + market-type gate are built from the real conventions, not guesses.
NO orders. Public Poly API only.
"""
from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402

SDTRADING = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"


def _shape(slug: str) -> str:
    # classify slug suffix after mlb-{a}-{b}-{date}
    m = re.match(r"^mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}(?P<suf>.*)$", slug or "")
    if m:
        suf = m.group("suf")
        if suf == "":
            return "MONEYLINE(mlb-a-b-date)"
        return f"SUFFIX:{suf}"
    if (slug or "").startswith("mlb-"):
        return "OTHER-mlb-slug"
    return "NON-mlb-slug"


async def main() -> int:
    async with PolymarketDataAPIClient() as client:
        rows = await client.fetch_activity(SDTRADING, limit=300, offset=0)
    print(f"fetched {len(rows)} activity rows for SDTrading")
    shapes = Counter()
    mlb_rows = []
    for r in rows:
        sh = _shape(r.slug)
        shapes[sh] += 1
        if (r.slug or "").startswith("mlb-"):
            mlb_rows.append(r)
    print("\n=== slug shapes (all rows) ===")
    for sh, n in shapes.most_common():
        print(f"  {n:>4}  {sh}")

    print("\n=== sample MLB rows (title | slug | outcome | side) — first 22 distinct slugs ===")
    seen = set()
    for r in mlb_rows:
        if r.slug in seen:
            continue
        seen.add(r.slug)
        print(f"  [{r.side:4}] outcome={r.outcome!r:28} | {r.title[:52]!r}")
        print(f"          slug={r.slug!r}  event={r.event_slug!r}")
        if len(seen) >= 22:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
