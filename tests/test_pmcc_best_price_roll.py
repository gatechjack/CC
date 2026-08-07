"""PMCC best-price roll — FIX 1/2/3 proofs on the 2026-08-07 LIVE TSLA + SMR chains.

Confirmed empirically (verbatim-prod fns on live RH quotes; see
reports/2026-08-07_pmcc_net_debit_reproduction/): the old worst-case gate wrongly
BLOCKED rolls that fill as credits at MID, and the nearest-delta picker landed on
marginal strikes. These tests pin the NEW behaviour:
  FIX 1  gate + combo tag + dispatch reprice + card estimate price on MID − give_up.
  FIX 2  the roll picker takes the BEST MID net within the δ window (TSLA $330 not
         $335; SMR $10.50, not the thin $11.00).
  FIX 3  the credit rule is ADVISORY — a debit roll BUILDS and is presented as a
         labeled DEBIT for HITL; the consent guard aborts a debit that reprices
         WORSE than approved (never fill a −$0.25 approval at −$1.00).

No real API/LLM calls; chains are the live fixtures pulled 2026-08-07 ~10:03-10:09 ET.
"""
from __future__ import annotations

import asyncio
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent,
    PMCCPosition,
    _select_best_net_weekly,
)
from trading_corp.agents.strategies._pmcc_combo import (
    assess_combo_reprice_consent,
    reprice_combo_from_quotes,
)
from trading_corp.persistence.models import ProposedOrder


def _c(strike, bid, ask, mark, delta, oi, vol):
    return {"strike_price": strike, "bid": bid, "ask": ask, "mark_price": mark,
            "delta": delta, "open_interest": oi, "volume": vol,
            "option_id": f"c_{strike}"}


# --- LIVE fixtures (2026-08-07) -------------------------------------------------
# TSLA spot 325.04; current short $322.50C 0-DTE ITM (buyback mark 3.575). New wk 08-14.
TSLA_0814 = [
    _c(322.5, 8.40, 8.55, 8.475, 0.560, 2288, 1025),
    _c(325.0, 7.15, 7.30, 7.225, 0.506, 7973, 4027),
    _c(330.0, 5.10, 5.20, 5.150, 0.401, 3108, 6043),   # <- best-net in-window pick
    _c(335.0, 3.55, 3.65, 3.600, 0.307, 2947, 5647),   # <- old nearest-delta (marginal)
    _c(340.0, 2.45, 2.49, 2.470, 0.228, 7276, 2281),   # <- deep-OTM escape (debit)
    _c(345.0, 1.68, 1.71, 1.695, 0.167, 1357, 4011),
    _c(350.0, 1.16, 1.18, 1.170, 0.121, 7627, 7800),
]
TSLA_BUYBACK = 3.575

# SMR spot 9.67; current short $10.00C 08-14 (buyback mark 0.345). New wk 08-21.
SMR_0821 = [
    _c(9.5, 0.70, 0.79, 0.745, 0.560, 1468, 43),
    _c(10.0, 0.50, 0.53, 0.515, 0.446, 7276, 133),
    _c(10.5, 0.34, 0.39, 0.365, 0.346, 876, 124),      # <- best-net in-window pick
    _c(11.0, 0.24, 0.26, 0.250, 0.258, 5562, 19),      # <- thin far-OTM (excluded)
    _c(11.5, 0.16, 0.21, 0.185, 0.201, 1341, 22),
]
SMR_BUYBACK = 0.345


# ===========================================================================
# FIX 2 — pure best-net selection near the δ target.
# ===========================================================================

def test_best_net_tsla_picks_330_not_335():
    """TSLA required outcome: $330 (δ0.401, mid net +1.575) beats the nearest-δ $335
    (δ0.307, +0.025). Both are in the [0.28,0.42] window; best-net takes $330."""
    best = _select_best_net_weekly(TSLA_0814, TSLA_BUYBACK)
    assert best is not None
    assert best["strike_price"] == 330.0
    assert best["strike_price"] != 335.0


