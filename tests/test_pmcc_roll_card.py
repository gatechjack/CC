"""Roll-card consent enhancements (2026-07-28), stacked on the P1 earnings gate.

A. earnings-imminent card states (off PMCCAgent.earnings_card_state → the SAME
   _earnings_gate_state the backend roll path uses).
B. live debit/credit/net estimate (off estimate_roll_from_quotes → the SAME
   broker.get_option_quote + natural formula reprice_combo_from_quotes uses at
   dispatch), incl. a lock test proving the estimate does NOT diverge from the
   placed order, plus the un-buildable "no estimate → reason" path.
Also: the route orchestrator + a template render for the three card states.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import trading_corp.utils.market_data as _md_mod
from trading_corp.persistence.models import ProposedOrder
from trading_corp.agents.strategies._pmcc_combo import (
    estimate_roll_from_quotes,
    reprice_combo_from_quotes,
)
from trading_corp.web.pmcc_roll_card import build_pmcc_roll_card_extras


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _dt(days: int):
    return datetime.now(timezone.utc) + timedelta(days=days)


def _leg(side, effect, strike, expiration, *, action, limit):
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol="AAPL", side=side, qty=1.0,
        order_type="limit", limit_price=limit, rationale="",
        extra={
            "is_option": True, "underlying": "AAPL", "option_type": "call",
            "expiration": expiration, "strike": strike, "position_effect": effect,
            "action": action, "is_multi_leg": True, "combo_id": "c1",
            "combo_direction": "credit", "net_limit_price": 0.60, "ratio_quantity": 1,
        },
    )


def _roll_legs(close_strike=170.0, open_strike=175.0,
               close_exp="2026-07-31", open_exp="2026-08-07"):
    """A 2-leg roll_short: buy-to-close current short + sell-to-open new short."""
    return [
        _leg("buy", "close", close_strike, close_exp,
             action="roll_short_call_close", limit=1.20),
        _leg("sell", "open", open_strike, open_exp,
             action="roll_short_call_open", limit=1.80),
    ]


class _QBroker:
    """Mock broker: get_option_quote keyed by strike; quote() returns spot."""
    def __init__(self, quotes: dict, spot: float = 150.0):
        self._q = quotes
        self._spot = spot

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        return self._q.get(round(float(strike), 2))

    async def quote(self, symbol):
        return self._spot


# --------------------------------------------------------------------------
# B — estimate_roll_from_quotes + consent-integrity lock to reprice
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_estimate_debit_credit_net_and_attribution():
    legs = _roll_legs()
    broker = _QBroker({
        170.0: {"bid": 1.10, "ask": 1.20, "mark": 1.15},   # close (buy) → debit=ask=1.20
        175.0: {"bid": 1.80, "ask": 1.95, "mark": 1.88},   # open  (sell) → credit=bid=1.80
    })
    est = await estimate_roll_from_quotes(legs, broker)
    assert est["debit"] == 1.20                     # buy-to-close pays the ask
    assert est["credit"] == 1.80                    # sell-to-open collects the bid
    assert est["net"] == 0.60                        # credit − debit
    assert est["net_abs"] == 0.60
    assert est["direction"] == "credit"
    # attributable: the number is tied to the ACTUAL selected strike/expiry
    assert est["open_strike"] == 175.0 and est["open_expiration"] == "2026-08-07"
    assert est["close_strike"] == 170.0 and est["close_expiration"] == "2026-07-31"


@pytest.mark.asyncio
async def test_estimate_net_matches_dispatch_reprice_natural():
    """CONSENT INTEGRITY: the card's net is the SAME natural the dispatch reprice
    derives the placed limit from — NOT a divergent calc. reprice's limit is the
    card net minus give_up, tick-rounded."""
    give_up = 0.02
    quotes = {
        170.0: {"bid": 1.10, "ask": 1.20, "mark": 1.15},
        175.0: {"bid": 1.80, "ask": 1.95, "mark": 1.88},
    }
    est = await estimate_roll_from_quotes(_roll_legs(), _QBroker(quotes))
    # fresh legs for reprice (it mutates); same broker/quotes
    direction, limit = await reprice_combo_from_quotes(
        _roll_legs(), _QBroker(quotes), give_up=give_up)
    assert direction == est["direction"] == "credit"
    expected_limit = round(round((est["net"] - give_up) / 0.01) * 0.01, 2)
    assert limit == expected_limit == 0.58     # 0.60 natural − 0.02 give_up


@pytest.mark.asyncio
async def test_estimate_none_when_quote_missing():
    legs = _roll_legs()
    broker = _QBroker({170.0: {"bid": 1.10, "ask": 1.20}})   # open-leg quote absent
    assert await estimate_roll_from_quotes(legs, broker) is None


@pytest.mark.asyncio
async def test_estimate_does_not_mutate_legs():
    legs = _roll_legs()
    before = [(o.extra or {}).get("net_limit_price") for o in legs]
    await estimate_roll_from_quotes(legs, _QBroker({
        170.0: {"bid": 1.1, "ask": 1.2}, 175.0: {"bid": 1.8, "ask": 1.95}}))
    after = [(o.extra or {}).get("net_limit_price") for o in legs]
    assert before == after   # the pending combo must reach dispatch untouched


# --------------------------------------------------------------------------
# A — PMCCAgent.earnings_card_state (off the same gate as the backend)
# --------------------------------------------------------------------------

@pytest.fixture
def pmcc_agent(tmp_path):
    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    s = tmp_path / "strategies.yaml"
    s.write_text(
        "robinhood_pmcc:\n  enabled: true\n  underlying_criteria:\n"
        "    earnings_buffer_days: 7\n", encoding="utf-8")
    r = tmp_path / "risk.yaml"
    r.write_text("global: {}\npmcc: {}\noverrides: {}\n", encoding="utf-8")
    return PMCCAgent(strategies_yaml=s, risk_yaml=r)


def _patch_sources(monkeypatch, broker, feed):
    monkeypatch.setattr(_md_mod, "get_broker_earnings", lambda s, *a, **k: broker)
    monkeypatch.setattr(_md_mod, "get_next_earnings", lambda s, *a, **k: feed)


def test_card_state_blocked_hides_roll_and_recommends(pmcc_agent, monkeypatch):
    _patch_sources(monkeypatch, (_dt(1), True), None)   # broker verified 1d out
    st = pmcc_agent.earnings_card_state("HOOD")
    assert st["kind"] == "blocked"
    assert st["offer_roll"] is False
    assert st["verified"] is True
    assert "let the current short call expire" in st["recommendation"]
    assert st["date"] is not None


def test_card_state_unverified_keeps_roll_with_flag(pmcc_agent, monkeypatch):
    _patch_sources(monkeypatch, (None, False), None)    # neither source
    st = pmcc_agent.earnings_card_state("RIOT")
    assert st["kind"] == "unverified"
    assert st["offer_roll"] is True
    assert "unverified" in st["flag"]


def test_card_state_clear_is_rollable(pmcc_agent, monkeypatch):
    _patch_sources(monkeypatch, (_dt(60), True), None)  # 60d out
    st = pmcc_agent.earnings_card_state("RIOT")
    assert st["kind"] == "clear"
    assert st["offer_roll"] is True


def test_card_state_itm_edge_surfaces_assignment_caveat(pmcc_agent, monkeypatch):
    _patch_sources(monkeypatch, (_dt(1), True), None)
    st = pmcc_agent.earnings_card_state("HOOD", short_strike=100.0, spot=110.0)
    assert st["kind"] == "blocked"
    assert st["caveat"] is not None and "assignment" in st["caveat"]
    # default is NOT overridden — still recommends let it expire
    assert "let the current short call expire" in st["recommendation"]


# --------------------------------------------------------------------------
# orchestrator — build_pmcc_roll_card_extras
# --------------------------------------------------------------------------

class _StubAgent:
    def __init__(self, earnings):
        self._e = earnings

    def earnings_card_state(self, symbol, short_strike=None, spot=None):
        return self._e


class _Entry:
    def __init__(self, orders):
        self.orders = orders
        self.underlying = "AAPL"


@pytest.mark.asyncio
async def test_extras_blocked_skips_estimate():
    entry = _Entry(_roll_legs())
    broker = _QBroker({170.0: {"bid": 1.1, "ask": 1.2}, 175.0: {"bid": 1.8, "ask": 1.95}})
    agent = _StubAgent({"kind": "blocked", "offer_roll": False, "recommendation": "x",
                        "date": "2026-08-05", "verified": True, "flag": None,
                        "caveat": None, "source": "broker"})
    out = await build_pmcc_roll_card_extras(entry, broker, agent)
    assert out["earnings"]["kind"] == "blocked"
    assert out["estimate"] is None            # no estimate on a blocked card
    assert out["estimate_reason"] is None


@pytest.mark.asyncio
async def test_extras_clear_renders_estimate():
    entry = _Entry(_roll_legs())
    broker = _QBroker({170.0: {"bid": 1.1, "ask": 1.2}, 175.0: {"bid": 1.8, "ask": 1.95}})
    agent = _StubAgent({"kind": "clear", "offer_roll": True, "recommendation": None,
                        "date": None, "verified": False, "flag": None, "caveat": None,
                        "source": None})
    out = await build_pmcc_roll_card_extras(entry, broker, agent)
    assert out["estimate"] is not None
    assert out["estimate"]["net"] == 0.60
    assert out["estimate_reason"] is None


@pytest.mark.asyncio
async def test_extras_unbuildable_shows_reason_no_estimate():
    entry = _Entry(_roll_legs())
    broker = _QBroker({170.0: {"bid": 1.1, "ask": 1.2}})   # open quote missing
    agent = _StubAgent({"kind": "clear", "offer_roll": True, "recommendation": None,
                        "date": None, "verified": False, "flag": None, "caveat": None,
                        "source": None})
    out = await build_pmcc_roll_card_extras(entry, broker, agent)
    assert out["estimate"] is None
    assert out["estimate_reason"] and "no order sent" in out["estimate_reason"]
