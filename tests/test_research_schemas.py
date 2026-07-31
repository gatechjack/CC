"""Pydantic round-trip + refusal-validity tests for research firm schemas.

Pins:
  - WatchlistScope.n_candidates is hard-capped at 5 (Q2)
  - AnalystReport rejects data_sufficiency=False without refusal_reason
  - EngagementSpec.scope must match product_type (discriminator validator)
  - Product schemas round-trip through model_dump → model_validate
"""
from __future__ import annotations

import pytest

from trading_corp.agents.research import schemas


def _spec_kwargs():
    return dict(
        triggered_by="telegram",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )


def test_watchlist_scope_caps_n_candidates_at_5():
    schemas.WatchlistScope(
        target_universe_key="x.y", n_candidates=5,
        starter_universe_key="large_mid_cap",
    )
    with pytest.raises(Exception):
        schemas.WatchlistScope(
            target_universe_key="x.y", n_candidates=6,
            starter_universe_key="large_mid_cap",
        )


def test_watchlist_scope_min_n_candidates():
    with pytest.raises(Exception):
        schemas.WatchlistScope(
            target_universe_key="x.y", n_candidates=0,
            starter_universe_key="large_mid_cap",
        )


def test_analyst_report_refusal_requires_reason():
    with pytest.raises(Exception):
        schemas.AnalystReport(
            role="sentiment", engagement_id="e1", symbol="AAPL",
            summary="x", data_sufficiency=False, refusal_reason=None,
        )
    # Valid form
    r = schemas.AnalystReport(
        role="sentiment", engagement_id="e1", symbol="AAPL",
        summary="x", data_sufficiency=False, refusal_reason="no source",
    )
    assert r.refusal_reason == "no source"


def test_engagement_spec_rejects_scope_mismatch():
    """ThesisScope on a watchlist_recommendation must reject."""
    with pytest.raises(Exception):
        schemas.EngagementSpec(
            requesting_division="robinhood_pmcc",
            product_type="watchlist_recommendation",
            scope=schemas.ThesisScope(symbol="AAPL"),
            **_spec_kwargs(),
        )


def test_engagement_spec_accepts_matching_scope():
    spec = schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="watchlist_recommendation",
        scope=schemas.WatchlistScope(
            target_universe_key="x.y", n_candidates=3,
            starter_universe_key="large_mid_cap",
        ),
        **_spec_kwargs(),
    )
    assert spec.engagement_id   # auto-generated UUID


def test_watchlist_recommendation_round_trip():
    rec = schemas.WatchlistRecommendation(
        engagement_id="e1",
        requesting_division="robinhood_pmcc",
        target_universe_key="robinhood_pmcc.scout.universe",
        additions=["NVDA", "AAPL"],
        rationale_per_symbol={"NVDA": "x", "AAPL": "y"},
        fit_score_per_symbol={"NVDA": 0.7, "AAPL": 0.6},
    )
    d = rec.model_dump()
    rec2 = schemas.WatchlistRecommendation.model_validate(d)
    assert rec2.additions == ["NVDA", "AAPL"]
    assert rec2.fit_score_per_symbol["NVDA"] == 0.7


def test_trade_proposal_size_capped_at_10pct():
    """size_pct_equity hard-capped at 10% (design §3.4.4)."""
    schemas.TradeProposal(
        engagement_id="e1", requesting_division="robinhood_pmcc",
        symbol="AAPL", side="buy", instrument="equity",
        conviction_tier="medium", size_pct_equity=0.10, rationale="x",
    )
    with pytest.raises(Exception):
        schemas.TradeProposal(
            engagement_id="e1", requesting_division="robinhood_pmcc",
            symbol="AAPL", side="buy", instrument="equity",
            conviction_tier="medium", size_pct_equity=0.15, rationale="x",
        )


def test_audit_kind_constants_exist():
    """Sanity check the audit-kind constants are wired so callers don't
    string-literal them and drift."""
    assert schemas.AUDIT_KIND_WATCHLIST_EMITTED == "research_watchlist_emitted"
    assert schemas.AUDIT_KIND_ANALYST_REFUSED == "research_analyst_refused"
    assert schemas.AUDIT_KIND_COST_WARNING == "research_engagement_cost_warning"
    assert schemas.RESEARCH_ACTOR == "research_firm"
