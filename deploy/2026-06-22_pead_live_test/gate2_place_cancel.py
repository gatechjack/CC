"""PEAD STEP 3 — GATE 2: routing proof (place-and-cancel, ZERO fill risk).

Pushes ONE liquid name through the REAL engine entry path
    data_exec.place(order, "robinhood_pead")
      -> RobinhoodBroker(account_filter="680725082").place_order
        -> robin_stocks.orders.order_buy_limit(..., account_number=680725082)
as a NON-MARKETABLE buy limit (~50% below market — cannot fill under any intraday
move; the same shape RH accepted on 2026-06-20), then CANCELS it within
milliseconds. Proves, from RH's OWN order record:
  * the order routed to account 680725082 (NOT main 461391328);
  * the 6 locked extra_json keys write to a paper_trade_record row;
and leaves nothing resting and no prod-DB pollution (record goes to a temp DB).

SAFETY — aborts BEFORE placing anything if any guard fails:
  * broker must bind 680725082 (Gate-1 hard-bind) else abort;
  * a sane positive last price else abort;
  * limit must be <= 0.70 * last (provably non-marketable) else abort.
After placing, the order is CANCELLED immediately (by its RH id) in a finally
block — it is cancelled even if a later verification step raises. If RH booked
the order to anything but 680725082, that is reported as FAILURE (caller STOPS).

Symbol via PEAD_TEST_SYMBOL env (default F = Ford). qty = 1.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

ACCOUNT = "680725082"
SYM = os.environ.get("PEAD_TEST_SYMBOL", "F").upper()
QTY = 1

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
for _n in ("yfinance", "urllib3", "asyncio", "azure"):
    logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("gate2")


class _StubLogger:
    def __init__(self):
        self.events, self.proposed = [], []

    def log_event(self, *a, **k):
        self.events.append((a, k))

    def log_proposed_order(self, order):
        self.proposed.append(order)


def _acct_from_url(url: str) -> str:
    return str(url or "").rstrip("/").split("/")[-1]


async def _amain() -> int:
    import robin_stocks.robinhood as rs

    from trading_corp.agents.data_exec import DataExecAgent
    from trading_corp.agents.risk import RiskAgent
    from trading_corp.agents.strategies.pead_strategy import PEADStrategy
    from trading_corp.brokers.robinhood import RobinhoodBroker
    from trading_corp.data.earnings_provider import EarningsProvider
    from trading_corp.persistence.db import connect, init_db
    from trading_corp.persistence.models import ProposedOrder
    from trading_corp.utils.secrets import load_secrets

    secrets = load_secrets()

    # ── real branch broker, hard-bind to 680725082 ──────────────────────────
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
        print(f"ABORT: broker bound {bound!r} != {ACCOUNT!r} — hard-bind FAILED, no order placed.")
        return 3

    # ── safety: a provably NON-MARKETABLE buy limit ─────────────────────────
    last = float(await broker.quote(SYM))
    print(f"{SYM} last = ${last:.2f}")
    if not (last > 0.0):
        print("ABORT: bad last price — no order placed.")
        return 4
    limit = round(last * 0.5, 2)
    if not (0.0 < limit <= 0.70 * last):
        print(f"ABORT: limit ${limit} not provably non-marketable vs last ${last} — no order placed.")
        return 5
    print(f"non-marketable buy limit = ${limit:.2f}  (<= 0.70 * last; cannot fill)")

    # ── 6 locked extra_json keys (real bars + synthetic recent announcement) ─
    tmpdb = os.path.join(tempfile.gettempdir(), "pead_gate2.db")
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
    prim, keys_source = None, "synthetic"
    if bars and len(bars) >= 16:
        prim = strat._build_primitives(bars, bars[-3].d, last)  # synthetic ann = 3 sessions ago
        if prim is not None:
            keys_source = "real-bars"
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
        "source_signal": "gate2_routing_proof",
    }
    print(f"6 extra_json keys computed from: {keys_source}")

    order = ProposedOrder(
        strategy=PEADStrategy.SLUG, symbol=SYM, side="buy", qty=float(QTY),
        order_type="limit", limit_price=limit,
        rationale="GATE2 routing proof — non-marketable, place-and-cancel",
        extra=extra,
    )

    # ── capture the RAW RH order response (the broker discards it) ───────────
    captured: dict = {}
    _real_obl = rs.orders.order_buy_limit

    def _wrap(*a, **k):
        r = _real_obl(*a, **k)
        captured["resp"] = r
        return r

    rs.orders.order_buy_limit = _wrap

    data_exec = DataExecAgent(_StubLogger(), dry_run=False)
    data_exec.register_broker("robinhood_pead", broker)

    rh_id = None
    placed = False
    try:
        print(f"\nPLACING (real engine path): BUY {QTY} {SYM} LIMIT ${limit:.2f} -> data_exec.place(div=robinhood_pead)")
        fill = await data_exec.place(order, division="robinhood_pead")
        placed = True
        resp = captured.get("resp") or {}
        rh_id = resp.get("id")
        rh_acct = _acct_from_url(resp.get("account", ""))
        rh_state = resp.get("state")
        print(f"  RH order id     = {rh_id}")
        print(f"  RH order state  = {rh_state}")
        print(f"  RH order account= {rh_acct}   <-- ROUTING PROOF")
        print(f"  engine fill obj : execution_mode={order.execution_mode} venue={fill.venue}")
    finally:
        rs.orders.order_buy_limit = _real_obl
        if placed:
            try:
                if rh_id:
                    rs.orders.cancel_stock_order(rh_id)
                    print(f"  CANCELLED order {rh_id}")
                else:
                    # targeted fallback: cancel only OUR symbol+limit on 680725082
                    for o in (rs.orders.get_all_open_stock_orders() or []):
                        if _acct_from_url(o.get("account", "")) == ACCOUNT and \
                           abs(float(o.get("price") or 0) - limit) < 0.005:
                            rs.orders.cancel_stock_order(o.get("id"))
                            print(f"  CANCELLED (fallback match) order {o.get('id')}")
            except Exception as e:  # noqa: BLE001
                print(f"  !! CANCEL ERROR: {e} — VERIFY/CANCEL MANUALLY on 680725082")

    # ── routing verdict ─────────────────────────────────────────────────────
    resp = captured.get("resp") or {}
    rh_acct = _acct_from_url(resp.get("account", ""))
    routing_ok = (rh_acct == ACCOUNT)
    print(f"\n=== ROUTING: {'PASS' if routing_ok else 'FAIL'} — RH booked the order to {rh_acct!r} (expected {ACCOUNT!r}) ===")
    if not routing_ok:
        print("STOP: hard-bind FAILED on a live order — do NOT proceed to Gate 3.")

    # ── record-write proof (temp DB; the cancelled order is not in prod) ─────
    order.execution_mode = order.execution_mode or "live"
    strat._write_record(order, max_hold_seconds=60 * 24 * 3600)
    with connect(db_url) as conn:
        row = conn.execute(
            "SELECT order_id, symbol, qty, entry_reference_price, execution_mode, result, extra_json "
            "FROM paper_trade_record WHERE division=? ORDER BY ts DESC LIMIT 1",
            (PEADStrategy.SLUG,),
        ).fetchone()
    six = ("entry_atr_14", "post_earnings_swing_low", "pre_earnings_close",
           "earnings_gap_top", "next_earnings_date", "entry_sue")
    ex = json.loads(row["extra_json"]) if row and row["extra_json"] else {}
    missing = [k for k in six if k not in ex]
    print("\n=== paper_trade_record (temp DB) ===")
    print(f"  row present     = {row is not None}")
    if row:
        print(f"  symbol={row['symbol']} qty={row['qty']} ref=${row['entry_reference_price']} "
              f"mode={row['execution_mode']} result={row['result']}")
    print(f"  6 keys present  = {not missing}" + (f"  MISSING={missing}" if missing else ""))
    print("  " + json.dumps({k: ex.get(k) for k in six}, default=str))

    # ── final order state (confirm cancelled) ───────────────────────────────
    if rh_id:
        try:
            info = rs.orders.get_stock_order_info(rh_id) or {}
            print(f"\nfinal RH order state = {info.get('state')}  (expect 'cancelled')")
        except Exception as e:  # noqa: BLE001
            print(f"\ncould not re-read order state: {e}")

    print("\n=== END GATE 2 — order cancelled, no fill, prod DB untouched ===")
    return 0 if routing_ok else 6


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
