"""Kalshi market census (READ-ONLY, no DB). Counts settled + open markets per
target series with FULL pagination, and estimates the S3.4 candle-pull scale
(one candlesticks call per market). Sizes the 'big' ladder pull before it runs.

Usage: python research/kalshi_crypto_v2/loaders/probe_kalshi_census.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

# 4x 15m up/down + 4x hourly ladders (KXSOL inactive -> KXSOLE is the SOL ladder)
SERIES = [
    ("KXBTC15M", "BTC", "15m"), ("KXETH15M", "ETH", "15m"),
    ("KXSOL15M", "SOL", "15m"), ("KXXRP15M", "XRP", "15m"),
    ("KXBTC", "BTC", "ladder"), ("KXETH", "ETH", "ladder"),
    ("KXSOLE", "SOL", "ladder"), ("KXXRP", "XRP", "ladder"),
]


def count(rest: KalshiRest, series: str, status: str) -> list[dict]:
    return rest.paginated("/markets", "markets",
                          {"series_ticker": series, "status": status, "limit": 1000},
                          max_pages=200)


def main() -> int:
    try:
        rest = KalshiRest()
    except KalshiAuthError as e:
        print(f"STOP creds: {e}")
        return 2
    print(f"creds={rest.source}\n")
    print(f"{'series':10} {'asset':4} {'kind':7} {'settled':>8} {'open':>6} "
          f"{'earliest':16} {'latest':16}")
    print("-" * 74)
    grand = 0
    for s, asset, kind in SERIES:
        try:
            settled = count(rest, s, "settled")
            op = rest.get("/markets", {"series_ticker": s, "status": "open", "limit": 1000})
            nop = len(op.get("markets", []) or [])
            closes = [m.get("close_time") for m in settled if m.get("close_time")]
            e0 = min(closes)[:16].replace("T", " ") if closes else "-"
            e1 = max(closes)[:16].replace("T", " ") if closes else "-"
            print(f"{s:10} {asset:4} {kind:7} {len(settled):>8} {nop:>6} {e0:16} {e1:16}")
            grand += len(settled)
        except KalshiAuthError as e:
            print(f"{s:10} {asset:4} {kind:7} ERR {str(e)[:50]}")
    # scale estimate: 1 candlesticks call per settled market; ~10 req/s signed
    print(f"\nTOTAL settled markets: {grand}")
    print(f"est. candle calls ~= {grand} ; at 8 req/s ~= {grand/8/60:.1f} min "
          f"(15m markets ~15 candles, ladder ~60 -> under the 5000 cap, 1 call each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
