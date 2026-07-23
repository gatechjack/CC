"""Phase A (2026-07-22): roll_short atomic-combo dispatch + advisory roll_leap guard.

Self-contained unit tests for the typed `dispatch` field, the fail-closed
`data_exec` guard, and `propose_pmcc_combo`. Combo-tagging of roll_short legs and
advisory-marking of roll_leap legs are covered in test_pmcc_logic.py (which owns
the PMCC broker fixtures)."""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.agents.data_exec import (
    AdvisoryOrderError,
    DataExecAgent,
    _is_advisory_order,
)
from trading_corp.agents.strategies._pmcc_combo import propose_pmcc_combo
from trading_corp.comms.pending_combo_registry import PendingComboRegistry
from trading_corp.persistence.models import FillEvent, ProposedOrder


def _order(action=None, dispatch="executable", side="buy", **extra):
    e = {"is_option": True}
    if action is not None:
        e["action"] = action
    e.update(extra)
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol="MSTR", side=side, qty=1.0,
        order_type="limit", limit_price=1.0, extra=e, dispatch=dispatch,
    )


class _FakeLogger:
    def __init__(self):
        self.events = []

    def log_proposed_order(self, order):
        pass

    def log_event(self, *a, **k):
        self.events.append((a, k))


class _FakeBroker:
    name = "fake"
    paper = True

    def __init__(self):
        self.placed = []

    async def place_order(self, order):
        self.placed.append(order)
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            qty=order.qty, price=order.limit_price or 0.0,
            ts="2026-07-22T00:00:00+00:00", venue="fake",
        )


# ── typed dispatch field default ──────────────────────────────────────

def test_proposed_order_dispatch_defaults_executable():
    o = ProposedOrder(strategy="x", symbol="Y", side="buy", qty=1.0)
    assert o.dispatch == "executable"


# ── _is_advisory_order: BOTH conditions ───────────────────────────────

def test_is_advisory_detects_dispatch_flag():
    assert _is_advisory_order(_order(action="roll_short_call_open", dispatch="advisory"))


def test_is_advisory_detects_roll_leap_action_only():
    # The ceo_graph reconstruct case: the typed field is lost (defaults to
    # executable) but the action survives in extra — must STILL be caught.
    o = _order(action="roll_leap_close", dispatch="executable")
    assert o.dispatch == "executable"
    assert _is_advisory_order(o)


def test_is_advisory_false_for_normal_roll_short():
    assert not _is_advisory_order(_order(action="roll_short_call_open"))
    assert not _is_advisory_order(_order(action=None))


# ── fail-closed guard: data_exec.place ────────────────────────────────

def test_place_raises_on_advisory_dispatch():
    de = DataExecAgent(_FakeLogger())
    broker = _FakeBroker()
    de.register_broker("robinhood_pmcc", broker)
    o = _order(action="roll_leap_open", dispatch="advisory")
    with pytest.raises(AdvisoryOrderError):
        asyncio.run(de.place(o, division="robinhood_pmcc"))
    assert broker.placed == []          # guard fired BEFORE any broker call


def test_place_raises_on_roll_leap_action_even_without_flag():
    de = DataExecAgent(_FakeLogger())
    broker = _FakeBroker()
    de.register_broker("robinhood_pmcc", broker)
    o = _order(action="roll_leap_close_short", dispatch="executable")  # reconstruct case
    with pytest.raises(AdvisoryOrderError):
        asyncio.run(de.place(o, division="robinhood_pmcc"))
    assert broker.placed == []


def test_place_normal_executable_order_proceeds():
    de = DataExecAgent(_FakeLogger())
    broker = _FakeBroker()
    de.register_broker("robinhood_pmcc", broker)
    o = _order(action="roll_short_call_open")
    fill = asyncio.run(de.place(o, division="robinhood_pmcc"))
    assert fill.order_id == o.id
    assert len(broker.placed) == 1


# ── fail-closed guard: data_exec.place_combo ──────────────────────────

def test_place_combo_raises_if_any_leg_advisory():
    de = DataExecAgent(_FakeLogger())
    de.register_broker("robinhood_pmcc", _FakeBroker())
    legs = [
        _order(action="roll_leap_close", dispatch="advisory", combo_id="c1"),
        _order(action="roll_leap_open", dispatch="advisory", combo_id="c1"),
    ]
    with pytest.raises(AdvisoryOrderError):
        asyncio.run(de.place_combo(legs, division="robinhood_pmcc"))


