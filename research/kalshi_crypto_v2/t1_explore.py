"""T1 exploration: list live Kalshi Crypto SERIES and their taxonomy so we can
classify 15-min up/down vs hourly strike-ladder for BTC/ETH/SOL/XRP without
hardcoding rotating tickers. READ-ONLY (signed GET, in-memory creds).
Usage: run_capped python research/kalshi_crypto_v2/t1_explore.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

ASSET_HINTS = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "XRP": "XRP"}


def main() -> int:
    try:
        rest = KalshiRest()
    except KalshiAuthError as e:
        print(f"STOP - creds: {e}")
        return 2
    print(f"creds source={rest.source}\n")

    series = rest.paginated("/series", "series", {"category": "Crypto", "limit": 200})
    print(f"Crypto series returned: {len(series)}\n")
    if series:
        print("=== fields on a sample series ===")
        for k, v in sorted(series[0].items()):
            print(f"  {k}: {str(v)[:100]}")
        print()

    print("=== all Crypto series (ticker | frequency | title) ===")
    for s in sorted(series, key=lambda x: x.get("ticker", "")):
        tk = s.get("ticker", "?")
        freq = s.get("frequency", "?")
        title = (s.get("title", "") or "")[:70]
        asset = next((a for a, h in ASSET_HINTS.items() if h in tk.upper() or h in title.upper()), "-")
        print(f"  {tk:22} {freq:12} [{asset}] {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
