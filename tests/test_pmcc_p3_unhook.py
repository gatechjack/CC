"""P3 (2026-07-31): unhook PMCC SHORT-SIDE actions from the global /approvals +
ceo_graph/Telegram approval onto the division panel (the sole approval surface).

P3a (administrative removal) coverage:
- _is_pmcc_short_side_group classification, incl. the mandate-safe exclusion of any
  LEAP-touching group (open_pmcc / close_all / roll_leap never go panel-only).
- /approvals/pmcc-combos/{id} GET + decide routes removed -> 404.
- WebDeps no longer carries pmcc_pending_combo_registry (other divisions' kept).
- global /approvals still serves other divisions.

(close_short / open_short panel affordances + their skip are covered in P3b sections.)
"""
from __future__ import annotations

import dataclasses
import types

import pytest
from fastapi.testclient import TestClient

from trading_corp.persistence import db
from trading_corp.web import data as web_data
from trading_corp.web.app import WebDeps, create_app
from trading_corp.main import _is_pmcc_short_side_group, _PMCC_SHORT_SIDE_ACTIONS


def _o(action):
    return types.SimpleNamespace(extra={"action": action})


# ── _is_pmcc_short_side_group (mandate-safe classification) ───────────────

def test_roll_short_combo_is_short_side():
    grp = [_o("roll_short_call_close"), _o("roll_short_call_open")]
    assert _is_pmcc_short_side_group(grp) is True


def test_roll_leap_group_is_not_short_side():
    # roll_leap rebuilds the LEAP -> must stay advisory, never panel-routed.
    grp = [_o("roll_leap_close_short"), _o("roll_leap_close"),
           _o("roll_leap_open"), _o("roll_leap_open_short")]
    assert _is_pmcc_short_side_group(grp) is False


def test_any_leap_leg_excludes_the_group():
    # close_all = buy-to-close short + SELL the LEAP -> LEAP leg -> excluded.
    assert _is_pmcc_short_side_group(
        [_o("close_short_urgent"), _o("close_leap_urgent")]) is False
    # open_pmcc = buy LEAP + sell short -> LEAP leg -> excluded.
    assert _is_pmcc_short_side_group([_o("open_leap"), _o("open_short_call")]) is False


def test_untagged_or_empty_is_not_short_side():
    assert _is_pmcc_short_side_group([]) is False
    assert _is_pmcc_short_side_group([types.SimpleNamespace(extra=None)]) is False
    # a mix of tagged + untagged is NOT purely short-side.
    assert _is_pmcc_short_side_group(
        [_o("roll_short_call_close"), types.SimpleNamespace(extra={})]) is False


def test_short_side_set_contains_roll_short():
    assert {"roll_short_call_close", "roll_short_call_open"} <= _PMCC_SHORT_SIDE_ACTIONS


# ── routes / app ─────────────────────────────────────────────────────────

@pytest.fixture
def _stub_cc(monkeypatch):
    async def _stub(deps):
        return types.SimpleNamespace(
            mode=deps.mode, dry_run=False, regime="neutral", vix=15.0,
            health=types.SimpleNamespace(
                brokers=[], scheduler=types.SimpleNamespace(last_run=None)),
            equity_curve=[],
        )
    monkeypatch.setattr(web_data, "build_command_center", _stub)


@pytest.fixture
def client(tmp_db, _stub_cc):
    db.init_db(tmp_db)
    deps = WebDeps(
        db_url=tmp_db, db_path=tmp_db.replace("sqlite:///", ""), mode="PAPER",
        logger_agent=None, data_exec=None, trend_agent=None, portfolio=None,
        pmcc_agent=None, fidelity_agent=None, paper_broker=None, secrets=None,
        risk_agent=None, pending_registry=None,
    )
    return TestClient(create_app(deps))


def test_pmcc_combos_get_route_removed_404(client):
    assert client.get("/approvals/pmcc-combos/anything").status_code == 404


def test_pmcc_combos_decide_route_removed_404(client):
    r = client.post("/approvals/pmcc-combos/anything/decide", data={"decision": "approve"})
    assert r.status_code == 404


def test_global_approvals_still_served(client):
    # Other divisions' generic /approvals index remains intact.
    assert client.get("/approvals").status_code == 200


