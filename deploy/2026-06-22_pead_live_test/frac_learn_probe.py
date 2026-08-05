"""LEARNING PROBE (operator-authorized, real ~$5 F, market closed): place ONE
fractional notional buy through OUR fractional broker path
(_place_fractional_stock_order / _poll_fractional_fill) to capture what a QUEUED
pre-open fractional order looks like + confirm our timeout->cancel + no-phantom-write.

We don't care if it fills (it won't — market closed → it queues → our 90s poll times
out → our code cancels). The verbatim queued response is the artifact the
deferred-fill-reconcile build needs. robin_stocks fns are WRAPPED (call-through) so
OUR broker still drives — we only observe the raw responses.
"""
import asyncio
import json
import sqlite3

import robin_stocks.robinhood as rs

from trading_corp.brokers.robinhood import RobinhoodBroker, RobinhoodOrderError
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.secrets import load_secrets

ACCT = "680725082"
DB = "/home/azureuser/trading_corp/data/trading_corp.db"
cap = {"place_args": None, "place": None, "polls": [], "cancels": []}


def _wrap():
    _place = rs.orders.order_buy_fractional_by_price
    def wplace(symbol, amountInDollars, **k):
        r = _place(symbol, amountInDollars, **k)
        cap["place_args"] = dict(symbol=symbol, amountInDollars=amountInDollars, **k)
        cap["place"] = r
        return r
    rs.orders.order_buy_fractional_by_price = wplace

    _info = rs.orders.get_stock_order_info
    def winfo(oid):
        r = _info(oid)
        cap["polls"].append(r)
        return r
    rs.orders.get_stock_order_info = winfo

    _cancel = rs.orders.cancel_stock_order
    def wcancel(oid):
        r = _cancel(oid)
        cap["cancels"].append({"oid": oid, "resp": r})
        return r
    rs.orders.cancel_stock_order = wcancel
    return _info  # the un-wrapped info, for the final read


def _pead_null():
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM paper_trade_record "
                  "WHERE division='robinhood_pead' AND result IS NULL").fetchone()[0]
    c.close()
    return n


async def main():
    raw_info = _wrap()
    secrets = load_secrets()
    broker = RobinhoodBroker(username=secrets.robinhood_username,
                             password=secrets.robinhood_password,
                             mfa_secret=secrets.robinhood_mfa_secret, account_filter=ACCT)
    await broker.connect()
    bound = getattr(broker, "_account_number", "")
    print("broker bound account:", bound)
    if bound != ACCT:
        print("ABORT: bind != 680725082"); return

    before = _pead_null()
    print("pead result-NULL rows BEFORE:", before)

    order = ProposedOrder(strategy="robinhood_pead", symbol="F", side="buy", qty=0.0,
                          order_type="market", notional_usd=5.0, fractional=True, extra={})
    print("placing $5 F via broker._place_fractional_stock_order (OUR code; ~90s poll "
          "expected on a closed-market queue)...")
    err = None
    try:
        fill = await broker._place_fractional_stock_order(order)
        print("UNEXPECTED fill (did it actually fill?):", fill)
    except RobinhoodOrderError as e:
        err = str(e)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    after = _pead_null()
    oid = (cap["place"] or {}).get("id") if isinstance(cap["place"], dict) else None
    final = None
    if oid:
        try:
            final = raw_info(oid)
        except Exception as e:  # noqa: BLE001
            final = f"final-read-err {e}"

    print("\n========== CAPTURE ==========")
    print("PLACE ARGS:", json.dumps(cap["place_args"], default=str))
    print("\n--- 1. PLACEMENT RESPONSE (the QUEUED pre-open fractional order, verbatim) ---")
    print(json.dumps(cap["place"], default=str, indent=2))
    print("\n--- poll behavior (_poll_fractional_fill) ---")
    print("poll count:", len(cap["polls"]))
    if cap["polls"]:
        print("FIRST poll:", json.dumps(cap["polls"][0], default=str))
        print("LAST  poll:", json.dumps(cap["polls"][-1], default=str))
    print("\n--- cancels our code issued (cancel-on-timeout) ---")
    print(json.dumps(cap["cancels"], default=str))
    print("\n--- FINAL order state ---")
    print(json.dumps(final, default=str, indent=2) if isinstance(final, dict) else final)
    print("\n--- our broker's raised result ---")
    print("RAISED:", err)
    print("\n--- DB no-phantom-write check ---")
    print(f"pead result-NULL BEFORE={before} AFTER={after} -> no phantom: {before == after}")
    # belt-and-suspenders: if our code didn't cancel it, do it now (GT_Jack backup else)
    fstate = (final or {}).get("state", "").lower() if isinstance(final, dict) else ""
    if oid and fstate not in ("cancelled", "canceled", "rejected", "failed", "filled"):
        print(f"\nWARN: order {oid} state={fstate!r} not terminal — issuing explicit cancel")
        try:
            print("explicit cancel resp:", rs.orders.cancel_stock_order(oid))
        except Exception as e:  # noqa: BLE001
            print(f"explicit cancel FAILED: {e} — GT_Jack should UI-cancel {oid}")


asyncio.run(main())
