"""Return-fast architecture pin (BACKLOG.md 2026-05-02 webhook refactor).

The webhook handlers (lord_otter / market_cypher) MUST:

  1. Return HTTP 200 with `{"status": "accepted", ...}` for any valid
     alert. The actual outcome (alert_ignored / risk_rejected /
     would_have_placed / filled / skipped_by_research / agent_error)
     lands in the AUDIT log, not the HTTP body. This is the contract
     that prevents TradingView's 10s timeout from biting us when the
     downstream consult/risk-gate/place chain is slow.

  2. Audit `webhook_received` BEFORE handing off to the background task,
     so even if the background processing crashes we have a record of
     the inbound alert.

  3. Catch-all the background task. Any unhandled exception writes an
     `agent_error` audit row tagged with `phase=background_processing`
     AND a Telegram notify fires so silent crashes are impossible.

These tests pin the contract. Failure means TV will start timing out
again on slow alerts — exactly the bug we shipped this fix for.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_corp.agents.strategies.lord_otter import LordOtterAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import ProposedOrder
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
  division: coinbase_spot
""".strip(),
        encoding="utf-8",
    )
    return p


def _alert_payload(secret: str = "ok") -> dict:
    return {
        "secret": secret,
        "ticker": "BTCUSD",
        "signal": "otter_buy",
        "price": "65000.0",
        "time": datetime.now(timezone.utc).isoformat(),
        "interval": "3",
    }


def _make_agent_with_fixed_order(otter_yaml: Path) -> LordOtterAgent:
    """Real LordOtterAgent but on_alert returns a fixed order so the test
    isn't sensitive to bias/tier-classifier internals."""
    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    agent.on_alert = lambda payload, *, account_equity=None, held_qty=None: (
        ProposedOrder(
            strategy="lord_otter", symbol="BTC/USD",
            side="buy", qty=0.01, rationale="test",
            extra={"tier": "standard"},
        ),
        "test order",
    )
    return agent


def _make_agent_returns_none(otter_yaml: Path) -> LordOtterAgent:
    """Agent whose on_alert returns (None, 'ignored') — short-circuits
    in the background task BEFORE any slow research consult."""
    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    agent.on_alert = lambda payload, *, account_equity=None, held_qty=None: (
        None, "test ignored: nothing to act on"
    )
    return agent


def _build_deps(*, db_url: str, otter_agent: LordOtterAgent,
                telegram=None, research_firm=None):
    init_db(db_url)
    logger_agent = LoggerAgent(db_url)

    class _Deps:
        pass
    deps = _Deps()
    deps.logger_agent = logger_agent
    deps.lord_otter_agent = otter_agent
    deps.market_cypher_agent = None
    deps.data_exec = None
    deps.trend_agent = None
    deps.risk_agent = MagicMock()
    deps.research_firm = research_firm
    deps.telegram_channel = telegram
    return deps, logger_agent


def _build_app(deps) -> FastAPI:
    app = FastAPI()
    app.state.deps = deps
    webhooks.register(app)
    return app


# ── Contract 1: HTTP body is uniformly "accepted" on valid alerts ──────


def test_otter_valid_alert_returns_accepted_body(
    otter_yaml, tmp_db, monkeypatch,
):
    """Any valid Otter alert (regardless of downstream outcome) returns
    200 + `{"status":"accepted"}`. This is the load-bearing contract:
    if a future refactor breaks this and starts returning the actual
    outcome inline, the TV-timeout bug is back."""
    monkeypatch.setenv("TEST_OTTER_SECRET", "ok")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    agent = _make_agent_returns_none(otter_yaml)
    deps, _ = _build_deps(db_url=tmp_db, otter_agent=agent)
    app = _build_app(deps)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter", json=_alert_payload(),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"
    assert body["signal"] == "otter_buy"
    assert body["symbol"] == "BTC/USD"
    # Outcome-shaped keys MUST NOT be in the body — that pattern was
    # the pre-refactor behavior and it's exactly what TV's 10s timeout
    # punished. See module docstring.
    assert "verdict" not in body
    assert "order_id" not in body
    assert "decision" not in body


# ── Contract 2: webhook_received audit lands during the SYNC phase ─────


def test_webhook_received_audit_written_synchronously(
    otter_yaml, tmp_db, monkeypatch,
):
    """The webhook_received audit row lands BEFORE the background task is
    dispatched. Even if the background task crashes immediately, we
    still have a record that the inbound alert arrived.

    Implementation detail this pins: audit-write order in the handler.
    `deps.logger_agent.log_event(... kind='webhook_received' ...)` must
    appear BEFORE `background_tasks.add_task(...)`.
    """
    monkeypatch.setenv("TEST_OTTER_SECRET", "ok")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    agent = _make_agent_returns_none(otter_yaml)
    deps, logger_agent = _build_deps(db_url=tmp_db, otter_agent=agent)
    app = _build_app(deps)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter", json=_alert_payload(),
    )

    assert r.status_code == 200
    events = logger_agent.recent_events(limit=20)
    received = [e for e in events if e["kind"] == "webhook_received"]
    assert len(received) == 1
    assert received[0]["actor"] == "lord_otter"
    assert received[0]["payload"]["signal"] == "otter_buy"


