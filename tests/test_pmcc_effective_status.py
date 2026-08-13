"""PMCC UX-truthfulness enhancement (2026-08-13) — Issues 1 + 2 as one change.

Issue 2: ONE shared `_pmcc_status.effective_status` computes the EFFECTIVE post-gate
status (raw judgment + cheap earnings gate + live pricing buildability) and drives BOTH
the tile badge (web/data.py) and the Expert panel (web/routes.py) so they can never
disagree (the BULL "ROLL SHORT EARLY" tile vs earnings-suppressed panel desync).

Issue 1: a FRESH, ACTIONABLE credit roll on the stored-verdict panel becomes Approve-ready
LLM-free (price_and_stash — NO _llm_analyze_position), gated on the SAME effective status;
an earnings-suppressed / stale roll shows the effective status + reason and NO Approve.
"""
from __future__ import annotations

import types

import pytest

import trading_corp.persistence.db as db
from trading_corp.agents.divisions import _pmcc_status
from trading_corp.agents.divisions._pmcc_status import effective_status
from trading_corp.web.data import _build_pmcc_tile_status
from trading_corp.web import routes as _routes
from trading_corp.web import pmcc_pricing
from trading_corp.web.pmcc_pricing import PricedRoll


# ── pure effective_status — each gate → label + actionable/suppressed ─────────

def test_effective_actionable_credit_roll():
    eff = effective_status("roll_short_early", earnings_state="clear", buildable=True)
    assert eff["actionable"] is True and eff["suppressed"] is False
    assert eff["label"] == "ROLL SHORT EARLY" and eff["kind"] == "actionable"


def test_effective_actionable_debit_roll_is_actionable():
    # A net-DEBIT roll is buildable → ACTIONABLE (presented per the best-price fix).
    # effective_status keys off buildability, NOT credit/debit direction.
    eff = effective_status("roll_short", earnings_state="clear", buildable=True)
    assert eff["actionable"] is True and eff["suppressed"] is False


def test_effective_earnings_blocked_is_earnings_window_and_suppressed():
    eff = effective_status("roll_short_early", earnings_state="blocked",
                           earnings_reason="earnings on 2026-08-15 (2d away, buffer=7d)")
    assert eff["label"] == "EARNINGS WINDOW"
    assert eff["suppressed"] is True and eff["actionable"] is False
    assert "let the short expire" in eff["reason"]          # operator guidance kept
    assert eff["detail"] == "earnings on 2026-08-15 (2d away, buffer=7d)"


def test_effective_earnings_wins_over_buildable():
    # Even a (stale) buildable build must never override an earnings suppression.
    eff = effective_status("roll_short", earnings_state="blocked", buildable=True)
    assert eff["label"] == "EARNINGS WINDOW" and eff["actionable"] is False


def test_effective_cant_price_is_suppressed():
    eff = effective_status("roll_short", earnings_state="clear", buildable=False,
                           price_reason="a sparse chain")
    assert eff["label"] == "CAN'T PRICE" and eff["suppressed"] is True
    assert eff["actionable"] is False and eff["reason"] == "a sparse chain"


def test_effective_market_closed_is_not_a_suppression():
    eff = effective_status("roll_short_early", earnings_state="clear",
                           buildable=False, market_closed=True)
    # Market closed: show the judgment, not actionable, but NOT suppressed.
    assert eff["label"] == "ROLL SHORT EARLY" and eff["kind"] == "market_closed"
    assert eff["suppressed"] is False and eff["actionable"] is False


def test_effective_buildable_true_wins_over_market_closed():
    # A concretely priced build stays approvable regardless of the wall clock (guards the
    # monkeypatched-broker path forcing buildable=True off-hours).
    eff = effective_status("close_short", earnings_state="clear",
                           buildable=True, market_closed=True)
    assert eff["actionable"] is True and eff["kind"] == "actionable"


def test_effective_pending_when_unpriced():
    eff = effective_status("roll_short", earnings_state="clear", buildable=None)
    assert eff["label"] == "ROLL SHORT" and eff["kind"] == "pending"
    assert eff["actionable"] is False and eff["suppressed"] is False


def test_effective_roll_leap_is_advisory_never_actionable():
    eff = effective_status("roll_leap", earnings_state="clear", buildable=True)
    assert eff["advisory"] is True and eff["actionable"] is False
    assert eff["suppressed"] is False and eff["label"] == "ROLL LEAP"


@pytest.mark.parametrize("act", ["hold", "watch", ""])
def test_effective_non_actions_never_actionable(act):
    eff = effective_status(act, buildable=True)
    assert eff["actionable"] is False and eff["suppressed"] is False


# ── integration fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    db.init_db(url)
    return url


_NOW = "2026-08-13T14:00:00+00:00"
_REC_TS = "2026-08-13T13:00:00+00:00"          # +1h from _NOW → fresh at 8h window


def _blocked_agent():
    return types.SimpleNamespace(
        _cfg={},
        _earnings_gate_state=lambda s: (
            "blocked", "earnings on 2026-08-15 (2d away, buffer=7d)"),
    )


def _clear_agent(**extra):
    return types.SimpleNamespace(
        _cfg={}, _earnings_gate_state=lambda s: ("clear", ""), **extra)


def _deps(agent, db_url):
    return types.SimpleNamespace(
        pmcc_agent=agent, db_url=db_url,
        data_exec=types.SimpleNamespace(brokers={"robinhood_pmcc": object()}))


# ── Issue 2: tile ↔ panel agree on the earnings suppression (the BULL case) ───

