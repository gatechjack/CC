"""Pydantic schemas for the research firm (v3).

Source of truth for engagement specs, expert reports, debate outcomes,
and the four product types. See planning/research_firm_design.md §3.

Pydantic v2. EVERY product is written to `audit_event` with its full
payload before any routing branch (CLAUDE.md §1, design §4.2).
"""
from __future__ import annotations

import uuid
from typing import Literal, Union

from pydantic import BaseModel, Field, model_validator


# ──────────────────────────────────────────────────────────────────────────
# 1. Top-level enums
# ──────────────────────────────────────────────────────────────────────────


RequestingDivision = Literal[
    "robinhood_pmcc",
    "robinhood_ira",
    "robinhood_joint",
    "lord_otter",
    "market_cypher",
    "fidelity_options",
    "board",
]

ProductType = Literal[
    "candidate_recommendation",
    "trade_confirmation",
    "position_context",
    "thesis",
]

AssetClass = Literal["equity", "option", "crypto_spot"]


# ──────────────────────────────────────────────────────────────────────────
# 2. Scope shapes (one per product_type)
# ──────────────────────────────────────────────────────────────────────────


class CandidateScope(BaseModel):
    """For 'find me N things that fit' questions.

    `mandate` is the requesting division's strategy block loaded
    verbatim from config/strategies.yaml (Q4). The research team does
    NOT interpret division config; it passes the dict to the synthesis
    prompt as fit context.

    `capacity_dollars` is computed by the requesting division (Q3) and
    used by synthesis to size-frame each candidate's thesis.

    `current_holdings` excludes symbols already held — division
    typically passes its current position list.

    `starter_universe_key`, when present, points at a JSON file under
    data/research_starter_universes/. When absent, experts screen
    broadly within asset_class (Phase 1c+ — Phase 1a always passes
    a starter key for cost predictability).
    """
    mandate: dict
    capacity_dollars: float = Field(ge=0.0)
    current_holdings: list[str] = Field(default_factory=list)
    n_candidates: int = Field(ge=1, le=5)
    starter_universe_key: str | None = None
    earnings_buffer_days: int = Field(default=7, ge=0, le=60)


class TradeConfirmationScope(BaseModel):
    """For 'I'm about to do X, sanity-check me' questions.

    `proposed_action` is the division's pre-built order-shape — fields
    typical to the division (symbol, side, size_pct_equity, instrument,
    entry_price, rationale, tier, etc). Schema is intentionally
    free-form because divisions vary; the synthesis prompt formats
    whatever is present.

    `context` is the surrounding situational data — alert payload,
    recent setup state, regime, etc. Same free-form treatment.
    """
    proposed_action: dict
    context: dict = Field(default_factory=dict)


class PositionContextScope(BaseModel):
    """For 'what's the situational picture for X' questions.

    Pre-emptive cache pattern (Q7) lives in the consuming agent, not in
    the engagement graph.
    """
    symbol: str
    time_horizon_hours: int = Field(ge=1, le=168)
    current_position_qty: float
    current_position_avg_price: float
    current_position_age_hours: float


class ThesisScope(BaseModel):
    """For Board ad-hoc 'tell me about X' questions. No production
    decision flow consumes Thesis."""
    symbol: str
    depth: Literal["standard", "deep"] = "standard"


Scope = Union[CandidateScope, TradeConfirmationScope, PositionContextScope, ThesisScope]


# ──────────────────────────────────────────────────────────────────────────
# 3. EngagementSpec — top-level "what's being asked"
# ──────────────────────────────────────────────────────────────────────────


class EngagementSpec(BaseModel):
    engagement_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    requesting_division: RequestingDivision
    product_type: ProductType
    asset_class: AssetClass
    scope: Scope
    constraints: dict = Field(default_factory=dict)
    triggered_by: Literal["division_agent", "telegram", "dashboard"]
    triggered_ts: str

    @model_validator(mode="after")
    def _scope_matches_product(self) -> "EngagementSpec":
        expected = {
            "candidate_recommendation": CandidateScope,
            "trade_confirmation": TradeConfirmationScope,
            "position_context": PositionContextScope,
            "thesis": ThesisScope,
        }[self.product_type]
        if not isinstance(self.scope, expected):
            raise ValueError(
                f"product_type={self.product_type!r} requires scope of "
                f"type {expected.__name__}, got {type(self.scope).__name__}"
            )
        return self


