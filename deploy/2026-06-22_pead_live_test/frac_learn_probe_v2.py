"""LEARNING PROBE v2 — BARE-LOGIN + MANUAL-BIND (operator-authorized, real ~$5 F).

Same goal as frac_learn_probe.py — place ONE fractional notional buy through OUR
fractional broker path (_place_fractional_stock_order / _poll_fractional_fill) to
capture what a QUEUED pre-open fractional order looks like + confirm timeout->cancel
+ no-phantom-write — BUT establishes the session via the PROVEN bare `rs.login()`
(pickle reuse, the path that worked for close_position.py / acct_check today) and
MANUALLY binds the broker (_connected/_account_number). It NEVER calls
broker.connect(), whose credentialed `rs.login(user,pw,mfa,store_session=True)` on an
expired session triggers a device challenge + can corrupt the shared pickle (observed
2026-06-23 → 429 rate-limit).

PRECONDITION: the prod pickle must be valid + the 429 cooled off. A read-only
load_account_profile guard runs FIRST and ABORTS before any order if 680725082 isn't
reachable — so a bad session fails safe (no placement).

robin_stocks order fns are WRAPPED (call-through) so OUR broker still drives; we only
observe the raw responses.
"""
import asyncio
import json
import sqlite3

import robin_stocks.robinhood as rs

from trading_corp.brokers.robinhood import RobinhoodBroker, RobinhoodOrderError
from trading_corp.persistence.models import ProposedOrder

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
    return _info


def _pead_null():
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM paper_trade_record "
                  "WHERE division='robinhood_pead' AND result IS NULL").fetchone()[0]
    c.close()
    return n


async def main():
    raw_info = _wrap()

    # PROVEN auth: bare login (pickle reuse) — NOT broker.connect()'s credentialed login.
    rs.login()

    # READ-ONLY SANITY GUARD: confirm 680725082 is reachable before ANY order. If the
    # pickle is bad / session is broken, this fails safe → no placement.
    prof = rs.profiles.load_account_profile(account_number=ACCT) or {}
    print("auth sanity: account_number=%r buying_power=%r type=%r"
          % (prof.get("account_number"), prof.get("buying_power"), prof.get("type")))
    if prof.get("account_number") != ACCT:
        print("ABORT: bare-login session cannot reach 680725082 (pickle still bad?) — no order placed.")
        return

    # MANUAL BIND — bypass broker.connect()'s credentialed login entirely.
    broker = RobinhoodBroker(username="", password="", mfa_secret=None, account_filter=ACCT)
    broker._account_number = ACCT
    broker._connected = True

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