def test_best_net_smr_picks_10_5_not_thin_11():
    """SMR required outcome: $10.50 (δ0.346, the only in-window strike) — NOT the thin
    far-OTM $11.00 (δ0.258, a mid DEBIT −0.095) the plain δ0.30 picker would grab."""
    best = _select_best_net_weekly(SMR_0821, SMR_BUYBACK)
    assert best is not None
    assert best["strike_price"] == 10.5
    assert best["strike_price"] != 11.0


def test_best_net_open_path_falls_back_to_nearest_delta():
    """close_buyback None (OPEN path — nothing to net against) → nearest-delta, not
    best-net, so an open still builds."""
    best = _select_best_net_weekly(SMR_0821, None, target_delta=0.35)
    assert best is not None
    assert best["strike_price"] == 10.5     # δ0.346 nearest 0.35


def test_best_net_empty_window_falls_back_to_nearest_delta():
    """No candidate in [0.28,0.42] → fall back to nearest-delta so a build always
    happens (never abort on an empty window)."""
    chain = [_c(50.0, 5.0, 5.2, 5.10, 0.60, 1000, 1000),
             _c(60.0, 0.10, 0.12, 0.11, 0.10, 1000, 1000)]
    best = _select_best_net_weekly(chain, 2.0, target_delta=0.35)
    assert best is not None                 # nearest-delta fallback (0.60 nearest 0.35)


# ===========================================================================
# FIX 1 + 3 — e2e: SMR/TSLA mid-credit BUILDS; a genuine debit BUILDS labeled.
# ===========================================================================

@pytest.fixture
def agent(tmp_path: Path) -> PMCCAgent:
    s = tmp_path / "strategies.yaml"
    s.write_text(
        "robinhood_pmcc:\n"
        "  enabled: true\n"
        "  auto_execute: false\n"
        "  universe_source: positions\n"
        "  underlying_criteria:\n"
        "    earnings_buffer_days: 0\n",       # skip the earnings gate's network call
        encoding="utf-8",
    )
    r = tmp_path / "risk.yaml"
    r.write_text("pmcc:\n  short_call_target_delta: 0.30\n", encoding="utf-8")
    return PMCCAgent(strategies_yaml=s, risk_yaml=r)


class _FakeChainBroker:
    """Structural OptionBroker: a roll-out chain + a fresh buyback quote. No orders."""
    name = "fake"
    paper = True

    def __init__(self, chain, roll_exp, cur_short_exp, cur_strike, buyback):
        self._chain = chain
        self._roll_exp = roll_exp
        self._cur_short_exp = cur_short_exp
        self._cur_strike = cur_strike
        self._buyback = buyback

    async def get_option_positions_detail(self):
        return []

    async def get_expiration_dates(self, symbol):
        t = date.today()
        return [(t + timedelta(days=d)).isoformat() for d in (1, 7, 14, 21)]

    async def get_calls_for_expiry(self, symbol, expiry):
        return self._chain if expiry == self._roll_exp else []

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        if abs(float(strike) - self._cur_strike) < 1e-9 and expiration == self._cur_short_exp:
            return dict(self._buyback)
        return {"bid": None, "ask": None, "mark_price": None}


def _leg_pos(symbol, cur_short_exp, strike, mark, leap_strike):
    return PMCCPosition(
        symbol=symbol,
        long_leg_expiry=(date.today() + timedelta(days=500)).isoformat(),
        long_leg_strike=leap_strike, long_leg_delta=0.80, long_leg_dte=500,
        long_leg_qty=1, long_leg_avg_price=99.0, long_leg_symbol=f"{symbol} LEAP",
        long_leg_mark=50.0,
        short_leg_expiry=cur_short_exp, short_leg_strike=strike, short_leg_dte=7,
        short_leg_pnl_pct=0.05, short_leg_qty=-1, short_leg_mark=mark,
        short_leg_avg_price=mark, short_leg_symbol=f"{symbol} C{strike}",
    )


def _an(target_delta=0.35, target_dte=7):
    return types.SimpleNamespace(
        action="roll_short", target_delta=target_delta, target_dte=target_dte,
        target_strike=None, target_delta_low=None, target_delta_high=None, override=None,
        confidence=0.8, urgency="attention", summary="roll", rationale="roll",
        warnings=[], format_rich=lambda: "(expert)",
    )


