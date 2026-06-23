"""Fractional / notional sizing — adversarial suite (PEAD go-live execution path).

Build verification for the equal-DOLLAR notional rebuild. Pins the live-money
invariants of the NEW isolated fractional path:

  * notional sizing (_notional_budget): equal $/candidate, price-independent;
    position_notional fixed-$ override.
  * broker fractional BUY records the REALIZED cumulative_quantity + avg fill price
    polled from the fill — NEVER the (client-computed) request qty; carries
    executed_notional + RH's real id + the account it hit.
  * broker RAISES on a non-accepted notional order (None return below $1 / on a
    price-fetch fail / an error dict) AND on an unconfirmed fill (cancel first, then
    raise) — the Bug-1 fake-fill discipline holds on the notional path.
  * partial fill → the REALIZED cumulative_quantity is recorded (decision #2).
  * exit sells the stored FRACTIONAL qty via order_sell_fractional_by_quantity (the
    old whole-share int()-floor would silently sell 0).
  * eligibility skip via instrument.fractional_tradability (cached).
  * #3 ISOLATION: a NON-fractional order still routes to the UNCHANGED whole-share
    path (order_buy_market), never the fractional path.

No network — robin_stocks order/stock functions are monkeypatched; sys.modules
pollution is healed (same approach as test_robinhood_place_failure).
"""
from __future__ import annotations

import asyncio
import importlib
import sys

import pytest

from trading_corp.brokers.robinhood import RobinhoodBroker, RobinhoodOrderError
from trading_corp.persistence.models import FillEvent, ProposedOrder

ACCT_URL = "https://api.robinhood.com/accounts/680725082/"


def _heal():
    """Drop + reimport robin_stocks (heals the suite-wide sys.modules stub pollution)
    and return the robinhood module so we can monkeypatch .orders / .stocks."""
    for m in [k for k in list(sys.modules)
              if k == "robin_stocks" or k.startswith("robin_stocks.")]:
        sys.modules.pop(m, None)
    return importlib.import_module("robin_stocks.robinhood")


def _broker():
    b = RobinhoodBroker(username="u", password="p", mfa_secret="m", account_filter="680725082")
    b._account_number = "680725082"   # mimic a connected, hard-bound broker
    b._connected = True               # place_order() guards on this
    return b


async def _aio_noop(*_a, **_k):
    return None


def _no_sleep(monkeypatch):
    monkeypatch.setattr("trading_corp.brokers.robinhood.asyncio.sleep", _aio_noop)


def _frac_buy(notional, **kw):
    return ProposedOrder(strategy="robinhood_pead", symbol="F", side="buy", qty=0.0,
                         order_type="market", notional_usd=notional, fractional=True,
                         extra={}, **kw)


# ── notional sizing (pure) ─────────────────────────────────────────────────────
def test_notional_budget_equal_dollars_and_override():
    from trading_corp.agents.strategies.pead_strategy import PEADStrategy
    f = PEADStrategy._notional_budget
    assert f({"position_pct": 0.10}, 75.0) == pytest.approx(7.5)      # same $ regardless of price
    assert f({"position_pct": 0.10}, 15000.0) == pytest.approx(1500.0)  # auto-scales with equity
    assert f({"position_pct": 0.10, "position_notional": 5}, 75.0) == 5.0  # fixed-$ override wins
    assert f({"position_pct": 0.10, "position_notional": "x"}, 75.0) == pytest.approx(7.5)  # bad override → pct


