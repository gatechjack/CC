"""PEAD STEP 3 — GATE 2 PAYLOAD-FIRST inspection. READ-ONLY. NO POST.

Builds the EXACT payload robin_stocks.orders.order() assembles for our
non-marketable buy limit, by intercepting `request_post` (capture url+payload,
return None — the network POST is NEVER sent). Reveals any malformed/suspect
field without placing anything.

Why this matters (offline robin_stocks 3.4.0 source, order(), lines 87-90):
for a regular-hours BUY it INJECTS `preset_percent_limit="0.05"` (a 5% collar)
and forces `type='limit'`. Our limit is ~50% below market — that violates the
5% collar and is the leading reason RH 400-rejected the POST. ask_price/bid_price
also come from get_latest_price and may be 0.00 with the market closed.
"""
from __future__ import annotations

import asyncio
import json

ACCOUNT = "680725082"
SYM = "F"


async def _amain() -> int:
    import robin_stocks.robinhood as rs

    from trading_corp.brokers.robinhood import RobinhoodBroker
    from trading_corp.utils.secrets import load_secrets

    secrets = load_secrets()
    broker = RobinhoodBroker(
        username=secrets.robinhood_username,
        password=secrets.robinhood_password,
        mfa_secret=secrets.robinhood_mfa_secret,
        account_filter=ACCOUNT,
    )
    await broker.connect()
    print(f"broker bound account = {getattr(broker, '_account_number', '')!r}")
    last = float(await broker.quote(SYM))
    limit = round(last * 0.5, 2)
    print(f"{SYM} last=${last:.2f}  non-marketable buy limit=${limit:.2f}\n")

    # ── intercept the POST: capture the payload, send NOTHING ────────────────
    cap: dict = {}
    _real_post = rs.orders.request_post

    def _intercept(url, payload=None, *a, **k):
        cap["url"] = url
        cap["payload"] = payload
        return None  # DO NOT POST

    rs.orders.request_post = _intercept
    try:
        # exactly the broker's call shape: order_buy_limit(symbol, qty, limit, account_number=acct)
        rs.orders.order_buy_limit(SYM, 1, limit, account_number=ACCOUNT)
    finally:
        rs.orders.request_post = _real_post

    pl = cap.get("payload") or {}
    print("=== EXACT order() POST (intercepted — NOT sent) ===")
    print(f"POST url: {cap.get('url')}")
    print(json.dumps(pl, indent=2, default=str))
    print("\n--- suspects ---")
    print(f"  preset_percent_limit = {pl.get('preset_percent_limit')!r}  "
          f"(robin_stocks injects '0.05' = 5% collar for regular-hours buys)")
    print(f"  price={pl.get('price')!r}  ask_price={pl.get('ask_price')!r}  bid_price={pl.get('bid_price')!r}")
    print(f"  type={pl.get('type')!r}  time_in_force={pl.get('time_in_force')!r}  trigger={pl.get('trigger')!r}")
    print(f"  account={pl.get('account')!r}")
    print(f"  quantity={pl.get('quantity')!r}  market_hours={pl.get('market_hours')!r}")
    print("\n(no POST made — request_post intercepted; nothing placed)")
    print("=== END PAYLOAD INSPECT ===")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
