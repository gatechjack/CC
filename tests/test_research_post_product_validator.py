"""Layer 2 post-product validator tests (design §6.3 Layer 2).

Pins (LLM output cannot bypass):
  - target_universe_key drift rejects
  - len(additions) + len(drops) > scope.n_candidates rejects
  - symbol in both additions and drops rejects
  - missing rationale for an added/dropped symbol rejects
"""
from __future__ import annotations

from trading_corp.agents.research import schemas
from trading_corp.agents.research.graph import _validate_product_layer2


def _spec(n=3):
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type="watchlist_recommendation",
        scope=schemas.WatchlistScope(
            target_universe_key="robinhood_pmcc.scout.universe",
            n_candidates=n,
            starter_universe_key="large_mid_cap",
        ),
        triggered_by="telegram",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )


def _good_product(spec, additions=None):
    additions = additions or ["AAPL", "MSFT"]
    return {
        "engagement_id": spec.engagement_id,
        "requesting_division": spec.requesting_division,
        "target_universe_key": spec.scope.target_universe_key,
        "additions": additions,
        "drops": [],
        "rationale_per_symbol": {s: f"thesis for {s}" for s in additions},
        "fit_score_per_symbol": {s: 0.6 for s in additions},
        "analyst_audit_row_ids": [],
    }


def test_layer2_accepts_well_formed_product():
    spec = _spec()
    ok, reason = _validate_product_layer2(spec, _good_product(spec))
    assert ok, reason


def test_layer2_rejects_target_key_drift():
    spec = _spec()
    p = _good_product(spec)
    p["target_universe_key"] = "WRONG.PATH"
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "drift" in reason


def test_layer2_rejects_overflow_n_candidates():
    spec = _spec(n=2)
    p = _good_product(spec, additions=["A", "B", "C"])
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "n_candidates" in reason


def test_layer2_rejects_symbol_in_both_add_and_drop():
    spec = _spec()
    p = _good_product(spec)
    p["drops"] = ["AAPL"]   # AAPL already in additions
    p["rationale_per_symbol"]["AAPL"] = "thesis"
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "both" in reason


def test_layer2_rejects_missing_rationale():
    spec = _spec()
    p = _good_product(spec)
    p["rationale_per_symbol"] = {"AAPL": "x"}   # MSFT rationale missing
    ok, reason = _validate_product_layer2(spec, p)
    assert not ok
    assert "rationale" in reason


def test_layer2_rejects_schema_mismatch():
    spec = _spec()
    # Missing required keys → Pydantic rejects
    bad = {"engagement_id": "e1"}
    ok, reason = _validate_product_layer2(spec, bad)
    assert not ok
    assert "schema" in reason
