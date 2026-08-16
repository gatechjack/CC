#!/usr/bin/env python3
"""n=1 fill verification — fetch each whale's REAL Polymarket MLB bet (read-only)
and confirm it matches the club the Kalshi order bet (no home/away inversion)."""
import asyncio, sys
from pathlib import Path
WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
from trading_corp.data.mlb_poly_kalshi_match import parse_poly_mlb_bet

# (whale, wallet, expected club the order bet, kalshi ticker)
CASES = [
    ("SDTrading",   "0x16bb9951a36fce71e2ef57890b786145e0ba8492", "Miami Marlins",         "KXMLBGAME-26AUG161340MIACIN-MIA"),
    ("0x0x23kj...", "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9", "Cincinnati Reds",       "KXMLBGAME-26AUG161340MIACIN-CIN"),
    ("xifutloong3", "0x2dc13c6bda81b202281e796953a7323de675b33c", "Arizona Diamondbacks",  "KXMLBGAME-26AUG161335AZATL-AZ"),
]


async def main():
    async with PolymarketDataAPIClient() as client:
        for name, wallet, expect_club, tkr in CASES:
            print("=== %s  %s ===" % (name, wallet))
            print("    order bet: YES on %s   (ticker %s)" % (expect_club, tkr))
            try:
                rows = await client.fetch_activity(wallet, limit=300, offset=0)
            except Exception as e:  # noqa: BLE001
                print("    POLY FETCH FAILED:", type(e).__name__, e); continue
            hits = 0
            for r in rows:
                slug = getattr(r, "slug", "") or ""
                if "mlb-" not in slug.lower():
                    continue
                p = parse_poly_mlb_bet(slug, getattr(r, "outcome", "") or "",
                                       getattr(r, "title", "") or "", getattr(r, "event_slug", "") or "")
                if p.date_iso != "2026-08-16" or p.market_type != "moneyline":
                    continue
                hits += 1
                match = "OK" if (p.side_name == expect_club) else "*** MISMATCH ***"
                print("    poly: slug=%s outcome=%s side=%s price=%s ts=%s -> bet_club=%s  [%s]" % (
                    slug, getattr(r, "outcome", None), getattr(r, "side", None),
                    getattr(r, "price", None), getattr(r, "timestamp", None), p.side_name, match))
            if hits == 0:
                print("    (no 2026-08-16 MLB moneyline trade found in newest 300 activity rows)")


if __name__ == "__main__":
    asyncio.run(main())
