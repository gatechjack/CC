"""T1 recon: shapes of market + candlestick responses for KXBTC15M so the full
census uses correct field names. READ-ONLY signed GET, in-memory creds."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kalshi_auth import KalshiRest  # noqa: E402

S = "KXBTC15M"


def dump(label, obj, n=1):
    print(f"--- {label} ---")
    print(json.dumps(obj, indent=2)[:1400] if not isinstance(obj, list)
          else json.dumps(obj[:n], indent=2)[:1400])
    print()


def main() -> int:
    rest = KalshiRest()
    print(f"creds source={rest.source}\n")

    op = rest.get("/markets", {"series_ticker": S, "status": "open", "limit": 5})
    print(f"OPEN markets returned: {len(op.get('markets', []))}")
    if op.get("markets"):
        print("open market keys:", sorted(op["markets"][0].keys()))
        dump("sample OPEN market", op["markets"][0])

    st = rest.get("/markets", {"series_ticker": S, "status": "settled", "limit": 5})
    ms = st.get("markets", [])
    print(f"SETTLED markets (1 page of 5) returned: {len(ms)}  cursor={bool(st.get('cursor'))}")
    if ms:
        print("settled market keys:", sorted(ms[0].keys()))
        dump("sample SETTLED market", ms[0])
        tkr = ms[0]["ticker"]
        # candlesticks for that settled market — try 1-minute
        import time as _t
        close_ts = ms[0].get("close_time")
        # request a wide window; use market's open/close if present as epoch secs
        try:
            cs = rest.get(f"/series/{S}/markets/{tkr}/candlesticks",
                          {"period_interval": 1,
                           "start_ts": (ms[0].get("open_ts") or 0),
                           "end_ts": (ms[0].get("close_ts") or int(_t.time()))})
            arr = cs.get("candlesticks", [])
            print(f"candlesticks(1m) for {tkr}: {len(arr)}")
            if arr:
                print("candle keys:", sorted(arr[0].keys()))
                dump("sample candle", arr[-1])
        except Exception as e:
            print(f"candlestick fetch note: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
