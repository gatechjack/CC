"""Broker fail-loud + routing-identity contract (PEAD STEP 3, Bug 1 + Bug 2).

These started as CHARACTERIZATION tests that PASSED against the pre-fix 3ad16a2
broker blob (observed: a 400 reject -> a fake FillEvent; a success -> RH's id +
account dropped). They are now FLIPPED to the FIXED contract:

  * Bug 1 — fed the verbatim live HTTP-400 `non_field_errors` body, the broker
    RAISES RobinhoodOrderError carrying RH's reason, and never returns a fill.
  * Bug 2 — on a success response, the FillEvent carries RH's real order id
    (`broker_order_id`) and the account it hit (`account`).

Regression-guard property (what makes them meaningful): each of these FAILS
against the unfixed broker (we observed the old fake-fill / dropped-id behavior
on 3ad16a2) and passes only with the fix. No network — robin_stocks order
functions are monkeypatched.
"""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.brokers.robinhood import RobinhoodBroker, RobinhoodOrderError
from trading_corp.persistence.models import FillEvent, ProposedOrder

# Verbatim body captured from the live POST (HTTP 400):
ERROR_400 = {
    "non_field_errors": [
        "We're required to have you answer some questions about your investing "
        "goals before we can allow you to continue using Robinhood."
    ]
}

# A realistic SUCCESS response shape (RH returns its own order id + account URL):
SUCCESS = {
    "id": "RH-REAL-ORDER-ID-abc123",
    "account": "https://api.robinhood.com/accounts/680725082/",
    "state": "confirmed",
    "average_price": "14.05",
}


def _heal_orders(monkeypatch):
    """Return the REAL robin_stocks.robinhood.orders submodule, healing suite
    pollution. Other suites replace sys.modules['robin_stocks.robinhood'] with a
    non-package stub and LEAK it (the same pre-existing pollution that breaks
    test_robinhood_multi_leg / test_tasty in the full -k sweep on HEAD —
    'robin_stocks.robinhood is not a package'). monkeypatch can't restore a
    sys.modules swap, so reload the real package + submodule from disk; this also
    heals it for whatever runs after."""
    import importlib
    import sys
    for m in [k for k in list(sys.modules)
              if k == "robin_stocks" or k.startswith("robin_stocks.")]:
        sys.modules.pop(m, None)
    importlib.import_module("robin_stocks.robinhood")
    return importlib.import_module("robin_stocks.robinhood.orders")


def _broker_with(order_fn_result, monkeypatch):
    rs_orders = _heal_orders(monkeypatch)
    for name in ("order_buy_limit", "order_sell_limit",
                 "order_buy_market", "order_sell_market"):
        monkeypatch.setattr(rs_orders, name, lambda *a, **k: dict(order_fn_result))
    broker = RobinhoodBroker(username="u", password="p", mfa_secret="m",
                             account_filter="680725082")
    broker._account_number = "680725082"  # mimic a connected, hard-bound broker
    return broker


def _order(order_type, limit_price=None):
    return ProposedOrder(strategy="robinhood_pead", symbol="F", side="buy",
                         qty=1.0, order_type=order_type, limit_price=limit_price,
                         extra={})


# ── Bug 1: a 400 reject must RAISE, never synthesize a fill ───────────────────


def test_market_order_raises_on_live_400(monkeypatch):
    broker = _broker_with(ERROR_400, monkeypatch)
    with pytest.raises(RobinhoodOrderError) as ei:
        asyncio.run(broker._place_stock_order(_order("market")))
    # RH's verbatim reason is surfaced, not swallowed:
    assert "investing" in str(ei.value).lower()


def test_limit_order_raises_on_live_400(monkeypatch):
    broker = _broker_with(ERROR_400, monkeypatch)
    with pytest.raises(RobinhoodOrderError):
        asyncio.run(broker._place_stock_order(_order("limit", 7.03)))


def test_option_order_raises_on_live_400(monkeypatch):
    """Platform-wide: the option single-leg path raises on the same 400."""
    rs_orders = _heal_orders(monkeypatch)
    monkeypatch.setattr(rs_orders, "order_buy_option_limit",
                        lambda *a, **k: dict(ERROR_400))
    broker = RobinhoodBroker(username="u", password="p", mfa_secret="m",
                             account_filter="680725082")
    broker._account_number = "680725082"
    opt = ProposedOrder(strategy="robinhood_joint", symbol="SPY", side="buy",
                        qty=1.0, order_type="limit", limit_price=1.0,
                        extra={"is_option": True, "underlying": "SPY",
                               "expiration": "2026-07-17", "strike": 500.0,
                               "option_type": "call", "position_effect": "open"})
    with pytest.raises(RobinhoodOrderError):
        asyncio.run(broker._place_option_order(opt))


# ── Bug 2: a success must carry RH's real order id + the account it hit ───────


def test_market_orders_use_gfd_not_gtc(monkeypatch):
    """Bug 3: a true market order can't be GTC — RH rejects market sells placed
    GTC ('Invalid Good Til Canceled order', observed live 2026-06-23). The broker
    must pass timeInForce='gfd' for market orders. Capture the kwargs for a market
    buy + sell."""
    rs_orders = _heal_orders(monkeypatch)
    ok = {"id": "RH-OK", "account": "https://api.robinhood.com/accounts/680725082/",
          "average_price": "13.80"}
    cap: dict = {}

    def _mk(side):
        def _fn(*a, **k):
            cap[side] = k
            return dict(ok)
        return _fn

    monkeypatch.setattr(rs_orders, "order_buy_market", _mk("buy"))
    monkeypatch.setattr(rs_orders, "order_sell_market", _mk("sell"))
    broker = RobinhoodBroker(username="u", password="p", mfa_secret="m",
                             account_filter="680725082")
    broker._account_number = "680725082"
    asyncio.run(broker._place_stock_order(_order("market")))
    sell = ProposedOrder(strategy="robinhood_pead", symbol="F", side="sell",
                         qty=1.0, order_type="market", extra={})
    asyncio.run(broker._place_stock_order(sell))
    assert cap["buy"].get("timeInForce") == "gfd", cap["buy"]
    assert cap["sell"].get("timeInForce") == "gfd", cap["sell"]


def test_success_carries_rh_id_and_account(monkeypatch):
    broker = _broker_with(SUCCESS, monkeypatch)
    order = _order("market")
    fill = asyncio.run(broker._place_stock_order(order))
    assert isinstance(fill, FillEvent)
    assert fill.broker_order_id == "RH-REAL-ORDER-ID-abc123"   # RH's real id, carried (Bug 2)
    assert fill.account == "680725082"                          # the account it hit, parsed
    assert fill.order_id == order.id                            # our id kept for correlation
    assert fill.order_id != SUCCESS["id"]
    assert fill.price == 14.05
