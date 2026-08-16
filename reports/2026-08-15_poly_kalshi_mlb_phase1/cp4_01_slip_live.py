#!/usr/bin/env python3
"""CP4 [G-slip] live-book demo — fetch REAL open KXMLBGAME books and run them
through the slippage guard. READ-ONLY, dry-run, no orders. Shows: (a) the live
book fetch works, (b) the guard PASSES a tight book and BLOCKS a wide one, using
the SAME real yes_ask and varying only the whale's base price.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.agents.strategies.poly_kalshi_executor import (  # noqa: E402
    PolyKalshiExecutor, translate_whale_action,
)


async def main() -> int:
    from pykalshi import MarketStatus
    s = load_secrets(ENV_FILE)
    b = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await b.connect()
    ms = await b._client.get_markets(series_ticker="KXMLBGAME", status=MarketStatus.OPEN, limit=1000)
    # keep only tickers with a real two-sided book, YES side of a real game (endswith a team code)
    books = []
    for m in ms:
        ya = getattr(m, "yes_ask_dollars", None); yb = getattr(m, "yes_bid_dollars", None)
        t = getattr(m, "ticker", "") or ""
        if ya is not None and 0.0 < float(ya) < 1.0:   # ask-only book is enough for a BUY
            books.append((t, float(ya), float(yb or 0.0)))
    print(f"(open markets scanned={len(ms)}, with a usable ask={len(books)})")
    books = books[:3]
    ex = PolyKalshiExecutor(dry_run=True, db_url="sqlite:///data/trading_corp.db",
                            strategy="poly_kalshi_mlb_slipdemo", max_slippage_cents=2)

    print("=== LIVE Kalshi books fetched (yes_ask_dollars / yes_bid_dollars) ===")
    for t, ya, yb in books:
        print(f"  {t}  yes_ask={ya:.2f} yes_bid={yb:.2f}")

    print("\n=== [G-slip] against the REAL book: tight base passes, wide base blocks ===")
    for t, ya, yb in books:
        quote = {"yes_ask": ya, "yes_bid": yb}
        tight_base = round(ya - 0.01, 2)     # whale price 1c under ask -> slip 1c < 2c cap
        wide_base = round(ya - 0.10, 2)      # whale price 10c under ask -> slip 10c > 2c cap
        o_ok = translate_whale_action(whale="w", kalshi_ticker=t, confidence=1.0, whale_side="BUY",
                                      base_price=max(0.02, tight_base), stake_usd=2.0)
        o_wide = translate_whale_action(whale="w2", kalshi_ticker=t, confidence=1.0, whale_side="BUY",
                                        base_price=max(0.02, wide_base), stake_usd=2.0)
        r_ok = await ex.submit(o_ok, market_quote=quote)
        r_wide = await ex.submit(o_wide, market_quote=quote)
        print(f"  {t}")
        print(f"     base={o_ok.base_price:.2f} vs ask={ya:.2f} (slip {ya-o_ok.base_price:+.2f}) -> {r_ok['status']}")
        print(f"     base={o_wide.base_price:.2f} vs ask={ya:.2f} (slip {ya-o_wide.base_price:+.2f}) -> {r_wide['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
