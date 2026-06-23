"""PEAD STEP 3 — GATE 2 raw POST capture. Captures RH's ACTUAL order-POST
response (HTTP status + error body) OUTSIDE the broker, so the broker's
fake-fill masking never touches it.

This DOES send one POST (the data we've been missing) — but it is a NON-MARKETABLE
buy limit (~50% below market, cannot fill; the account is clean), and if RH
unexpectedly creates an order it is cancelled by id IMMEDIATELY in a finally
block. The wrapper calls the real request_post with jsonify_data=False to read
res.status_code + res.json(), then returns the body to order() unchanged.

Safety aborts before the POST if bind != 680725082 or the limit isn't provably
non-marketable.
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
    bound = getattr(broker, "_account_number", "")
    print(f"broker bound account = {bound!r}")
    if bound != ACCOUNT:
        print(f"ABORT: bind {bound!r} != {ACCOUNT!r} — no POST.")
        return 3
    last = float(await broker.quote(SYM))
    limit = round(last * 0.5, 2)
    if not (0.0 < limit <= 0.70 * last):
        print(f"ABORT: limit ${limit} not provably non-marketable vs ${last} — no POST.")
        return 4
    print(f"{SYM} last=${last:.2f}  non-marketable buy limit=${limit:.2f}\n")

    cap: dict = {}
    _real_post = rs.orders.request_post

    def _capture(url, payload=None, *a, **k):
        cap["url"] = url
        cap["payload"] = payload
        res = _real_post(url, payload, jsonify_data=False)  # get the response object
        cap["status"] = getattr(res, "status_code", None)
        try:
            cap["body"] = res.json()
        except Exception:  # noqa: BLE001
            cap["body"] = getattr(res, "text", None)
        return cap["body"]  # hand order() exactly what it expects

    rs.orders.request_post = _capture
    created_id = None
    try:
        print("POSTING (outside the broker) the exact non-marketable limit, capturing the response...")
        rs.orders.order_buy_limit(SYM, 1, limit, account_number=ACCOUNT)
        body = cap.get("body")
        if isinstance(body, dict):
            created_id = body.get("id")
    finally:
        rs.orders.request_post = _real_post
        if created_id:
            try:
                rs.orders.cancel_stock_order(created_id)
                print(f"NOTE: RH CREATED order {created_id} -> CANCELLED immediately.")
            except Exception as e:  # noqa: BLE001
                print(f"!! order {created_id} created but CANCEL FAILED: {e} — CANCEL MANUALLY on 680725082")

    print("\n=== RAW POST RESULT (outside the broker — trustworthy) ===")
    print(f"HTTP status : {cap.get('status')}")
    print(f"response body:\n{json.dumps(cap.get('body'), indent=2, default=str)[:1800]}")
    print(f"\norder created? {bool(created_id)}  (cancelled above if so; account otherwise clean)")
    print("=== END POST CAPTURE ===")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
