"""PMCC spread-gate + selection + credit-basis fix (2026-08-06).

Two compounding bugs, confirmed by a live mid-session reproduction (RIOT 15:17 ET):
  FIX 1  `_passes_liquidity` no longer rejects on raw bid/ask SPREAD WIDTH — the
         operator fills at MID ~100%, so a wide-but-two-sided market on an OI-liquid
         strike is tradeable. The width gate was rejecting RIOT's whole on-target
         delta chain (12-22% spreads) and silently substituting a far-OTM low-bid
         strike ($25.0, δ0.29) → false "net debit". The two-sided-market floor
         (no bid / no ask / inverted) + Liveness (OI/vol) are PRESERVED.
  FIX 2  the roll credit gate evaluates a SAME-TIMESTAMP MID net (new mark − fresh
         buyback mark), not fresh-new-bid − stale-scan-mark.
  FIX 3  `_select_weekly_strike` clamps to the nearest strike by delta (never lets
         the <0.40 OTM cutoff exclude a strictly-closer strike / abort on an empty
         band) — the OPEN open_short coarse-spacing case.

Fixture chains are the ACTUAL Robinhood live quotes pulled 2026-08-06 ~15:17-15:50 ET
(see reports/2026-08-06_pmcc_riot_false_debit_block/). No real API/LLM calls.
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
    _select_weekly_strike,
    _short_roll_credit,
)


# ---------------------------------------------------------------------------
# LIVE fixtures (RIOT spot 21.495; OPEN spot 3.445) — bid/ask/mark/delta/OI/vol.
# ---------------------------------------------------------------------------
def _c(strike, bid, ask, mark, delta, oi, vol):
    return {"strike_price": strike, "bid": bid, "ask": ask, "mark_price": mark,
            "delta": delta, "open_interest": oi, "volume": vol,
            "option_id": f"c_{strike}"}

# RIOT 2026-08-21 (roll-out expiry for the $23.5 8/14 short); spreads 11-30%.
RIOT_821 = [
    _c(21.5, 1.81, 2.05, 1.930, 0.550, 687, 10),
    _c(22.0, 1.57, 1.85, 1.710, 0.509, 2102, 30),
    _c(22.5, 1.40, 1.62, 1.510, 0.468, 1224, 136),
    _c(23.0, 1.29, 1.44, 1.365, 0.432, 5911, 410),
    _c(23.5, 1.04, 1.30, 1.170, 0.392, 1348, 2),
    _c(24.0, 0.91, 1.13, 1.020, 0.355, 6475, 2439),   # <- the on-target δ0.35 strike
    _c(24.5, 0.86, 0.97, 0.915, 0.326, 1515, 65),
    _c(25.0, 0.75, 0.82, 0.785, 0.292, 35802, 1207),  # <- the old (wrong) substitution
    _c(25.5, 0.64, 0.87, 0.755, 0.275, 2334, 15),
    _c(26.0, 0.53, 0.69, 0.610, 0.238, 3358, 10),
    _c(26.5, 0.47, 0.64, 0.555, 0.219, 63, 0),         # <- OI 63/vol 0 → liveness drops it
]

# OPEN 2026-08-14 (open_short on an uncovered LEAP); coarse $0.50 spacing.
OPEN_814 = [
    _c(2.5, 0.67, 1.29, 0.980, 0.923, 6, 5),           # OI 6/vol 5 → liveness drops it
    _c(3.0, 0.25, 0.71, 0.480, 0.867, 333, 184),
    _c(3.5, 0.12, 0.13, 0.125, 0.469, 2475, 2856),     # <- nearest to band-mid 0.375
    _c(4.0, 0.03, 0.04, 0.035, 0.164, 6432, 1963),     # <- old (wrong) pick under <0.40 cutoff
    _c(4.5, 0.01, 0.02, 0.015, 0.078, 4170, 746),
    _c(5.0, 0.00, 0.01, 0.005, 0.039, 10150, 510),     # <- BID 0 → no-bid, dropped
]


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


# ===========================================================================
# FIX 1 — the on-target chain is now liquid; liveness / two-sided floor preserved.
# ===========================================================================

def test_riot_on_target_passes_liquidity_after_fix1(agent: PMCCAgent):
    liquid = agent._filter_liquid(RIOT_821, "RIOT")
    strikes = {o["strike_price"] for o in liquid}
    assert 24.0 in strikes        # 21.6% spread — REJECTED before Fix 1, now PASSES
    assert 23.5 in strikes        # 22.2% spread — passes
    assert 25.0 in strikes        # tight strike still passes
    assert 26.5 not in strikes    # OI 63 AND vol 0 → liveness still drops it


def test_riot_picker_stays_on_target_no_substitution(agent: PMCCAgent):
    """THE PRIMARY FIX: with the on-target chain liquid, the δ0.35 picker STAYS on
    $24.0 (δ0.355) — it is NOT substituted onto the far-OTM $25.0 (δ0.29)."""
    liquid = agent._filter_liquid(RIOT_821, "RIOT")
    best = _select_weekly_strike(liquid, 0.35)
    assert best is not None
    assert best["strike_price"] == 24.0
    assert best["strike_price"] != 25.0


def test_riot_credit_gate_mid_is_a_credit(agent: PMCCAgent):
    """The on-target $24.0 vs the fresh $23.5-8/14 buyback mid (0.75): mid net is a
    real credit (+0.27), and even the bid-based conservative net (+0.16) clears."""
    new = next(o for o in RIOT_821 if o["strike_price"] == 24.0)
    cons, mid, open_bid = _short_roll_credit(new, 0.75)   # fresh buyback mid
    assert round(mid, 2) == 0.27 and mid > 0              # mid credit → gate clears
    assert round(cons, 2) == 0.16 and cons > 0
    assert open_bid == 0.91


# ===========================================================================
# FIX 3 — coarse-spacing / empty-band clamp to nearest delta (OPEN open_short).
# ===========================================================================

def test_open_empty_band_clamps_to_nearest_sellable(agent: PMCCAgent):
    """OPEN open_short: no strike lands in the δ0.30-0.45 band ($3.5=0.469 just above,
    $4.0=0.164 below). The picker CLAMPS to the nearest-by-delta $3.5 (NOT the far-OTM
    $4.0 the old <0.40 cutoff forced), and $3.5 has a real bid → sellable."""
    liquid = agent._filter_liquid(OPEN_814, "OPEN")
    strikes = {o["strike_price"] for o in liquid}
    assert 5.0 not in strikes and 2.5 not in strikes     # no-bid / liveness dropped
    assert {3.0, 3.5, 4.0, 4.5} <= strikes               # wide-but-two-sided all pass
    best = _select_weekly_strike(
        liquid, 0.35, target_delta_low=0.30, target_delta_high=0.45)
    assert best is not None
    assert best["strike_price"] == 3.5                   # nearest to band-mid; NOT 4.0
    assert best["bid"] > 0                               # sells for the mid credit


def test_point_target_clamps_over_otm_cutoff(agent: PMCCAgent):
    """Point-target variant: a δ0.47 strike next to a δ0.16 with target 0.375 clamps
    to 0.47 (strictly nearer) rather than the <0.40 OTM pick."""
    chain = [_c(3.5, 0.12, 0.13, 0.125, 0.469, 2475, 2856),
             _c(4.0, 0.03, 0.04, 0.035, 0.164, 6432, 1963)]
    best = _select_weekly_strike(chain, 0.375)
    assert best["strike_price"] == 3.5


# ===========================================================================
# NO SILENT SUBSTITUTION + regression floors.
# ===========================================================================

def test_all_on_target_untradeable_yields_no_liquid_not_substitution(agent: PMCCAgent):
    """If the whole on-target chain is GENUINELY untradeable (no bid), `_filter_liquid`
    returns [] → `_find_best_weekly` returns None → the caller aborts with a sparse-
    chain reason. It does NOT substitute a far strike and does NOT report a net debit."""
    chain = [_c(24.0, 0.0, 0.10, 0.05, 0.355, 9000, 9000),
             _c(24.5, 0.0, 0.08, 0.04, 0.326, 9000, 9000)]
    assert agent._filter_liquid(chain, "X") == []


def test_no_bid_no_ask_zero_oi_still_rejected(agent: PMCCAgent):
    assert not agent._passes_liquidity(_c(24, 0.0, 0.10, 0.05, 0.35, 9000, 9000))[0]   # no bid
    assert not agent._passes_liquidity(_c(24, 0.50, 0.0, 0.25, 0.35, 9000, 9000))[0]   # no ask
    assert not agent._passes_liquidity(_c(24, 0.50, 0.60, 0.55, 0.35, 5, 5))[0]        # liveness


def test_genuine_mid_debit_still_blocks():
    """A REAL mid-to-mid debit (buy back a 0.80-mark short, sell a 0.50-mark new short)
    still yields mid_net < 0 → the gate still blocks. The credit rule is preserved."""
    new = _c(30.0, 0.45, 0.55, 0.50, 0.25, 1000, 1000)
    _cons, mid, _ob = _short_roll_credit(new, 0.80)      # fresh buyback mid 0.80
    assert mid < 0


# ===========================================================================
# END-TO-END — `_propose_roll_short` builds the credit roll on $24 (no substitution).
# ===========================================================================

class _FakeOptBroker:
    """Structural OptionBroker (runtime_checkable Protocol) — RIOT roll-out chain +
    a fresh buyback quote. Never places an order."""
    name = "fake"
    paper = True

    def __init__(self, roll_exp: str, cur_short_exp: str):
        self._roll_exp = roll_exp
        self._cur_short_exp = cur_short_exp

    async def get_option_positions_detail(self):
        return []

    async def get_expiration_dates(self, symbol):
        today = date.today()
        return [(today + timedelta(days=d)).isoformat() for d in (1, 8, 15, 22)]

    async def get_calls_for_expiry(self, symbol, expiry):
        return RIOT_821 if expiry == self._roll_exp else []

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        # Fresh build-time buyback quote for the current $23.5 8/14 short.
        if abs(float(strike) - 23.5) < 1e-9 and expiration == self._cur_short_exp:
            return {"bid": 0.66, "ask": 0.84, "mark_price": 0.75}
        return {"bid": None, "ask": None, "mark_price": None}


def _riot_leg(cur_short_exp: str) -> PMCCPosition:
    return PMCCPosition(
        symbol="RIOT",
        long_leg_expiry=(date.today() + timedelta(days=400)).isoformat(),
        long_leg_strike=15.0, long_leg_delta=0.85, long_leg_dte=400,
        long_leg_qty=4, long_leg_avg_price=8.0, long_leg_symbol="RIOT LEAP C 15.00",
        long_leg_mark=7.0,
        short_leg_expiry=cur_short_exp, short_leg_strike=23.5, short_leg_dte=8,
        short_leg_pnl_pct=0.05, short_leg_qty=-4, short_leg_mark=0.75,
        short_leg_avg_price=0.79, short_leg_symbol="RIOT C 23.50",
    )


def _analysis():
    return types.SimpleNamespace(
        action="roll_short", target_delta=0.35, target_dte=7, target_strike=None,
        target_delta_low=None, target_delta_high=None, override=None,
        confidence=0.8, urgency="attention", summary="roll", rationale="roll",
        warnings=[], format_rich=lambda: "(expert commentary)",
    )


def test_e2e_riot_roll_builds_credit_on_target(agent: PMCCAgent):
    cur_short_exp = (date.today() + timedelta(days=8)).isoformat()
    roll_exp = (date.today() + timedelta(days=15)).isoformat()
    broker = _FakeOptBroker(roll_exp=roll_exp, cur_short_exp=cur_short_exp)
    leg = _riot_leg(cur_short_exp)

    orders = asyncio.run(
        agent._propose_roll_short("RIOT", leg, broker, analysis=_analysis(), preview=True))

    # Built a 2-leg roll (credit NOT blocked) …
    assert len(orders) == 2
    sells = [o for o in orders if o.side == "sell"]
    buys = [o for o in orders if o.side == "buy"]
    assert len(sells) == 1 and len(buys) == 1
    # … on the ON-TARGET $24.0 8/21 strike — NOT the far-OTM $25.0 substitution.
    assert float((sells[0].extra or {}).get("strike")) == 24.0
    # … tagged on the DISPATCH basis (natural − give_up = 0.91 − 0.84 − 0.02 = 0.05),
    # so the operator-approved snapshot matches what the live reprice fires on.
    assert (sells[0].extra or {}).get("combo_direction") == "credit"
    assert (sells[0].extra or {}).get("net_limit_price") == 0.05
    # … and no net-debit abort was stashed.
    ab = agent._last_roll_abort
    assert ab is None or ab.get("reason") != "net_debit_roll"


def test_e2e_riot_genuine_debit_blocks_with_high_buyback(agent: PMCCAgent):
    """Same chain, but the fresh buyback mark is read HIGH (1.10 > every on-target
    strike's mid) → the mid net IS a debit → the gate STILL blocks (credit rule kept)."""
    cur_short_exp = (date.today() + timedelta(days=8)).isoformat()
    roll_exp = (date.today() + timedelta(days=15)).isoformat()

    class _HighBuyback(_FakeOptBroker):
        async def get_option_quote(self, symbol, expiration, strike, option_type):
            if abs(float(strike) - 23.5) < 1e-9 and expiration == self._cur_short_exp:
                return {"bid": 1.00, "ask": 1.20, "mark_price": 1.10}
            return {"bid": None, "ask": None, "mark_price": None}

    broker = _HighBuyback(roll_exp=roll_exp, cur_short_exp=cur_short_exp)
    leg = _riot_leg(cur_short_exp)
    orders = asyncio.run(
        agent._propose_roll_short("RIOT", leg, broker, analysis=_analysis(), preview=True))
    assert orders == []                                   # blocked
    assert agent._last_roll_abort is not None
    assert agent._last_roll_abort.get("reason") == "net_debit_roll"
    assert agent._last_roll_abort.get("dispatch_net") < 0


def test_e2e_dispatch_alignment_blocks_mid_credit_but_dispatch_debit(agent: PMCCAgent):
    """PART A item 2: a roll that is a CREDIT at mid but a DEBIT at the dispatch basis
    (natural − give_up) is now blocked AT PROPOSAL — no 'approved then aborted at
    dispatch' on the give_up margin. Buyback fresh bid 0.85 / ask 1.10 / mark 0.95 vs
    new $24 (bid 0.91, mark 1.02): mid_net = +0.07 (credit) but
    dispatch_net = 0.91 − 1.10 − 0.02 = −0.21 (debit) → BLOCKED."""
    cur_short_exp = (date.today() + timedelta(days=8)).isoformat()
    roll_exp = (date.today() + timedelta(days=15)).isoformat()

    class _MarginBuyback(_FakeOptBroker):
        async def get_option_quote(self, symbol, expiration, strike, option_type):
            if abs(float(strike) - 23.5) < 1e-9 and expiration == self._cur_short_exp:
                return {"bid": 0.85, "ask": 1.10, "mark_price": 0.95}
            return {"bid": None, "ask": None, "mark_price": None}

    broker = _MarginBuyback(roll_exp=roll_exp, cur_short_exp=cur_short_exp)
    orders = asyncio.run(agent._propose_roll_short(
        "RIOT", _riot_leg(cur_short_exp), broker, analysis=_analysis(), preview=True))
    assert orders == []
    ab = agent._last_roll_abort
    assert ab is not None and ab.get("reason") == "net_debit_roll"
    assert ab.get("mid_net") > 0                 # would have cleared on mid …
    assert ab.get("dispatch_net") < 0            # … but blocked on the dispatch basis


def test_roll_leap_gate_uses_mid_not_bid_basis():
    """PART A item 1: the roll_leap short-leg gate now decides on the MID net
    (`_short_roll_credit` mark_net, fresh buyback) — not the bid-based conservative.
    Discriminating case (buyback fresh mid 0.95 vs new $24 bid 0.91 / mark 1.02):
      conservative (bid) = 0.91 − 0.95 = −0.04  (old gate: BLOCK)
      mid (mark)         = 1.02 − 0.95 = +0.07  (new gate: CLEAR).
    (The roll_leap gate path itself is exercised by the existing roll_leap suite; this
    pins the basis math the two sites now read via `rl_mark_net`.)"""
    new = next(o for o in RIOT_821 if o["strike_price"] == 24.0)
    cons, mid, _ob = _short_roll_credit(new, 0.95)   # fresh buyback mid 0.95
    assert cons < 0 and mid > 0


def test_fresh_mark_prefers_fresh_else_scan(agent: PMCCAgent):
    assert agent._fresh_mark({"mark_price": 0.75}, fallback=1.23) == (0.75, "fresh")
    assert agent._fresh_mark({"bid": 0.66, "ask": 0.84}, fallback=1.23) == (0.75, "fresh")
    assert agent._fresh_mark(None, fallback=1.23) == (1.23, "scan")
    assert agent._fresh_mark({}, fallback=1.23) == (1.23, "scan")


# ---- roll_leap e2e (drives Site A via propose_orders_for_pair) -------------

class _FakeRollLeapBroker:
    """Covered RIOT PMCC (LEAP + $23.5 8/14 short) + a roll-out chain + a fresh
    buyback quote whose MID clears but whose BID would block. Structural OptionBroker."""
    name = "fake"
    paper = True

    def __init__(self):
        t = date.today()
        self.leap_exp = (t + timedelta(days=400)).isoformat()
        self.cur_short_exp = (t + timedelta(days=8)).isoformat()
        self.roll_exp = (t + timedelta(days=15)).isoformat()

    async def quote(self, symbol):
        return 21.5

    async def get_option_positions_detail(self):
        return [
            {"option_type": "call", "chain_symbol": "RIOT", "quantity": 4, "dte": 400,
             "expiration_date": self.leap_exp, "strike_price": 15.0, "avg_price": 8.0,
             "delta": 0.96, "mark_price": 7.1, "option_id": "leap"},
            {"option_type": "call", "chain_symbol": "RIOT", "quantity": -4, "dte": 8,
             "expiration_date": self.cur_short_exp, "strike_price": 23.5,
             "avg_price": 0.79, "delta": 0.35, "mark_price": 0.95, "option_id": "short"},
        ]

    async def get_expiration_dates(self, symbol):
        t = date.today()
        return [(t + timedelta(days=d)).isoformat() for d in (1, 8, 15, 22)] + [self.leap_exp]

    async def get_calls_for_expiry(self, symbol, expiry):
        if expiry == self.leap_exp:
            return [_c(15.0, 7.0, 7.2, 7.10, 0.96, 500, 100)]   # deep-ITM LEAP (δ>=0.80)
        if expiry == self.roll_exp:
            return RIOT_821
        return []

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        # Fresh buyback for the current short: MID 0.95 clears vs new $24 (mark 1.02),
        # but BID-based conservative (0.91 − 0.95 = −0.04) would have BLOCKED.
        if abs(float(strike) - 23.5) < 1e-9 and expiration == self.cur_short_exp:
            return {"bid": 0.90, "ask": 1.00, "mark_price": 0.95}
        return {"bid": None, "ask": None, "mark_price": None}


def test_e2e_roll_leap_gate_on_mid_basis_clears(agent: PMCCAgent):
    """PART A item 1, path-level: a roll_leap whose short-leg pair is a CREDIT at mid
    but a (small) DEBIT at the bid-based conservative now CLEARS — proving the gate
    reads the same-timestamp MID net. It builds the multi-leg roll_leap on the on-target
    $24 8/21 short (Fix 1 selection), with NO net-debit abort."""
    broker = _FakeRollLeapBroker()
    analysis = types.SimpleNamespace(
        action="roll_leap", target_delta=0.35, target_dte=7, target_strike=None,
        target_delta_low=None, target_delta_high=None, override=None, confidence=0.8,
        urgency="attention", summary="roll leap", rationale="roll leap", warnings=[],
        format_rich=lambda: "(expert)",
    )
    orders = asyncio.run(
        agent.propose_orders_for_pair(broker, "RIOT", analysis, preview=True))
    assert orders, "roll_leap should build (mid credit) — bid-basis would have blocked"
    sells_open = [o for o in orders
                  if o.side == "sell" and (o.extra or {}).get("position_effect") == "open"]
    assert sells_open and float((sells_open[0].extra or {}).get("strike")) == 24.0
    ab = agent._last_roll_abort
    assert ab is None or ab.get("reason") != "net_debit_roll"