# ── Contract 3: alert_ignored landing happens in the background task ───


def test_alert_ignored_audit_writes_after_background_runs(
    otter_yaml, tmp_db, monkeypatch,
):
    """The alert_ignored audit row is written by the BACKGROUND task,
    not the sync handler. With FastAPI's TestClient the background
    task does run before the test's next line, so we can still see
    the audit row — but the sync HTTP body is `accepted`, not
    `ignored`."""
    monkeypatch.setenv("TEST_OTTER_SECRET", "ok")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    agent = _make_agent_returns_none(otter_yaml)
    deps, logger_agent = _build_deps(db_url=tmp_db, otter_agent=agent)
    app = _build_app(deps)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter", json=_alert_payload(),
    )
    assert r.json()["status"] == "accepted"   # sync response

    # Background ran by now — alert_ignored is on disk.
    events = logger_agent.recent_events(limit=20)
    ignored = [e for e in events if e["kind"] == "alert_ignored"]
    assert len(ignored) == 1
    assert ignored[0]["payload"]["reason"] == "test ignored: nothing to act on"


# ── Contract 4: background-task crash writes agent_error + Telegram ────


def test_background_crash_audits_and_notifies_telegram(
    otter_yaml, tmp_db, monkeypatch,
):
    """If the background task hits an unhandled exception, the catch-all
    MUST write an `agent_error` audit row (with phase=background_processing)
    AND fire a Telegram notify so the Board sees it. Silent crashes
    are unacceptable in the return-fast architecture — without this,
    TV gets a 200 and the whole alert disappears."""
    monkeypatch.setenv("TEST_OTTER_SECRET", "ok")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    # Patch the consult to raise. The agent returns a real order so we
    # reach the consult call inside the background task.
    async def boom(**kw):
        raise RuntimeError("simulated crash mid-consult")

    agent = _make_agent_with_fixed_order(otter_yaml)
    telegram = MagicMock()
    telegram.push = AsyncMock()
    deps, logger_agent = _build_deps(
        db_url=tmp_db, otter_agent=agent,
        telegram=telegram, research_firm=object(),
    )
    app = _build_app(deps)

    client = TestClient(app)
    with patch(
        "trading_corp.agents.research.trade_confirmation_consult."
        "consult_research_for_trade_confirmation",
        boom,
    ):
        r = client.post(
            "/webhook/tradingview/lord-otter", json=_alert_payload(),
        )

    # Sync handler still responded normally.
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"

    # Background catch-all fired both side effects.
    events = logger_agent.recent_events(limit=20)
    crashes = [
        e for e in events
        if e["kind"] == "agent_error"
        and e["payload"].get("phase") == "background_processing"
    ]
    assert len(crashes) == 1
    assert "simulated crash" in crashes[0]["payload"]["error"]

    telegram.push.assert_called_once()
    notify_msg = telegram.push.call_args[0][0]
    assert "background crash" in notify_msg
    assert "lord-otter" in notify_msg


# ── Cypher handler has the same contract ───────────────────────────────


def test_cypher_valid_alert_returns_accepted_body(tmp_path, tmp_db, monkeypatch):
    """Mirror of test_otter_valid_alert_returns_accepted_body for the
    Cypher handler. Both webhooks must implement the return-fast contract
    or the timeout bug returns asymmetrically."""
    cypher_yaml = tmp_path / "strategies.yaml"
    cypher_yaml.write_text(
        """
market_cypher:
  enabled: true
  auto_execute: false
  symbols: [BTC/USD]
  webhook_secret_env: TEST_CYPHER_SECRET
  division: coinbase_spot
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_CYPHER_SECRET", "ok")
    monkeypatch.setenv("MARKET_CYPHER_DISABLE_IP_CHECK", "1")

    from trading_corp.agents.strategies.market_cypher import MarketCypherAgent
    from trading_corp.data.macro_calendar import MacroCalendar
    agent = MarketCypherAgent(
        strategies_yaml=cypher_yaml,
        macro_calendar=MacroCalendar(path=cypher_yaml.parent / "no.yaml"),
        db_url=None,
    )
    agent.on_alert = lambda payload, *, account_equity=None, held_qty=None: (
        None, "test ignored",
    )

    init_db(tmp_db)
    logger_agent = LoggerAgent(tmp_db)

    class _Deps:
        pass
    deps = _Deps()
    deps.logger_agent = logger_agent
    deps.lord_otter_agent = None
    deps.market_cypher_agent = agent
    deps.data_exec = None
    deps.trend_agent = None
    deps.risk_agent = MagicMock()
    deps.research_firm = None
    deps.telegram_channel = None

    app = FastAPI()
    app.state.deps = deps
    webhooks.register(app)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/market-cypher",
        json={
            "secret": "ok", "ticker": "BTCUSD", "signal": "mc_a_red_diamond",
            "price": "65000", "time": datetime.now(timezone.utc).isoformat(),
            "interval": "240",
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"
    assert "verdict" not in body
    assert "order_id" not in body