def _run_roll(agent, symbol, chain, cur_strike, buyback, leap_strike, cur_mark):
    t = date.today()
    cur = (t + timedelta(days=7)).isoformat()
    roll = (t + timedelta(days=14)).isoformat()
    broker = _FakeChainBroker(chain, roll, cur, cur_strike, buyback)
    leg = _leg_pos(symbol, cur, cur_strike, cur_mark, leap_strike)
    return asyncio.run(
        agent._propose_roll_short(symbol, leg, broker, analysis=_an(), preview=True))


def test_e2e_smr_mid_credit_builds_as_credit(agent: PMCCAgent):
    """SMR: the exact live case the old gate BLOCKED. mid_net = 0.365 − 0.345 = +0.02
    (a credit at mid); the roll now BUILDS on $10.50 and is tagged a CREDIT (was blocked
    on the worst-case bid/ask/give_up basis)."""
    orders = _run_roll(agent, "SMR", SMR_0821, 10.0,
                       {"bid": 0.32, "ask": 0.37, "mark_price": 0.345}, 10.0, 0.345)
    assert len(orders) == 2                       # BUILDS (not blocked)
    sell = next(o for o in orders if o.side == "sell")
    assert float((sell.extra or {}).get("strike")) == 10.5
    assert (sell.extra or {}).get("combo_direction") == "credit"
    ab = agent._last_roll_abort
    assert ab is None or ab.get("reason") != "net_debit_roll"


def test_e2e_tsla_builds_credit_on_330_not_marginal_335(agent: PMCCAgent):
    """TSLA: the 0-DTE ITM short rolls to $330 for a CLEAR mid credit (mid_net 5.150 −
    3.575 = +1.575; − give_up 0.02 = +1.555 → net_limit 1.55/1.56), NOT the marginal
    $335 (+0.025). Presented as a CREDIT."""
    orders = _run_roll(agent, "TSLA", TSLA_0814, 322.5,
                       {"bid": 3.50, "ask": 3.65, "mark_price": 3.575}, 310.0, 3.575)
    assert len(orders) == 2
    sell = next(o for o in orders if o.side == "sell")
    assert float((sell.extra or {}).get("strike")) == 330.0
    assert (sell.extra or {}).get("combo_direction") == "credit"
    assert (sell.extra or {}).get("net_limit_price") >= 1.5   # clear credit, not ~0
    ab = agent._last_roll_abort
    assert ab is None or ab.get("reason") != "net_debit_roll"


def test_e2e_tsla_deep_otm_debit_builds_and_labels_debit(agent: PMCCAgent):
    """FIX 3: a GENUINE debit roll BUILDS and is presented as a labeled DEBIT for HITL.
    Buyback mark 6.00 is above every in-window strike's mid, so best-net picks the
    least-debit $330 (mid 5.150): mid_net = 5.150 − 6.00 = −0.85; − give_up 0.02 →
    a $0.87 DEBIT. Not blocked."""
    orders = _run_roll(agent, "TSLA", TSLA_0814, 322.5,
                       {"bid": 5.90, "ask": 6.10, "mark_price": 6.00}, 310.0, 6.00)
    assert len(orders) == 2                       # BUILDS (not blocked)
    sell = next(o for o in orders if o.side == "sell")
    assert (sell.extra or {}).get("combo_direction") == "debit"
    assert (sell.extra or {}).get("net_limit_price") > 0     # a bounded debit magnitude
    ab = agent._last_roll_abort
    assert ab is None or ab.get("reason") != "net_debit_roll"


# ===========================================================================
# FIX 1 dispatch — reprice places a credit/debit LIMIT (never worse than tagged).
# ===========================================================================

class _FakeQuoteBroker:
    """get_option_quote keyed by strike → {bid, ask, mark_price}."""
    def __init__(self, by_strike):
        self._by = by_strike

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        return dict(self._by[float(strike)])


