"""One-off probe: enumerate live Kalshi team codes per in-scope series
and cross-check against our LEAGUE_TEAMS mapping.

Reports per league:
  (a) all codes seen in current OPEN markets
  (b) codes in our mapping (good)
  (c) codes seen on Kalshi but NOT in our mapping (gaps to fix)
  (d) codes in our mapping NOT seen on Kalshi (informational; unused aliases or out-of-season teams)
"""
import asyncio
import re
import sys
from collections import Counter


SERIES = {
    "MLB": "KXMLBGAME",
    "NBA": "KXNBAGAME",
    "NHL": "KXNHLGAME",
    "MLS": "KXMLSGAME",
}

# Replicates _TICKER_RE / parse_sports_ticker logic minus the LEAGUE_TEAMS lookup
TICKER_RE = re.compile(
    r"^KX(?P<league>[A-Z]+)GAME-"
    r"(?P<date>\d{2}[A-Z]{3}\d{2})"
    r"(?P<time>\d{4})?"
    r"(?P<blob>[A-Z]+)-"
    r"(?P<yes>[A-Z]+)\d*$"
)


def extract_team_codes(ticker: str) -> tuple[str, str] | None:
    """Return (team_a, team_b) Kalshi codes if parseable; None for TIE/DRAW/unparseable."""
    m = TICKER_RE.match(ticker)
    if not m:
        return None
    yes_side = m.group("yes")
    if yes_side in ("TIE", "DRAW"):
        return None
    blob = m.group("blob")
    if blob.startswith(yes_side):
        team_b = blob[len(yes_side):]
    elif blob.endswith(yes_side):
        team_b = blob[:-len(yes_side)]
    else:
        return None
    if not team_b:
        return None
    return (yes_side, team_b)


async def main():
    from pykalshi import MarketStatus
    from trading_corp.utils.secrets import load_secrets
    from trading_corp.brokers.kalshi import KalshiBroker
    from trading_corp.data.sports_team_mapping import LEAGUE_TEAMS

    secrets = load_secrets()
    broker = KalshiBroker(
        api_key_id=secrets.kalshi_api_key_id,
        private_key_pem=secrets.kalshi_private_key_pem,
    )
    await broker.connect()

    try:
        for league, series_ticker in SERIES.items():
            kalshi_codes_seen: Counter[str] = Counter()
            n_markets = 0
            n_tie_skipped = 0
            n_unparseable = 0
            try:
                markets = await broker._client.get_markets(
                    series_ticker=series_ticker,
                    status=MarketStatus.OPEN,
                    limit=500,
                )
            except Exception as e:
                print(f"{league}: ERROR fetching markets: {e}")
                continue

            for m in markets:
                ticker = getattr(m, "ticker", "") or ""
                if not ticker:
                    continue
                n_markets += 1
                pair = extract_team_codes(ticker)
                if pair is None:
                    rgx = TICKER_RE.match(ticker)
                    if rgx and rgx.group("yes") in ("TIE", "DRAW"):
                        n_tie_skipped += 1
                    else:
                        n_unparseable += 1
                    continue
                a, b = pair
                kalshi_codes_seen[a] += 1
                kalshi_codes_seen[b] += 1

            mapping_keys = set(LEAGUE_TEAMS[league].keys())
            kalshi_set = set(kalshi_codes_seen.keys())
            gaps = kalshi_set - mapping_keys
            unused = mapping_keys - kalshi_set

            print(f"\n=== {league} (series {series_ticker}) ===")
            print(f"  markets fetched:    {n_markets}")
            print(f"  TIE skipped:        {n_tie_skipped}")
            print(f"  unparseable:        {n_unparseable}")
            print(f"  unique codes seen:  {len(kalshi_set)}")
            print(f"  in our mapping:     {len(kalshi_set & mapping_keys)}")
            print(f"  GAPS (Kalshi NOT in mapping): {sorted(gaps)}")
            if gaps:
                print(f"    occurrences of each gap:")
                for c in sorted(gaps):
                    print(f"      {c}: appears {kalshi_codes_seen[c]} times")
            print(f"  unused in mapping (informational): {sorted(unused)}")
    finally:
        await broker.disconnect()


asyncio.run(main())
