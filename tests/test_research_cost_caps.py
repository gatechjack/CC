"""Cost-cap behavior tests (design §6.6, Q8).

Pins:
  - Soft cap fires `research_engagement_cost_warning` audit row + (in
    production) Telegram notification — once per engagement, not duplicated
  - Hard cap aborts with `research_engagement_no_action` (reason includes
    cost_cap_exceeded)
  - Both caps come from `config/research.yaml:cost_caps:<product_type>`
  - TradeConfirmation cap is tested via mock since Phase 1e ships its
    synthesis (we exercise the cap-resolution helper directly)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.graph import (
    _cost_caps, build_engagement_graph,
)
from trading_corp.persistence.db import init_db


# ── Fakes ────────────────────────────────────────────────────────────────


class _BillableTechExpert:
    """Returns valid reports but bills $X per call so we can drive cap fires."""
    role = "technical"

    def __init__(self, cost_per_call: float):
        self.cost_per_call = cost_per_call

    async def analyze(self, *, engagement_id, symbol, context=None, on_data_fetch=None):
        return (
            schemas.ExpertReport(
                role="technical", engagement_id=engagement_id, symbol=symbol,
                summary=f"{symbol}: tech",
                confidence_score=0.6, directional_lean="bullish",
                data_sufficiency=True,
            ),
            self.cost_per_call,
        )


class _FreeMacroExpert:
    role = "macro"

    async def analyze(self, *, engagement_id, symbol, context=None, on_data_fetch=None):
        return (
            schemas.ExpertReport(
                role="macro", engagement_id=engagement_id, symbol=symbol,
                summary=f"{symbol}: macro",
                confidence_score=0.5, directional_lean="neutral",
                data_sufficiency=True,
            ),
            0.0,
        )


def _build_deps(tmp_db, monkeypatch, *, tech_cost: float, n_universe: int = 4):
    init_db(tmp_db)
    from trading_corp.agents.research import graph as graph_mod
    universe = [f"SYM{i}" for i in range(n_universe)]
    monkeypatch.setattr(
        graph_mod, "_load_starter_universe", lambda key: universe,
    )
    from trading_corp.utils import market_data
    monkeypatch.setattr(market_data, "get_next_earnings", lambda *a, **kw: None)

    logger_agent = LoggerAgent(tmp_db)
    experts = {
        "technical": _BillableTechExpert(cost_per_call=tech_cost),
        "macro": _FreeMacroExpert(),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


def _spec(n: int):
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={}, capacity_dollars=10_000.0,
            n_candidates=n,
            starter_universe_key="large_mid_cap",
        ),
        triggered_by="telegram",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )


# ── CandidateRecommendation cap behavior ────────────────────────────────


async def test_soft_cap_fires_warning_once(tmp_db, monkeypatch):
    """Default soft cap = $1.00. Each candidate's tech call costs $0.40.
    After 3 candidates ($1.20) the soft cap fires once; remaining
    candidates must NOT fire it again."""
    deps = _build_deps(tmp_db, monkeypatch, tech_cost=0.40, n_universe=4)
    spec = _spec(n=2)
    await run_engagement(spec, deps=deps)

    warnings = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_COST_WARNING
    ]
    assert len(warnings) == 1, f"expected exactly 1 cost warning, got {len(warnings)}"
    payload = warnings[0]["payload"]
    assert payload["soft_cap_dollars"] == pytest.approx(1.00)
    assert payload["hard_cap_dollars"] == pytest.approx(2.50)


async def test_hard_cap_aborts_with_no_action(tmp_db, monkeypatch):
    """Default hard cap = $2.50. Each candidate's tech call costs $1.50,
    so the second candidate triggers the cap; the engagement aborts with
    `research_engagement_no_action` (reason=cost_cap_exceeded). Acceptance
    criterion #6 — verifies the test fixture-forced over-spend path."""
    deps = _build_deps(tmp_db, monkeypatch, tech_cost=1.50, n_universe=4)
    spec = _spec(n=2)
    rec = await run_engagement(spec, deps=deps)

    kinds = [e["kind"] for e in deps.logger_agent.recent_events(limit=200)]
    assert schemas.AUDIT_KIND_NO_ACTION in kinds

    no_action_rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_NO_ACTION
    ]
    reasons = [r["payload"].get("reason") for r in no_action_rows]
    assert any("cost_cap_exceeded" in (r or "") for r in reasons)
    # No CandidateRecommendation should have been emitted post-abort.
    emitted = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_CANDIDATE_RECOMMENDATION_EMITTED
    ]
    assert not emitted
    assert rec is None


async def test_under_cap_no_warning(tmp_db, monkeypatch):
    """Cheap engagement: stays under soft cap, no cost-warning rows.
    Acceptance criterion #6 happy path — stays under $1.00 soft cap."""
    deps = _build_deps(tmp_db, monkeypatch, tech_cost=0.10, n_universe=4)
    spec = _spec(n=2)
    rec = await run_engagement(spec, deps=deps)
    assert rec is not None

    warnings = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_COST_WARNING
    ]
    assert not warnings


async def test_no_action_terminal_pins_started_and_completed_ts(tmp_db, monkeypatch):
    """Q11: even the cost-cap-aborted no_action terminal must pin both
    timestamps in the audit row payload."""
    deps = _build_deps(tmp_db, monkeypatch, tech_cost=1.50, n_universe=4)
    spec = _spec(n=2)
    await run_engagement(spec, deps=deps)
    no_action_rows = [
        e for e in deps.logger_agent.recent_events(limit=200)
        if e["kind"] == schemas.AUDIT_KIND_NO_ACTION
    ]
    assert no_action_rows
    payload = no_action_rows[0]["payload"]
    assert payload.get("engagement_started_ts")
    assert payload.get("engagement_completed_ts")


# ── Per-product cap resolution (Q8 table) ───────────────────────────────


def test_cost_caps_per_product_type():
    """Verify the per-product cap table from design §6.6 / Q8 — including
    trade_confirmation which is tested via the helper since Phase 1e
    hasn't shipped its synthesis yet."""
    soft, hard = _cost_caps("candidate_recommendation")
    assert soft == pytest.approx(1.00)
    assert hard == pytest.approx(2.50)

    soft, hard = _cost_caps("trade_confirmation")
    assert soft == pytest.approx(0.30)
    assert hard == pytest.approx(0.75)

    soft, hard = _cost_caps("position_context")
    assert soft == pytest.approx(0.50)
    assert hard == pytest.approx(1.00)

    soft, hard = _cost_caps("thesis")
    assert soft == pytest.approx(0.50)
    assert hard == pytest.approx(1.50)