def test_tile_shows_effective_earnings_window(db_url):
    _pmcc_status.record_pmcc_decision(
        "BULL", status="roll_short_early", source="scan", computed_at=_REC_TS,
        db_url=db_url, urgency="urgent")
    tile = _build_pmcc_tile_status(
        "BULL", db_url=db_url, now=_NOW, cfg={"staleness_hours": 8},
        agent=_blocked_agent(), slug=None)
    assert tile["state"] == "fresh"
    assert tile["status_label"] == "EARNINGS WINDOW"          # NOT the raw "ROLL SHORT EARLY"
    assert tile["suppressed"] is True and tile["actionable"] is False


@pytest.mark.asyncio
async def test_tile_and_panel_agree_on_earnings_suppression(db_url, monkeypatch):
    _pmcc_status.record_pmcc_decision(
        "BULL", status="roll_short_early", source="scan", computed_at=_REC_TS,
        db_url=db_url, urgency="urgent", target_delta_low=0.2, target_delta_high=0.4,
        target_dte=7)
    monkeypatch.setattr(_pmcc_status, "classify_freshness", lambda r, now, h: "fresh")
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)
    monkeypatch.setattr(pmcc_pricing, "cached", lambda slug, symbol: None)

    async def _blocked_price(pmcc_agent, broker, slug, symbol, db_url, *, now=None):
        # The earnings gate makes propose return [] → non-buildable + the reason.
        return PricedRoll(
            slug=slug, symbol=symbol, priced_at=0.0, buildable=False,
            estimate_reason="earnings within the buffer — roll suppressed (let the short expire)")
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _blocked_price)

    agent = _blocked_agent()
    tile = _build_pmcc_tile_status(
        "BULL", db_url=db_url, now=_NOW, cfg={"staleness_hours": 8},
        agent=agent, slug="robinhood_pmcc")
    panel = await _routes._render_pmcc_record_panel(_deps(agent, db_url), "robinhood_pmcc", "BULL")

    # SAME effective status on both surfaces; NO Approve on either.
    assert tile["status_label"] == "EARNINGS WINDOW"
    assert "EARNINGS WINDOW" in panel
    assert "Approve &amp; Execute" not in panel and "preview_id" not in panel
    assert "let the short expire" in panel                    # guidance text kept


# ── Issue 1: a FRESH actionable roll is Approve-ready LLM-free ────────────────

@pytest.mark.asyncio
async def test_fresh_roll_is_approve_ready_llm_free(db_url, monkeypatch):
    _pmcc_status.record_pmcc_decision(
        "RKLB", status="roll_short_early", source="scan", computed_at=_REC_TS,
        db_url=db_url, urgency="elevated", target_delta_low=0.2, target_delta_high=0.4,
        target_dte=7)
    monkeypatch.setattr(_pmcc_status, "classify_freshness", lambda r, now, h: "fresh")
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)

    async def _boom_llm(*a, **k):
        raise AssertionError("the record panel must NOT call the LLM for a stored roll")

    async def _fake_price(pmcc_agent, broker, slug, symbol, db_url, *, now=None):
        return PricedRoll(
            slug=slug, symbol=symbol, priced_at=0.0, buildable=True,
            estimate={"debit": 0.40, "credit": 1.10, "net": 0.70, "net_abs": 0.70,
                      "direction": "credit", "close_strike": 30.0, "open_strike": 31.0},
            stash_token=("pid-roll", "fp-roll"))
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _fake_price)

    agent = _clear_agent(_llm_analyze_position=_boom_llm, analyze_symbol=_boom_llm)
    html = await _routes._render_pmcc_record_panel(_deps(agent, db_url), "robinhood_pmcc", "RKLB")

    # Priced Approve wired with the consent stash (shown == fires); LLM never called
    # (_boom_llm would have raised) — the roll is priced LLM-FREE from the stored judgment.
    assert "pid-roll" in html and "fp-roll" in html
    assert "preview_id" in html and "fingerprint" in html
    assert "Approve &amp; Execute" in html


@pytest.mark.asyncio
async def test_fresh_roll_earnings_suppressed_hides_approve(db_url, monkeypatch):
    _pmcc_status.record_pmcc_decision(
        "BULL", status="roll_short_early", source="scan", computed_at=_REC_TS,
        db_url=db_url, urgency="urgent", target_delta_low=0.2, target_delta_high=0.4,
        target_dte=7)
    monkeypatch.setattr(_pmcc_status, "classify_freshness", lambda r, now, h: "fresh")
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)

    async def _blocked_price(pmcc_agent, broker, slug, symbol, db_url, *, now=None):
        return PricedRoll(slug=slug, symbol=symbol, priced_at=0.0, buildable=False,
                          estimate_reason="earnings within the buffer — roll suppressed (let the short expire)")
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _blocked_price)

    html = await _routes._render_pmcc_record_panel(
        _deps(_blocked_agent(), db_url), "robinhood_pmcc", "BULL")
    assert "EARNINGS WINDOW" in html
    assert "Approve &amp; Execute" not in html and "preview_id" not in html


@pytest.mark.asyncio
async def test_stale_roll_not_auto_approvable(db_url, monkeypatch):
    _pmcc_status.record_pmcc_decision(
        "RKLB", status="roll_short", source="scan", computed_at=_REC_TS,
        db_url=db_url, urgency="elevated")
    monkeypatch.setattr(_pmcc_status, "classify_freshness", lambda r, now, h: "stale")
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)

    async def _boom_price(*a, **k):
        raise AssertionError("a STALE roll must NOT be auto-priced (freshness gate)")
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _boom_price)

    html = await _routes._render_pmcc_record_panel(
        _deps(_clear_agent(), db_url), "robinhood_pmcc", "RKLB")
    assert "preview_id" not in html                           # no auto-Approve on a stale judgment
    assert "Re-analyze" in html and "approve that exact combo" in html
