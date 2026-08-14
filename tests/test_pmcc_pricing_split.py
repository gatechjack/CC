"""P1 (2026-07-31) — judgment/pricing split unit suite.

Covers: band-aware strike selection, the decision-record δ-band/DTE schema
round-trip (+ backward-compat), the dispatch net-drift/sign-flip consent guard,
the Approve-gate (suppress when unbuildable), and the two-clock pricing-age coloring.
The zero-Anthropic `price_and_stash` test lives in test_pmcc_logic.py (reuses the
agent + broker fixtures there).
"""
from __future__ import annotations

import types

import pytest

from trading_corp.persistence.models import ProposedOrder
from trading_corp.agents.divisions.pmcc_robinhood import _select_weekly_strike
from trading_corp.agents.divisions import _pmcc_status
from trading_corp.agents.strategies._pmcc_combo import (
    snapshot_combo_for_consent, assess_combo_reprice_consent,
)
from trading_corp.web.routes import _render_pair_analysis
from trading_corp.web import pmcc_pricing, pmcc_preview


# ==========================================================================
# 2 — band-aware _select_weekly_strike
# ==========================================================================

def _c(strike, delta):
    return {"strike_price": strike, "delta": delta}


def test_band_picks_best_within_band():
    calls = [_c(100, 0.20), _c(105, 0.28), _c(110, 0.33), _c(115, 0.45)]
    # band [0.25, 0.35] → in-band {105:0.28, 110:0.33}; mid 0.30 → 105 (|0.28-0.30|<|0.33-0.30|)
    best = _select_weekly_strike(calls, target_delta=0.30,
                                 target_delta_low=0.25, target_delta_high=0.35)
    assert best["strike_price"] == 105


def test_empty_band_falls_back_to_point_default():
    calls = [_c(100, 0.20), _c(110, 0.33)]
    best = _select_weekly_strike(calls, target_delta=0.30)   # no band → point 0.30 OTM
    assert best["strike_price"] == 110


def test_band_with_no_in_band_strike_uses_midpoint():
    calls = [_c(100, 0.10), _c(120, 0.50)]                   # none in [0.25,0.35]
    best = _select_weekly_strike(calls, target_delta=0.99,
                                 target_delta_low=0.25, target_delta_high=0.35)
    # falls through to point at midpoint 0.30; OTM(<0.40) pool = {0.10} → 100
    assert best["strike_price"] == 100


# ==========================================================================
# 3 — decision-record δ-band/DTE schema round-trip + backward-compat
# ==========================================================================

def test_schema_roundtrips_band_and_dte(tmp_db):
    from trading_corp.persistence import db
    db.init_db(tmp_db)
    _pmcc_status.record_pmcc_decision(
        "AAPL", status="roll_short", source="scan",
        computed_at="2026-07-31T13:00:00+00:00", db_url=tmp_db,
        target_delta_low=0.25, target_delta_high=0.35, target_dte=7)
    rec = _pmcc_status.load_decision("AAPL", db_url=tmp_db)
    assert rec["target_delta_low"] == 0.25
    assert rec["target_delta_high"] == 0.35
    assert rec["target_dte"] == 7


def test_old_record_without_band_loads_to_none(tmp_db):
    from trading_corp.persistence import db
    db.init_db(tmp_db)
    # simulate a PRE-schema record (no band keys) via the raw store
    db.set_agent_state(
        _pmcc_status._AGENT, _pmcc_status.decision_key("AAPL"),
        {"symbol": "AAPL", "status": "roll_short", "source": "scan",
         "computed_at": "2026-07-31T13:00:00+00:00", "warnings": []},
        db_url=tmp_db)
    rec = _pmcc_status.load_decision("AAPL", db_url=tmp_db)
    assert rec is not None
    assert rec.get("target_delta_low") is None      # missing → None → config default
    assert rec.get("target_dte") is None


# ==========================================================================
# 4 — dispatch consent guard (net-drift + sign-flip)
# ==========================================================================

def _roll_leg(side, effect, strike, *, direction="credit", net=0.60):
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol="AAPL", side=side, qty=1.0,
        order_type="limit", limit_price=1.0, rationale="",
        extra={"is_option": True, "underlying": "AAPL", "option_type": "call",
               "strike": strike, "position_effect": effect, "is_multi_leg": True,
               "combo_id": "c1", "combo_direction": direction, "net_limit_price": net,
               "ratio_quantity": 1},
    )


