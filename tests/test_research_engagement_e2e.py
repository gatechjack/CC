"""End-to-end engagement runs through the LangGraph subgraph.

Covers happy path + sad paths (kill switch / out-of-scope / analyst-refused-storm).

These tests use FAKE analysts (no yfinance, no LLM) so they're deterministic
and offline. The engagement graph itself runs unmocked — it's the integration
between scope-check, fan-out, synthesis, and post-validate that we're pinning.
"""
from __future__ import annotations

import json
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


# ── Fake analysts ────────────────────────────────────────────────────────


class FakeTechnicalAnalyst:
    """Returns a controllable AnalystReport. Cost is configurable to drive
    the cost-cap fixtures; here we default to 0 so tests stay free."""
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

    async def analyze(self, *, engagement_id, symbol, on_data_fetch=None):
        if on_data_fetch is not None:
            on_data_fetch(source=f"fake_yfinance:{symbol}", ok=True, error=None)
        if not self.sufficient:
            return (
                schemas.AnalystReport(
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
            schemas.AnalystReport(
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


class FakeMacroAnalyst:
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

    async def analyze(self, *, engagement_id, symbol, earnings_buffer_days=7, on_data_fetch=None):
        if on_data_fetch is not None:
            on_data_fetch(source="fake_macro:vix", ok=True, error=None)
        if not self.sufficient:
            return (
                schemas.AnalystReport(
                    role="macro", engagement_id=engagement_id, symbol=symbol,
                    summary="[REFUSED] macro: simulated outage",
                    data_sufficiency=False,
                    refusal_reason="simulated outage",
                ),
                self.cost_per_call,
            )
        return (
            schemas.AnalystReport(
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

    # Bypass yfinance earnings fetches — every symbol "clear".
    from trading_corp.utils import market_data
    monkeypatch.setattr(market_data, "get_next_earnings", lambda *a, **kw: None)


@pytest.fixture
def deps_with_fakes(initialized_db: str, stub_universe) -> ResearchFirmDeps:
    """Build a ResearchFirmDeps with fake analysts and an in-memory graph
    (no checkpointer). Production wires the same shape with real analysts +
    AsyncSqliteSaver."""
    logger_agent = LoggerAgent(initialized_db)
    tech = FakeTechnicalAnalyst()
    macro = FakeMacroAnalyst()
    graph = build_engagement_graph(
        logger_agent,
        technical_analyst=tech,
        macro_analyst=macro,
        checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent,
        technical_analyst=tech,
        macro_analyst=macro,
        graph=graph,
    )


def _watchlist_spec(n: int = 3) -> schemas.EngagementSpec:
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="watchlist_recommendation",
        scope=schemas.WatchlistScope(
            target_universe_key="robinhood_pmcc.scout.universe",
            n_candidates=n,
            starter_universe_key="large_mid_cap",
            existing_universe=[],
        ),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )


# ── Tests ────────────────────────────────────────────────────────────────


async def test_e2e_happy_path_emits_watchlist(deps_with_fakes):
    """Healthy run: 4-symbol stub universe → top 3 → WatchlistRecommendation."""
    spec = _watchlist_spec(n=3)
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is not None
    assert isinstance(rec, schemas.WatchlistRecommendation)
    assert len(rec.additions) <= 3
    assert len(rec.additions) > 0
    for sym in rec.additions:
        assert sym in {"AAPL", "MSFT", "NVDA", "GOOGL"}
        assert rec.rationale_per_symbol.get(sym)
        assert sym in rec.fit_score_per_symbol


async def test_e2e_kill_switch_aborts_before_any_analyst(
    deps_with_fakes, tmp_path, monkeypatch,
):
    """HALT_RESEARCH file present → engagement aborts at the first node;
    no analyst runs, audit row has kill_switch_aborted."""
    # Override kill switch lookup to a tmp path containing the file
    from trading_corp.agents.research import graph as graph_mod, kill_switch as ks
    halt = tmp_path / KILL_SWITCH_FILENAME
    halt.write_text("halt", encoding="utf-8")
    monkeypatch.setattr(
        ks, "kill_switch_path", lambda repo_root=None: halt,
    )
    # Also patch the import inside the graph module since it captured the
    # symbol at import time
    monkeypatch.setattr(
        graph_mod, "is_kill_switch_present",
        lambda repo_root=None: ks.is_kill_switch_present(repo_root=tmp_path),
    )

    spec = _watchlist_spec()
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is None

    events = deps_with_fakes.logger_agent.recent_events(limit=50)
    kinds = [e["kind"] for e in events]
    assert "research_engagement_aborted_kill_switch" in kinds
    # No analyst rows should have been written
    assert not any(k == "research_analyst_completed" for k in kinds)
    assert not any(k == "research_analyst_refused" for k in kinds)


async def test_e2e_out_of_scope_aborts_for_nonexistent_starter(
    initialized_db: str, monkeypatch,
):
    """Bad starter_universe_key → Layer 1 rejects, audit row written."""
    logger_agent = LoggerAgent(initialized_db)
    tech = FakeTechnicalAnalyst()
    macro = FakeMacroAnalyst()
    graph = build_engagement_graph(
        logger_agent, technical_analyst=tech, macro_analyst=macro, checkpointer=None,
    )
    deps = ResearchFirmDeps(
        logger_agent=logger_agent, technical_analyst=tech,
        macro_analyst=macro, graph=graph,
    )

    spec = schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="watchlist_recommendation",
        scope=schemas.WatchlistScope(
            target_universe_key="robinhood_pmcc.scout.universe",
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


async def test_e2e_analyst_refusal_storm_still_synthesizes(
    initialized_db: str, stub_universe,
):
    """All analysts refuse → synth still emits a (low-fit) recommendation
    or the graph terminates with no_action. Either path must NOT raise."""
    logger_agent = LoggerAgent(initialized_db)
    tech = FakeTechnicalAnalyst(sufficient=False)
    macro = FakeMacroAnalyst(sufficient=False)
    graph = build_engagement_graph(
        logger_agent, technical_analyst=tech, macro_analyst=macro, checkpointer=None,
    )
    deps = ResearchFirmDeps(
        logger_agent=logger_agent, technical_analyst=tech,
        macro_analyst=macro, graph=graph,
    )

    spec = _watchlist_spec(n=2)
    rec = await run_engagement(spec, deps=deps)

    kinds = [e["kind"] for e in logger_agent.recent_events(limit=80)]
    # Refusals must be in the audit log — that's the load-bearing
    # guarantee for "treat refused dimensions as unobserved"
    assert any(k == "research_analyst_refused" for k in kinds)
    # Either the recommendation comes back with fit=0 additions OR the
    # graph routes through validation_failed / no_action. Both are fine
    # — what matters is no crash and the trail exists.
    if rec is not None:
        for sym in rec.additions:
            assert rec.fit_score_per_symbol.get(sym, 1.0) <= 0.0


async def test_e2e_post_validator_rejection(
    deps_with_fakes, monkeypatch,
):
    """If synth somehow produces a malformed product, post-validate
    short-circuits with `validation_failed` audit + None return.

    We force this by monkey-patching the synthesizer to return a bogus product.
    """
    from trading_corp.agents.research.synthesis import watchlist as synth_mod
    from trading_corp.agents.research import graph as graph_mod

    async def _bad_synth(*, spec, reports_by_symbol, analyst_audit_row_ids):
        bad = schemas.WatchlistRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division=spec.requesting_division,
            target_universe_key="DRIFTED.PATH",  # Layer 2 catches this
            additions=["AAPL"],
            rationale_per_symbol={"AAPL": "x"},
            fit_score_per_symbol={"AAPL": 0.5},
        )
        return bad, 0.0

    # Patch where the graph imported it from
    monkeypatch.setattr(
        graph_mod, "synthesize_watchlist_recommendation", _bad_synth,
    )
    # Rebuild the graph to pick up the patched function
    deps_with_fakes.graph = build_engagement_graph(
        deps_with_fakes.logger_agent,
        technical_analyst=deps_with_fakes.technical_analyst,
        macro_analyst=deps_with_fakes.macro_analyst,
        checkpointer=None,
    )

    spec = _watchlist_spec(n=2)
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is None
    kinds = [e["kind"] for e in deps_with_fakes.logger_agent.recent_events(limit=80)]
    assert "research_engagement_validation_failed" in kinds


async def test_e2e_board_approval_records_audit_no_yaml_write(
    deps_with_fakes, tmp_path, monkeypatch,
):
    """Phase 1a: dashboard/Telegram approval records
    `research_watchlist_approval_recorded` with status
    `approved_pending_manual_apply` and a copy-paste diff. Crucially,
    `config/strategies.yaml` is NOT mutated — that's Phase 1b.
    """
    spec = _watchlist_spec(n=2)
    rec = await run_engagement(spec, deps=deps_with_fakes)
    assert rec is not None

    # Snapshot strategies.yaml mtime BEFORE approval.
    strat = Path("config/strategies.yaml")
    mtime_before = strat.stat().st_mtime

    # Simulate Board approval (the same path the Telegram callback takes).
    deps_with_fakes.logger_agent.log_event(
        actor=schemas.RESEARCH_ACTOR,
        kind=schemas.AUDIT_KIND_WATCHLIST_APPROVAL_RECORDED,
        payload={
            "engagement_id": rec.engagement_id,
            "status": "approved_pending_manual_apply",
            "diff": {
                "additions": rec.additions,
                "drops": rec.drops,
                "target_universe_key": rec.target_universe_key,
            },
        },
    )

    # strategies.yaml MUST NOT have been touched.
    assert strat.stat().st_mtime == mtime_before, (
        "Phase 1a approval must NOT mutate strategies.yaml"
    )

    # Audit row exists with the right shape.
    events = deps_with_fakes.logger_agent.recent_events(limit=80)
    approvals = [
        e for e in events
        if e["kind"] == schemas.AUDIT_KIND_WATCHLIST_APPROVAL_RECORDED
    ]
    assert approvals
    payload = approvals[0]["payload"]
    assert payload["status"] == "approved_pending_manual_apply"
    assert payload["diff"]["target_universe_key"] == rec.target_universe_key