def test_webdeps_has_no_pmcc_combo_registry():
    fields = {f.name for f in dataclasses.fields(WebDeps)}
    assert "pmcc_pending_combo_registry" not in fields
    assert "tasty_pending_combo_registry" in fields   # other divisions' registries kept
    assert "pending_registry" in fields


# ── P3b: single-leg SHORT-side pricing + unhook (close_short / open_short) ──

from trading_corp.persistence.models import ProposedOrder as _PO
from trading_corp.agents.strategies._pmcc_combo import estimate_single_leg_from_quote
from trading_corp.web.pmcc_roll_card import build_pmcc_roll_card_extras


class _QB:
    """Mock broker returning one option quote + a spot."""
    def __init__(self, q): self._q = q
    async def get_option_quote(self, sym, exp, strike, ot): return self._q
    async def quote(self, sym): return 12.0


def _single(side, strike, action):
    return _PO(
        strategy="robinhood_pmcc", symbol="RKLB", side=side, qty=1.0,
        order_type="limit", limit_price=0.0, rationale="",
        extra={"is_option": True, "underlying": "RKLB", "option_type": "call",
               "expiration": "2026-08-07", "strike": strike,
               "position_effect": ("close" if side == "buy" else "open"),
               "action": action, "ratio_quantity": 1},
    )


class _ClearAgent:
    def earnings_card_state(self, symbol, short_strike=None, spot=None):
        return {"kind": "clear", "offer_roll": True, "recommendation": None,
                "date": None, "verified": False, "flag": None, "caveat": None, "source": None}


@pytest.mark.asyncio
async def test_estimate_single_close_short_is_debit_at_ask():
    e = await estimate_single_leg_from_quote(
        _single("buy", 30.0, "close_short_urgent"), _QB({"bid": 0.30, "ask": 0.38}))
    assert e["direction"] == "debit" and e["debit"] == 0.38 and e["net"] == -0.38
    # LEAP-mandate: the estimate strikes are the SHORT leg's own — never the LEAP.
    assert e["close_strike"] == 30.0 and e["open_strike"] is None


@pytest.mark.asyncio
async def test_estimate_single_open_short_is_credit_at_bid():
    e = await estimate_single_leg_from_quote(
        _single("sell", 32.0, "open_short_call"), _QB({"bid": 0.30, "ask": 0.38}))
    assert e["direction"] == "credit" and e["credit"] == 0.30 and e["net"] == 0.30
    assert e["open_strike"] == 32.0 and e["close_strike"] is None


@pytest.mark.asyncio
async def test_estimate_single_missing_quote_is_none():
    assert await estimate_single_leg_from_quote(
        _single("buy", 30.0, "close_short_urgent"), _QB({"bid": 0.30})) is None    # no ask
    assert await estimate_single_leg_from_quote(
        _single("sell", 32.0, "open_short_call"), _QB({"ask": 0.38})) is None       # no bid


@pytest.mark.asyncio
async def test_extras_prices_single_leg_close_short():
    entry = types.SimpleNamespace(
        orders=[_single("buy", 30.0, "close_short_urgent")], underlying="RKLB")
    out = await build_pmcc_roll_card_extras(entry, _QB({"bid": 0.30, "ask": 0.38}), _ClearAgent())
    assert out["estimate"] and out["estimate"]["direction"] == "debit"
    assert out["estimate"]["debit"] == 0.38


@pytest.mark.asyncio
async def test_extras_prices_single_leg_open_short():
    entry = types.SimpleNamespace(
        orders=[_single("sell", 32.0, "open_short_call")], underlying="RKLB")
    out = await build_pmcc_roll_card_extras(entry, _QB({"bid": 0.30, "ask": 0.38}), _ClearAgent())
    assert out["estimate"] and out["estimate"]["direction"] == "credit"
    assert out["estimate"]["credit"] == 0.30


def test_p3b_close_and_open_are_now_short_side():
    assert _is_pmcc_short_side_group([_o("close_short_urgent")]) is True
    assert _is_pmcc_short_side_group([_o("open_short_call")]) is True
    assert {"close_short_urgent", "open_short_call"} <= _PMCC_SHORT_SIDE_ACTIONS


def test_close_all_stays_excluded_from_short_side():
    # close_all carries a LEAP leg (close_leap_urgent) -> NOT panel-only; keeps its
    # existing ceo_graph route (flagged separately as out-of-mandate).
    assert _is_pmcc_short_side_group(
        [_o("close_short_urgent"), _o("close_leap_urgent")]) is False
