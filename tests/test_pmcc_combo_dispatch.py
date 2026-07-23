"""Combo-dispatch hardening (2026-07-23): group-by-combo_id partition, dispatch
re-pricing from the natural, deterministic ref_id, multi-leg fill recording, and
close_all / Scout OPEN combo-tagging. Pure helpers are unit-tested here; the
route wiring in execute_pair_orders composes them."""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.strategies._pmcc_combo import (
    _net_tick_for_price,
    combo_ref_id,
    partition_combo_orders,
    reprice_combo_from_quotes,
)
from trading_corp.persistence.models import FillEvent, ProposedOrder


def _leg(side="buy", *, combo_id=None, is_multi_leg=False, is_option=True,
         strike=100.0, expiration="2026-07-24", option_type="call",
         underlying="TSLA", limit=1.0, net_limit=1.30, direction="credit",
         action=None):
    ex = {"is_option": is_option, "expiration": expiration, "strike": strike,
          "option_type": option_type, "underlying": underlying, "ratio_quantity": 1}
    if action is not None:
        ex["action"] = action
    if combo_id is not None:
        ex["combo_id"] = combo_id
    if is_multi_leg:
        ex["is_multi_leg"] = True
        ex["combo_direction"] = direction
        ex["net_limit_price"] = net_limit
    return ProposedOrder(strategy="robinhood_pmcc", symbol=underlying, side=side,
                         qty=1.0, order_type="limit", limit_price=limit, extra=ex)


class _FakeLogger:
    db_url = None

    def __init__(self):
        self.events = []

    def log_proposed_order(self, order):
        pass

    def log_event(self, *a, **k):
        self.events.append((a, k))


# ── 1. two distinct combos in one batch → two groups, correct grouping ────────

def test_partition_two_distinct_combos():
    legs = [_leg("buy", combo_id="c1", is_multi_leg=True),
            _leg("sell", combo_id="c1", is_multi_leg=True),
            _leg("buy", combo_id="c2", is_multi_leg=True),
            _leg("sell", combo_id="c2", is_multi_leg=True)]
    groups, singles = partition_combo_orders(legs)
    assert set(groups) == {"c1", "c2"}
    assert len(groups["c1"]) == 2 and len(groups["c2"]) == 2
    assert singles == []


# ── 2. mixed batch: 1 combo + 1 single-leg action ────────────────────────────

def test_partition_mixed_combo_and_single():
    single = _leg("buy", is_option=True)                      # lone single-leg action
    legs = [_leg("buy", combo_id="c1", is_multi_leg=True),
            _leg("sell", combo_id="c1", is_multi_leg=True),
            single]
    groups, singles = partition_combo_orders(legs)
    assert set(groups) == {"c1"} and len(groups["c1"]) == 2
    assert singles == [single]


# ── 3. malformed groups → raise, place nothing ───────────────────────────────

def test_partition_lone_combo_leg_raises():
    with pytest.raises(ValueError, match="malformed combo group"):
        partition_combo_orders([_leg("buy", combo_id="c1", is_multi_leg=True)])


def test_partition_same_underlying_untagged_multi_raises():
    # >1 untagged option leg SHARING an underlying = an un-tagged combo → refuse.
    with pytest.raises(ValueError, match="share underlying"):
        partition_combo_orders([_leg("buy", is_option=True, underlying="TSLA"),
                                _leg("sell", is_option=True, underlying="TSLA")])


def test_partition_independent_singles_different_underlyings_pass():
    # Item 5: independent single-leg option actions on DIFFERENT underlyings in one
    # batch are legitimate — they pass to the per-leg loop, not blocked.
    a = _leg("buy", is_option=True, underlying="TSLA")
    b = _leg("sell", is_option=True, underlying="MSTR")
    groups, singles = partition_combo_orders([a, b])
    assert groups == {} and singles == [a, b]


# ── item 3: ref_id vs combo_id lifecycle ─────────────────────────────────────