def _combo_leg(side, strike, *, combo_id="cc", direction="credit", net=0.0):
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol=f"X C{strike}", side=side, qty=1,
        order_type="limit",
        extra={
            "underlying": "X", "option_type": "call", "expiration": "2026-08-21",
            "strike": strike, "position_effect": "close" if side == "buy" else "open",
            "is_multi_leg": True, "combo_id": combo_id, "combo_direction": direction,
            "net_limit_price": net, "ratio_quantity": 1,
        },
    )


def test_reprice_credit_roll_places_credit_limit():
    """A credit roll reprices to a CREDIT net-limit (fills only at that credit or better —
    never a debit). TSLA $330 vs $322.5 buyback: MID net 5.15 − 3.575 = 1.575 credit."""
    legs = [_combo_leg("buy", 322.5), _combo_leg("sell", 330.0)]
    broker = _FakeQuoteBroker({322.5: {"bid": 3.50, "ask": 3.65, "mark_price": 3.575},
                               330.0: {"bid": 5.10, "ask": 5.20, "mark_price": 5.15}})
    direction, limit = asyncio.run(reprice_combo_from_quotes(legs, broker, give_up=0.02))
    assert direction == "credit"
    assert limit > 0


def test_reprice_debit_roll_places_bounded_debit_limit():
    """A genuine debit roll reprices to a DEBIT net-limit (fills only at that debit or
    better). Buyback mark 6.00 vs $330 mid 5.15: MID net −0.85 → debit ~0.87."""
    legs = [_combo_leg("buy", 322.5, direction="debit"),
            _combo_leg("sell", 330.0, direction="debit")]
    broker = _FakeQuoteBroker({322.5: {"bid": 5.90, "ask": 6.10, "mark_price": 6.00},
                               330.0: {"bid": 5.10, "ask": 5.20, "mark_price": 5.15}})
    direction, limit = asyncio.run(reprice_combo_from_quotes(legs, broker, give_up=0.02))
    assert direction == "debit"
    assert limit > 0


# ===========================================================================
# FIX 3c — consent guard aborts a DEBIT that reprices WORSE than approved.
# ===========================================================================

def _snap(direction, net):
    return {"direction": direction, "net_limit_price": net,
            "strikes": {}}   # empty strikes → strike-drift guard is a no-op here


def test_consent_debit_worsened_aborts():
    """Approved a $0.25 debit; dispatch reprices to a $1.00 debit → ABORT (don't fill a
    roll at 4x the consented cost)."""
    legs = [_combo_leg("buy", 322.5, direction="debit", net=1.00),
            _combo_leg("sell", 330.0, direction="debit", net=1.00)]
    ok, why = assess_combo_reprice_consent(
        legs, _snap("debit", 0.25), max_adverse_net_deviation=0.05)
    assert ok is False and "debit worsened" in why


def test_consent_debit_improved_allows():
    """Approved a $0.25 debit; dispatch reprices to a $0.10 debit (cheaper) → ALLOW."""
    legs = [_combo_leg("buy", 322.5, direction="debit", net=0.10),
            _combo_leg("sell", 330.0, direction="debit", net=0.10)]
    ok, _why = assess_combo_reprice_consent(
        legs, _snap("debit", 0.25), max_adverse_net_deviation=0.05)
    assert ok is True


def test_consent_debit_to_credit_allows():
    """Approved a debit; dispatch reprices to a CREDIT (strictly better) → ALLOW."""
    legs = [_combo_leg("buy", 322.5, direction="credit", net=0.30),
            _combo_leg("sell", 330.0, direction="credit", net=0.30)]
    ok, _why = assess_combo_reprice_consent(
        legs, _snap("debit", 0.25), max_adverse_net_deviation=0.05)
    assert ok is True


def test_consent_credit_to_debit_still_aborts():
    """Regression: a CREDIT approval repriced to a DEBIT still ABORTS (sign flip)."""
    legs = [_combo_leg("buy", 322.5, direction="debit", net=0.20),
            _combo_leg("sell", 330.0, direction="debit", net=0.20)]
    ok, why = assess_combo_reprice_consent(
        legs, _snap("credit", 0.30), max_adverse_net_deviation=0.05)
    assert ok is False and "DEBIT" in why
