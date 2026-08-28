"""Render-then-stream (2026-08-28) — #1 remove the inline synchronous pricing from the
PMCC page render; #2 scope + de-stack the 45s OOB pricing endpoint.

Covers:
  (a) a cold-cache placeable tile is PENDING (not CAN'T PRICE), not actionable;
  (b) earnings-window precedence holds with a cold cache (no live price needed);
  (c) the coalesce guard: a tick while a refresh is in-flight renders from cache and does
      NOT start a second refresh_division fan-out;
  (d) a raising refresh_division still discards the slug (finally), so the next tick refreshes;
  + endpoint scoping to ?syms (not the whole accumulated cache), the chip+badge OOB response,
    the "pricing... refreshing" pending chip (market-open gated, NOT can't-price), and source
    guards that the synchronous pricing is gone and the coalesce/scoping shape is present.

Display-only: no order/dispatch/consent code is touched by this change.
"""
from __future__ import annotations

import inspect
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from trading_corp.persistence import db
from trading_corp.agents.divisions._pmcc_status import effective_status
from trading_corp.agents.divisions import _pmcc_status
from trading_corp.web.data import _build_pmcc_tile_status
from trading_corp.web import data as web_data
from trading_corp.web import routes as web_routes
from trading_corp.web import pmcc_pricing
from trading_corp.web.app import WebDeps, create_app


# ── (a)/(b) effective_status: cold cache is PENDING, earnings still wins ──────

def test_cold_cache_placeable_is_pending_not_cant_price():
    # buildable None (never priced) + market open + earnings clear -> the tile shows the
    # ACTION, pending and NOT actionable; it must NOT read as CAN'T PRICE (that is only a
    # real failed build, buildable is False).
    eff = effective_status("roll_short", earnings_state="clear",
                           buildable=None, market_closed=False)
    assert eff["kind"] == "pending"
    assert eff["actionable"] is False
    assert eff["suppressed"] is False
    assert eff["label"] == "ROLL SHORT"          # raw action, not "CAN'T PRICE"


def test_cant_price_still_requires_a_real_failed_build():
    # buildable is False (a live pricing attempt could not build) -> CAN'T PRICE. This is
    # the line render-then-stream must NOT trip on mere absence.
    eff = effective_status("roll_short", earnings_state="clear",
                           buildable=False, market_closed=False)
    assert eff["kind"] == "cant_price" and eff["suppressed"] is True
    assert eff["label"] == "CAN'T PRICE"


def test_earnings_window_wins_over_cold_cache():
    # earnings blocked outranks buildability -> EARNINGS WINDOW renders on first paint with
    # NO live price (earnings is 24h-cached, not pricing-dependent).
    eff = effective_status("roll_short", earnings_state="blocked",
                           earnings_reason="earnings on 2026-09-01",
                           buildable=None, market_closed=False)
    assert eff["kind"] == "earnings"
    assert eff["label"] == "EARNINGS WINDOW"
    assert eff["suppressed"] is True


def test_tile_status_cold_cache_is_pending(tmp_db, monkeypatch):
    # End-to-end through the tile helper: a fresh roll_short verdict with an EMPTY pricing
    # cache renders the action, pending + not actionable (render-then-stream first paint).
    db.init_db(tmp_db)
    pmcc_pricing._CACHE.clear()
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)
    now = datetime.now(timezone.utc)
    _pmcc_status.record_pmcc_decision(
        "AAPL", status="roll_short", source="scan",
        computed_at=now.isoformat(), db_url=tmp_db, urgency="elevated")
    agent = types.SimpleNamespace(_earnings_gate_state=lambda s: ("clear", ""))
    tile = _build_pmcc_tile_status("AAPL", db_url=tmp_db, now=now,
                                   cfg={"staleness_hours": 8}, agent=agent,
                                   slug="robinhood_pmcc")
    assert tile["state"] == "fresh"
    assert tile["status_label"] == "ROLL SHORT"
    assert tile["actionable"] is False
    assert tile["suppressed"] is False           # pending, NOT a CAN'T PRICE suppression


# ── pending chip render (market-open gated) ──────────────────────────────────

def _render_chip(pricing, market_open=True):
    tdir = Path(web_routes.__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(tdir)))
    env.filters["strike"] = lambda v: ("%.2f" % v) if v is not None else "-"
    return env.get_template("partials/_pmcc_pricing.html").render(
        pricing=pricing, market_open=market_open)


def test_pending_chip_refreshing_when_market_open_not_cant_price():
    cold = {"state": "none", "label": "not priced", "net_abs": None, "direction": None,
            "strike": None, "buildable": False, "market_closed": False}
    open_html = _render_chip(cold, market_open=True)
    assert "refreshing" in open_html and "can't price" not in open_html
    # off-hours: neither refreshing nor can't-price (absence is not a failed build)
    closed_html = _render_chip(cold, market_open=False)
    assert "refreshing" not in closed_html and "can't price" not in closed_html
    # a None pricing (a cache-read failure) behaves the same as not-priced during RTH
    assert "refreshing" in _render_chip(None, market_open=True)


