"""Audit-trail completeness for the TradingView webhook handlers.

Pins the 2026-05-01 fix for the silent 503-on-empty-secret branch in
both Lord Otter and Market Cypher webhook handlers. Originally the
empty-secret path returned 503 with no audit row written, which masked
a 7-day Cypher outage. The fix writes a `webhook_rejected` row with
reason='server_side_secret_unset' so misconfiguration shows up in the
dashboard, not just the systemd journal.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_corp.agents.divisions.lord_otter import LordOtterAgent
from trading_corp.agents.divisions.market_cypher import MarketCypherAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence.db import init_db
from trading_corp.web import webhooks


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def otter_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
lord_otter:
  enabled: true
  auto_execute: false
  symbols: [BTC/USD]
  webhook_secret_env: TEST_OTTER_SECRET
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def cypher_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
market_cypher:
  enabled: true
  auto_execute: false
  symbols: [BTC/USD]
  webhook_secret_env: TEST_CYPHER_SECRET
""".strip(),
        encoding="utf-8",
    )
    return p


def _build_app_with(otter_agent=None, cypher_agent=None, *, db_url: str) -> tuple[FastAPI, LoggerAgent]:
    """Construct a minimal FastAPI app with deps shape `webhooks.register`
    expects — only the fields the empty-secret branches touch."""
    init_db(db_url)
    logger_agent = LoggerAgent(db_url)

    class _Deps:
        pass
    deps = _Deps()
    deps.logger_agent = logger_agent
    deps.lord_otter_agent = otter_agent
    deps.market_cypher_agent = cypher_agent

    app = FastAPI()
    app.state.deps = deps
    webhooks.register(app)
    return app, logger_agent


# ── Otter empty-secret branch ───────────────────────────────────────────


def test_otter_empty_secret_returns_503_AND_writes_audit_row(
    otter_yaml, tmp_db, monkeypatch,
):
    """The fix: when LORD_OTTER_WEBHOOK_SECRET is unset, the 503 path
    MUST write a webhook_rejected row with reason='server_side_secret_unset'.
    This is the load-bearing diagnostic — without it, misconfiguration
    is invisible in the dashboard."""
    # Deliberately do NOT set TEST_OTTER_SECRET → the empty-secret branch fires.
    monkeypatch.delenv("TEST_OTTER_SECRET", raising=False)
    # Disable IP-check so we get past it and reach the secret check.
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter",
        json={"secret": "anything", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 503
    assert "secret" in r.json()["reason"].lower()

    # The audit row is the actual fix being pinned
    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected, (
        "empty-secret 503 must write a webhook_rejected audit row "
        "(this gap masked a real-world 7-day Cypher outage)"
    )
    assert rejected[0]["payload"]["reason"] == "server_side_secret_unset"


# ── Cypher empty-secret branch ──────────────────────────────────────────


def test_cypher_empty_secret_returns_503_AND_writes_audit_row(
    cypher_yaml, tmp_db, monkeypatch,
):
    """Same fix on the Cypher side — this was the branch that originally
    silently dropped 7 days of TradingView Cypher alerts."""
    monkeypatch.delenv("TEST_CYPHER_SECRET", raising=False)
    monkeypatch.setenv("MARKET_CYPHER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = MarketCypherAgent(
        strategies_yaml=cypher_yaml,
        macro_calendar=MacroCalendar(path=cypher_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(cypher_agent=agent, db_url=tmp_db)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/market-cypher",
        json={"secret": "anything", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 503

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "market_cypher" and e["kind"] == "webhook_rejected"
    ]
    assert rejected, (
        "empty-secret 503 on Cypher must write a webhook_rejected audit row "
        "— this is the exact gap that masked the 7-day outage"
    )
    payload = rejected[0]["payload"]
    assert payload["reason"] == "server_side_secret_unset"
    assert payload["strategy"] == "market_cypher"


# ── Sanity: bad-secret path still works (regression check) ──────────────


def test_otter_bad_secret_path_still_audits(otter_yaml, tmp_db, monkeypatch):
    """The bad-secret 401 path was already correct. Verify the empty-secret
    fix didn't accidentally break it."""
    monkeypatch.setenv("TEST_OTTER_SECRET", "the_correct_secret")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter",
        json={"secret": "wrong_secret", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 401

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected
    assert rejected[0]["payload"]["reason"] == "bad_secret"
