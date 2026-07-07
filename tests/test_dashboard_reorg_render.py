"""Render + relocation regression for the 2026-07-07 dashboard tile reorg.

Pins the outcome of moving the Stage-1 monitoring tiles off the Overview:
  - Stage-1 row gone; Live trade flow renders as a bottom strip.
  - HITL activity folded into the Pending Approvals stat card, and the
    pending count is registry-backed (not the all-time proposed_order
    risk_approved DB residue that caused the "59 but blank /approvals" bug).
  - Gate (a) resilience relocated to the bitunix_futures division page.
  - The /partials/stage1-monitoring route is removed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trading_corp.comms.pending_registry import PendingApprovalRegistry
from trading_corp.persistence import db
from trading_corp.web import data as web_data
from trading_corp.web.app import WebDeps, create_app


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """build_command_center fans out to the market ribbon + VIX; stub both so
    the render tests stay offline + deterministic."""
    async def _no_ribbon():
        return []
    monkeypatch.setattr(web_data, "_build_market_ribbon", _no_ribbon)
    monkeypatch.setattr(web_data, "_safe_get_vix", lambda: None)


def _deps(db_url, registry):
    return WebDeps(
        db_url=db_url, db_path=db_url.replace("sqlite:///", ""), mode="PAPER",
        logger_agent=None, data_exec=None, trend_agent=None, portfolio=None,
        pmcc_agent=None, fidelity_agent=None, paper_broker=None, secrets=None,
        risk_agent=None, pending_registry=registry,
    )


@pytest.fixture
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'reorg.db'}"
    db.init_db(db_url)
    deps = _deps(db_url, PendingApprovalRegistry(logger_agent=None))
    return TestClient(create_app(deps))


def test_home_renders_without_stage1_row(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Stage 1 monitoring" not in r.text


def test_home_trade_flow_strip_present(client):
    assert "Live trade flow" in client.get("/").text


def test_home_pending_approvals_tile_absorbs_hitl(client):
    body = client.get("/").text
    assert "Pending approvals" in body
    # HITL sub-signals folded into the merged stat card.
    assert "auto-live 24h" in body
    assert "appr 24h" in body


def test_stage1_monitoring_partial_route_removed(client):
    assert client.get("/partials/stage1-monitoring").status_code == 404


def test_bitunix_futures_page_has_gate_a_tile(client):
    r = client.get("/division/bitunix_futures")
    assert r.status_code == 200
    assert "Gate (a) resilience" in r.text


def test_pending_count_is_registry_backed(tmp_path):
    """The merged tile's count comes from the registry (actionable), not the
    DB risk_approved residue — this is the split-brain fix."""
    db_url = f"sqlite:///{tmp_path / 'reg.db'}"
    db.init_db(db_url)
    deps = _deps(db_url, SimpleNamespace(pending_count=lambda: 7))
    snap = asyncio.run(web_data.build_command_center(deps))
    assert snap.pending_approvals == 7
    assert snap.hitl["pending"] == 7
