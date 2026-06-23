"""PEAD STEP 3 — GATE 2 routing proof through the NOW-FIXED broker.

Routes ONE non-marketable buy limit through the REAL engine entry path
(data_exec.place(div='robinhood_pead') -> RobinhoodBroker.place_order). The fixed
broker tells the truth:
  * FAILURE -> raises RobinhoodOrderError with RH's verbatim reason (no fake fill,
    no phantom position);
  * SUCCESS -> returns a FillEvent carrying RH's REAL order id (broker_order_id)
    and the account it hit (account) — the routing proof, straight from RH.

Three outcomes are reported explicitly:
  1. ACCEPTED  -> order created + rests + we cancel by RH id; account is the
     routing proof (must be 680725082, not 461391328).
  2. 400 investing-goals -> the questionnaire block isn't lifted yet (delay /
     another requirement) — wait + retry.
  3. a DIFFERENT 400 / clean raise -> the fix works (real error, no phantom).

Safety: aborts before the POST if bind != 680725082 or limit not <= 0.70*last;
the limit is ~50% below market (cannot fill); cancels by id immediately if RH
creates the order. The verbatim raw order response is also captured + printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

ACCOUNT = "680725082"
SYM = "F"


class _StubLogger:
    def __init__(self):
        self.events, self.proposed = [], []

    def log_event(self, *a, **k):
        self.events.append((a, k))

    def log_proposed_order(self, order):
        self.proposed.append(order)


async def _amain() -> int:
    import robin_stocks.robinhood as rs

    from trading_corp.agents.data_exec import DataExecAgent
    from trading_corp.agents.risk import RiskAgent
    from trading_corp.agents.strategies.pead_strategy import PEADStrategy
    from trading_corp.brokers.robinhood import RobinhoodBroker, RobinhoodOrderError
    from trading_corp.data.earnings_provider import EarningsProvider
    from trading_corp.persistence.db import init_db
    from trading_corp.persistence.models import ProposedOrder
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

    # Build the order with the 6 locked extra_json keys (real engine entry shape).
    tmpdb = os.path.join(tempfile.gettempdir(), "pead_routeproof.db")
    if os.path.exists(tmpdb):
        os.remove(tmpdb)
    db_url = f"sqlite:///{tmpdb}"
    init_db(db_url)
    strat = PEADStrategy(
        db_url=db_url, risk_agent=RiskAgent(narrator_enabled=False),
        data_exec=None, logger_agent=_StubLogger(),
        earnings_provider=EarningsProvider(api_key=secrets.eodhd_api_key, db_url=db_url),
        execution_mode="live",
    )
    bars = strat._fetch_daily_bars(SYM)
    prim = strat._build_primitives(bars, bars[-3].d, last) if bars and len(bars) >= 16 else None
    if prim is None:
        prim = {"entry_atr_14": round(last * 0.03, 4),
                "post_earnings_swing_low": round(last * 0.95, 2),
                "pre_earnings_close": round(last * 0.97, 2),
                "earnings_gap_top": round(last * 1.02, 2),
                "stop_level": round(last * 0.92, 2)}
    extra = {
        "entry_atr_14": prim["entry_atr_14"],
        "post_earnings_swing_low": prim["post_earnings_swing_low"],
        "pre_earnings_close": prim["pre_earnings_close"],
        "earnings_gap_top": prim["earnings_gap_top"],
        "next_earnings_date": None,
        "entry_sue": 0.0,
        "name": SYM,
        "entry_reference_price": last,
        "stop_price": prim["stop_level"],
        "source_signal": "gate2_routeproof",
    }
    order = ProposedOrder(
        strategy=PEADStrategy.SLUG, symbol=SYM, side="buy", qty=1.0,
        order_type="limit", limit_price=limit,
        rationale="GATE2 routing proof — non-marketable", extra=extra,
    )

    # Capture the verbatim raw RH response too (belt-and-suspenders).
    cap: dict = {}
    _real = rs.orders.order_buy_limit

    def _wrap(*a, **k):
        r = _real(*a, **k)
        cap["raw"] = r
        return r

    rs.orders.order_buy_limit = _wrap

    data_exec = DataExecAgent(_StubLogger(), dry_run=False)
    data_exec.register_broker("robinhood_pead", broker)

    print("PLACING via engine path: data_exec.place(div=robinhood_pead) -> FIXED broker\n")
    created_id = None
    try:
        fill = await data_exec.place(order, division="robinhood_pead")
        created_id = fill.broker_order_id
        print("=== OUTCOME 1: RH ACCEPTED — order created (rests; non-marketable) ===")
        print(f"  RH order id (broker_order_id) = {fill.broker_order_id!r}")
        print(f"  account (RH booked to)        = {fill.account!r}   <-- ROUTING PROOF")
        print(f"  price={fill.price}  execution_mode={order.execution_mode}")
        routing_ok = (str(fill.account) == ACCOUNT)
        print(f"  ROUTING: {'PASS — bound to 680725082 (NOT 461391328)' if routing_ok else 'FAIL — booked to ' + str(fill.account)}")
    except RobinhoodOrderError as e:
        msg = str(e)
        low = msg.lower()
        print("=== broker RAISED — no fake fill, no phantom position (the fix working) ===")
        print(f"  reason: {msg}")
        if "investing" in low or "goals" in low:
            print("  => OUTCOME 2: questionnaire block NOT lifted yet (delay / another requirement). Wait + retry.")
        else:
            print("  => OUTCOME 3: a DIFFERENT reason — real error surfaced cleanly. Diagnose.")
    finally:
        rs.orders.order_buy_limit = _real
        if created_id:
            try:
                rs.orders.cancel_stock_order(created_id)
                print(f"  CANCELLED order {created_id}")
            except Exception as e:  # noqa: BLE001
                print(f"  !! CANCEL ERROR: {e} — verify/cancel manually on 680725082")

    print("\n--- verbatim raw RH order response ---")
    print(json.dumps(cap.get("raw"), indent=2, default=str)[:1400])
    print("=== END ROUTE PROOF — nothing that can fill; cancelled if created ===")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
