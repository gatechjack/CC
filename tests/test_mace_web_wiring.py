"""MACE web-wiring completeness — ties the /mace cockpit to the main.py WebDeps
construction site (plan § Dashboard v1 + the Phase-4 "construction-completeness
test" the coupled unit calls for).

Guards the class of bug where the view field exists on WebDeps but nobody wires
it at the construction site (silent None on the dashboard forever), plus the
end-to-end render path (honest-empty on a scratch DB, and the config_hash header
when a manager is present — the Checkpoint-4 "config hash on /mace" gate).
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from trading_corp.persistence import db
from trading_corp.web import mace_view
from trading_corp.web.app import WebDeps

_ROOT = Path(__file__).resolve().parents[1]
_MAIN_PY = _ROOT / "trading_corp" / "main.py"
_ROUTES_PY = _ROOT / "trading_corp" / "web" / "routes.py"


# ── WebDeps has the fields, defaulting None ──────────────────────────────
def test_webdeps_has_mace_fields_defaulting_none():
    fields = {f.name: f for f in dataclasses.fields(WebDeps)}
    for name in ("mace_division", "mace_manager"):
        assert name in fields, f"WebDeps missing field {name}"
        assert fields[name].default is None, f"WebDeps.{name} must default None"


# ── AST: the main.py WebDeps construction site wires BOTH mace fields ─────
def test_main_py_webdeps_construction_wires_mace_fields():
    tree = ast.parse(_MAIN_PY.read_text(encoding="utf-8"))
    webdeps_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "WebDeps"
    ]
    assert webdeps_calls, "no WebDeps(...) construction found in main.py"
    # At least one construction site (the one feeding create_app) must pass BOTH
    # mace fields — otherwise the /mace view can never see the live engine.
    wired = [
        c for c in webdeps_calls
        if {"mace_division", "mace_manager"} <= {kw.arg for kw in c.keywords if kw.arg}
    ]
    assert wired, (
        "no WebDeps(...) in main.py passes both mace_division= and mace_manager= — "
        "the /mace view would render an unwired page forever")


# ── routes.py registers the view ─────────────────────────────────────────
def test_routes_registers_mace_view():
    src = _ROUTES_PY.read_text(encoding="utf-8")
    assert "from trading_corp.web import mace_view" in src
    assert "mace_view.register(app)" in src


# ── mace_badge is honest-empty (never raises) when unwired ───────────────
def test_mace_badge_unwired_when_no_division():
    class _Deps:
        mace_division = None
        mace_manager = None
        data_exec = None
    badge = mace_view.mace_badge(_Deps())
    assert badge["state"] == "unwired"
    assert badge["standby"] is True and badge["auto_execute"] is False


# ── end-to-end render (create_app runs routes.register -> mace_view) ──────
def _scratch_db(tmp_path) -> str:
    from trading_corp.persistence.db import SCHEMA
    db_url = f"sqlite:///{tmp_path / 'mace_web.db'}"
    with db.connect(db_url) as conn:
        conn.executescript(SCHEMA)
    return db_url


def _app(db_url, *, mace_division=None, mace_manager=None):
    from unittest.mock import MagicMock
    from trading_corp.agents.logger import LoggerAgent
    from trading_corp.web.app import create_app

    dx = MagicMock()
    dx.brokers = {}
    deps = WebDeps(
        db_url=db_url, db_path=db_url, mode="PAPER",
        logger_agent=LoggerAgent(db_url=db_url), data_exec=dx,
        trend_agent=None, portfolio=None, pmcc_agent=None, fidelity_agent=None,
        paper_broker=MagicMock(), secrets=None,
        mace_division=mace_division, mace_manager=mace_manager,
    )
    return create_app(deps)


def test_mace_route_registered():
    """create_app must mount /mace + the rungs partial via routes.register."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        app = _app(_scratch_db(Path(d)))
    paths = {r.path for r in app.routes}
    assert "/mace" in paths
    assert "/mace/partials/rungs" in paths


def test_mace_page_renders_empty_state(tmp_path):
    """Scratch DB, no manager -> 200, honest-empty, NEVER a 500."""
    from fastapi.testclient import TestClient
    app = _app(_scratch_db(tmp_path))
    client = TestClient(app)
    resp = client.get("/mace")
    assert resp.status_code == 200
    html = resp.text
    assert "MACE Engine" in html
    assert "no open rungs" in html
    assert "NOT WIRED" in html                 # no division -> unwired badge


def test_mace_rungs_partial_renders_empty(tmp_path):
    from fastapi.testclient import TestClient
    app = _app(_scratch_db(tmp_path))
    resp = TestClient(app).get("/mace/partials/rungs")
    assert resp.status_code == 200
    assert "no open rungs" in resp.text
    # self-poll wiring present so the panel keeps refreshing
    assert 'hx-get="/mace/partials/rungs"' in resp.text


def test_mace_page_shows_config_hash_when_manager_present(tmp_path):
    """With a real frozen MaceConfig on the manager, the header shows config_hash
    and the effective-config section binds — the Checkpoint-4 'config hash on
    /mace' gate."""
    from fastapi.testclient import TestClient
    from trading_corp.agents.divisions.robinhood_mace import RobinhoodMaceAgent
    from trading_corp.mace.config import load_mace_config

    cfg = load_mace_config(
        _ROOT / "config" / "mace.yaml",
        exdiv_calendar_path=_ROOT / "config" / "ex_dividend_calendar.yaml")

    class _Mgr:
        pass
    mgr = _Mgr()
    mgr.cfg = cfg
    division = RobinhoodMaceAgent(
        cfg, divisions_yaml=_ROOT / "config" / "divisions.yaml",
        strategies_yaml=_ROOT / "config" / "strategies.yaml")

    app = _app(_scratch_db(tmp_path), mace_division=division, mace_manager=mgr)
    resp = TestClient(app).get("/mace")
    assert resp.status_code == 200
    html = resp.text
    assert cfg.config_hash[:12] in html        # header config_hash chip
    assert "Effective config" in html
    assert "SPY" in html                        # retired symbol still rendered (defined block)
    assert "IBIT" in html and "XLE" in html and "GDX" in html   # 3-active universe rows
