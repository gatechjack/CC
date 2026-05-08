"""Webhook-level integration tests for the Phase 1e TradeConfirmation consult.

The consult logic itself is unit-tested in
`test_research_trade_confirmation_consult.py`. This file pins the
**wiring** between the FastAPI webhook handler and the consult helper —
the place where research can prevent a real order from reaching the
risk gate.

What's pinned here:
  - push_back: handler returns 200 + skipped_by_research, telegram_notify
    fires with the rationale, risk_agent.evaluate is NEVER called, and an
    audit row lands with kind=research_tradeconf_pushback_acted_on.
  - no_research smoke: research_firm=None routes through the consult's
    no_research branch and reaches the risk gate as before. The consult
    is invisible when not wired.

Stub policy: minimal. The agent's on_alert is monkey-patched to return a
fixed ProposedOrder so we don't drag tier-classification details into a
test about webhook wiring. The risk_agent is a Mock so we can assert
"never called" cleanly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_corp.agents.strategies.lord_otter import LordOtterAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import ResearchFirmDeps
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import ProposedOrder
from trading_corp.web import webhooks

# Reuse the deterministic experts from the e2e test module.
from tests.test_research_engagement_e2e import (
    FakeMacroExpert, FakeSentimentExpert, FakeTechnicalExpert,
)


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


def _fixed_proposed_order() -> ProposedOrder:
    """The order Otter pretends to produce. Buy BTC/USD at market — the
    consult sees this verbatim and decides what to do with it."""
    return ProposedOrder(
        strategy="lord_otter",
        symbol="BTC/USD",
        side="buy",
        qty=0.01,
        order_type="market",
        rationale="alert-driven (test)",
        extra={"tier": "standard", "size_pct_equity": 0.015},
    )


def _alert_payload(secret: str = "the_correct_secret") -> dict:
    """Minimal TradingView alert payload that passes JSON + secret +
    timestamp-skew checks."""
    return {
        "secret": secret,
        "ticker": "BTCUSD",
        "signal": "otter_buy",
        "price": "65000.0",
        "time": datetime.now(timezone.utc).isoformat(),
        "interval": "3",
    }


def _build_deps(
    *,
    db_url: str,
    otter_agent: LordOtterAgent,
    research_firm: ResearchFirmDeps | None,
    risk_agent: MagicMock,
    telegram_channel: MagicMock | None,
):
    """Construct a deps object shaped like WebDeps with only the fields
    the lord_otter webhook touches."""
    init_db(db_url)
    logger_agent = LoggerAgent(db_url)

    class _Deps:
        pass
    deps = _Deps()
    deps.logger_agent = logger_agent
    deps.lord_otter_agent = otter_agent
    deps.market_cypher_agent = None
    deps.data_exec = None              # broker None — webhook short-circuits at risk gate
    deps.trend_agent = None
    deps.risk_agent = risk_agent
    deps.research_firm = research_firm
    deps.telegram_channel = telegram_channel
    return deps, logger_agent


def _build_app(deps) -> FastAPI:
    app = FastAPI()
    app.state.deps = deps
    webhooks.register(app)
    return app


def _make_otter_agent(otter_yaml: Path) -> LordOtterAgent:
    """Build a real LordOtterAgent with on_alert monkey-patched to
    return a fixed ProposedOrder. Bypasses bias / classify_tier /
    halt logic — those are not what this test pins."""
    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    agent.on_alert = lambda payload, *, account_equity=None, held_qty=None: (
        _fixed_proposed_order(), "fixed test order"
    )
    return agent


# ── Test 1: push_back wiring ────────────────────────────────────────────


def test_push_back_skips_order_and_notifies_board(
    otter_yaml, tmp_db, monkeypatch,
):
    """When the research firm returns push_back, the webhook handler MUST:
      - return 200 with status=skipped_by_research + verdict=push_back
      - call telegram_channel.push with the rationale
      - write a research_tradeconf_pushback_acted_on audit row
      - NOT call risk_agent.evaluate (the order is dead at this stage)
    """
    monkeypatch.setenv("TEST_OTTER_SECRET", "the_correct_secret")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    init_db(tmp_db)
    logger_agent = LoggerAgent(tmp_db)

    # All-bearish experts on a 'buy' proposal -> deterministic push_back.
    experts = {
        "technical": FakeTechnicalExpert(lean="bearish", confidence=0.8),
        "macro": FakeMacroExpert(lean="bearish", confidence=0.7),
        "sentiment": FakeSentimentExpert(lean="bearish", confidence=0.6),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    research_firm = ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )

    risk_agent = MagicMock()
    telegram = MagicMock()
    telegram.push = AsyncMock()

    agent = _make_otter_agent(otter_yaml)
    deps, _ = _build_deps(
        db_url=tmp_db,
        otter_agent=agent,
        research_firm=research_firm,
        risk_agent=risk_agent,
        telegram_channel=telegram,
    )
    deps.logger_agent = logger_agent  # share the same handle so we read it back
    app = _build_app(deps)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter", json=_alert_payload(),
    )

    # Return-fast architecture (2026-05-02): the HTTP response is now
    # uniform `{"status":"accepted"}`. The push_back outcome lands in the
    # audit + Telegram side-effects below, which is the load-bearing
    # contract for downstream consumers (dashboard reads audit rows;
    # the Board reads Telegram). Tests now assert on those side effects.
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"

    # Risk gate MUST not have been called — the order is dead.
    risk_agent.evaluate.assert_not_called()

    # Telegram notify MUST have been called with the rationale.
    telegram.push.assert_called_once()
    notify_msg = telegram.push.call_args[0][0]
    assert "lord-otter" in notify_msg
    assert "research vetoed" in notify_msg

    # Division-side audit row pinned to the engagement.
    events = logger_agent.recent_events(limit=40)
    pushback = [
        e for e in events
        if e["kind"] == "research_tradeconf_pushback_acted_on"
    ]
    assert len(pushback) == 1
    payload = pushback[0]["payload"]
    assert payload["symbol"] == "BTC/USD"
    assert payload["side"] == "buy"
    assert payload.get("engagement_id")  # joinable to the engagement-side row

    # Engagement-side row also lands.
    kinds = [e["kind"] for e in events]
    assert "research_trade_confirmation_emitted" in kinds


# ── Test 2: no_research smoke ───────────────────────────────────────────


def test_no_research_firm_falls_through_to_existing_flow(
    otter_yaml, tmp_db, monkeypatch,
):
    """research_firm=None must NOT skip-by-research. The consult returns
    no_research transparently and the webhook proceeds to the existing
    risk-gate flow.

    Side-effect we observe: the response is NOT skipped_by_research.
    Audit log shows zero research_tradeconf_* rows. risk_agent.evaluate
    isn't reached either, but only because the broker isn't wired in
    this minimal fixture (handler short-circuits with 503 'no broker
    for division'). Either way: the consult was invisible."""
    monkeypatch.setenv("TEST_OTTER_SECRET", "the_correct_secret")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    risk_agent = MagicMock()
    telegram = MagicMock()
    telegram.push = AsyncMock()

    agent = _make_otter_agent(otter_yaml)
    deps, logger_agent = _build_deps(
        db_url=tmp_db,
        otter_agent=agent,
        research_firm=None,            # <- the variable under test
        risk_agent=risk_agent,
        telegram_channel=telegram,
    )
    app = _build_app(deps)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter", json=_alert_payload(),
    )

    # Return-fast: HTTP body is uniformly {"status":"accepted"}.
    # Pre-refactor this asserted body wasn't `skipped_by_research`;
    # post-refactor that's tautologically true (always `accepted`),
    # so the load-bearing contract is the absence of research_*
    # audit rows + Telegram NOT being called, asserted further down.
    body = r.json()
    assert body.get("status") == "accepted"

    # Telegram notify must NOT fire on the no-research path.
    telegram.push.assert_not_called()

    # No Phase 1e audit rows of any kind.
    events = logger_agent.recent_events(limit=40)
    for e in events:
        assert not e["kind"].startswith("research_tradeconf_"), (
            f"unexpected research_tradeconf_* row when research_firm=None: "
            f"{e['kind']}"
        )
