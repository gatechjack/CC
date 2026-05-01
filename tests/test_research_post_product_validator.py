"""Layer 2 post-product validator tests (design §6.3 Layer 2, v3).

Pins (LLM output cannot bypass):
  - len(candidates) > scope.n_candidates rejects (CandidateRecommendation)
  - candidate symbol in scope.current_holdings rejects
  - requesting_division drift rejects
  - asset_class drift rejects
  - missing required Pydantic fields rejects
  - TradeConfirmation forward-compat: structural plausibility checks
    (entry_price > 0, side ∈ {buy,sell}, rationale non-empty) — Layer 2
    does NOT duplicate division risk-cap logic (Refinement to §6.3)
"""
from __future__ import annotations

from trading_corp.agents.research import schemas
from trading_corp.agents.research.graph import _validate_product_layer2


# ── CandidateRecommendation cases ───────────────────────────────────────


def _candidate_spec(n=3, current_holdings=None):
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="candidate_recommendation",
        asset_class="equity",
        scope=schemas.CandidateScope(
            mandate={"category": "large_cap"},
            capacity_dollars=10_000.0,
            n_candidates=n,
            current_holdings=current_holdings or [],
            starter_universe_key="large_mid_cap",
        ),
        triggered_by="telegram",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )


def _good_candidate_product(spec, *, symbols=None):
    symbols = symbols or ["AAPL", "MSFT"]
    return {
        "engagement_id": spec.engagement_id,
        "requesting_division": spec.requesting_division,
        "asset_class": spec.asset_class,
        "candidates": [
            {
                "symbol": s, "thesis": f"thesis {s}",
                "conviction": "medium", "fit_rationale": f"fit {s}",
                "fit_score": 0.5,
            }
            for s in symbols
        ],
        "expert_audit_row_ids": [],
    }


def test_layer2_accepts_well_formed_candidate_product():
    spec = _candidate_spec()
    ok, reason = _validate_product_layer2(spec, _good_candidate_product(spec))
    assert ok, reason


def test_layer2_rejects_overflow_n_candidates():
    spec = _candidate_spec(n=2)
    p = _good_candidate_product(spec, symbols=["A", "B", "C"])
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "n_candidates" in reason or "candidates" in reason


def test_layer2_rejects_candidate_in_current_holdings():
    spec = _candidate_spec(current_holdings=["AAPL"])
    p = _good_candidate_product(spec, symbols=["AAPL", "MSFT"])
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "current_holdings" in reason


def test_layer2_rejects_requesting_division_drift():
    spec = _candidate_spec()
    p = _good_candidate_product(spec)
    p["requesting_division"] = "lord_otter"   # drift
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "requesting_division" in reason


def test_layer2_rejects_asset_class_drift():
    spec = _candidate_spec()
    p = _good_candidate_product(spec)
    p["asset_class"] = "crypto_spot"
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "asset_class" in reason


def test_layer2_rejects_schema_mismatch():
    spec = _candidate_spec()
    bad = {"engagement_id": "e1"}   # missing required fields
    ok, reason = _validate_product_layer2(spec, bad)
    assert not ok
    assert "schema" in reason


# ── TradeConfirmation forward-compat (Phase 1e ships synthesis) ─────────


def _tc_spec():
    return schemas.EngagementSpec(
        requesting_division="lord_otter",
        product_type="trade_confirmation",
        asset_class="crypto_spot",
        scope=schemas.TradeConfirmationScope(
            proposed_action={"symbol": "BTC/USD", "side": "buy",
                             "size_pct_equity": 0.05},
        ),
        triggered_by="division_agent",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )


def _good_tc_product(spec, *, verdict="confirm", modifications=None):
    p = {
        "engagement_id": spec.engagement_id,
        "requesting_division": spec.requesting_division,
        "subject_action": spec.scope.proposed_action,
        "verdict": verdict,
        "rationale": "looks reasonable",
        "risks_flagged": [],
        "expert_audit_row_ids": [],
    }
    if modifications is not None:
        p["suggested_modifications"] = modifications
    return p


def test_layer2_tc_accepts_confirm():
    spec = _tc_spec()
    ok, reason = _validate_product_layer2(spec, _good_tc_product(spec))
    assert ok, reason


def test_layer2_tc_rejects_subject_symbol_drift():
    spec = _tc_spec()
    p = _good_tc_product(spec)
    p["subject_action"] = {"symbol": "ETH/USD", "side": "buy"}
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "symbol" in reason


def test_layer2_tc_conditional_modifications_structural():
    """Layer 2 enforces structural plausibility on modifications:
    entry_price > 0, side ∈ {buy,sell}, rationale non-empty. It does NOT
    duplicate the division's risk-cap logic — that lives in
    `RiskAgent.evaluate()` downstream."""
    spec = _tc_spec()
    p = _good_tc_product(spec, verdict="conditional", modifications={
        "size_pct_equity": 0.04, "rationale": "halve size",
    })
    ok, reason = _validate_product_layer2(spec, p)
    assert ok, reason


def test_layer2_tc_conditional_rejects_zero_entry_price():
    spec = _tc_spec()
    p = _good_tc_product(spec, verdict="conditional", modifications={
        "entry_price": 0.0, "rationale": "x",
    })
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "entry_price" in reason
