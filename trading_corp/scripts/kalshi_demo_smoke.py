"""K5·5 — Kalshi DEMO validation smoke (operator-run, NO real capital).

Exercises the live KalshiLiveBroker end-to-end against Kalshi's DEMO environment
(demo.kalshi.com) with demo money: connect + funded preflight, snapshot (validates
the positions field-bug fix on a real account), and a single marketable-IOC
place -> confirm -> FillEvent / KalshiNoFill -> cancel. Kalshi has a real demo env,
so this is strictly better than a real-money $1 shakedown.

PREREQS (operator):
  * Demo API keypair provisioned in env:
      KALSHI_API_KEY_ID=<demo key id>
      KALSHI_PRIVATE_KEY_PEM=<demo RSA private key PEM, newlines preserved>
  * KALSHI_USE_DEMO=1   (this script forces demo=True regardless, but set it too)
  * A currently-tradeable DEMO market ticker (find one on demo.kalshi.com).

RUN (from the repo root, with the prod venv):
  KALSHI_USE_DEMO=1 python -m trading_corp.scripts.kalshi_demo_smoke \
      --ticker <DEMO_TICKER> --outcome yes --price 0.50 --usd 1.0

Safety: defaults to a $1 / 1-2 contract IOC; IOC self-cancels any unfilled
remainder; the script also issues an explicit cancel of whatever order id came
back. It NEVER runs against production (demo=True is hard-coded).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from trading_corp.brokers.kalshi_live import KalshiLiveBroker, KalshiNoFill, OrderPlacementError
from trading_corp.persistence.models import ProposedOrder


def _build_order(ticker: str, outcome: str, price: float, usd: float) -> ProposedOrder:
    return ProposedOrder(
        strategy="kalshi_demo_smoke",
        symbol=f"{ticker}:{outcome}",
        side="buy",
        qty=usd,
        order_type="market",
        limit_price=price,
        extra={
            "is_entry": True,
            "outcome": outcome,
            "ticker": ticker,
            "whale_handle": "DEMO_SMOKE",
            "division": "kalshi_copy_trading",
        },
    )


async def _run(args) -> int:
    key = os.getenv("KALSHI_API_KEY_ID")
    pem = os.getenv("KALSHI_PRIVATE_KEY_PEM")
    if not (key and pem):
        print("ERROR: set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PEM (demo creds).")
        return 2

    broker = KalshiLiveBroker(
        api_key_id=key, private_key_pem=pem,
        demo=True,                       # hard-coded: NEVER production
        order_type="ioc", max_slippage_cents=args.slippage,
    )
    print(f"[1/4] connecting (DEMO, slip={args.slippage}c) ...")
    await broker.connect()

    print("[2/4] snapshot() ...")
    snap = await broker.snapshot()
    print(f"      equity=${snap.equity:.2f} cash=${snap.cash:.2f} "
          f"positions={len(snap.positions)}")
    for p in snap.positions[:10]:
        print(f"        {p.symbol}: qty={p.qty} avg=${p.avg_price:.2f}")

    fill = None
    try:
        order = _build_order(args.ticker, args.outcome, args.price, args.usd)
        print(f"[3/4] place_order IOC {args.outcome} {args.ticker} "
              f"~${args.usd:.2f} @ ceiling(base={args.price}) ...")
        fill = await broker.place_order(order)
        print(f"      FILLED: qty={fill.qty} price=${fill.price:.2f} "
              f"fee=${fill.fee:.4f} role={fill.role} order_id={fill.order_id}")
    except KalshiNoFill as e:
        print(f"      NO FILL (benign): {e}")
    except OrderPlacementError as e:
        print(f"      PLACEMENT ERROR (loud): {e}")
        await broker.disconnect()
        return 1

    print("[4/4] cancel_order (idempotent; IOC self-cancels its remainder) ...")
    if fill is not None:
        ok = await broker.cancel_order(fill.order_id)
        print(f"      cancel_order -> {ok}")
    await broker.disconnect()
    print("DONE — demo round-trip exercised.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kalshi DEMO smoke for KalshiLiveBroker (K5).")
    ap.add_argument("--ticker", required=True, help="A tradeable DEMO market ticker.")
    ap.add_argument("--outcome", default="yes", choices=("yes", "no"))
    ap.add_argument("--price", type=float, default=0.50,
                    help="Per-contract base price in dollars (ceiling = base + slippage).")
    ap.add_argument("--usd", type=float, default=1.0, help="USD copy size (-> contracts).")
    ap.add_argument("--slippage", type=int, default=2, help="max_slippage_cents (default 2).")
    args = ap.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