# ──────────────────────────────────────────────────────────────────────────
# 4. ExpertReport (renamed from v2's AnalystReport)
# ──────────────────────────────────────────────────────────────────────────


class EvidenceItem(BaseModel):
    claim: str
    source: str
    source_ts: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ExpertReport(BaseModel):
    """One expert's read on a question. The role is `str` (not Literal)
    so new experts can register without a schema change — the registry
    in experts/registry.py enforces which roles are valid for a given
    product."""
    role: str
    engagement_id: str
    symbol: str                       # may be "" for whole-engagement-level work
    summary: str
    key_evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.0)
    directional_lean: Literal["bullish", "bearish", "neutral"] | None = None
    data_sufficiency: bool
    refusal_reason: str | None = None

    @model_validator(mode="after")
    def _refusal_requires_reason(self) -> "ExpertReport":
        if not self.data_sufficiency and not self.refusal_reason:
            raise ValueError(
                "data_sufficiency=False requires non-empty refusal_reason"
            )
        return self


# ──────────────────────────────────────────────────────────────────────────
# 5. Debate (Phase 1f; defined here so 1a-1 tests can forward-compat)
# ──────────────────────────────────────────────────────────────────────────


class JudgeScore(BaseModel):
    evidence_quality: float = Field(ge=0.0, le=1.0)
    logical_consistency: float = Field(ge=0.0, le=1.0)
    falsifiability: float = Field(ge=0.0, le=1.0)
    notes: str


class DebateOutcome(BaseModel):
    engagement_id: str
    symbol: str
    invoked_reason: str
    bull_case: str
    bear_case: str
    judge_bull_score: JudgeScore
    judge_bear_score: JudgeScore
    # Judge scores QUALITY only — never produces a verdict.
    # CLAUDE.md §1: deterministic-then-narrate. Synthesis synthesizes.
    synthesis: str


# ──────────────────────────────────────────────────────────────────────────
# 6. Product types — first-class outputs
# ──────────────────────────────────────────────────────────────────────────


class Candidate(BaseModel):
    """One row inside a CandidateRecommendation.

    `conviction` is the team's "is this a good idea right now" call —
    LLM-narrated, categorical (high/medium/low). It folds in current
    momentum, regime, situational quality.

    `fit_score` is the team's "does this match the division's mandate"
    call — more deterministic, derived from how well the candidate's
    structural attributes line up against the mandate dict. [0.0, 1.0].

    Both are kept distinct because the cross product is diagnostic:
    high-conviction-low-fit is a red flag (the v2 BEN/FITB/VTR pattern);
    high-fit-low-conviction is "your kind of trade, wrong moment."
    """
    symbol: str
    thesis: str                       # 1-paragraph
    conviction: Literal["high", "medium", "low"]
    fit_rationale: str
    fit_score: float = Field(ge=0.0, le=1.0)


class CandidateRecommendation(BaseModel):
    engagement_id: str
    requesting_division: str
    asset_class: str
    candidates: list[Candidate]
    expert_audit_row_ids: list[int] = Field(default_factory=list)
    debate_audit_row_id: int | None = None


class SuggestedModifications(BaseModel):
    """Structured sub-schema for TradeConfirmation conditional verdicts.
    Free-form dict was rejected because it invites the LLM to hallucinate
    field names; adding a new modification type is a schema change,
    which is the point — registry growth, not magic strings.

    All fields except `rationale` are optional. The LLM populates only
    the fields it is suggesting changes to; absent fields mean "leave
    as proposed."
    """
    size_pct_equity: float | None = Field(default=None, ge=0.0, le=0.10)
    entry_price: float | None = None
    side: Literal["buy", "sell"] | None = None
    rationale: str                    # required — why these changes