# ── broker fractional BUY: REALIZED qty/price from the polled fill ─────────────
def test_fractional_buy_records_realized_not_request(monkeypatch):
    rh = _heal()
    placed: dict = {}

    def _buy(symbol, amountInDollars, **k):
        placed["amount"], placed["kw"] = amountInDollars, k
        return {"id": "RH-FRAC-1", "account": ACCT_URL, "state": "queued"}

    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price", _buy)
    monkeypatch.setattr(rh.orders, "get_stock_order_info", lambda oid: {
        "state": "filled", "cumulative_quantity": "0.347000", "average_price": "14.40",
        "executed_notional": {"amount": "5.00", "currency_code": "USD"}})

    fill = asyncio.run(_broker().place_order(_frac_buy(5.0)))
    assert isinstance(fill, FillEvent)
    assert placed["amount"] == 5.0                                   # placed BY DOLLARS
    assert placed["kw"].get("account_number") == "680725082"
    assert placed["kw"].get("timeInForce") == "gfd"
    assert fill.qty == pytest.approx(0.347)                          # REALIZED cumulative_quantity
    assert fill.price == pytest.approx(14.40)                        # realized avg fill
    assert fill.executed_notional == pytest.approx(5.00)
    assert fill.broker_order_id == "RH-FRAC-1" and fill.account == "680725082"


def test_fractional_buy_polls_through_queued(monkeypatch):
    """A market order is QUEUED at placement (cum_qty 0 / avg null) — we must POLL
    to the realized fill, not trust the placement response."""
    rh = _heal()
    _no_sleep(monkeypatch)
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price",
                        lambda *a, **k: {"id": "RH-Q", "account": ACCT_URL})
    seq = iter([
        {"state": "queued", "cumulative_quantity": "0", "average_price": None},
        {"state": "queued", "cumulative_quantity": "0", "average_price": None},
        {"state": "filled", "cumulative_quantity": "0.50", "average_price": "10.00",
         "executed_notional": {"amount": "5.00"}},
    ])
    monkeypatch.setattr(rh.orders, "get_stock_order_info", lambda oid: next(seq))
    fill = asyncio.run(_broker().place_order(_frac_buy(5.0)))
    assert fill.qty == pytest.approx(0.50) and fill.price == pytest.approx(10.00)


def test_fractional_buy_raises_on_none_return(monkeypatch):
    """order_buy_fractional_by_price returns None below $1 / on a price-fetch fail →
    the broker RAISES (no fake fill; Bug-1 discipline on the notional path)."""
    rh = _heal()
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price", lambda *a, **k: None)
    with pytest.raises(RobinhoodOrderError):
        asyncio.run(_broker().place_order(_frac_buy(5.0)))


def test_fractional_buy_raises_on_error_dict(monkeypatch):
    rh = _heal()
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price",
                        lambda *a, **k: {"non_field_errors": ["nope"]})
    with pytest.raises(RobinhoodOrderError):
        asyncio.run(_broker().place_order(_frac_buy(5.0)))


def test_fractional_buy_below_min_raises_before_placing(monkeypatch):
    """notional < $1 RH minimum → raise before any placement call."""
    rh = _heal()
    called = {"n": 0}
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(RobinhoodOrderError):
        asyncio.run(_broker().place_order(_frac_buy(0.50)))
    assert called["n"] == 0


def test_fractional_unconfirmed_fill_raises(monkeypatch):
    """Accepted (has id) but the poll sees a terminal NON-fill (rejected, 0 filled)
    → raise, never a phantom fill."""
    rh = _heal()
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price",
                        lambda *a, **k: {"id": "RH-R", "account": ACCT_URL})
    monkeypatch.setattr(rh.orders, "get_stock_order_info",
                        lambda oid: {"state": "rejected", "cumulative_quantity": "0"})
    with pytest.raises(RobinhoodOrderError):
        asyncio.run(_broker().place_order(_frac_buy(5.0)))


def test_poll_timeout_cancels_and_returns_none(monkeypatch):
    """A never-filling (perpetually queued) order: the poll times out → CANCELS to
    stop any further fill → returns None (the caller then raises)."""
    rh = _heal()
    _no_sleep(monkeypatch)
    cancels = {"n": 0}
    monkeypatch.setattr(rh.orders, "get_stock_order_info",
                        lambda oid: {"state": "queued", "cumulative_quantity": "0"})
    monkeypatch.setattr(rh.orders, "cancel_stock_order",
                        lambda oid: cancels.__setitem__("n", cancels["n"] + 1))
    out = asyncio.run(_broker()._poll_fractional_fill("RH-Q", timeout_s=0.01, interval_s=0.005))
    assert out is None and cancels["n"] >= 1