# ── propose_pmcc_combo ────────────────────────────────────────────────

class _Verdict:
    def __init__(self, verdict, reason=""):
        self.verdict = verdict
        self.reason = reason


class _FakeRisk:
    def __init__(self, verdict="approve"):
        self._v = verdict
        self.calls = 0

    def evaluate(self, order, account, strategy_state, regime=None, rvol=None, db_url=None):
        self.calls += 1
        return _Verdict(self._v)


def _combo_pair():
    a = _order(action="roll_short_call_close", is_multi_leg=True, combo_id="cid1",
               combo_direction="credit", net_limit_price=0.5, ratio_quantity=1,
               underlying="MSTR")
    b = _order(action="roll_short_call_open", side="sell", is_multi_leg=True,
               combo_id="cid1", combo_direction="credit", net_limit_price=0.5,
               ratio_quantity=1, underlying="MSTR")
    return [a, b]


def test_propose_pmcc_combo_registers_when_risk_approves():
    reg = PendingComboRegistry(logger_agent=_FakeLogger())
    risk = _FakeRisk("approve")
    ok = asyncio.run(propose_pmcc_combo(
        _combo_pair(), risk_agent=risk, logger_agent=_FakeLogger(),
        pending_combo_registry=reg, db_url=None,
    ))
    assert ok is True
    assert risk.calls == 2                       # both legs risk-gated
    entry = reg.get("cid1")
    assert entry is not None and len(entry.orders) == 2


def test_propose_pmcc_combo_aborts_whole_combo_on_reject():
    reg = PendingComboRegistry(logger_agent=_FakeLogger())
    ok = asyncio.run(propose_pmcc_combo(
        _combo_pair(), risk_agent=_FakeRisk("reject"), logger_agent=_FakeLogger(),
        pending_combo_registry=reg, db_url=None,
    ))
    assert ok is False
    assert reg.get("cid1") is None               # no partial state queued


# ── place_combo: routes to ONE multi-leg call + stamps live (2026-07-23) ──────

class _FakeComboBroker:
    """Live-like broker: implements place_multi_leg (the atomic spread path) and
    records whether the single-leg path is ever touched."""
    name = "rh-fake"
    paper = False

    def __init__(self):
        self.multi_leg_calls = []
        self.single_calls = []

    async def place_multi_leg(self, orders, *, ref_id=None):
        self.multi_leg_calls.append(list(orders))
        return [
            FillEvent(order_id=o.id, symbol=o.symbol, side=o.side, qty=o.qty,
                      price=(o.limit_price or 0.0), ts="2026-07-24T00:00:00+00:00",
                      venue="robinhood")
            for o in orders
        ]

    async def place_order(self, order):
        self.single_calls.append(order)
        return FillEvent(order_id=order.id, symbol=order.symbol, side=order.side,
                         qty=order.qty, price=order.limit_price or 0.0,
                         ts="2026-07-24T00:00:00+00:00", venue="robinhood")


def test_place_combo_routes_to_multi_leg_and_stamps_live():
    """The roll must go to Robinhood as ONE spread (place_multi_leg), never the
    per-leg single path, and the legs must be stamped execution_mode='live' from
    the real (paper=False) broker — even before the fill returns."""
    de = DataExecAgent(_FakeLogger())
    broker = _FakeComboBroker()
    de.register_broker("robinhood_pmcc", broker)
    # Isolate the DISPATCH/routing assertion from DB persistence (position rows
    # are covered by test_place_combo.py; _FakeLogger carries no db_url).
    de._persist_combo_positions = lambda *a, **k: None
    legs = _combo_pair()
    fills = asyncio.run(de.place_combo(legs, division="robinhood_pmcc"))
    assert len(broker.multi_leg_calls) == 1          # ONE spread order
    assert len(broker.multi_leg_calls[0]) == 2       # both legs in it
    assert broker.single_calls == []                 # never legged in single
    assert len(fills) == 2
    assert all(f.venue == "robinhood" for f in fills)
    assert all(o.execution_mode == "live" for o in legs)   # item #3 stamp
    assert all(o.status == "filled" for o in legs)
