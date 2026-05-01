"""Public entry point for the research firm.

`run_engagement(spec, deps)` is the one function callers invoke. It:

  1. Builds (or reuses) the compiled engagement graph,
  2. Constructs initial `EngagementState` from the spec,
  3. Awaits the graph to a terminal node,
  4. Returns the typed product (or None on abort).

Phase 1a-1 only emits `CandidateRecommendation`. Other product types
route through Layer 1's phase-pointer rejection (see graph.py) and
return None with a `research_engagement_aborted_out_of_scope` audit row
explaining the phase gap.

The runner deliberately does NOT call `data_exec.place()` or import it
for execution. The hand-off boundary to the CEO graph is enforced by
the absence of the import path — a division acting on a recommendation
constructs its own ProposedOrder via its existing `_build_order` (Phase
1a-2 wires the PMCC scout).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research.experts import (
    FundamentalExpert, MacroExpert, SentimentExpert, TechnicalExpert,
)
from trading_corp.agents.research.experts.base import Expert
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.agents.research.schemas import (
    CandidateRecommendation, EngagementSpec, PositionContext,
    ResearchProduct, Thesis, TradeConfirmation,
)
from trading_corp.agents.research.state import EngagementState

log = logging.getLogger(__name__)


@dataclass
class ResearchFirmDeps:
    """Live references the engagement runner needs.

    Constructed once at startup and reused for every engagement. The
    LangGraph compiled graph is held here so build cost is paid once.

    `experts` maps role-string → Expert instance. The graph fan-out
    looks up roles via this dict; missing roles fall back to
    `stub_expert_report(role, ...)` (load-bearing for Phase 1a-1's
    fundamental + sentiment stubs).
    """
    logger_agent: LoggerAgent
    experts: dict[str, Expert] = field(default_factory=dict)
    graph: Any = None                          # compiled engagement graph (LangGraph CompiledStateGraph)


def build_research_firm_deps(
    logger_agent: LoggerAgent,
    *,
    checkpointer: Any | None = None,
    experts: dict[str, Expert] | None = None,
) -> ResearchFirmDeps:
    """Construct a ResearchFirmDeps. `checkpointer` is None for tests
    (in-memory mode) AND for production (design §2.4 — engagement
    graph is one-shot, no interrupt() / resume).

    Default expert roster (post-Phase 1c): real `technical`, `macro`,
    `fundamental`, `sentiment`. All four are yfinance-backed; the
    fundamental + sentiment experts refuse gracefully on non-equity
    symbols (crypto_spot) since yfinance's coverage there is unreliable.
    """
    if experts is None:
        experts = {
            "technical": TechnicalExpert(),
            "macro": MacroExpert(),
            "fundamental": FundamentalExpert(),
            "sentiment": SentimentExpert(),
        }
    graph = build_engagement_graph(
        logger_agent,
        experts=experts,
        checkpointer=checkpointer,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent,
        experts=experts,
        graph=graph,
    )


async def run_engagement(
    spec: EngagementSpec,
    *,
    deps: ResearchFirmDeps,
) -> ResearchProduct | None:
    """Execute one engagement. Returns the typed product or None on abort.

    Terminal status → return mapping:
      - kill_switch_aborted, out_of_scope, validation_failed, no_action → None
      - candidate_recommendation_emitted → CandidateRecommendation
      - trade_confirmation_emitted → TradeConfirmation (Phase 1e)
      - position_context_emitted → PositionContext (Phase 1d)
      - thesis_emitted → Thesis (Phase 1b)

    Phase 1a-1 only returns `CandidateRecommendation` or None. Other
    product_type values get routed through the no_action / out_of_scope
    terminal in the graph and return None with an audit trail explaining
    the phase gap.
    """
    initial: EngagementState = {
        "engagement_id": spec.engagement_id,
        "engagement_spec": spec.model_dump(),
        "product_type": spec.product_type,
        "asset_class": spec.asset_class,
        "requesting_division": spec.requesting_division,
        "triggered_by": spec.triggered_by,
        "triggered_ts": spec.triggered_ts,
        "kill_switch_present": False,
        "scope_ok": False,
        "scope_reject_reason": None,
        "expert_roles": [],
        "candidates": [],
        "expert_reports": [],
        "expert_audit_row_ids": [],
        "debate_invoked": False,
        "debate_invoked_reason": None,
        "debate_outcome": None,
        "product": None,
        "product_audit_row_id": None,
        "cost_dollars": 0.0,
        "cost_warning_emitted": False,
        "final_status": None,
        "final_reason": None,
    }

    config = {"configurable": {"thread_id": f"research:{spec.engagement_id}"}}
    final_state: EngagementState = await deps.graph.ainvoke(initial, config=config)

    status = final_state.get("final_status")
    product_d = final_state.get("product")

    if not product_d:
        return None

    try:
        if status == "candidate_recommendation_emitted":
            return CandidateRecommendation.model_validate(product_d)
        if status == "trade_confirmation_emitted":
            return TradeConfirmation.model_validate(product_d)
        if status == "position_context_emitted":
            return PositionContext.model_validate(product_d)
        if status == "thesis_emitted":
            return Thesis.model_validate(product_d)
    except Exception as e:
        log.error("product round-trip failed for status=%s: %s", status, e)
        return None

    return None