def _credit_combo(net=0.60):
    return [_roll_leg("buy", "close", 170.0, net=net),
            _roll_leg("sell", "open", 175.0, net=net)]


def test_consent_ok_within_tolerance():
    legs = _credit_combo(net=0.60)
    snap = snapshot_combo_for_consent(legs)
    for o in legs:               # dispatch reprice shaves 0.02 (give_up) — within tol
        o.extra["net_limit_price"] = 0.58
    ok, why = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.05)
    assert ok, why


def test_consent_aborts_on_credit_collapse():
    legs = _credit_combo(net=0.60)
    snap = snapshot_combo_for_consent(legs)
    for o in legs:               # dropped 0.15 > 0.05 tol
        o.extra["net_limit_price"] = 0.45
    ok, why = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.05)
    assert not ok and "collaps" in why.lower()


def test_consent_aborts_on_sign_flip():
    legs = _credit_combo(net=0.60)
    snap = snapshot_combo_for_consent(legs)
    for o in legs:               # credit → debit
        o.extra["combo_direction"] = "debit"
    ok, why = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.05)
    assert not ok and "debit" in why.lower()


def test_consent_aborts_on_strike_drift():
    legs = _credit_combo(net=0.60)
    snap = snapshot_combo_for_consent(legs)
    legs[1].extra["strike"] = 180.0        # sell strike drifted from approved 175
    ok, why = assess_combo_reprice_consent(legs, snap, max_adverse_net_deviation=0.05)
    assert not ok and "strike" in why.lower()


def test_stash_fingerprint_matches_dispatched_orders():
    orders = _credit_combo()
    pid, fp = pmcc_preview.stash_preview("robinhood_pmcc", "AAPL", orders, action="roll_short")
    assert fp == pmcc_preview.fingerprint(orders)   # shown stash == what dispatch fingerprints


# ==========================================================================
# 5 — Approve-gate (suppress when unbuildable)
# ==========================================================================

def _analysis(action="roll_short"):
    return types.SimpleNamespace(
        action=action, urgency="elevated", confidence=0.8,
        summary="roll up-and-out", rationale="ITM", warnings=[],
        target_delta=0.30, target_dte=7)


_CLEAR = {"kind": "clear", "offer_roll": True, "date": None, "verified": False,
          "source": None, "recommendation": None, "flag": None, "caveat": None}


def test_approve_rendered_when_estimate_built():
    extras = {"earnings": dict(_CLEAR), "estimate_reason": None,
              "estimate": {"debit": 1.20, "credit": 1.80, "net": 0.60, "net_abs": 0.60,
                           "direction": "credit", "close_strike": 170.0,
                           "close_expiration": "2026-08-01", "open_strike": 175.0,
                           "open_expiration": "2026-08-08"}}
    html = _render_pair_analysis(_analysis(), slug="robinhood_pmcc", symbol="AAPL",
                                 roll_extras=extras, preview_token=("pid9", "fp9"))
    assert "Approve &amp; Execute" in html
    assert "1.20" in html and "1.80" in html and "0.60" in html and "175.00" in html
    assert 'name="preview_id" value="pid9"' in html


def test_approve_suppressed_when_unbuildable_with_reason():
    extras = {"earnings": dict(_CLEAR), "estimate": None,
              "estimate_reason": "Live estimate unavailable — market may be closed."}
    html = _render_pair_analysis(_analysis(), slug="robinhood_pmcc", symbol="AAPL",
                                 roll_extras=extras)
    assert "Approve &amp; Execute" not in html
    assert "Can't be priced right now" in html
    assert "Live estimate unavailable" in html
    assert "Refresh pricing" in html


# ==========================================================================
# 6 — two-clock pricing-age coloring
# ==========================================================================

def test_pricing_age_state_coloring():
    pr = pmcc_pricing.PricedRoll(slug="s", symbol="A", priced_at=1000.0, buildable=True)
    assert pmcc_pricing.pricing_age_state(pr, ttl=45, now=1010.0)["state"] == "green"
    assert pmcc_pricing.pricing_age_state(pr, ttl=45, now=1060.0)["state"] == "amber"
    assert pmcc_pricing.pricing_age_state(pr, ttl=45, now=1200.0)["state"] == "red"