class TradeConfirmation(BaseModel):
    engagement_id: str
    requesting_division: str
    subject_action: dict              # echoes scope.proposed_action for join traceability
    verdict: Literal["confirm", "push_back", "conditional"]
    rationale: str
    risks_flagged: list[str] = Field(default_factory=list)
    suggested_modifications: SuggestedModifications | None = None
    expert_audit_row_ids: list[int] = Field(default_factory=list)
    debate_audit_row_id: int | None = None

    @model_validator(mode="after")
    def _conditional_requires_modifications(self) -> "TradeConfirmation":
        if self.verdict == "conditional" and self.suggested_modifications is None:
            raise ValueError(
                "verdict='conditional' requires suggested_modifications"
            )
        return self


class PositionContext(BaseModel):
    """Returned to requesting division agent for internal consumption.
    REGARDLESS of routing, the PositionContext is written to audit_event
    with kind='research_position_context_emitted' (design §3.4 / §4.2)."""
    engagement_id: str
    requesting_division: str
    symbol: str
    time_horizon_hours: int
    macro_summary: str
    sentiment_summary: str
    risk_flags: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
    expert_audit_row_ids: list[int] = Field(default_factory=list)


class Thesis(BaseModel):
    """Board ad-hoc only. Renamed from v2 UnderlyingThesis — applies
    to any asset class, not just equity underlyings. No fit_score here
    because Thesis is exploratory, not divisionally targeted."""
    engagement_id: str
    symbol: str
    summary: str                      # 1-paragraph thesis
    key_drivers: list[str]
    key_risks: list[str]
    earnings_window_clear: bool
    expert_audit_row_ids: list[int] = Field(default_factory=list)
    debate_audit_row_id: int | None = None


# Convenience union for typed return signatures elsewhere.
ResearchProduct = Union[
    CandidateRecommendation,
    TradeConfirmation,
    PositionContext,
    Thesis,
]


# ──────────────────────────────────────────────────────────────────────────
# 7. Audit kinds — single source of truth (design §3.6)
# ──────────────────────────────────────────────────────────────────────────

# Engagement-side (actor='research_firm')
AUDIT_KIND_ENGAGEMENT_STARTED = "research_engagement_started"
AUDIT_KIND_ENGAGEMENT_KILLSWITCH = "research_engagement_aborted_kill_switch"
AUDIT_KIND_ENGAGEMENT_OUT_OF_SCOPE = "research_engagement_aborted_out_of_scope"
# Refinement 4 — fires ONLY on FAILURE (rate-limit, timeout, schema change,
# network error). Successful fetches are silent — the ExpertReport itself
# is evidence of retrieval.
AUDIT_KIND_DATA_FETCH = "research_data_fetch_attempted"
AUDIT_KIND_EXPERT_COMPLETED = "research_expert_completed"
AUDIT_KIND_EXPERT_REFUSED = "research_expert_refused"
AUDIT_KIND_DEBATE_INVOKED = "research_debate_invoked"
AUDIT_KIND_DEBATE_COMPLETED = "research_debate_completed"
AUDIT_KIND_CANDIDATE_RECOMMENDATION_EMITTED = "research_candidate_recommendation_emitted"
AUDIT_KIND_TRADE_CONFIRMATION_EMITTED = "research_trade_confirmation_emitted"
AUDIT_KIND_POSITION_CONTEXT_EMITTED = "research_position_context_emitted"
AUDIT_KIND_THESIS_EMITTED = "research_thesis_emitted"
AUDIT_KIND_VALIDATION_FAILED = "research_engagement_validation_failed"
AUDIT_KIND_NO_ACTION = "research_engagement_no_action"
AUDIT_KIND_COST_WARNING = "research_engagement_cost_warning"

# Division-side (actor=<division_slug>) — written by 1a-2; constants live
# here so 1a-2 doesn't redefine them.
AUDIT_KIND_CANDIDATE_ACTED_ON = "research_candidate_acted_on"
AUDIT_KIND_CANDIDATE_SKIPPED = "research_candidate_skipped"
AUDIT_KIND_RESEARCH_EXTENDED_OUTAGE = "pmcc_research_extended_outage"

RESEARCH_ACTOR = "research_firm"
