"""Audit-trail completeness tests for the research firm (v3).

Pins (CLAUDE.md §1, design §3.6, §4.3):
  - Every product is written to audit_event BEFORE any routing branch
  - Every research-firm audit row carries `actor='research_firm'`,
    `payload.engagement_id`, `payload.requesting_division`,
    `payload.product_type`, `payload.asset_class`
  - Audit kinds match the constants in schemas.py
  - Q11: every terminal row pins both engagement_started_ts and
    engagement_completed_ts
  - Refinement 4: research_data_fetch_attempted is FAILURE-ONLY —
    successful fetches do NOT write the audit row
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


# ── Fakes ────────────────────────────────────────────────────────────────


class _FakeTech:
    role = "technical"

    def __init__(self, *, fetch_failure: bool = False):
        self.fetch_failure = fetch_failure

    async def analyze(self, *, engagement_id, symbol, context=None, on_data_fetch=None):
        if self.fetch_failure and on_data_fetch is not None:
            on_data_fetch(source=f"fake:{symbol}", ok=False, error="simulated")
            return (
                schemas.ExpertReport(
                    role="technical", engagement_id=engagement_id, symbol=symbol,
                    summary="[REFUSED] technical: simulated",
                    data_sufficiency=False, refusal_reason="simulated",
                ),
                0.0,
            )
        return (
            schemas.ExpertReport(
                role="technical", engagement_id=engagement_id, symbol=symbol,
                summary=f"{symbol}: fake bullish",
                confidence_score=0.7, directional_lean="bullish",
                data_sufficiency=True,
            ),
            0.0,
        )


class _FakeMacro:
    role = "macro"

    async def analyze(self, *, engagement_id, symbol, context=None, on_data_fetch=None):
        return (
            schemas.ExpertReport(
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
    experts = {"technical": _FakeTech(), "macro": _FakeMacro()}
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


@pytest.fixture
def deps_with_fetch_failure(tmp_db: str, monkeypatch) -> ResearchFirmDeps:
    init_db(tmp_db)
    from trading_corp.agents.research import graph as graph_mod
    monkeypatch.setattr(
        graph_mod, "_load_starter_universe",
        lambda key: ["AAPL", "MSFT"],
    )
    from trading_corp.utils import market_data
    monkeypatch.setattr(market_data, "get_next_earnings", lambda *a, **kw: None)

    logger_agent = LoggerAgent(tmp_db)
    experts = {"technical": _FakeTech(fetch_failure=True), "macro": _FakeMacro()}
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


def _make_spec():
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={"category": "large_cap"},
            capacity_dollars=10_000.0,
            n_candidates=2,
            starter_universe_key="large_mid_cap",
        ),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )


# ── Tests ────────────────────────────────────────────────────────────────


async def test_every_research_audit_row_carries_canonical_tags(deps):
    """Every research_firm audit row MUST carry engagement_id +
    requesting_division + product_type + asset_class in payload
    (design §4.3)."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)

    events = deps.logger_agent.recent_events(limit=200)
    research_rows = [e for e in events if e["actor"] == "research_firm"]
    assert research_rows, "no research_firm audit rows"

    for row in research_rows:
        payload = row["payload"]
        assert payload.get("engagement_id") == spec.engagement_id
        assert payload.get("requesting_division") == "robinhood_pmcc"
        assert payload.get("product_type") == "candidate_recommendation"
        assert payload.get("asset_class") == "equity"


async def test_product_audit_kind_emitted_with_full_payload(deps):
    """`research_candidate_recommendation_emitted` row must contain the
    full product payload (design §4.2 universal audit-write rule)."""
    spec = _make_spec()
    rec = await run_engagement(spec, deps=deps)
    assert rec is not None

    events = deps.logger_agent.recent_events(limit=200)
    emitted = [
        e for e in events
        if e["kind"] == schemas.AUDIT_KIND_CANDIDATE_RECOMMENDATION_EMITTED
    ]
    assert len(emitted) == 1
    product = emitted[0]["payload"]["product"]
    assert product["asset_class"] == rec.asset_class
    assert len(product["candidates"]) == len(rec.candidates)


async def test_engagement_started_row_written_after_kill_switch_passes(deps):
    """The `research_engagement_started` row should appear when the
    kill switch is absent — ordering matters."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    kinds = [e["kind"] for e in deps.logger_agent.recent_events(limit=80)]
    assert schemas.AUDIT_KIND_ENGAGEMENT_STARTED in kinds


async def test_engagement_started_ts_pinned_in_started_row(deps):
    """Q11: the engagement_started row payload includes the started ts."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    started_rows = [
        e for e in deps.logger_agent.recent_events(limit=80)
        if e["kind"] == schemas.AUDIT_KIND_ENGAGEMENT_STARTED
    ]
    assert len(started_rows) == 1
    assert started_rows[0]["payload"].get("engagement_started_ts")


async def test_terminal_row_pins_both_started_and_completed_ts(deps):
    """Q11: every terminal audit row carries engagement_started_ts +
    engagement_completed_ts in payload (so dashboard can compute duration
    without joining)."""
    spec = _make_spec()
    rec = await run_engagement(spec, deps=deps)
    assert rec is not None

    events = deps.logger_agent.recent_events(limit=200)
    emitted = [
        e for e in events
        if e["kind"] == schemas.AUDIT_KIND_CANDIDATE_RECOMMENDATION_EMITTED
    ]
    assert emitted
    payload = emitted[0]["payload"]
    assert payload.get("engagement_started_ts")
    assert payload.get("engagement_completed_ts")


async def test_data_fetch_audit_failure_only(deps):
    """Refinement 4: research_data_fetch_attempted fires ONLY on failure.
    With a happy-path fake (no fetch failures), there should be ZERO
    data_fetch rows even though the engagement succeeded."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_DATA_FETCH
    ]
    assert rows == [], (
        "data_fetch_attempted must be FAILURE-ONLY — successful fetches "
        "are silent (Refinement 4)"
    )


async def test_data_fetch_audit_fires_on_failure(deps_with_fetch_failure):
    """Conversely, when a fetch fails, the audit row IS written."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps_with_fetch_failure)
    rows = [
        e for e in deps_with_fetch_failure.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_DATA_FETCH
    ]
    assert rows, "expected a data_fetch row on simulated failure"
    for r in rows:
        assert r["payload"]["ok"] is False
        assert r["payload"]["error"]


async def test_expert_completed_rows_for_real_experts(deps):
    """Real experts (technical + macro) write `research_expert_completed`
    on data_sufficiency=True."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_EXPERT_COMPLETED
    ]
    # 2 real experts × 2 candidates = 4 completed rows
    assert len(rows) == 4
    roles = {r["payload"]["expert_role"] for r in rows}
    assert "technical" in roles
    assert "macro" in roles


async def test_expert_refused_rows_for_stub_experts(deps):
    """Stub experts (sentiment + fundamental in 1a-1) write
    `research_expert_refused` rows — load-bearing for the synthesis
    'treat refused dimension as unobserved' semantics."""
    spec = _make_spec()
    await run_engagement(spec, deps=deps)
    rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_EXPERT_REFUSED
    ]
    # 2 stubs × 2 candidates = 4 refusals
    assert len(rows) == 4
    roles = {r["payload"]["expert_role"] for r in rows}
    assert "sentiment" in roles
    assert "fundamental" in roles
