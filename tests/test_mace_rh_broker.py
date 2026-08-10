"""Phase-3 tests for mace/rh_broker.py — the neutral port over RobinhoodBroker.

Mocks the RobinhoodBroker (byte-untouched real-money adapter) and asserts the
translation each way: opening vs closing leg construction (incl. the fixed call
side), credit/debit direction, the combo-fill-timeout leg-0 extra, pending ->
non-terminal, reject -> propagate (fake-fill guard), resting GTC PT, order-status
mapping, and the settled-cash snapshot basis.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

import robin_stocks.robinhood as rs  # type: ignore

from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.robinhood import RobinhoodComboPending, RobinhoodOrderError
from trading_corp.mace import broker_port as bp
from trading_corp.mace.domain import CondorSpec
from trading_corp.mace.rh_broker import RobinhoodOptionsBroker
from trading_corp.persistence.models import FillEvent

SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)


class MockRHBroker:
    """Minimal async stand-in exposing exactly what rh_broker calls."""

    def __init__(self):
        self.option_level = "option_level_3"
        self._account_number = "116637293063"
        self._account_type = "joint_tenancy_with_ros"
        self.last_orders = None
        self.last_ref = None
        self.resting_orders = None
        self.resting_tif = None
        self.pml_raises = None       # set to an Exception to raise from place_multi_leg
        self.status_ret = {}
        self.positions = []
        self.snap = AccountSnapshot(account="a", equity=10000.0, buying_power=8000.0,
                                    cash=5000.0, positions=[], settled_cash=4200.0)
        self.cancelled = []

    async def place_multi_leg(self, orders, *, ref_id=None):
        self.last_orders = orders
        self.last_ref = ref_id
        if self.pml_raises is not None:
            raise self.pml_raises
        return [FillEvent(order_id="RH1", symbol="SPY", side=orders[0].side, qty=1.0,
                          price=0.0, ts="2026-08-10T19:45:00+00:00", venue="robinhood",
                          broker_order_id="RH1", account="https://api/accounts/116637293063/")]

    async def place_multi_leg_resting(self, orders, *, ref_id, time_in_force="gtc"):
        self.resting_orders = orders
        self.resting_tif = time_in_force
        self.last_ref = ref_id
        return "PT-RH-1"

    async def get_option_order_status(self, order_id):
        return dict(self.status_ret, id=order_id)

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True

    async def get_option_positions_detail(self):
        return list(self.positions)

    async def snapshot(self):
        return self.snap

    async def quote(self, symbol):
        return 610.0

    async def get_expiration_dates(self, symbol):
        return ["2026-09-18"]

    async def get_calls_for_expiry(self, symbol, expiry):
        return [{"strike_price": 615.0, "bid": 1.08, "ask": 1.12, "delta": 0.20,
                 "option_id": "C615"},
                {"strike_price": 618.0, "bid": 0.48, "ask": 0.52, "delta": 0.12,
                 "option_id": "C618"}]

    async def get_puts_for_expiry(self, symbol, expiry):
        return [{"strike_price": 585.0, "bid": 0.98, "ask": 1.02, "delta": -0.20,
                 "option_id": "P585"},
                {"strike_price": 582.0, "bid": 0.38, "ask": 0.42, "delta": -0.12,
                 "option_id": "P582"}]


def _port(broker):
    return RobinhoodOptionsBroker(broker, now_et_fn=lambda: __import__("datetime").datetime(
        2026, 8, 10, 15, 45))


def _legs_by_key(orders):
    return {(o.extra["option_type"], o.extra["strike"]): o for o in orders}


@pytest.mark.asyncio
async def test_place_condor_credit_uses_opening_legs():
    b = MockRHBroker(); port = _port(b)
    res = await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                  time_in_force="gfd", fill_timeout_s=60)
    assert res.is_filled and res.order_id == "RH1"
    legs = _legs_by_key(b.last_orders)
    # short put/call SELL, long put/call BUY, effect open
    assert legs[("put", 585.0)].side == "sell" and legs[("put", 585.0)].extra["position_effect"] == "open"
    assert legs[("put", 582.0)].side == "buy"
    assert legs[("call", 615.0)].side == "sell"
    assert legs[("call", 618.0)].side == "buy"
    assert all(o.extra["combo_direction"] == "credit" for o in b.last_orders)
    # combo-fill-timeout ONLY on leg 0 (the additive robinhood.py read site)
    assert b.last_orders[0].extra["combo_fill_timeout_s"] == 60.0
    assert "combo_fill_timeout_s" not in b.last_orders[1].extra
    assert b.last_ref == "mace-x-a1"


@pytest.mark.asyncio
async def test_place_condor_debit_uses_closing_legs_fixed_call_side():
    b = MockRHBroker(); port = _port(b)
    await port.place_condor(SPEC, 1, 2.04, "mace-x-x1", direction=bp.DIR_DEBIT,
                            time_in_force="gfd", fill_timeout_s=20)
    legs = _legs_by_key(b.last_orders)
    # flatten: buy back shorts, sell longs — call side must be buy/sell (the fix)
    assert legs[("put", 585.0)].side == "buy"
    assert legs[("put", 582.0)].side == "sell"
    assert legs[("call", 615.0)].side == "buy"     # buy back short call (was the bug)
    assert legs[("call", 618.0)].side == "sell"    # sell long call
    assert all(o.extra["position_effect"] == "close" for o in b.last_orders)
    assert all(o.extra["combo_direction"] == "debit" for o in b.last_orders)


@pytest.mark.asyncio
async def test_place_condor_pending_is_non_terminal():
    b = MockRHBroker(); port = _port(b)
    b.pml_raises = RobinhoodComboPending("still queued", order_id="RHPEND")
    res = await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                  time_in_force="gfd", fill_timeout_s=60)
    assert res.order_id == "RHPEND" and not res.is_terminal and not res.is_filled


@pytest.mark.asyncio
async def test_place_condor_reject_propagates_never_books():
    b = MockRHBroker(); port = _port(b)
    b.pml_raises = RobinhoodOrderError("compliance reject — no id")
    with pytest.raises(RobinhoodOrderError):
        await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                time_in_force="gfd", fill_timeout_s=60)


@pytest.mark.asyncio
async def test_place_resting_close_uses_closing_legs_gtc():
    b = MockRHBroker(); port = _port(b)
    pt_id = await port.place_resting_close(SPEC, 1, 0.59, "mace-x-pt")
    assert pt_id == "PT-RH-1" and b.resting_tif == "gtc"
    legs = _legs_by_key(b.resting_orders)
    assert legs[("call", 615.0)].side == "buy" and legs[("call", 618.0)].side == "sell"
    assert all(o.extra["position_effect"] == "close" for o in b.resting_orders)
    # a resting order sets NO combo-fill-timeout (it rests, never polls)
    assert "combo_fill_timeout_s" not in b.resting_orders[0].extra


@pytest.mark.asyncio
async def test_order_status_maps_state_and_quantities():
    b = MockRHBroker(); port = _port(b)
    b.status_ret = {"state": "Filled", "processed_quantity": "1", "pending_quantity": "0",
                    "time_in_force": "gtc"}
    res = await port.order_status("RH1")
    assert res.state == "filled" and res.is_filled and res.processed_quantity == 1.0
    assert res.time_in_force == "gtc" and res.order_id == "RH1"


@pytest.mark.asyncio
async def test_snapshot_uses_settled_cash_basis():
    b = MockRHBroker(); port = _port(b)
    snap = await port.snapshot()
    assert snap.equity == 4200.0 and snap.cash == 5000.0   # equity = settled cash


@pytest.mark.asyncio
async def test_account_assertions_parses_option_level():
    b = MockRHBroker(); port = _port(b)
    info = await port.account_assertions()
    assert info.account_number == "116637293063" and info.option_level == 3 and info.margin


@pytest.mark.asyncio
async def test_open_positions_map_to_neutral(monkeypatch):
    b = MockRHBroker(); port = _port(b)
    b.positions = [{"chain_symbol": "spy", "option_type": "put", "strike_price": 585.0,
                    "expiration_date": "2026-09-18", "quantity": -1.0, "option_id": "P585"}]
    out = await port.open_positions()
    assert out[0].symbol == "SPY" and out[0].option_id == "P585" and out[0].quantity == -1.0


@pytest.mark.asyncio
async def test_open_orders_map_ref_id(monkeypatch):
    b = MockRHBroker(); port = _port(b)
    monkeypatch.setattr(rs.orders, "get_all_open_option_orders",
                        lambda acct=None: [{"id": "O1", "state": "queued",
                                            "time_in_force": "gtc",
                                            "ref_id": "mace-SPY-x-pt"}], raising=False)
    out = await port.open_orders()
    assert out[0].order_id == "O1" and out[0].ref_id == "mace-SPY-x-pt"


@pytest.mark.asyncio
async def test_leg_quote_and_chain_bind_strikes():
    b = MockRHBroker(); port = _port(b)
    q = await port.leg_quote("SPY", date(2026, 9, 18), "put", 585.0)
    assert q is not None and q.bid == 0.98 and q.ask == 1.02 and q.delta == -0.20
    chain = await port.chain("SPY")
    assert chain.spot == 610.0 and date(2026, 9, 18) in chain.expiries
    assert chain.listed(date(2026, 9, 18), "call", 615.0)


# ── resilient cancel (Checkpoint-0 cancel-path fix) ──────────────────────

class _OrderSim:
    """Models one RH option order + the endpoints rh_broker.cancel drives."""

    def __init__(self, *, state="confirmed", cancel_url=None, cancel_url_after=0,
                 post_cancels=True, constructed_cancels=False):
        self.state = state
        self._cancel_url = cancel_url
        self.cancel_url_after = cancel_url_after   # reads before cancel_url appears
        self.post_cancels = post_cancels
        self.constructed_cancels = constructed_cancels
        self.reads = 0
        self.post_urls = []
        self.constructed_calls = 0

    def get_info(self, oid):
        self.reads += 1
        cu = self._cancel_url if self.reads > self.cancel_url_after else None
        return {"id": oid, "state": self.state, "cancel_url": cu}

    def request_post(self, url, *a, **k):
        self.post_urls.append(url)
        if self.post_cancels:
            self.state = "cancelled"        # RH accepted the cancel_url POST
        return {"id": "x", "state": self.state}

    def cancel_option_order(self, oid):
        self.constructed_calls += 1
        if self.constructed_cancels:
            self.state = "cancelled"
        return None


def _install(monkeypatch, sim):
    monkeypatch.setattr("robin_stocks.robinhood.orders.get_option_order_info",
                        sim.get_info, raising=False)
    monkeypatch.setattr("robin_stocks.robinhood.orders.cancel_option_order",
                        sim.cancel_option_order, raising=False)
    monkeypatch.setattr("robin_stocks.robinhood.helper.request_post",
                        sim.request_post, raising=False)


def _cancel_port():
    return RobinhoodOptionsBroker(MockRHBroker(), cancel_url_polls=3, cancel_url_poll_s=0.0,
                                  cancel_confirm_polls=3, cancel_confirm_poll_s=0.0)


@pytest.mark.asyncio
async def test_cancel_uses_order_cancel_url_first(monkeypatch):
    sim = _OrderSim(cancel_url="https://api.robinhood.com/options/orders/OID/cancel_v2/",
                    post_cancels=True)
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.post_urls == ["https://api.robinhood.com/options/orders/OID/cancel_v2/"]
    assert sim.constructed_calls == 0                 # never reached the constructed endpoint
    assert port._last_cancel_rung == "cancel_url"


@pytest.mark.asyncio
async def test_cancel_polls_for_cancel_url_to_populate(monkeypatch):
    sim = _OrderSim(cancel_url="https://api/cu/", cancel_url_after=2, post_cancels=True)
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.post_urls == ["https://api/cu/"] and port._last_cancel_rung == "cancel_url"


@pytest.mark.asyncio
async def test_cancel_falls_back_to_constructed(monkeypatch):
    sim = _OrderSim(cancel_url=None, constructed_cancels=True)
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.post_urls == []                        # no cancel_url -> never POSTed one
    assert sim.constructed_calls == 1 and port._last_cancel_rung == "constructed"


@pytest.mark.asyncio
async def test_cancel_raises_loudly_when_all_paths_fail(monkeypatch):
    sim = _OrderSim(cancel_url=None, constructed_cancels=False)   # nothing cancels it
    _install(monkeypatch, sim)
    port = _cancel_port()
    with pytest.raises(RuntimeError, match="NO terminal state"):
        await port.cancel("OID")


@pytest.mark.asyncio
async def test_cancel_noop_when_already_terminal(monkeypatch):
    sim = _OrderSim(state="cancelled")
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.post_urls == [] and sim.constructed_calls == 0
    assert port._last_cancel_rung == "already_terminal"