# ── OOB endpoint: scoping, coalesce, chip+badge, finally-discard ─────────────

@pytest.fixture
def pricing_client(tmp_db, monkeypatch):
    db.init_db(tmp_db)
    pmcc_pricing._CACHE.clear()
    web_routes._PMCC_REFRESH_INFLIGHT.clear()
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda now=None: True)
    agent = types.SimpleNamespace(_cfg={"tile_status": {"staleness_hours": 8}})
    deps = WebDeps(
        db_url=tmp_db, db_path=tmp_db.replace("sqlite:///", ""), mode="PAPER",
        logger_agent=None,
        data_exec=types.SimpleNamespace(brokers={"robinhood_pmcc": object()}),
        trend_agent=None, portfolio=None, pmcc_agent=agent, fidelity_agent=None,
        paper_broker=None, secrets=None, risk_agent=None, pending_registry=None,
    )
    return TestClient(create_app(deps))


def test_endpoint_scoped_to_syms_not_whole_cache(pricing_client, monkeypatch):
    seen = {}
    async def _spy(*a, **k):
        seen["symbols"] = list(a[3])             # refresh_division(agent, broker, slug, symbols, db_url)
    monkeypatch.setattr(pmcc_pricing, "refresh_division", _spy)
    # an accumulated cache entry that is NOT on the page must not be repriced/rendered
    pmcc_pricing._CACHE[("robinhood_pmcc", "ZZZZ")] = pmcc_pricing.PricedRoll(
        slug="robinhood_pmcc", symbol="ZZZZ", priced_at=0.0)
    r = pricing_client.get("/division/robinhood_pmcc/pmcc-pricing?syms=AAPL,MSFT,AAPL")
    assert r.status_code == 200
    assert seen["symbols"] == ["AAPL", "MSFT"]   # scoped + de-duped, NOT symbols_for (has ZZZZ)
    assert 'id="pmcc-pricing-ZZZZ"' not in r.text


def test_endpoint_response_has_chip_and_badge(pricing_client, monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(pmcc_pricing, "refresh_division", _noop)
    r = pricing_client.get("/division/robinhood_pmcc/pmcc-pricing?syms=AAPL")
    assert r.status_code == 200
    assert 'id="pmcc-pricing-AAPL"' in r.text    # the pricing chip
    assert 'id="pmcc-badge-AAPL"' in r.text      # the status badge (refines pending->...)


def test_coalesce_skips_refresh_when_inflight(pricing_client, monkeypatch):
    calls = []
    async def _spy(*a, **k):
        calls.append(1)
    monkeypatch.setattr(pmcc_pricing, "refresh_division", _spy)
    web_routes._PMCC_REFRESH_INFLIGHT.add("robinhood_pmcc")   # simulate an in-flight refresh
    try:
        r = pricing_client.get("/division/robinhood_pmcc/pmcc-pricing?syms=AAPL")
    finally:
        web_routes._PMCC_REFRESH_INFLIGHT.discard("robinhood_pmcc")
    assert r.status_code == 200
    assert 'id="pmcc-pricing-AAPL"' in r.text    # still renders from cache
    assert calls == []                           # coalesced: NO second refresh fan-out


def test_finally_discards_slug_and_next_tick_refreshes(pricing_client, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("rh down")
    monkeypatch.setattr(pmcc_pricing, "refresh_division", _boom)
    r = pricing_client.get("/division/robinhood_pmcc/pmcc-pricing?syms=AAPL")
    assert r.status_code == 200                                    # endpoint swallows the error
    assert "robinhood_pmcc" not in web_routes._PMCC_REFRESH_INFLIGHT  # finally discarded (no wedge)
    # a subsequent tick refreshes normally
    calls = []
    async def _spy(*a, **k):
        calls.append(1)
    monkeypatch.setattr(pmcc_pricing, "refresh_division", _spy)
    r2 = pricing_client.get("/division/robinhood_pmcc/pmcc-pricing?syms=AAPL")
    assert r2.status_code == 200 and calls == [1]


# ── source guards: the load-ordering + endpoint shape (render-then-stream) ────

def test_build_division_view_has_no_synchronous_pricing():
    src = inspect.getsource(web_data.build_division_view)
    assert "refresh_division" not in src                 # inline pricing pull removed
    assert "tile_pricing_view(" in src and "cached(" in src   # still reads cache for first paint


def test_pmcc_pricing_endpoint_scoped_and_coalesced():
    src = inspect.getsource(web_routes)
    assert "_PMCC_REFRESH_INFLIGHT" in src
    assert 'query_params.get("syms")' in src             # scoped to the page's tiles
    assert "_PMCC_REFRESH_INFLIGHT.discard(slug)" in src  # finally-discard present
    assert "_pmcc_tile_badge_oob(templates, deps, s)" in src  # badge refined on the tick
