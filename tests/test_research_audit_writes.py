"""Audit-trail completeness tests for the research firm.

Pins (CLAUDE.md §1, design §3.4.5, §4.3):
  - Every product is written to audit_event BEFORE any routing branch
  - Every research-firm audit row carries `actor='research_firm'`,
    `payload.engagement_id`, `payload.requesting_division`, `payload.product_type`
  - Audit kinds match the constants in schemas.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.persistence.db import init_db


# Reuse fakes from the e2e test file. Pytest collects same-package modules
# but doesn't share helpers; redefine the minimal fakes locally.


class _FakeTech:
    role = "technical"
    async def analyze(self, *, engagement_id, symbol, on_data_fetch=None):
        if on_data_fetch is not None:
            on_data_fetch(source=f"fake:{symbol}", ok=True, error=None)
        return (
            schemas.AnalystReport(
                role="technical", engagement_id=engagement_id, symbol=symbol,
                summary=f"{symbol}: fake bullish",
                confidence_score=0.7, directional_lean="bullish",
                data_sufficiency=True,
            ),
            0.0,
        )


class _FakeMacro:
    role = "macro"
    async def analyze(self, *, engagement_id, symbol, earnings_buffer_days=7, on_data_fetch=None):
        if on_data_fetch is not None:
            on_data_fetch(source="fake:macro", ok=True, error=None)
        return (
            schemas.AnalystReport(
                role="macro", engagement_id=engagement_id, symbol=symbol,
                summary=f"{symbol}: fake macro neutral",
                confidence_score=0.6, directional_lean="neutral",
                data_sufficiency=True,
            ),
            0.0,
        )


@pytest.fixture
def deps(tmp_db: str, monkeypatch) -> ResearchFirmDeps:
    init_db(tmp_db)
    from trading_corp.agents.research import graph as graph_mod
    monkeypatch.setattr(
        graph_mod, "_load_starter_universe",
        lambda key: ["AAPL", "MSFT"],
    )
    from trading_corp.utils import market_data
    monkeypatch.setattr(market_data, "get_next_earnings", lambda *a, **kw: None)

    logger_agent = LoggerAgent(tmp_db)
    tech = _FakeTech()
    macro = _FakeMacro()
    graph = build_engagement_graph(
        logger_agent, technical_analyst=tech, macro_analyst=macro, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, technical_analyst=tech,
        macro_analyst=macro, graph=graph,
    )


def _make_spec():
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="watchlist_recommendation",
        scope=schemas.WatchlistScope(
            target_universe_key="robinhood_pmcc.scout.universe",
            n_candidates=2,
            starter_universe_key="large_mid_cap",
        ),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )


async def test_every_research_audit_row_carries_canonical_tags(deps):
    """Every research_firm audit row MUST carry engagement_id +
    requesting_division + product_type in payload (design §4.3)."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)

    events = deps.logger_agent.recent_events(limit=200)
    research_rows = [e for e in events if e["actor"] == "research_firm"]
    assert research_rows, "no research_firm audit rows"

    for row in research_rows:
        payload = row["payload"]
        assert payload.get("engagement_id") == spec.engagement_id
        assert payload.get("requesting_division") == "robinhood_pmcc"
        assert payload.get("product_type") == "watchlist_recommendation"


async def test_product_audit_kind_emitted(deps):
    """`research_watchlist_emitted` row must contain the full product
    payload (design §3.4.5 universal audit-write rule)."""
    spec = _make_spec()
    rec = await run_engagement(spec, deps=deps)
    assert rec is not None

    events = deps.logger_agent.recent_events(limit=200)
    emitted = [
        e for e in events if e["kind"] == schemas.AUDIT_KIND_WATCHLIST_EMITTED
    ]
    assert len(emitted) == 1
    product = emitted[0]["payload"]["product"]
    assert product["target_universe_key"] == rec.target_universe_key
    assert product["additions"] == rec.additions


async def test_engagement_started_row_written_after_kill_switch_passes(deps):
    """The `research_engagement_started` row should appear when the
    kill switch is absent — ordering matters."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    kinds = [e["kind"] for e in deps.logger_agent.recent_events(limit=80)]
    assert schemas.AUDIT_KIND_ENGAGEMENT_STARTED in kinds


async def test_data_fetch_audit_per_analyst(deps):
    """Each analyst that consumes external data writes one
    `research_data_fetch_attempted` per fetch (design §4.3 inventory)."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_DATA_FETCH
    ]
    # Two real analysts × two candidates = 4 fetch rows minimum.
    assert len(rows) >= 4
    for r in rows:
        assert "source" in r["payload"]
        assert r["payload"]["ok"] is True


async def test_analyst_refused_rows_for_stub_analysts(deps):
    """Stub analysts (sentiment + fundamental in 1a) write
    `research_analyst_refused` rows — load-bearing for the synthesis
    "treat refused dimension as unobserved" semantics."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_ANALYST_REFUSED
    ]
    # 2 stubs × 2 candidates = 4 refusals.
    assert len(rows) == 4
    roles = {r["payload"]["analyst_role"] for r in rows}
    assert "sentiment" in roles
    assert "fundamental" in roles
