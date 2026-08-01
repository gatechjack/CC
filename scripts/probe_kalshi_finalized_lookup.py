"""Read-only probe: for the confirmed-finalized-but-unbooked kalshi_llm
markets, what does broker.get_market_resolution return for the MARKET
ticker vs the EVENT ticker? Also dump the raw pykalshi MarketModel so we
can see whether `.result` is populated. Resolves whether the gap is an
event-ticker mismatch (event lookup lands, market doesn't) or a
finalized-not-settled empty-`result` issue (market found, result empty).

GETs only. No orders, no writes. Run:
  KEY_VAULT_URI=<uri> /home/azureuser/trading_corp/venv/bin/python -
(piped via stdin) or as a file with the same env.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/home/azureuser/trading_corp")

from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.utils.secrets import load_secrets

# (label, market_ticker, event_ticker) — all confirmed status=finalized on
# the public API (SARB=no, BoK=yes, CPI=no).
CASES = [
    ("SARB (pub finalized=no)",  "KXCBDSA-26JUL23-H25",           "KXCBDSA-26JUL23"),
    ("BoK  (pub finalized=yes)", "KXCBDECISIONKOREA-26JUL15-H25", "KXCBDECISIONKOREA-26JUL15"),
    ("CPI  (pub finalized=no)",  "KXCPICOMBO-26JUN-0202",         "KXCPICOMBO-26JUN"),
]


async def main() -> None:
    secrets = load_secrets()
    broker = KalshiBroker(
        api_key_id=secrets.kalshi_api_key_id,
        private_key_pem=secrets.kalshi_private_key_pem,
        demo=False,
    )
    await broker.connect()
    print(f"broker: stub={broker._stub} connected={broker._connected}")
    for label, mkt, evt in CASES:
        print(f"\n=== {label} ===")
        rm = await broker.get_market_resolution(mkt)
        print(f"  get_market_resolution(MARKET {mkt}): status={rm.get('status')} result={rm.get('result')!r}")
        re_ = await broker.get_market_resolution(evt)
        print(f"  get_market_resolution(EVENT  {evt}): status={re_.get('status')} result={re_.get('result')!r}")
        try:
            m = await broker._client.get_market(mkt)
            print(f"  raw pykalshi get_market(MARKET): result={getattr(m,'result','<none>')!r} "
                  f"status={getattr(m,'status','<none>')!r} close_time={getattr(m,'close_time','<none>')!r} "
                  f"settlement_ts={getattr(m,'settlement_timestamp','<none>')!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  raw pykalshi get_market(MARKET) ERR: {type(e).__name__}: {e}")
    try:
        await broker.close()
    except Exception:  # noqa: BLE001
        pass


asyncio.run(main())