def test_fractional_partial_fill_records_realized(monkeypatch):
    """Partial fill (filled $3 of a $5 request) → the REALIZED cumulative_quantity is
    returned and recorded (decision #2: accept the realized partial)."""
    rh = _heal()
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price",
                        lambda *a, **k: {"id": "RH-P", "account": ACCT_URL})
    monkeypatch.setattr(rh.orders, "get_stock_order_info", lambda oid: {
        "state": "filled", "cumulative_quantity": "0.20", "average_price": "15.00",
        "executed_notional": {"amount": "3.00"}})
    fill = asyncio.run(_broker().place_order(_frac_buy(5.0)))
    assert fill.qty == pytest.approx(0.20)                 # realized, < the $5 request
    assert fill.executed_notional == pytest.approx(3.00)


# ── exit: fractional SELL uses _by_quantity (never int()-floored) ──────────────
def test_fractional_sell_uses_by_quantity_not_floored(monkeypatch):
    rh = _heal()
    cap: dict = {}

    def _sell(symbol, quantity, **k):
        cap["qty"], cap["kw"] = quantity, k
        return {"id": "RH-S", "account": ACCT_URL}

    monkeypatch.setattr(rh.orders, "order_sell_fractional_by_quantity", _sell)
    monkeypatch.setattr(rh.orders, "get_stock_order_info", lambda oid: {
        "state": "filled", "cumulative_quantity": "0.347000", "average_price": "16.00"})
    sell = ProposedOrder(strategy="robinhood_pead", symbol="F", side="sell", qty=0.347,
                         order_type="market", fractional=True, extra={})
    fill = asyncio.run(_broker().place_order(sell))
    assert cap["qty"] == pytest.approx(0.347)              # the FRACTIONAL qty, NOT int()→0
    assert cap["kw"].get("account_number") == "680725082"
    assert fill.qty == pytest.approx(0.347) and fill.price == pytest.approx(16.00)


# ── eligibility (cached) ───────────────────────────────────────────────────────
def test_fractional_eligible_tradable_untradable_and_cache(monkeypatch):
    rh = _heal()
    calls = {"n": 0}

    def _inst(sym):
        calls["n"] += 1
        return [{"fractional_tradability": "tradable" if sym == "F" else "untradable"}]

    monkeypatch.setattr(rh.stocks, "get_instruments_by_symbols", _inst)
    b = _broker()
    assert asyncio.run(b.fractional_eligible("F")) is True
    assert asyncio.run(b.fractional_eligible("XYZ")) is False
    asyncio.run(b.fractional_eligible("F"))                # cached → no new lookup
    assert calls["n"] == 2


# ── #3 ISOLATION: a non-fractional order keeps the UNCHANGED whole-share path ──
def test_nonfractional_order_uses_wholeshare_path_not_fractional(monkeypatch):
    rh = _heal()
    seen = {"market": 0, "frac": 0}

    def _mkt(symbol, quantity, **k):
        seen["market"] += 1
        return {"id": "RH-WS", "account": ACCT_URL, "average_price": "14.00"}

    monkeypatch.setattr(rh.orders, "order_buy_market", _mkt)
    monkeypatch.setattr(rh.orders, "order_buy_fractional_by_price",
                        lambda *a, **k: seen.__setitem__("frac", seen["frac"] + 1))
    order = ProposedOrder(strategy="robinhood_pmcc", symbol="F", side="buy", qty=2.0,
                          order_type="market", extra={})      # NOT fractional
    fill = asyncio.run(_broker().place_order(order))
    assert seen["market"] == 1 and seen["frac"] == 0          # whole-share path, fractional untouched
    assert fill.qty == 2.0                                    # whole-share request qty (int), unchanged
