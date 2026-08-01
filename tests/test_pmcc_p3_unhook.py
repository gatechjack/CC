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
