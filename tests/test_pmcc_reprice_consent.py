"""Item-2 guards (2026-07-24): reprice stale/wide-quote HOLD + dispatch consent
bail. The reprice fix (item 1) stops the phantom-debit; these guards stop a
credit approval from ever dispatching as a debit / off garbage quotes / much
worse than the operator approved (defense-in-depth for the scan-timing redesign).
"""
from __future__ import annotations

import pytest

from trading_corp.persistence.models import ProposedOrder
from trading_corp.agents.strategies._pmcc_combo import (
    reprice_combo_from_quotes,
    snapshot_combo_for_consent,
    assess_combo_reprice_consent,
)
from trading_corp.agents.strategies._ic_orchestration import (
    dispatch_approved_ic_combo,
)


def _leg(side, effect, strike, exp, limit, *, net_limit=1.14, direction="credit",
         combo_id="c1", underlying="RKLB"):
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol=underlying, side=side,  # type: ignore[arg-type]
        qty=1.0, order_type="limit", limit_price=limit,
        extra={
            "is_option": True, "is_multi_leg": True, "combo_id": combo_id,
            "combo_direction": direction, "net_limit_price": net_limit,
            "underlying": underlying, "expiration": exp, "strike": strike,
            "option_type": "call", "position_effect": effect, "ratio_quantity": 1,
        },
    )


def _roll(direction="credit", net_limit=1.14):
    """buy-to-close 74C + sell-to-open 75C, approved as a 1.14 credit."""
    return [
        _leg("buy", "close", 74.0, "2026-07-24", 0.03, net_limit=net_limit, direction=direction),
        _leg("sell", "open", 75.0, "2026-07-31", 1.25, net_limit=net_limit, direction=direction),
    ]


class _FakeBroker:
    def __init__(self, quotes):
        self._q = quotes                 # {strike: (bid, ask)}

    async def get_option_quote(self, underlying, expiration, strike, option_type):
        bid, ask = self._q[round(float(strike), 4)]
        return {"bid": bid, "ask": ask}


# --------------------------------------------------------------------------- #
# reprice stale/wide-quote HOLD
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reprice_holds_on_zero_bid_sell_leg():
    legs = _roll()
    broker = _FakeBroker({74.0: (0.02, 0.04), 75.0: (0.0, 1.30)})   # sell bid 0
    direction, limit = await reprice_combo_from_quotes(
        legs, broker, give_up=0.02, min_sell_bid=0.0, max_spread_pct=0.60)
    assert (legs[1].extra or {}).get("reprice_hold")
    assert direction == "credit" and limit == 1.14        # proposal tag kept


@pytest.mark.asyncio
async def test_reprice_holds_on_wide_sell_spread():
    legs = _roll()
    # sell 75C: bid .60 ask 1.90 -> spread 1.30 = 104% of mid (and > $0.10 abs).
    # buy 74C tight (spread .01 < $0.10 abs) so it does NOT trip.
    broker = _FakeBroker({74.0: (0.02, 0.03), 75.0: (0.60, 1.90)})
    await reprice_combo_from_quotes(
        legs, broker, give_up=0.02, max_spread_pct=0.60, min_spread_abs=0.10)
    assert (legs[1].extra or {}).get("reprice_hold")


@pytest.mark.asyncio
async def test_reprice_does_not_hold_cheap_leg_one_tick_spread():
    legs = _roll()
    # Both legs cheap-ish but tight in ABSOLUTE terms; 67%-of-mid must NOT trip.
    broker = _FakeBroker({74.0: (0.02, 0.04), 75.0: (1.20, 1.29)})
    await reprice_combo_from_quotes(
        legs, broker, give_up=0.02, max_spread_pct=0.60, min_spread_abs=0.10)
    assert not (legs[0].extra or {}).get("reprice_hold")
    assert not (legs[1].extra or {}).get("reprice_hold")


@pytest.mark.asyncio
async def test_reprice_normal_credit_computes_natural_minus_giveup():
    legs = _roll()
    broker = _FakeBroker({74.0: (0.02, 0.04), 75.0: (1.20, 1.29)})
    direction, limit = await reprice_combo_from_quotes(
        legs, broker, give_up=0.02, max_spread_pct=0.60)
    # natural = bid(sell 1.20) - ask(buy 0.04) = 1.16; limit = 1.16 - 0.02 = 1.14
    assert direction == "credit"
    assert limit == pytest.approx(1.14, abs=0.011)


