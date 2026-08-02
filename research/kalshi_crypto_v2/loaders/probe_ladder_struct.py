"""Probe hourly-ladder event structure to size the snapshot pull honestly
(READ-ONLY, no DB): does /events?series_ticker work, how many strikes per event,
and can we get an open-time price without a per-strike candle call?

Usage: python research/kalshi_crypto_v2/loaders/probe_ladder_struct.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")


def main() -> int:
    rest = KalshiRest()
    # events for a ladder series
    ev = rest.get("/events", {"series_ticker": "KXBTC", "status": "settled", "limit": 5})
    events = ev.get("events", []) or []
    print(f"/events KXBTC settled: got {len(events)} (has cursor={bool(ev.get('cursor'))})")
    for e in events[:3]:
        print(f"  event={e.get('event_ticker')} title={str(e.get('title'))[:40]!r}")
    if not events:
        print("no events; fallback to deriving events from /markets event_ticker")
        return 0
    etk = events[0]["event_ticker"]
    mk = rest.get("/markets", {"event_ticker": etk, "limit": 1000})
    markets = mk.get("markets", []) or []
    print(f"\n/markets event_ticker={etk}: {len(markets)} strike markets")
    for m in markets[:3]:
        print(f"  ticker={m.get('ticker')} floor={m.get('floor_strike')} "
              f"cap={m.get('cap_strike')} open={m.get('open_time')} "
              f"result={m.get('result')} yes_bid={m.get('yes_bid')} last={m.get('last_price')}")
    print("\nfull first market keys:", sorted(markets[0].keys()) if markets else "none")
    print("\n=> open-time price source: metadata carries only current/settlement "
          "price; an open-window value needs 1 candlesticks call PER strike market.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KalshiAuthError as e:
        print(f"STOP creds: {e}")
        raise SystemExit(2)