def test_ref_id_two_attempts_distinct_one_retry_same():
    # Builders mint a FRESH combo_id (uuid4[:8]) per proposal attempt, so two
    # attempts get distinct ref_ids (both place); a retry of the SAME attempt
    # (same combo_id) reuses the ref_id (RH dedupes).
    attempt1, attempt2 = "a1b2c3d4", "e5f6a7b8"
    assert combo_ref_id(attempt1) != combo_ref_id(attempt2)
    assert combo_ref_id(attempt1) == combo_ref_id(attempt1)


# ── item 6: net-price tick rule ──────────────────────────────────────────────

def test_net_tick_sub_3_is_penny():
    assert _net_tick_for_price([{}, {}], 1.29, def_above=0.05, def_below=0.01,
                               def_cutoff=3.0) == 0.01


def test_net_tick_at_or_above_3_is_nickel():
    assert _net_tick_for_price([{}, {}], 5.10, def_above=0.05, def_below=0.01,
                               def_cutoff=3.0) == 0.05


def test_net_tick_mismatched_legs_coarsest_wins():
    # one standard leg (0.01 below), one nickel-only leg (0.05 below) at a sub-$3
    # net → coarsest (0.05) governs, deterministically.
    legs = [{"below_tick": 0.01, "above_tick": 0.05, "cutoff": 3.0},
            {"below_tick": 0.05, "above_tick": 0.05, "cutoff": 3.0}]
    assert _net_tick_for_price(legs, 1.30, def_above=0.05, def_below=0.01,
                               def_cutoff=3.0) == 0.05


# ── item 4: close_all is marketable-THROUGH, and give_up can flip to debit ────

def test_reprice_close_all_marketable_through_larger_giveup():
    # sell LEAP @ bid 5.00, buy short @ ask 0.02 → natural credit 4.98; a big
    # give_up (0.25) crosses decisively → 4.73 → nickel tick → 4.75 (NOT natural−0.02=4.96).
    legs = [_leg("sell", combo_id="ca", is_multi_leg=True, strike=310.0,
                 action="close_leap_urgent"),
            _leg("buy", combo_id="ca", is_multi_leg=True, strike=405.0,
                 action="close_short_urgent")]
    broker = _FakeQuoteBroker({310.0: (5.00, 5.10), 405.0: (0.01, 0.02)})
    d, l = asyncio.run(reprice_combo_from_quotes(legs, broker, give_up=0.25))
    assert d == "credit" and l == 4.75 and l != 4.96


def test_reprice_giveup_exceeds_credit_flips_to_debit():
    # tiny natural credit 0.04, give_up 0.25 → signed −0.21 → DEBIT 0.21 (pay to exit).
    legs = [_leg("sell", combo_id="cb", is_multi_leg=True, strike=100.0),
            _leg("buy", combo_id="cb", is_multi_leg=True, strike=101.0)]
    broker = _FakeQuoteBroker({100.0: (0.08, 0.10), 101.0: (0.02, 0.04)})
    d, l = asyncio.run(reprice_combo_from_quotes(legs, broker, give_up=0.25))
    assert d == "debit" and l == 0.21


# ── 4. pricing: limit == natural ∓ give_up, correct direction, tick-rounded ───

class _FakeQuoteBroker:
    """get_option_quote keyed by strike → synthetic (bid, ask)."""
    def __init__(self, book):
        self.book = book          # {strike: (bid, ask)}

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        bid, ask = self.book[float(strike)]
        return {"bid": bid, "ask": ask, "mark": (bid + ask) / 2}


def test_reprice_credit_roll_natural_minus_giveup_tick_rounded():
    # sell 337.5C @ bid 1.34, buy 405C @ ask 0.03 → natural credit 1.31 − 0.02 = 1.29
    legs = [_leg("buy", combo_id="c1", is_multi_leg=True, strike=405.0),
            _leg("sell", combo_id="c1", is_multi_leg=True, strike=337.5)]
    broker = _FakeQuoteBroker({405.0: (0.01, 0.03), 337.5: (1.34, 1.38)})
    direction, limit = asyncio.run(
        reprice_combo_from_quotes(legs, broker, give_up=0.02))
    assert direction == "credit"
    assert limit == 1.29                                   # < $3 → 0.01 tick
    assert all((o.extra["net_limit_price"] == 1.29
                and o.extra["combo_direction"] == "credit") for o in legs)


