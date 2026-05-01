"""End-to-end engagement runs through the LangGraph subgraph (v3).

Covers happy path + sad paths (kill switch / out-of-scope / expert-refused-storm
/ Layer 2 rejection).

These tests use FAKE experts (no yfinance, no LLM) so they're deterministic
and offline. The engagement graph itself runs unmocked — it's the integration
between scope-check, registry lookup, fan-out, synthesis, and post-validate
that we're pinning.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.agents.research.kill_switch import KILL_SWITCH_FILENAME
from trading_corp.persistence.db import init_db


# ── Fake experts ─────────────────────────────────────────────────────────


class FakeTechnicalExpert:
    """Returns a controllable ExpertReport. Cost is configurable."""
    role = "technical"

    def __init__(
        self,
        *,
        confidence: float = 0.7,
        lean: str | None = "bullish",
        sufficient: bool = True,
        cost_per_call: float = 0.0,
    ) -> None:
        self.confidence = confidence
        self.lean = lean
        self.sufficient = sufficient
        self.cost_per_call = cost_per_call

    async def analyze(self, *, engagement_id, symbol, context=None, on_data_fetch=None):
        if not self.sufficient:
            return (
                schemas.ExpertReport(
                    role="technical",
                    engagement_id=engagement_id,
                    symbol=symbol,
                    summary="[REFUSED] technical: simulated outage",
                    data_sufficiency=False,
                    refusal_reason="simulated outage",
                ),
                self.cost_per_call,
            )
        return (
            schemas.ExpertReport(
                role="technical",
                engagement_id=engagement_id,
                symbol=symbol,
                summary=f"{symbol}: tech lean={self.lean} (FAKE)",
                key_evidence=[],
                confidence_score=self.confidence,
                directional_lean=self.lean,
                data_sufficiency=True,
            ),
            self.cost_per_call,
        )


class FakeMacroExpert:
    role = "macro"

    def __init__(
        self,
        *,
        confidence: float = 0.6,
        lean: str | None = "bullish",
        sufficient: bool = True,
        cost_per_call: float = 0.0,
    ) -> None:
        self.confidence = confidence
        self.lean = lean
        self.sufficient = sufficient
        self.cost_per_call = cost_per_call

    async def analyze(self, *, engagement_id, symbol, context=None, on_data_fetch=None):
        if not self.sufficient:
            return (
                schemas.ExpertReport(
                    role="macro", engagement_id=engagement_id, symbol=symbol,
                    summary="[REFUSED] macro: simulated outage",
                    data_sufficiency=False,
                    refusal_reason="simulated outage",
                ),
                self.cost_per_call,
            )
        return (
            schemas.ExpertReport(
                role="macro", engagement_id=engagement_id, symbol=symbol,
                summary=f"{symbol}: macro lean={self.lean} (FAKE)",
                confidence_score=self.confidence,
                directional_lean=self.lean,
                data_sufficiency=True,
            ),
            self.cost_per_call,
        )


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    init_db(tmp_db)
    return tmp_db


@pytest.fixture
def stub_universe(monkeypatch, tmp_path: Path):
    """Replace the live universe loader with a tiny in-memory list AND
    bypass yfinance earnings calls (treat all symbols as clear).

    Layer 1 still checks the real `data/research_starter_universes/large_mid_cap.json`
    file existence, which is checked in — so we leave that alone."""
    from trading_corp.agents.research import graph as graph_mod
    monkeypatch.setattr(
        graph_mod, "_load_starter_universe",
        lambda key: ["AAPL", "MSFT", "NVDA", "GOOGL"],
    )
    from trading_corp.utils import market_data
    monkeypatch.setattr(market_data, "get_next_earnings", lambda *a, **kw: None)


@pytest.fixture
def deps_with_fakes(initialized_db: str, stub_universe) -> ResearchFirmDeps:
    """Build a ResearchFirmDeps with fake experts and an in-memory graph
    (no checkpointer). Production wires the same shape with real experts."""
    logger_agent = LoggerAgent(initialized_db)
    experts = {"technical": FakeTechnicalExpert(), "macro": FakeMacroExpert()}
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


def _candidate_spec(n: int = 3) -> schemas.EngagementSpec:
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={"category": "large_cap"},
            capacity_dollars=10_000.0,
            n_candidates=n,
            starter_universe_key="large_mid_cap",
            current_holdings=[],
        ),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )


# ── Tests ────────────────────────────────────────────────────────────────


async def test_e2e_happy_path_emits_candidate_recommendation(deps_with_fakes):
    """Healthy run: 4-symbol stub universe → top 3 → CandidateRecommendation
    with conviction + fit_score on each candidate."""
    spec = _candidate_spec(n=3)
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is not None
    assert isinstance(rec, schemas.CandidateRecommendation)
    assert 0 < len(rec.candidates) <= 3
    for c in rec.candidates:
        assert c.symbol in {"AAPL", "MSFT", "NVDA", "GOOGL"}
        assert c.thesis
        assert c.fit_rationale
        assert 0.0 <= c.fit_score <= 1.0
        assert c.conviction in {"high", "medium", "low"}


async def test_e2e_kill_switch_aborts_before_any_expert(
    deps_with_fakes, tmp_path, monkeypatch,
):
    """HALT_RESEARCH file present → engagement aborts at the first node;
    no expert runs, audit row has kill_switch_aborted."""
    from trading_corp.agents.research import graph as graph_mod, kill_switch as ks
    halt = tmp_path / KILL_SWITCH_FILENAME
    halt.write_text("halt", encoding="utf-8")
    monkeypatch.setattr(
        ks, "kill_switch_path", lambda repo_root=None: halt,
    )
    monkeypatch.setattr(
        graph_mod, "is_kill_switch_present",
        lambda repo_root=None: ks.is_kill_switch_present(repo_root=tmp_path),
    )

    spec = _candidate_spec()
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is None

    events = deps_with_fakes.logger_agent.recent_events(limit=50)
    kinds = [e["kind"] for e in events]
    assert "research_engagement_aborted_kill_switch" in kinds
    assert not any(k == "research_expert_completed" for k in kinds)
    assert not any(k == "research_expert_refused" for k in kinds)


async def test_e2e_out_of_scope_aborts_for_nonexistent_starter(
    initialized_db: str,
):
    """Bad starter_universe_key → Layer 1 rejects, audit row written."""
    logger_agent = LoggerAgent(initialized_db)
    experts = {"technical": FakeTechnicalExpert(), "macro": FakeMacroExpert()}
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    deps = ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )

    spec = schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={}, capacity_dollars=0.0,
            n_candidates=3,
            starter_universe_key="nonexistent_universe_xyz",
        ),
        triggered_by="telegram",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )
    rec = await run_engagement(spec, deps=deps)
    assert rec is None

    kinds = [e["kind"] for e in logger_agent.recent_events(limit=20)]
    assert "research_engagement_aborted_out_of_scope" in kinds


async def test_e2e_expert_refusal_storm_still_terminates_cleanly(
    initialized_db: str, stub_universe,
):
    """All experts refuse → synth still runs but produces zero-fit candidates
    that get filtered; either path must NOT raise."""
    logger_agent = LoggerAgent(initialized_db)
    experts = {
        "technical": FakeTechnicalExpert(sufficient=False),
        "macro": FakeMacroExpert(sufficient=False),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    deps = ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )

    spec = _candidate_spec(n=2)
    rec = await run_engagement(spec, deps=deps)

    kinds = [e["kind"] for e in logger_agent.recent_events(limit=80)]
    # Refusals must be in the audit log — load-bearing for "treat refused
    # dimensions as unobserved"
    assert any(k == "research_expert_refused" for k in kinds)
    # Either rec=None (zero candidates passed fit_score>0 filter) OR rec
    # has all-zero fit_scores. Both are fine — what matters is no crash
    # and the trail exists.
    if rec is not None:
        for c in rec.candidates:
            assert c.fit_score <= 0.0


async def test_e2e_post_validator_rejection(deps_with_fakes, monkeypatch):
    """If synth somehow produces a malformed product, post-validate
    short-circuits with `validation_failed` audit + None return.

    We force this by monkey-patching the synthesizer to emit a candidate
    whose symbol appears in current_holdings — caught by Layer 2.
    """
    from trading_corp.agents.research import graph as graph_mod

    async def _bad_synth(*, spec, reports_by_symbol, expert_audit_row_ids):
        bad = schemas.CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division=spec.requesting_division,
            asset_class=spec.asset_class,
            candidates=[
                schemas.Candidate(
                    symbol="HELD_SYM", thesis="t", conviction="medium",
                    fit_rationale="fr", fit_score=0.5,
                ),
            ],
        )
        return bad, 0.0

    monkeypatch.setattr(
        graph_mod, "synthesize_candidate_recommendation", _bad_synth,
    )
    # Rebuild the graph to pick up the patched function.
    deps_with_fakes.graph = build_engagement_graph(
        deps_with_fakes.logger_agent,
        experts=deps_with_fakes.experts,
        checkpointer=None,
    )

    # Build a spec whose current_holdings includes the symbol the bad
    # synth emits — Layer 2's holdings check will flag it.
    spec = schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={}, capacity_dollars=10_000.0,
            n_candidates=2,
            starter_universe_key="large_mid_cap",
            current_holdings=["HELD_SYM"],
        ),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is None
    kinds = [e["kind"] for e in deps_with_fakes.logger_agent.recent_events(limit=80)]
    assert "research_engagement_validation_failed" in kinds


async def test_e2e_no_inline_keyboard_approval_path(deps_with_fakes):
    """v3: there is NO board-approval flow on the recommendation as a
    unit. The engagement returns the product; the division decides per
    candidate. Acceptance: emitting a recommendation must NOT write any
    `research_watchlist_approval_recorded` (dropped from v2) row."""
    spec = _candidate_spec(n=2)
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is not None

    events = deps_with_fakes.logger_agent.recent_events(limit=80)
    kinds = [e["kind"] for e in events]
    # These v2 audit kinds should never appear in a v3 run.
    assert not any(k == "research_watchlist_approval_recorded" for k in kinds)
    assert not any(k == "research_watchlist_emitted" for k in kinds)
    assert not any(k == "research_watchlist_rejected" for k in kinds)


async def test_e2e_thesis_happy_path_emits_thesis(deps_with_fakes):
    """Phase 1b happy path: ThesisScope on a single symbol → Thesis product
    with summary, key_drivers, key_risks, earnings_window_clear."""
    spec = schemas.EngagementSpec(
        requesting_division="board",
        product_type="thesis",
        asset_class="equity",
        scope=schemas.ThesisScope(symbol="AAPL", depth="standard"),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )
    product = await run_engagement(spec, deps=deps_with_fakes)
    assert product is not None
    assert isinstance(product, schemas.Thesis)
    assert product.symbol == "AAPL"
    assert product.summary
    assert isinstance(product.key_drivers, list) and product.key_drivers
    assert isinstance(product.key_risks, list)
    # FakeMacroExpert + FakeTechnicalExpert default to data_sufficiency=True,
    # so we expect at least one driver (bullish lean) and zero hard-bear risks.
    # earnings_window_clear defaults True (stub_universe monkeypatches earnings to None).
    assert product.earnings_window_clear is True

    # Audit row must be present.
    kinds = [e["kind"] for e in deps_with_fakes.logger_agent.recent_events(limit=40)]
    assert "research_thesis_emitted" in kinds


async def test_e2e_thesis_skips_shortlist(deps_with_fakes):
    """Thesis is single-symbol; the shortlist node (which excludes held names
    and applies earnings filters against a starter universe) must NOT run.
    Pinning this prevents a future refactor from accidentally routing thesis
    through the candidate path and dropping symbols silently."""
    spec = schemas.EngagementSpec(
        requesting_division="board",
        product_type="thesis",
        asset_class="equity",
        scope=schemas.ThesisScope(symbol="ZZZZ"),  # not in any starter universe
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )
    product = await run_engagement(spec, deps=deps_with_fakes)
    # If shortlist had run, ZZZZ wouldn't be in the starter universe and we'd
    # get no_action. Thesis path must reach experts → synthesis → emit.
    assert isinstance(product, schemas.Thesis)
    assert product.symbol == "ZZZZ"


async def test_e2e_thesis_all_experts_refuse_still_produces_thesis(initialized_db):
    """When every expert refuses, we still emit a Thesis (with risk-flagged
    refusals as key_risks). Prevents 'silent no-action' on the Board ad-hoc
    surface — the Board asked, the team should answer with what it has."""
    logger_agent = LoggerAgent(initialized_db)
    experts = {
        "technical": FakeTechnicalExpert(sufficient=False),
        "macro": FakeMacroExpert(sufficient=False),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    deps = ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )

    spec = schemas.EngagementSpec(
        requesting_division="board",
        product_type="thesis",
        asset_class="equity",
        scope=schemas.ThesisScope(symbol="MSFT"),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )
    product = await run_engagement(spec, deps=deps)
    assert isinstance(product, schemas.Thesis)
    # Refusals propagate into key_risks so the Board sees the gap.
    risks_text = " ".join(product.key_risks).lower()
    assert "data unavailable" in risks_text or "refused" in risks_text


async def test_e2e_thesis_layer2_blocks_symbol_drift(deps_with_fakes, monkeypatch):
    """Force the synthesizer to emit a Thesis whose symbol doesn't match the
    spec — Layer 2's symbol-drift guard must short-circuit with
    validation_failed and None return."""
    from trading_corp.agents.research import graph as graph_mod

    async def _bad_synth(*, spec, reports, expert_audit_row_ids):
        bad = schemas.Thesis(
            engagement_id=spec.engagement_id,
            symbol="WRONG_SYMBOL",  # spec asked for AAPL
            summary="t", key_drivers=["d"], key_risks=["r"],
            earnings_window_clear=True,
        )
        return bad, 0.0

    monkeypatch.setattr(graph_mod, "synthesize_thesis", _bad_synth)
    deps_with_fakes.graph = build_engagement_graph(
        deps_with_fakes.logger_agent,
        experts=deps_with_fakes.experts,
        checkpointer=None,
    )

    spec = schemas.EngagementSpec(
        requesting_division="board",
        product_type="thesis",
        asset_class="equity",
        scope=schemas.ThesisScope(symbol="AAPL"),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )
    product = await run_engagement(spec, deps=deps_with_fakes)
    assert product is None
    kinds = [e["kind"] for e in deps_with_fakes.logger_agent.recent_events(limit=40)]
    assert "research_engagement_validation_failed" in kinds


async def test_e2e_dropped_division_audit_kinds_not_emitted(deps_with_fakes):
    """Phase 1a-1 does NOT write division-side `research_candidate_acted_on`
    or `research_candidate_skipped` rows — those ship in 1a-2 (PMCC scout
    integration). Verify nothing emits them prematurely."""
    spec = _candidate_spec(n=2)
    await run_engagement(spec, deps=deps_with_fakes)
    events = deps_with_fakes.logger_agent.recent_events(limit=80)
    kinds = [e["kind"] for e in events]
    assert "research_candidate_acted_on" not in kinds
    assert "research_candidate_skipped" not in kinds
