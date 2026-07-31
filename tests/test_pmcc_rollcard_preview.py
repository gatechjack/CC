"""Division-panel consent surface + preview stash tests (2026-07-30).

Defect 2: the roll estimate (strike / debit / credit / net) + earnings states on the
DIVISION Expert panel, the stored-record panel HIDING Approve, and the preview stash
that carries the EXACT combo forward so Approve fires the contract that was shown.
"""
from __future__ import annotations

import types

import pytest

from trading_corp.persistence.models import ProposedOrder
from trading_corp.web import pmcc_preview
from trading_corp.web.routes import (
    _render_pair_analysis,
    _render_pmcc_record_panel,
    _exec_consent_mismatch_html,
)
from trading_corp.web.pmcc_roll_card import build_pmcc_roll_card_extras
from trading_corp.agents.strategies._pmcc_combo import reprice_combo_from_quotes


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

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
    return [
        _leg("buy", "close", close_strike, close_exp,
             action="roll_short_call_close", limit=1.20),
        _leg("sell", "open", open_strike, open_exp,
             action="roll_short_call_open", limit=1.80),
    ]


class _QBroker:
    def __init__(self, quotes, spot=150.0):
        self._q = quotes
        self._spot = spot

    async def get_option_quote(self, symbol, expiration, strike, option_type):
        return self._q.get(round(float(strike), 2))

    async def quote(self, symbol):
        return self._spot


def _analysis(action="roll_short"):
    return types.SimpleNamespace(
        action=action, urgency="elevated", confidence=0.8,
        summary="short breached, roll up-and-out", rationale="ITM by 3%",
        warnings=[], target_delta=0.30, target_dte=7,
    )


_CLEAR = {"kind": "clear", "offer_roll": True, "date": None, "verified": False,
          "source": None, "recommendation": None, "flag": None, "caveat": None}


# ==========================================================================
# stash module — fingerprint + hit/miss/single-use/expiry
# ==========================================================================

def test_fingerprint_is_price_independent():
    a = _roll_legs()
    b = _roll_legs()
    for o in b:                                    # move only the PRICE, not the shape
        o.limit_price = 9.99
        o.extra["net_limit_price"] = 9.99
    assert pmcc_preview.fingerprint(a) == pmcc_preview.fingerprint(b)


def test_fingerprint_changes_with_strike():
    assert pmcc_preview.fingerprint(_roll_legs()) != \
        pmcc_preview.fingerprint(_roll_legs(open_strike=180.0))


def test_stash_hit_then_single_use():
    orders = _roll_legs()
    pid, fp = pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", orders, action="roll_short")
    got = pmcc_preview.load_preview("robinhood_pmcc", "AAPL", pid, fp)
    assert [o.id for o in got.orders] == [o.id for o in orders]
    assert got.action == "roll_short"        # action carried for the LLM-free view
    # single-use: the slot is consumed on a hit
    assert pmcc_preview.load_preview("robinhood_pmcc", "AAPL", pid, fp) is None


def test_stash_wrong_fingerprint_or_id_misses():
    orders = _roll_legs()
    pid, fp = pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", orders, action="roll_short")
    assert pmcc_preview.load_preview("robinhood_pmcc", "AAPL", pid, "deadbeef00000000") is None
    assert pmcc_preview.load_preview("robinhood_pmcc", "AAPL", "nope", fp) is None
    assert pmcc_preview.load_preview("robinhood_pmcc", "AAPL", None, None) is None


def test_stash_expiry():
    orders = _roll_legs()
    pid, fp = pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", orders, action="roll_short", now=1000.0)
    assert pmcc_preview.load_preview(
        "robinhood_pmcc", "AAPL", pid, fp, now=1000.0 + 901) is None


def test_stash_empty_returns_none():
    assert pmcc_preview.stash_preview(
        "robinhood_pmcc", "AAPL", [], action="hold") is None


# ==========================================================================
# division panel render — estimate + earnings states + consent token
# ==========================================================================

def test_panel_renders_estimate_strike_expiry_and_token():
    extras = {
        "earnings": dict(_CLEAR),
        "estimate": {"debit": 1.20, "credit": 1.80, "net": 0.60, "net_abs": 0.60,
                     "direction": "credit", "close_strike": 170.0,
                     "close_expiration": "2026-07-31", "open_strike": 175.0,
                     "open_expiration": "2026-08-07"},
        "estimate_reason": None,
    }
    html = _render_pair_analysis(
        _analysis(), slug="robinhood_pmcc", symbol="AAPL",
        roll_extras=extras, preview_token=("pid123", "fpabc"))
    assert "Approve &amp; Execute" in html
    assert "1.20" in html and "1.80" in html and "0.60" in html
    assert "175.00" in html and "2026-08-07" in html          # attributable strike + expiry
    assert "actual fill will differ" in html
    # the consent token rides the Approve form so dispatch fires the shown combo
    assert 'name="preview_id" value="pid123"' in html
    assert 'name="fingerprint" value="fpabc"' in html