def test_reprice_debit_open_natural_plus_giveup_tick_rounded():
    # buy 310C @ ask 50.10, sell 405C @ bid 0.03 → natural −50.07 → debit 50.07 +
    # 0.02 = 50.09 → 0.05 tick (>= $3) → 50.10
    legs = [_leg("buy", combo_id="c2", is_multi_leg=True, strike=310.0, direction="debit"),
            _leg("sell", combo_id="c2", is_multi_leg=True, strike=405.0, direction="debit")]
    broker = _FakeQuoteBroker({310.0: (49.90, 50.10), 405.0: (0.03, 0.05)})
    direction, limit = asyncio.run(
        reprice_combo_from_quotes(legs, broker, give_up=0.02))
    assert direction == "debit"
    assert limit == 50.10


def test_reprice_missing_quote_is_failsafe():
    class _NoQuote:
        async def get_option_quote(self, *a, **k):
            return {"bid": None, "ask": None, "mark": None}
    legs = [_leg("buy", combo_id="c3", is_multi_leg=True, net_limit=1.30),
            _leg("sell", combo_id="c3", is_multi_leg=True, net_limit=1.30)]
    direction, limit = asyncio.run(
        reprice_combo_from_quotes(legs, _NoQuote(), give_up=0.02))
    assert (direction, limit) == ("credit", 1.30)          # proposal-time kept


# ── 5. deterministic ref_id ──────────────────────────────────────────────────

def test_combo_ref_id_deterministic_and_distinct():
    assert combo_ref_id("c870a9e6") == combo_ref_id("c870a9e6")   # same → same
    assert combo_ref_id("c870a9e6") != combo_ref_id("deadbeef")   # different → different


# ── 6. multi-leg fill: ONE broker order id, BOTH legs updated ─────────────────

class _FakeSpreadBroker:
    name = "rh-fake"
    paper = False

    def __init__(self):
        self.multi_leg_calls = []

    async def place_multi_leg(self, orders, *, ref_id=None):
        self.multi_leg_calls.append((list(orders), ref_id))
        return [FillEvent(order_id=o.id, symbol=o.symbol, side=o.side, qty=o.qty,
                          price=(o.limit_price or 0.0), ts="2026-07-24T00:00:00+00:00",
                          venue="robinhood", broker_order_id="RH-COMBO-1")
                for o in orders]


def test_place_combo_one_order_id_both_legs_and_passes_ref_id():
    logger = _FakeLogger()
    de = DataExecAgent(logger)
    broker = _FakeSpreadBroker()
    de.register_broker("robinhood_pmcc", broker)
    de._persist_combo_positions = lambda *a, **k: None
    legs = [_leg("buy", combo_id="c870a9e6", is_multi_leg=True),
            _leg("sell", combo_id="c870a9e6", is_multi_leg=True)]
    fills = asyncio.run(de.place_combo(legs, division="robinhood_pmcc"))
    # one spread call, our deterministic ref_id threaded through
    assert len(broker.multi_leg_calls) == 1
    _, ref = broker.multi_leg_calls[0]
    assert ref == combo_ref_id("c870a9e6")
    # both legs filled, both carry the SAME single RH order id
    assert all(o.status == "filled" for o in legs)
    assert {f.broker_order_id for f in fills} == {"RH-COMBO-1"}
    # combo_filled audit records the single broker order id (log_event kwargs form)
    filled = [k["payload"] for (a, k) in logger.events if k.get("kind") == "combo_filled"]
    assert filled and filled[0]["broker_order_id"] == "RH-COMBO-1"


# ── 7. Scout/close_all-shaped tagged legs route to a combo group ──────────────

def test_open_style_tagged_legs_route_to_combo_group():
    # As _propose_open_pmcc now emits: is_multi_leg + shared combo_id + net DEBIT.
    legs = [_leg("buy", combo_id="openX", is_multi_leg=True, is_option=True,
                 direction="debit", action="open_leap"),
            _leg("sell", combo_id="openX", is_multi_leg=True, is_option=True,
                 direction="debit", action="open_short_call")]
    groups, singles = partition_combo_orders(legs)
    assert set(groups) == {"openX"} and len(groups["openX"]) == 2
    assert singles == []          # NOT legged-in as singles
