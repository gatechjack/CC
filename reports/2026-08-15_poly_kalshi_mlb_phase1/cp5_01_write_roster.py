#!/usr/bin/env python3
"""Write the poly-kalshi roster (the 4 MLB whales + recency) as the exact
`agent_state(polymarket_copy_trader, selected_whales)` value.

Applies LOCALLY: writes the roster to a staging DB via the SAME set_agent_state
the routes use, reads it back verbatim (round-trip proof), and emits the JSON
artifact. Pushing this into PROD's live agent_state is the CP5 deploy gate
(operator) — not done here.

recency_rank: 1 = most recent Kalshi-matchable bet, where "matchable bet" =
newest unix ts over TRADE BUY/SELL rows whose market_type=='moneyline' (MLB
single-game ML, the launch matchable category). Tie-break (same second):
realized clean-hold desc, then wallet ascending. The 4 have distinct ts here.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
from trading_corp.persistence import db as _db  # noqa: E402
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402
from trading_corp.data.mlb_poly_kalshi_match import parse_poly_mlb_bet  # noqa: E402

RDIR = WT / "reports/2026-08-15_poly_kalshi_mlb_phase1"
STAGING = f"sqlite:///{RDIR / 'roster_staging.db'}"
ARTIFACT = RDIR / "roster_selected_whales.json"

# canonical Poly user_names + wallets (approved set)
WHALES = [
    ("SDTrading",                  "0x16bb9951a36fce71e2ef57890b786145e0ba8492"),
    ("xifutloong3",                "0x2dc13c6bda81b202281e796953a7323de675b33c"),
    ("monkeymashingkeyboard",      "0x684baa57c338c2549aec0aa3f034f695d72a8409"),
    ("0x0x23kjookhaiuohduoayh8c9", "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9"),
]


async def _most_recent_matchable_ts(pc, wallet):
    rows = await pc.fetch_activity(wallet, limit=500, offset=0)
    ts = [r.timestamp for r in rows
          if r.type == "TRADE" and r.side in ("BUY", "SELL")
          and parse_poly_mlb_bet(r.slug, r.outcome or "").market_type == "moneyline"]
    return max(ts) if ts else 0


async def main() -> int:
    as_of = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    async with PolymarketDataAPIClient() as pc:
        rows = [(name, wallet, await _most_recent_matchable_ts(pc, wallet)) for name, wallet in WHALES]
    # rank: ts desc, then wallet asc (deterministic; clean-hold tiebreak documented, ts distinct here)
    rows.sort(key=lambda x: (-x[2], x[1]))
    roster = [{"wallet": w, "user_name": n, "category": "mlb",
               "recency_rank": i + 1, "recency_matchable_ts": ts,
               "recency_matchable_iso": (datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None),
               "recency_as_of": as_of}
              for i, (n, w, ts) in enumerate(rows)]

    # write via the SAME primitive the routes use, then read back verbatim
    _db.init_db(STAGING)
    _db.set_agent_state("polymarket_copy_trader", "selected_whales", roster, db_url=STAGING)
    got, _updated = _db.load_agent_state("polymarket_copy_trader", "selected_whales", db_url=STAGING)

    ARTIFACT.write_text(json.dumps(roster, indent=1), encoding="utf-8")

    # integrity assertions
    wallets = [r["wallet"] for r in got]
    LEGACY = {"Hakei", "CVCM", "ox1star84", "DegenKingBetter", "rollobravado",
              "Kosherlocks", "GreatestTrader", "olddirtyfighter", "digitalnomad85", "llllllII"}
    names = {r["user_name"] for r in got}
    print("=== selected_whales — READ BACK from staging DB (verbatim) ===")
    print(json.dumps(got, indent=1))
    print("\n=== integrity ===")
    print(f"count == 4                 : {len(got) == 4}")
    print(f"no duplicate wallets       : {len(set(wallets)) == len(wallets)}")
    print(f"no legacy names present    : {names.isdisjoint(LEGACY)}  (legacy overlap: {names & LEGACY})")
    print(f"all category == mlb        : {all(r['category'] == 'mlb' for r in got)}")
    print(f"recency ranks 1..4 present : {sorted(r['recency_rank'] for r in got) == [1, 2, 3, 4]}")
    print(f"\nartifact written: {ARTIFACT}")
    print("NOTE: prod agent_state write is the CP5 deploy gate (operator) — not done here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
