"""Layer 1 scope validator tests (design §6.3 Layer 1).

Pins:
  - n_candidates > 5 rejects (Q2 hard cap, defensive re-check)
  - missing starter_universe_key file rejects
  - missing target_universe_key in strategies.yaml rejects (unless existing
    universe pre-populated on the spec)
  - product types not yet implemented (thesis/position_context/trade_proposal)
    reject with phase-pointer reason in 1a
"""
from __future__ import annotations

from pathlib import Path

from trading_corp.agents.research import schemas
from trading_corp.agents.research.graph import _validate_scope_layer1


def _build_spec(scope, product_type="watchlist_recommendation"):
    return schemas.EngagementSpec(
        requesting_division="robinhood_pmcc",
        product_type=product_type,
        scope=scope,
        triggered_by="telegram",
        triggered_ts="2026-04-30T10:00:00+00:00",
    )


def test_layer1_accepts_valid_watchlist_spec():
    """Real config: large_mid_cap starter exists, robinhood_pmcc.scout.universe
    is in committed strategies.yaml."""
    spec = _build_spec(schemas.WatchlistScope(
        target_universe_key="robinhood_pmcc.scout.universe",
        n_candidates=3,
        starter_universe_key="large_mid_cap",
    ))
    ok, reason = _validate_scope_layer1(spec)
    assert ok, reason


def test_layer1_rejects_missing_starter_file():
    spec = _build_spec(schemas.WatchlistScope(
        target_universe_key="robinhood_pmcc.scout.universe",
        n_candidates=3,
        starter_universe_key="does_not_exist",
    ))
    ok, reason = _validate_scope_layer1(spec)
    assert not ok
    assert "starter_universe_key" in reason


def test_layer1_rejects_unknown_target_universe_key():
    spec = _build_spec(schemas.WatchlistScope(
        target_universe_key="nonexistent.key.path",
        n_candidates=3,
        starter_universe_key="large_mid_cap",
    ))
    ok, reason = _validate_scope_layer1(spec)
    assert not ok
    assert "target_universe_key" in reason


def test_layer1_thesis_routes_to_phase_pointer():
    """In Phase 1a, ThesisScope rejects with a phase-pointer reason —
    not a silent no-op. Phase 1c flips this to accept."""
    spec = _build_spec(
        schemas.ThesisScope(symbol="AAPL"),
        product_type="underlying_thesis",
    )
    ok, reason = _validate_scope_layer1(spec)
    assert not ok
    assert "Phase 1c" in reason


def test_layer1_position_context_phase_pointer():
    spec = _build_spec(
        schemas.PositionContextScope(
            symbol="BTC/USD", time_horizon_hours=24,
            current_position_qty=0.5, current_position_avg_price=60000.0,
            current_position_age_hours=12.0,
        ),
        product_type="position_context",
    )
    ok, reason = _validate_scope_layer1(spec)
    assert not ok
    assert "Phase 1e" in reason


def test_layer1_trade_proposal_phase_pointer():
    spec = _build_spec(
        schemas.TradeProposalScope(symbol="AAPL", instrument="equity"),
        product_type="trade_proposal",
    )
    ok, reason = _validate_scope_layer1(spec)
    assert not ok
    assert "Phase 1f" in reason
