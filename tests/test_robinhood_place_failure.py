"""CHARACTERIZATION test — observes the CURRENT (unmodified) RobinhoodBroker
behavior when robin_stocks returns the EXACT 400 body we captured live.

No fix is applied. This feeds `_place_stock_order` the verbatim investing-goals
compliance reject and records what the broker does: RAISE (treats it as a failed
placement) or RETURN a FillEvent (reports a fill that never happened). The print
shows the literal observation; the assertions only pin whatever is observed so a
future fix flips them deliberately.
"""
from __future__ import annotations

import asyncio

from trading_corp.brokers.robinhood import RobinhoodBroker
from trading_corp.persistence.models import FillEvent, ProposedOrder

# Verbatim body captured from the live POST (HTTP 400):
ERROR_400 = {
    "non_field_errors": [
        "We're required to have you answer some questions about your investing "
        "goals before we can allow you to continue using Robinhood."
    ]
}


def _observe(order_type, limit_price, monkeypatch):
    import robin_stocks.robinhood as rs
    # Make every stock order function return the EXACT 400 body (no network).
    for name in ("order_buy_limit", "order_sell_limit", "order_buy_market", "order_sell_market"):
        monkeypatch.setattr(rs.orders, name, lambda *a, **k: dict(ERROR_400))

    broker = RobinhoodBroker(username="u", password="p", mfa_secret="m",
                             account_filter="680725082")
    broker._account_number = "680725082"  # mimic a connected, hard-bound broker
    order = ProposedOrder(strategy="robinhood_pead", symbol="F", side="buy",
                          qty=1.0, order_type=order_type, limit_price=limit_price,
                          extra={})
    raised, result = None, None
    try:
        result = asyncio.run(broker._place_stock_order(order))
    except Exception as e:  # noqa: BLE001
        raised = e
    print(f"\n--- CURRENT broker, order_type={order_type!r}, fed the live 400 ---")
    if raised is not None:
        print(f"  RAISED: {type(raised).__name__}: {raised}")
    else:
        print(f"  RETURNED {type(result).__name__}: price={getattr(result,'price',None)} "
              f"order_id={getattr(result,'order_id',None)} venue={getattr(result,'venue',None)}")
        print("  -> broker reported a FILL despite the 400 reject (fake fill).")
    return raised, result


def test_current_market_order_on_live_400(monkeypatch, capsys):
    raised, result = _observe("market", None, monkeypatch)
    with capsys.disabled():
        pass
    # Pin the OBSERVED current behavior (a real placement failure returns a fill,
    # never raises). When Bug 1 is fixed, this assertion is intentionally flipped.
    assert raised is None, "current broker unexpectedly raised — re-read; Bug 1 may not exist"
    assert isinstance(result, FillEvent), "current broker returned a FillEvent on a 400"


def test_current_limit_order_on_live_400(monkeypatch):
    raised, result = _observe("limit", 7.03, monkeypatch)
    assert raised is None
    assert isinstance(result, FillEvent)
    # the inferred '$7.03' — observed literally here:
    assert result.price == 7.03


# A realistic SUCCESS response shape (RH returns its own order id + the account URL):
SUCCESS = {
    "id": "RH-REAL-ORDER-ID-abc123",
    "account": "https://api.robinhood.com/accounts/680725082/",
    "state": "confirmed",
    "average_price": "14.05",
}


def test_current_broker_discards_rh_id_and_account_on_success(monkeypatch):
    """Bug 2 characterization: on a SUCCESSFUL order, does the FillEvent carry
    RH's real order id and the account it hit, or our order.id with no account?"""
    import robin_stocks.robinhood as rs
    monkeypatch.setattr(rs.orders, "order_buy_market", lambda *a, **k: dict(SUCCESS))

    broker = RobinhoodBroker(username="u", password="p", mfa_secret="m",
                             account_filter="680725082")
    broker._account_number = "680725082"
    order = ProposedOrder(strategy="robinhood_pead", symbol="F", side="buy",
                          qty=1.0, order_type="market", extra={})
    result = asyncio.run(broker._place_stock_order(order))

    has_account = hasattr(result, "account") and getattr(result, "account", None)
    print("\n--- CURRENT broker, SUCCESS response (RH id + account present) ---")
    print(f"  FillEvent.order_id = {result.order_id!r}")
    print(f"  RH real order id   = {SUCCESS['id']!r}")
    print(f"  carries RH order id? {result.order_id == SUCCESS['id']}")
    print(f"  carries the account? {bool(has_account)}  (RH account = {SUCCESS['account']!r})")
    print(f"  price = {result.price}")
    # Observed current behavior: RH's id + account are DROPPED.
    assert result.order_id == order.id, "current broker uses OUR order.id, not RH's"
    assert result.order_id != SUCCESS["id"], "current broker discards RH's real order id"
    assert not has_account, "current FillEvent has no account field — RH's account is dropped"