def test_panel_earnings_blocked_hides_approve_shows_rec():
    extras = {
        "earnings": {"kind": "blocked", "offer_roll": False, "date": "2026-08-05",
                     "verified": True, "source": "broker",
                     "recommendation": ("Earnings 2026-08-05 — let the current short "
                                        "call expire, then sell a new call after."),
                     "flag": None, "caveat": None},
        "estimate": None, "estimate_reason": None,
    }
    html = _render_pair_analysis(
        _analysis(), slug="robinhood_pmcc", symbol="AAPL", roll_extras=extras)
    assert "Approve &amp; Execute" not in html                # roll not offered
    assert "Approve disabled" in html
    assert "2026-08-05" in html
    assert "let the current short call expire" in html


def test_panel_earnings_unverified_no_estimate_suppresses_approve():
    """P1 (2026-07-31): the Approve-gate now suppresses a bare Approve when NO
    concrete estimate is built — even in the earnings-unverified case (previously
    this rendered a live Approve over 'Target δ/DTE'; that gap is now closed). The
    unverified flag + the no-estimate reason still show."""
    extras = {
        "earnings": {"kind": "unverified", "offer_roll": True, "date": None,
                     "verified": False, "source": "none", "recommendation": None,
                     "flag": "earnings date unverified — confirm before rolling",
                     "caveat": None},
        "estimate": None,
        "estimate_reason": "Live estimate unavailable — no order sent, re-prices at approval.",
    }
    html = _render_pair_analysis(
        _analysis(), slug="robinhood_pmcc", symbol="AAPL", roll_extras=extras)
    assert "Approve &amp; Execute" not in html                # bare Approve suppressed
    assert "Can't be priced right now" in html
    assert "unverified" in html and "confirm before rolling" in html
    assert "Live estimate unavailable" in html
    assert "Roll estimate" not in html                        # no estimate block, only the reason


@pytest.mark.asyncio
async def test_panel_estimate_equals_dispatch_natural():
    """CONSENT LOCK on the DIVISION panel: the net the panel prints is the SAME
    natural the dispatch reprice derives the placed limit from (net − give_up)."""
    quotes = {170.0: {"bid": 1.10, "ask": 1.20, "mark": 1.15},
              175.0: {"bid": 1.80, "ask": 1.95, "mark": 1.88}}
    entry = types.SimpleNamespace(orders=_roll_legs(), underlying="AAPL")
    agent = types.SimpleNamespace(
        earnings_card_state=lambda s, short_strike=None, spot=None: dict(_CLEAR))
    extras = await build_pmcc_roll_card_extras(entry, _QBroker(quotes), agent)
    assert extras["estimate"]["net"] == 0.60
    _, limit = await reprice_combo_from_quotes(_roll_legs(), _QBroker(quotes), give_up=0.02)
    assert round(extras["estimate"]["net"] - 0.02, 2) == limit == 0.58
    html = _render_pair_analysis(
        _analysis(), slug="robinhood_pmcc", symbol="AAPL", roll_extras=extras)
    assert "0.60" in html                                     # the consent net is on the panel


# ==========================================================================
# stored-record panel — hides Approve, prompts Re-analyze
# ==========================================================================

def test_record_panel_hides_approve_and_prompts_reanalyze(monkeypatch):
    from trading_corp.agents.divisions import _pmcc_status
    rec = {"status": "roll_short", "urgency": "elevated", "confidence": 0.8,
           "summary": "roll up-and-out", "rationale": "ITM", "warnings": [],
           "source": "scan"}
    monkeypatch.setattr(_pmcc_status, "load_decision", lambda symbol, db_url=None: rec)
    monkeypatch.setattr(_pmcc_status, "classify_freshness", lambda r, now, h: "fresh")
    monkeypatch.setattr(_pmcc_status, "age_hours", lambda r, now: 1.0)
    deps = types.SimpleNamespace(
        pmcc_agent=types.SimpleNamespace(_cfg={}), db_url=None)
    html = _render_pmcc_record_panel(deps, "robinhood_pmcc", "AAPL")
    assert "Approve &amp; Execute" not in html                # no Approve on a stored verdict
    assert "Re-analyze" in html                               # banner + prompt point to it
    assert "approve that exact combo" in html


def test_consent_mismatch_html_smoke():
    html = _exec_consent_mismatch_html("IREN")
    assert "IREN" in html and "not placed" in html and "Re-analyze" in html
