#!/usr/bin/env python3
"""CP1 step 5 — how does POLYMARKET name doubleheader games? READ-ONLY.

Kalshi DH convention is now known (G1/G2 blob suffix + distinct HHMM). This
probes the Poly side for the 3 real doubleheaders in the window, via the public
gamma events API, to see whether Poly deterministically distinguishes game 1 vs
game 2 (slug suffix? separate event? game number in title?). NO orders.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"

# (base event slug, human) for the 3 real doubleheaders found in Kalshi.
DH = [
    "mlb-stl-cin-2026-08-17",
    "mlb-mil-stl-2026-07-07",
    "mlb-tb-bos-2026-07-17",
]
# candidate game-2 slug patterns to probe
SUFFIXES = ["", "-game-2", "-g2", "-2", "-gm2", "-doubleheader-game-2"]


async def _get(client, path, params):
    try:
        r = await client.get(f"{path}", params=params, timeout=20)
        if r.status_code != 200:
            return {"_status": r.status_code, "_body": r.text[:200]}
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


async def main() -> int:
    async with httpx.AsyncClient(headers={"User-Agent": "cp1-probe"}) as c:
        for base in DH:
            print("\n" + "=" * 74)
            print(f"DOUBLEHEADER matchup: {base}")
            # 1) events by slug candidates
            for suf in SUFFIXES:
                slug = base + suf
                ev = await _get(c, f"{GAMMA}/events", {"slug": slug})
                if isinstance(ev, list) and ev:
                    for e in ev:
                        mkts = e.get("markets", []) or []
                        print(f"  EVENT slug={slug!r} title={e.get('title')!r} markets={len(mkts)}")
                        for m in mkts[:6]:
                            print(f"      market slug={m.get('slug')!r} q={m.get('question')!r}")
                elif isinstance(ev, dict) and ev.get("_status"):
                    pass  # not found / non-200, skip quietly
            # 2) markets endpoint directly for the base + game-2 candidates (moneyline slug)
            for suf in SUFFIXES:
                slug = base + suf
                mk = await _get(c, f"{GAMMA}/markets", {"slug": slug})
                if isinstance(mk, list) and mk:
                    for m in mk:
                        print(f"  MARKET slug={m.get('slug')!r} q={m.get('question')!r} "
                              f"gameStartTime={m.get('gameStartTime')!r} eventSlug={(m.get('events') or [{}])[0].get('slug') if m.get('events') else '?'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
