"""Regression coverage for the polymarket_arbitrage strategy + risk gate.

Network-free. The strategy's LLM call is exercised in a separate manual
smoke (live API). Risk-gate cap matrix uses real config/risk.yaml so a
typo in the polymarket: block fails fast.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trading_corp.agents.risk import RiskAgent
from trading_corp.agents.strategies.polymarket_arbitrage import (
    PolymarketArbitrageAgent, _ProbabilityEstimate,
)
from trading_corp.persistence.models import (
    AccountState, ProposedOrder, StrategyState,
)


# ── Strategy agent: pure-function helpers ──────────────────────────────


@pytest.fixture
def empty_yaml(tmp_path):
    p = tmp_path / "strategies.yaml"
    p.write_text("polymarket_arbitrage: {}")
    rp = tmp_path / "risk.yaml"
    rp.write_text("polymarket: {}")
    return p, rp


def test_default_disabled(empty_yaml):
    s, r = empty_yaml
    a = PolymarketArbitrageAgent(strategies_yaml=s, risk_yaml=r)
    assert a.enabled is False
    assert a.auto_execute is False
    assert a.division == "polymarket_arbitrage"


def test_implied_prob_outcome_prices_string():
    m = {"outcomePrices": '["0.62","0.38"]'}
    assert PolymarketArbitrageAgent._extract_implied_prob_yes(m) == 0.62


def test_implied_prob_outcome_prices_list():
    m = {"outcomePrices": ["0.7", "0.3"]}
    assert PolymarketArbitrageAgent._extract_implied_prob_yes(m) == 0.7


def test_implied_prob_last_trade_price():
    m = {"lastTradePrice": 0.45}
    assert PolymarketArbitrageAgent._extract_implied_prob_yes(m) == 0.45


def test_implied_prob_boundary_rejected():
    """Probability must be strictly inside (0, 1) — exact 0 or 1 = malformed."""
    assert PolymarketArbitrageAgent._extract_implied_prob_yes({"price": 1.0}) is None
    assert PolymarketArbitrageAgent._extract_implied_prob_yes({"price": 0.0}) is None


def test_implied_prob_missing():
    assert PolymarketArbitrageAgent._extract_implied_prob_yes({}) is None
    assert PolymarketArbitrageAgent._extract_implied_prob_yes({"otherField": "garbage"}) is None


def test_parse_clean_json():
    raw = '{"prob_yes": 0.68, "confidence": "medium", "reasoning": "Base rate is...", "key_unknowns": ["Q1 guidance"]}'
    est = PolymarketArbitrageAgent._parse_probability_response(raw)
    assert est is not None
    assert est.prob_yes == 0.68
    assert est.confidence == "medium"
    assert "Base rate" in est.reasoning
    assert est.key_unknowns == ["Q1 guidance"]


def test_parse_prose_wrapped_json():
    """Models occasionally wrap JSON in prose despite instructions; pull it out."""
    raw = (
        "Sure, here is my estimate:\n\n"
        '{"prob_yes": 0.42, "confidence": "high", "reasoning": "foo", "key_unknowns": []}'
        "\n\nLet me know if..."
    )
    est = PolymarketArbitrageAgent._parse_probability_response(raw)
    assert est is not None
    assert est.prob_yes == 0.42


def test_parse_clamps_oob_prob():
    raw = '{"prob_yes": 1.5, "confidence": "high", "reasoning": "x", "key_unknowns": []}'
    est = PolymarketArbitrageAgent._parse_probability_response(raw)
    assert est is not None
    assert est.prob_yes == 0.99   # clamped


def test_parse_rejects_unparseable_prob():
    raw = '{"prob_yes": "high", "confidence": "low"}'
    assert PolymarketArbitrageAgent._parse_probability_response(raw) is None


def test_parse_handles_garbage():
    assert PolymarketArbitrageAgent._parse_probability_response("") is None
    assert PolymarketArbitrageAgent._parse_probability_response("not even close") is None


def test_parse_normalizes_unknown_confidence():
    raw = '{"prob_yes": 0.5, "confidence": "uncertain"}'
    est = PolymarketArbitrageAgent._parse_probability_response(raw)
    assert est is not None
    assert est.confidence == "medium"


# ── Risk gate: polymarket cap matrix ───────────────────────────────────


def _account(equity: float = 100.0) -> AccountState:
    return AccountState(account="polymarket-test", equity=equity, peak_equity=equity)


def _order(*, implied: float = 0.5, qty: float = 2.0, price: float = 0.5) -> ProposedOrder:
    return ProposedOrder(
        strategy="polymarket_arbitrage",
        symbol="some-market:yes", side="buy", qty=qty,
        order_type="limit", limit_price=price,
        rationale="test",
        extra={
            "is_prediction_market": True,
            "implied_prob_at_entry": implied,
            "outcome": "yes",
            "market_slug": "some-market",
            "condition_id": "0xabc",
        },
    )


@pytest.fixture
def risk_agent():
    """Uses the real config/risk.yaml — verifies the polymarket: block
    parses cleanly under the actual schema."""
    return RiskAgent()


def test_polymarket_approve_within_caps(risk_agent):
    v = risk_agent.evaluate(
        _order(qty=2.0, price=0.5),  # $1 notional
        _account(100.0),
        StrategyState(strategy="polymarket_arbitrage"),
    )
    assert v.verdict == "approve", f"unexpected: {v.reason}"


def test_polymarket_reject_implied_below_5pct(risk_agent):
    v = risk_agent.evaluate(
        _order(implied=0.03),
        _account(100.0),
        StrategyState(strategy="polymarket_arbitrage"),
    )
    assert v.verdict == "reject"
    assert "outside" in v.reason


def test_polymarket_reject_implied_above_95pct(risk_agent):
    v = risk_agent.evaluate(
        _order(implied=0.97),
        _account(100.0),
        StrategyState(strategy="polymarket_arbitrage"),
    )
    assert v.verdict == "reject"
    assert "outside" in v.reason


def test_polymarket_reject_single_market_above_250usd(risk_agent):
    v = risk_agent.evaluate(
        _order(qty=600.0, price=0.5),  # $300 notional
        _account(100_000.0),  # plenty of equity
        StrategyState(strategy="polymarket_arbitrage"),
    )
    assert v.verdict == "reject"
    assert "single-market" in v.reason


def test_polymarket_reject_position_above_5pct_equity(risk_agent):
    v = risk_agent.evaluate(
        _order(qty=20.0, price=0.5),  # $10 on $100 equity = 10% of div equity
        _account(100.0),
        StrategyState(strategy="polymarket_arbitrage"),
    )
    assert v.verdict == "reject"
    assert "% of" in v.reason or "equity" in v.reason


def test_polymarket_strategy_halt_overrides_polymarket_branch(risk_agent):
    """Halt check (#1 in evaluate) must run before polymarket branch (#2.5)."""
    halted = StrategyState(strategy="polymarket_arbitrage", halted=True, halt_reason="manual")
    v = risk_agent.evaluate(_order(), _account(100.0), halted)
    assert v.verdict == "reject"
    assert "halted" in v.reason


def test_non_polymarket_order_unaffected(risk_agent):
    """An order without is_prediction_market must NOT route through the
    polymarket branch — verifies the routing flag is the only switch."""
    o = ProposedOrder(
        strategy="lord_otter", symbol="BTC/USD", side="buy",
        qty=0.001, order_type="limit", limit_price=80_000.0,
        rationale="test", extra={},  # NO is_prediction_market flag
    )
    v = risk_agent.evaluate(
        o, _account(10_000.0),
        StrategyState(strategy="lord_otter"),
    )
    assert v.verdict in ("approve", "resize", "reject")
    # If it went through polymarket branch, the reason would mention
    # "polymarket"; verify it didn't.
    assert "polymarket" not in (v.reason or "").lower()
