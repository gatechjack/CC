"""Pydantic round-trip + refusal-validity tests for research firm schemas (v3).

Pins:
  - CandidateScope.n_candidates is hard-capped at 5 (design §3.2)
  - ExpertReport rejects data_sufficiency=False without refusal_reason
  - EngagementSpec.scope must match product_type (discriminator validator)
  - SuggestedModifications structure + TradeConfirmation conditional rule
  - All four product schemas round-trip through model_dump → model_validate
"""
from __future__ import annotations

import pytest

from trading_corp.agents.research import schemas


def _spec_kwargs():
    return dict(
        triggered_by="telegram",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )


# ── Scope shape pins ─────────────────────────────────────────────────────


def test_candidate_scope_caps_n_candidates_at_5():
    schemas.CandidateScope(
        mandate={"category": "large_cap"},
        capacity_dollars=10_000.0,
        n_candidates=5,
    )
    with pytest.raises(Exception):
        schemas.CandidateScope(
            mandate={"category": "large_cap"},
            capacity_dollars=10_000.0,
            n_candidates=6,
        )


def test_candidate_scope_min_n_candidates():
    with pytest.raises(Exception):
        schemas.CandidateScope(
            mandate={},
            capacity_dollars=0.0,
            n_candidates=0,
        )


def test_candidate_scope_capacity_dollars_nonneg():
    with pytest.raises(Exception):
        schemas.CandidateScope(
            mandate={},
            capacity_dollars=-1.0,
            n_candidates=1,
        )


# ── ExpertReport pins ────────────────────────────────────────────────────


def test_expert_report_refusal_requires_reason():
    with pytest.raises(Exception):
        schemas.ExpertReport(
            role="sentiment", engagement_id="e1", symbol="AAPL",
            summary="x", data_sufficiency=False, refusal_reason=None,
        )
    r = schemas.ExpertReport(
        role="sentiment", engagement_id="e1", symbol="AAPL",
        summary="x", data_sufficiency=False, refusal_reason="no source",
    )
    assert r.refusal_reason == "no source"


def test_expert_report_role_is_open_string():
    """role is `str` (not Literal) so new experts can register without
    a schema change — the registry enforces validity."""
    r = schemas.ExpertReport(
        role="some_future_expert",
        engagement_id="e1", symbol="AAPL",
        summary="x", data_sufficiency=True,
    )
    assert r.role == "some_future_expert"


# ── EngagementSpec pins ─────────────────────────────────────────────────


def test_engagement_spec_rejects_scope_mismatch():
    """ThesisScope on a candidate_recommendation must reject."""
    with pytest.raises(Exception):
        schemas.EngagementSpec(
            requesting_division="robinhood_pmcc",
            product_type="candidate_recommendation",
            asset_class="equity",
            scope=schemas.ThesisScope(symbol="AAPL"),
            **_spec_kwargs(),
        )


def test_engagement_spec_accepts_matching_candidate_scope():
    spec = schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={}, capacity_dollars=1000.0, n_candidates=3,
        ),
        **_spec_kwargs(),
    )
    assert spec.engagement_id   # auto-generated UUID


def test_engagement_spec_accepts_trade_confirmation_scope():
    spec = schemas.EngagementSpec(
        requesting_division="lord_otter",
        product_type="trade_confirmation",
        asset_class="crypto_spot",
        scope=schemas.TradeConfirmationScope(
            proposed_action={"symbol": "BTC/USD", "side": "buy"},
        ),
        **_spec_kwargs(),
    )
    assert spec.scope.proposed_action["symbol"] == "BTC/USD"


# ── Product schema round-trips ──────────────────────────────────────────


def test_candidate_recommendation_round_trip():
    rec = schemas.CandidateRecommendation(
        engagement_id="e1",
        requesting_division="robinhood_pmcc",
        asset_class="equity",
        candidates=[
            schemas.Candidate(
                symbol="NVDA", thesis="t", conviction="high",
                fit_rationale="fr", fit_score=0.7,
            ),
            schemas.Candidate(
                symbol="AAPL", thesis="t2", conviction="medium",
                fit_rationale="fr2", fit_score=0.5,
            ),
        ],
    )
    d = rec.model_dump()
    rec2 = schemas.CandidateRecommendation.model_validate(d)
    assert [c.symbol for c in rec2.candidates] == ["NVDA", "AAPL"]
    assert rec2.candidates[0].fit_score == 0.7


def test_trade_confirmation_conditional_requires_modifications():
    with pytest.raises(Exception):
        schemas.TradeConfirmation(
            engagement_id="e1",
            requesting_division="lord_otter",
            subject_action={"symbol": "BTC/USD", "side": "buy"},
            verdict="conditional",
            rationale="x",
        )
    # Confirm + push_back don't require modifications
    schemas.TradeConfirmation(
        engagement_id="e1",
        requesting_division="lord_otter",
        subject_action={"symbol": "BTC/USD", "side": "buy"},
        verdict="confirm",
        rationale="ok",
    )


def test_suggested_modifications_size_capped_at_10pct():
    """size_pct_equity hard-capped at 10% (design §3.5)."""
    schemas.SuggestedModifications(
        size_pct_equity=0.10, rationale="cap edge",
    )
    with pytest.raises(Exception):
        schemas.SuggestedModifications(
            size_pct_equity=0.15, rationale="x",
        )


def test_suggested_modifications_rationale_required():
    with pytest.raises(Exception):
        schemas.SuggestedModifications(
            size_pct_equity=0.05,
        )


def test_position_context_round_trip():
    pc = schemas.PositionContext(
        engagement_id="e1",
        requesting_division="lord_otter",
        symbol="BTC/USD",
        time_horizon_hours=24,
        macro_summary="m",
        sentiment_summary="s",
        risk_flags=["earnings"],
        confidence_score=0.6,
    )
    d = pc.model_dump()
    pc2 = schemas.PositionContext.model_validate(d)
    assert pc2.symbol == "BTC/USD"
    assert pc2.confidence_score == 0.6


def test_thesis_round_trip():
    t = schemas.Thesis(
        engagement_id="e1",
        symbol="AAPL",
        summary="s",
        key_drivers=["d1"],
        key_risks=["r1"],
        earnings_window_clear=True,
    )
    d = t.model_dump()
    t2 = schemas.Thesis.model_validate(d)
    assert t2.symbol == "AAPL"


# ── Audit-kind constants pin ────────────────────────────────────────────


def test_audit_kind_constants_exist():
    """Sanity check the audit-kind constants are wired so callers don't
    string-literal them and drift."""
    assert (
        schemas.AUDIT_KIND_CANDIDATE_RECOMMENDATION_EMITTED
        == "research_candidate_recommendation_emitted"
    )
    assert schemas.AUDIT_KIND_EXPERT_REFUSED == "research_expert_refused"
    assert schemas.AUDIT_KIND_EXPERT_COMPLETED == "research_expert_completed"
    assert schemas.AUDIT_KIND_COST_WARNING == "research_engagement_cost_warning"
    assert schemas.AUDIT_KIND_DATA_FETCH == "research_data_fetch_attempted"
    assert schemas.AUDIT_KIND_CANDIDATE_ACTED_ON == "research_candidate_acted_on"
    assert schemas.AUDIT_KIND_CANDIDATE_SKIPPED == "research_candidate_skipped"
    assert schemas.AUDIT_KIND_RESEARCH_EXTENDED_OUTAGE == "pmcc_research_extended_outage"
    assert schemas.RESEARCH_ACTOR == "research_firm"