# --------------------------------------------------------------------------- #
# assess_combo_reprice_consent (pure)
# --------------------------------------------------------------------------- #


def test_consent_ok_when_close_to_approved():
    legs = _roll()
    snap = snapshot_combo_for_consent(legs)
    for o in legs:
        (o.extra or {})["net_limit_price"] = 1.10          # mild drift
    ok, reason = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.25)
    assert ok, reason


def test_consent_bails_on_sign_flip():
    legs = _roll()
    snap = snapshot_combo_for_consent(legs)                 # credit
    for o in legs:
        (o.extra or {})["combo_direction"] = "debit"
    ok, reason = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.25)
    assert not ok and "DEBIT" in reason


def test_consent_bails_on_credit_collapse():
    legs = _roll()
    snap = snapshot_combo_for_consent(legs)                 # 1.14
    for o in legs:
        (o.extra or {})["net_limit_price"] = 0.50          # dropped 0.64 > 0.25
    ok, reason = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.25)
    assert not ok and "collapsed" in reason


def test_consent_bails_on_strike_change():
    legs = _roll()
    snap = snapshot_combo_for_consent(legs)
    (legs[1].extra or {})["strike"] = 70.0                 # 75 -> 70
    ok, reason = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.25)
    assert not ok and "strike changed" in reason


def test_consent_bails_on_reprice_hold_marker():
    legs = _roll()
    snap = snapshot_combo_for_consent(legs)
    for o in legs:
        (o.extra or {})["reprice_hold"] = "RKLB sell-leg bid 0.00 <= 0.00"
    ok, reason = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.25)
    assert not ok and "stale/wide" in reason


# --------------------------------------------------------------------------- #
# dispatch integration — bail books NOTHING (never reaches the broker)
# --------------------------------------------------------------------------- #


class _FakeStrategy:
    def __init__(self, verdict):
        self._verdict = verdict                            # (ok, reason)
        self.on_filled_called = False

    async def reprice_combo(self, legs, broker):
        for o in legs:
            (o.extra or {})["net_limit_price"] = 1.10
        return "credit", 1.10

    def assess_combo_consent(self, legs, snapshot):
        return self._verdict

    def on_combo_filled(self, combo_id, fills):
        self.on_filled_called = True


class _FakeLogger:
    def __init__(self):
        self.events = []

    def log_event(self, *, actor, kind, payload):
        self.events.append((actor, kind, payload))


class _FakeDataExec:
    def __init__(self, broker):
        self.brokers = {"robinhood_pmcc": broker, "default": broker}
        self.logger = _FakeLogger()
        self.place_called = False

    async def place_combo(self, combo, *, division):
        self.place_called = True
        return [object()]                                  # non-empty fills


@pytest.mark.asyncio
async def test_dispatch_bails_and_never_places_on_consent_fail():
    legs = _roll()
    broker = _FakeBroker({74.0: (0.02, 0.04), 75.0: (1.20, 1.29)})
    strat = _FakeStrategy((False, "credit proposal repriced to a DEBIT limit"))
    dx = _FakeDataExec(broker)
    fills = await dispatch_approved_ic_combo(
        legs, strategy=strat, data_exec=dx, division="robinhood_pmcc")
    assert fills == []
    assert dx.place_called is False                        # never reached broker
    assert strat.on_filled_called is False
    assert any(k == "combo_reprice_bail" for (_, k, _) in dx.logger.events)


@pytest.mark.asyncio
async def test_dispatch_places_when_consent_ok():
    legs = _roll()
    broker = _FakeBroker({74.0: (0.02, 0.04), 75.0: (1.20, 1.29)})
    strat = _FakeStrategy((True, ""))
    dx = _FakeDataExec(broker)
    fills = await dispatch_approved_ic_combo(
        legs, strategy=strat, data_exec=dx, division="robinhood_pmcc")
    assert dx.place_called is True
    assert fills
    assert strat.on_filled_called is True
