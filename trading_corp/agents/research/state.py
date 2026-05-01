"""LangGraph state container for the research firm engagement subgraph (v3).

Separate from `graph/ceo_graph.py:TradeFlowState` — no schema collision.
The engagement subgraph runs with `checkpointer=None` in production
(design §2.4) so process memory IS the state for one engagement run.

See planning/research_firm_design.md §2.3.
"""
from __future__ import annotations

from typing import Literal, TypedDict


FinalStatus = Literal[
    "kill_switch_aborted",
    "out_of_scope",
    "validation_failed",
    "no_action",
    "candidate_recommendation_emitted",
    "trade_confirmation_emitted",
    "position_context_emitted",
    "thesis_emitted",
]


class EngagementState(TypedDict, total=False):
    """One trip through the engagement subgraph.

    All fields optional via `total=False` so partial states (e.g. after
    an early kill-switch abort) don't trip TypedDict.
    """
    engagement_id: str
    engagement_spec: dict             # EngagementSpec.model_dump()
    product_type: str                 # one of ProductType (string-typed for state)
    asset_class: str                  # equity | option | crypto_spot
    requesting_division: str
    triggered_by: str                 # division_agent | telegram | dashboard
    triggered_ts: str
    engagement_started_ts: str        # set in kill_switch_check_node (Q11)

    # Pre-cycle gates
    kill_switch_present: bool
    scope_ok: bool
    scope_reject_reason: str | None

    # Expert pass
    expert_roles: list[str]           # from registry lookup
    candidates: list[str]             # for multi-symbol products only
    expert_reports: list[dict]        # ExpertReport.model_dump() entries
    expert_audit_row_ids: list[int]

    # Debate (Phase 1f — placeholders unused in 1a-1)
    debate_invoked: bool
    debate_invoked_reason: str | None
    debate_outcome: dict | None

    # Product
    product: dict | None              # serialized ResearchProduct
    product_audit_row_id: int | None

    # Cost tracking
    cost_dollars: float               # cumulative LLM (+ data, post-1c) spend
    cost_warning_emitted: bool        # one-shot to suppress duplicate warnings

    final_status: FinalStatus | None
    final_reason: str | None
    engagement_completed_ts: str      # set on terminal nodes (Q11)
