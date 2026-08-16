#!/usr/bin/env python3
"""Wallet-keyed idempotency proof — the CP4 dedup scenario re-run under WALLET
keying. Shows: (1) same wallet + different display name -> SAME key -> 2nd
suppressed; (2) one whale action -> <=1 order. Pure, dry-run, no orders."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
from trading_corp.agents.strategies.poly_kalshi_executor import (  # noqa: E402
    PolyKalshiExecutor, translate_whale_action,
)

# the real CP4 case: xifutloong3 BUY Miami Marlins, entry, filled twice.
WALLET = "0x2dc13c6bda81b202281e796953a7323de675b33c"
TICKER = "KXMLBGAME-26AUG151840MIACIN-MIA"


def _order(display):
    return translate_whale_action(whale=display, whale_wallet=WALLET, kalshi_ticker=TICKER,
                                  confidence=1.0, whale_side="BUY", base_price=0.52, stake_usd=5.0)


async def main() -> int:
    # two fills of the SAME action, logged under DIFFERENT display names
    o_short = _order("xifutloong3")            # canonical here, but vary to prove immunity:
    o_trunc = _order("xifu")                   # truncated display label, SAME wallet
    print("idempotency key (name='xifutloong3'):", o_short.idempotency_key)
    print("idempotency key (name='xifu')       :", o_trunc.idempotency_key)
    print("SAME key regardless of display name :", o_short.idempotency_key == o_trunc.idempotency_key)

    ex = PolyKalshiExecutor(dry_run=True, db_url="sqlite:///data/trading_corp.db",
                            strategy="poly_kalshi_mlb_dedupproof")
    r1 = await ex.submit(o_short)              # first fill
    r2 = await ex.submit(o_trunc)              # second fill (same wallet+ticker+action)
    print(f"\nsubmit #1 -> {r1['status']}   deployed_usd={ex._deployed_usd}")
    print(f"submit #2 -> {r2['status']}   deployed_usd={ex._deployed_usd}")
    print(f"orders retained: {len(ex._placed)}  (one whale action -> <=1 order: "
          f"{len(ex._placed) == 1 and r2['status'] == 'suppressed_duplicate'})")

    # a DIFFERENT wallet is a different action -> different key -> not suppressed
    other = translate_whale_action(whale="xifutloong3", whale_wallet="0x0000000000000000000000000000000000000000",
                                   kalshi_ticker=TICKER, confidence=1.0, whale_side="BUY",
                                   base_price=0.52, stake_usd=5.0)
    print(f"\ndifferent wallet -> different key: {other.idempotency_key != o_short.idempotency_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