def test_pricing_age_state_market_closed_and_none():
    prc = pmcc_pricing.PricedRoll(slug="s", symbol="A", priced_at=1000.0, market_closed=True)
    assert pmcc_pricing.pricing_age_state(prc)["state"] == "closed"
    assert pmcc_pricing.pricing_age_state(None)["state"] == "none"


# ==========================================================================
# #1 — build_division_view tile pricing: cache reuse, market-closed, tile view + partial
# ==========================================================================

@pytest.mark.asyncio
async def test_refresh_division_reuses_fresh_cache(monkeypatch):
    pmcc_pricing._CACHE.clear()
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)
    calls = []
    async def _spy(agent, broker, slug, symbol, db_url, *, now=None):
        calls.append(symbol)
        return pmcc_pricing.PricedRoll(slug=slug, symbol=symbol, priced_at=now or 0.0)
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _spy)
    pmcc_pricing._CACHE[("robinhood_pmcc", "AAPL")] = pmcc_pricing.PricedRoll(
        slug="robinhood_pmcc", symbol="AAPL", priced_at=1000.0, buildable=True)
    await pmcc_pricing.refresh_division(None, None, "robinhood_pmcc", ["AAPL"], "db",
                                        ttl=45, now=1010.0)
    assert calls == []                                   # age 10 < TTL 45 → NO re-pull


@pytest.mark.asyncio
async def test_refresh_division_off_hours_no_pull_marks_closed(monkeypatch):
    pmcc_pricing._CACHE.clear()
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: False)
    calls = []
    async def _spy(*a, **k):
        calls.append(1)
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _spy)
    pmcc_pricing._CACHE[("robinhood_pmcc", "AAPL")] = pmcc_pricing.PricedRoll(
        slug="robinhood_pmcc", symbol="AAPL", priced_at=1000.0, buildable=True)
    await pmcc_pricing.refresh_division(None, None, "robinhood_pmcc", ["AAPL"], "db", now=9e9)
    assert calls == []                                   # off-hours → NO RH pull
    assert pmcc_pricing.cached("robinhood_pmcc", "AAPL").market_closed is True


def test_tile_pricing_view_maps_estimate_and_coloring():
    pmcc_pricing._CACHE.clear()
    est = {"net_abs": 0.26, "direction": "credit", "open_strike": 185.0}
    pr = pmcc_pricing.PricedRoll(slug="s", symbol="A", priced_at=1000.0,
                                 buildable=True, estimate=est)
    v = pmcc_pricing.tile_pricing_view(pr, ttl=45, now=1010.0)
    assert v["state"] == "green" and v["buildable"]
    assert v["net_abs"] == 0.26 and v["direction"] == "credit" and v["strike"] == 185.0
    assert pmcc_pricing.tile_pricing_view(pr, ttl=45, now=1200.0)["state"] == "red"
    prc = pmcc_pricing.PricedRoll(slug="s", symbol="A", priced_at=1000.0, market_closed=True)
    assert pmcc_pricing.tile_pricing_view(prc)["market_closed"] is True
    assert pmcc_pricing.tile_pricing_view(None)["state"] == "none"


def _render_pricing_partial(pricing):
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    import trading_corp.web.routes as routes_mod
    tdir = Path(routes_mod.__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(tdir)))
    env.filters["strike"] = lambda v: ("%.2f" % v) if v is not None else "—"
    return env.get_template("partials/_pmcc_pricing.html").render(pricing=pricing)


def test_pricing_partial_renders_all_states():
    buildable = {"state": "green", "label": "12s", "net_abs": 0.26, "direction": "credit",
                 "strike": 185.0, "buildable": True, "market_closed": False}
    html = _render_pricing_partial(buildable)
    assert "+$0.26" in html and "185.00C" in html and "12s" in html
    closed = {"state": "closed", "label": "market closed", "net_abs": 0.26,
              "direction": "credit", "strike": 185.0, "buildable": False, "market_closed": True}
    assert "market closed" in _render_pricing_partial(closed)
    cant = {"state": "amber", "label": "", "net_abs": None, "direction": None,
            "strike": None, "buildable": False, "market_closed": False}
    assert "can't price" in _render_pricing_partial(cant)
