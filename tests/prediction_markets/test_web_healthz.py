"""Tests for pm_web /healthz (P2 CP2 Phase 1). Offline; FastAPI TestClient; proves standalone (no engine imports).

Spec: reports/prediction_markets/P2_PLAN.md §3.1, §6.0.
"""
import subprocess
import sys

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db


def _client(monkeypatch, tmp_path, *, init: bool):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)          # /healthz reads the DB path from env, per request
    if init:
        db.init_db(p)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_healthz_ok(tmp_path, monkeypatch):
    r = _client(monkeypatch, tmp_path, init=True).get("/healthz")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["service"] == "pm_web"
    assert j["pm_db_schema_version"] == 8        # migrations 1-8 applied (P1..CP3b-2 007, CP3b Stage-0 008 pm_watchlist.active)


def test_healthz_degraded_on_unmigrated_db(tmp_path, monkeypatch):
    # DB path set but NOT initialized -> schema_version table absent -> 503 degraded, NOT a faked 200.
    r = _client(monkeypatch, tmp_path, init=False).get("/healthz")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"


def test_pm_web_imports_no_engine():
    # Standalone by construction: importing the app must NOT pull trading_corp.web / main / agents. Run in a
    # FRESH interpreter so the assertion is not polluted by other tests' imports in the same session.
    code = (
        "import trading_corp.prediction_markets.web.app, sys; "
        "bad=sorted(m for m in sys.modules if m.split('.')[:2]==['trading_corp','web'] "
        "or m.startswith('trading_corp.main') or m.startswith('trading_corp.agents')); "
        "print('ENGINE_MODULES', bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, "pm_web pulled engine modules:\n" + r.stdout + r.stderr
