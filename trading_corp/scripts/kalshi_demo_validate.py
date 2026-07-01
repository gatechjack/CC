"""K5·1b — Kalshi DEMO validation (operator-run, NO real capital).

Validates the V2 event-order path end-to-end on Kalshi's DEMO env
(external-api.demo.kalshi.co) with demo money. Gates the live flip — does NOT
run against prod (KalshiLiveBroker(demo=True) is hard-coded here).

PREREQS (operator):
  KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PEM = a SEPARATE demo keypair from
  demo.kalshi.co (prod keys 401 on demo), in env (a `.env.demo` you load) +
  a funded demo account. A tradeable demo market ticker + its current YES ask.

RUN (from the repo root, prod venv):
  python -m trading_corp.scripts.kalshi_demo_validate --ticker <DEMO_TICKER> --price 0.50

Steps (each PASS/FAIL): connect+balance · no-fill (FOK below market) · fill +
position read (the 3-bug fix on a NON-zero position) + balance delta · exit
(reduce_only, closes) · idempotency (same client_order_id, no double-fill) ·
cancel (rest a GTC, cancel it) · NO-mapping (buy/sell NO = ask/bid on the YES book).
It flattens what it opens. Safe: demo money only, 1 contract per order.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from trading_corp.brokers.kalshi_live import (
    KalshiLiveBroker, KalshiNoFill, OrderPlacementError,
    build_v2_event_order, client_order_id, _V2_ORDERS_PATH,
)
from trading_corp.persistence.models import ProposedOrder

_PASS, _FAIL = "PASS", "FAIL"


def _order(ticker, *, outcome, side, price, usd=0.55, oid=None):
    o = ProposedOrder(
        strategy="kalshi_demo_validate", symbol=f"{ticker}:{outcome}", side=side, qty=usd,
        order_type="market", limit_price=price,
        extra={"is_entry": side == "buy", "outcome": outcome, "ticker": ticker,
               "whale_handle": "DEMO_VALIDATE", "division": "kalshi_copy_trading"},
    )
    if oid:
        o.id = oid
    return o


async def _positions(b):
    snap = await b.snapshot()
    return {p.symbol: p for p in snap.positions}, snap.cash


async def run(args) -> int:
    key, pem = os.environ.get("KALSHI_API_KEY_ID"), os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    if not (key and pem):
        print("ERROR: set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM (DEMO creds).")
        return 2

    # FOK for clean fill-or-nothing validation; demo=True hard-codes the demo host.
    b = KalshiLiveBroker(api_key_id=key, private_key_pem=pem, demo=True,
                         order_type="fok", max_slippage_cents=args.slippage)
    print(f"[connect] DEMO host={b._api_base}")
    await b.connect()
    _, cash0 = await _positions(b)
    print(f"  {_PASS} connected; demo cash=${cash0:.2f}")

    results = {}

    # 1. No-fill: FOK buy YES far below market -> should not fill.
    try:
        await b.place_order(_order(args.ticker, outcome="yes", side="buy", price=0.01))
        results["no_fill"] = (_FAIL, "expected KalshiNoFill, got a fill")
    except KalshiNoFill:
        results["no_fill"] = (_PASS, "FOK below market -> KalshiNoFill (benign)")
    except Exception as e:
        results["no_fill"] = (_FAIL, f"{type(e).__name__}: {e}")

    # 2. Fill + position read (3-bug fix on a non-zero position) + balance delta.
    try:
        fill = await b.place_order(_order(args.ticker, outcome="yes", side="buy", price=args.price))
        pos, cash1 = await _positions(b)
        held = pos.get(f"{args.ticker}:yes") or pos.get(args.ticker.upper())
        ok = fill.qty > 0 and held is not None and abs((cash1 - cash0)) > 0
        results["fill"] = (
            _PASS if ok else _FAIL,
            f"fill qty={fill.qty} price=${fill.price:.4f} fee=${fill.fee:.4f} | "
            f"position={'yes(qty=%s avg=$%s exp=%s)' % (getattr(held,'qty',None), getattr(held,'avg_price',None), getattr(held,'extra',{})) if held else 'MISSING'} | "
            f"cash {cash0:.2f}->{cash1:.2f} (delta {cash1-cash0:+.4f})",
        )
    except Exception as e:
        results["fill"] = (_FAIL, f"{type(e).__name__}: {e}")

    # 3. Exit (reduce_only) -> position closes.
    try:
        ex = await b.place_order(_order(args.ticker, outcome="yes", side="sell", price=args.price))
        pos, _ = await _positions(b)
        closed = (f"{args.ticker}:yes" not in pos and args.ticker.upper() not in pos)
        results["exit"] = (_PASS if ex.qty > 0 and closed else _FAIL,
                           f"exit qty={ex.qty} | flat_after={closed}")
    except Exception as e:
        results["exit"] = (_FAIL, f"{type(e).__name__}: {e}")

    # 4. Idempotency: same client_order_id re-submit -> no double-fill / no new cash move.
    try:
        _, c_a = await _positions(b)
        o = _order(args.ticker, outcome="yes", side="buy", price=args.price, oid="IDEMP-1")
        await b.place_order(o)
        _, c_b = await _positions(b)
        try:
            await b.place_order(_order(args.ticker, outcome="yes", side="buy", price=args.price, oid="IDEMP-1"))
        except KalshiNoFill:
            pass  # resubmit may return the already-terminal order with 0 new fill
        _, c_c = await _positions(b)
        # second submit should not move cash again (idempotent)
        results["idempotency"] = (_PASS if abs(c_c - c_b) < 1e-6 else _FAIL,
                                  f"cash after 1st={c_b:.4f} after resubmit={c_c:.4f} (delta {c_c-c_b:+.4f})")
        # flatten the idempotency lot
        try:
            await b.place_order(_order(args.ticker, outcome="yes", side="sell", price=args.price))
        except Exception:
            pass
    except Exception as e:
        results["idempotency"] = (_FAIL, f"{type(e).__name__}: {e}")

    # 5. Cancel: rest a non-marketable GTC via raw client, then cancel_order().
    try:
        body, _, _ = build_v2_event_order(
            ticker=args.ticker, outcome="yes", is_buy=True, base_price=0.02, copy_usd=0.10,
            max_slippage_cents=0, tif="good_till_canceled",
            client_order_id=client_order_id("demo", "validate", args.ticker, "yes", "cancel-test"),
        )
        resp = await b._client().post(_V2_ORDERS_PATH, body)
        oid = resp.get("order_id")
        ok = bool(oid) and await b.cancel_order(oid)
        status = None
        try:
            status = (await b._client().get(f"/portfolio/orders/{oid}")).get("status")
        except Exception:
            pass
        results["cancel"] = (_PASS if ok else _FAIL, f"order_id={oid} cancel_ok={ok} status_after={status}")
    except Exception as e:
        results["cancel"] = (_FAIL, f"{type(e).__name__}: {e}")

    # 6. NO mapping (highest risk): buy NO (=ask on YES book) then sell NO (=bid) reduce_only.
    try:
        # NO price ~ 1 - yes ask; marketable.
        no_price = round(1.0 - args.price, 2)
        nf = await b.place_order(_order(args.ticker, outcome="no", side="buy", price=no_price))
        pos, _ = await _positions(b)
        held_no = pos.get(f"{args.ticker}:no") or pos.get(args.ticker.upper())
        ne = await b.place_order(_order(args.ticker, outcome="no", side="sell", price=no_price))
        pos2, _ = await _positions(b)
        flat = f"{args.ticker}:no" not in pos2
        results["no_mapping"] = (_PASS if nf.qty > 0 and ne.qty > 0 and flat else _FAIL,
                                 f"buy_no qty={nf.qty} sell_no qty={ne.qty} flat_after={flat} "
                                 f"(VERIFY this is the NO side on the demo UI!)")
    except Exception as e:
        results["no_mapping"] = (_FAIL, f"{type(e).__name__}: {e}")

    _, cashN = await _positions(b)
    await b.disconnect()

    print("\n=== DEMO VALIDATION RESULTS ===")
    allpass = True
    for k, (verdict, detail) in results.items():
        allpass = allpass and verdict == _PASS
        print(f"  [{verdict}] {k}: {detail}")
    print(f"\nfinal demo cash=${cashN:.2f} (started ${cash0:.2f})")
    print("OVERALL:", "ALL PASS" if allpass else "FAILURES PRESENT — review above")
    return 0 if allpass else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kalshi DEMO validation for the V2 KalshiLiveBroker (K5·1b).")
    ap.add_argument("--ticker", required=True, help="A tradeable DEMO market ticker.")
    ap.add_argument("--price", type=float, default=0.50, help="Current YES ask (dollars); used for marketable orders.")
    ap.add_argument("--slippage", type=int, default=2, help="max_slippage_cents (default 2).")
    ap.add_argument("--env-file", default=None,
                    help="dotenv file with the DEMO KALSHI_API_KEY_ID/PEM to load before reading env.")
    args = ap.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
