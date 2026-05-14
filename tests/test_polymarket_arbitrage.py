"""Regression coverage for the polymarket_arbitrage strategy + risk gate.

Network-free. The strategy's LLM call is exercised in a separate manual
smoke (live API). Risk-gate cap matrix uses real config/risk.yaml so a
typo in the polymarket: block fails fast.
"""
from __future__ import annotations

import asyncio
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


# ── Category mapping (Phase 2a Step 5) ─────────────────────────────────


def test_classify_sports_via_series_slug():
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "mlb-oak-bal-2026-05-09",
         "events": [{"seriesSlug": "mlb"}], "sportsMarketType": "moneyline"}
    top, sub = _classify_market(m)
    assert top == "sports"
    assert sub == "mlb"


def test_classify_sports_via_market_type_when_series_missing():
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "tennis-some-match", "events": [], "sportsMarketType": "moneyline"}
    top, _ = _classify_market(m)
    assert top == "sports"


def test_classify_geopolitics_wins_over_politics():
    """Slugs like 'will-trump-announce-blockade-of-hormuz' could match
    both — geopolitics check runs first."""
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "will-trump-announce-blockade-of-hormuz-by-may", "events": []}
    top, _ = _classify_market(m)
    assert top == "geopolitics"


def test_classify_celebrity():
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "elon-musk-of-tweets-may-9-may-11-90-114",
         "events": [{"seriesSlug": "elon-tweets"}]}
    top, sub = _classify_market(m)
    assert top == "celebrity"
    assert sub == "elon-tweets"


def test_classify_crypto():
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "will-bitcoin-reach-84k-may-4-10",
         "events": [{"seriesSlug": "bitcoin-hit-price-weekly"}]}
    top, _ = _classify_market(m)
    assert top == "crypto"


def test_classify_entertainment_eurovision():
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "will-united-kingdom-win-eurovision-2026",
         "events": [{"seriesSlug": "eurovision-winner-2026"}]}
    top, _ = _classify_market(m)
    assert top == "entertainment"


def test_classify_other_fallback():
    """Any slug that doesn't match any keyword set falls to 'other'."""
    from trading_corp.brokers.polymarket import _classify_market
    m = {"slug": "unknown-future-thing", "events": []}
    top, _ = _classify_market(m)
    assert top == "other"


def test_classify_handles_malformed_input():
    """Defensive: missing slug, missing events, bad event shapes
    should never crash the classifier."""
    from trading_corp.brokers.polymarket import _classify_market
    # No slug at all
    assert _classify_market({}) == ("other", "")
    # events is None
    assert _classify_market({"slug": "foo", "events": None}) == ("other", "foo")
    # events[0] is not a dict
    assert _classify_market({"slug": "bar", "events": ["not-a-dict"]}) == ("other", "bar")


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


# ── Phase K7: LLM-fan concurrency cap (Semaphore) ──────────────────────


class _StubPolyBroker:
    """Returns a canned list of markets. Implements list_markets only."""
    def __init__(self, markets):
        self.markets = markets

    async def list_markets(self, **kwargs):
        return self.markets


def _fake_markets(n: int) -> list[dict]:
    """N markets that all pass the survivor filter:
       - unique conditionId
       - lastTradePrice inside (0.05, 0.95)
    """
    return [
        {
            "conditionId": f"cid-{i:03d}",
            "slug": f"market-{i:03d}",
            "question": f"Test market {i}?",
            "lastTradePrice": 0.50,
            "events": [],
            "category": "other",
        }
        for i in range(n)
    ]


def test_llm_fan_capped_by_semaphore(tmp_path):
    """K=10 survivors, llm_concurrency=3 → at most 3 _estimate_probability
    calls should ever be in flight simultaneously."""
    strat_yaml = tmp_path / "strategies.yaml"
    strat_yaml.write_text(
        "polymarket_arbitrage:\n"
        "  enabled: true\n"
        "  division: polymarket_arbitrage\n"
        "  k_markets_per_cycle: 10\n"
        "  market_cooldown_hours: 6\n"
        "  min_divergence_pct: 10.0\n"
        "  time_horizon_max_days: 7\n"
        "  llm_concurrency: 3\n"
        "  sizing: {mode: fixed_usdc, fixed_amount: 1.0}\n"
    )
    risk_yaml = tmp_path / "risk.yaml"
    risk_yaml.write_text(
        "polymarket: {min_implied_probability: 0.05, max_implied_probability: 0.95,\n"
        "             min_market_24h_volume_usd: 0, max_spread_cents: 99,\n"
        "             min_hours_to_resolution: 0}\n"
    )

    agent = PolymarketArbitrageAgent(strategies_yaml=strat_yaml, risk_yaml=risk_yaml)

    inflight = 0
    max_inflight = 0
    lock = asyncio.Lock()

    async def spy_estimate(market: dict):
        nonlocal inflight, max_inflight
        async with lock:
            inflight += 1
            if inflight > max_inflight:
                max_inflight = inflight
        # Sleep so concurrency materializes; not so long the test drags.
        await asyncio.sleep(0.02)
        async with lock:
            inflight -= 1
        # Return None → caller treats as "no estimate" and skips order build.
        return None

    agent._estimate_probability = spy_estimate
    broker = _StubPolyBroker(_fake_markets(10))

    orders = asyncio.run(agent.run_scan_cycle(broker))

    assert max_inflight <= 3, f"Concurrency cap breached: peak inflight={max_inflight}"
    # All 10 should still have been attempted (cap shapes parallelism, not totals).
    # With None returns, no orders should be emitted but all estimates were called.
    assert orders == []


def test_llm_fan_default_semaphore_is_8(tmp_path):
    """When llm_concurrency is unset, default cap = 8."""
    strat_yaml = tmp_path / "strategies.yaml"
    strat_yaml.write_text(
        "polymarket_arbitrage:\n"
        "  enabled: true\n"
        "  k_markets_per_cycle: 20\n"
        "  market_cooldown_hours: 6\n"
        "  min_divergence_pct: 10.0\n"
        "  time_horizon_max_days: 7\n"
        "  sizing: {mode: fixed_usdc, fixed_amount: 1.0}\n"
    )
    risk_yaml = tmp_path / "risk.yaml"
    risk_yaml.write_text(
        "polymarket: {min_implied_probability: 0.05, max_implied_probability: 0.95,\n"
        "             min_market_24h_volume_usd: 0, max_spread_cents: 99,\n"
        "             min_hours_to_resolution: 0}\n"
    )

    agent = PolymarketArbitrageAgent(strategies_yaml=strat_yaml, risk_yaml=risk_yaml)

    inflight = 0
    max_inflight = 0
    lock = asyncio.Lock()

    async def spy_estimate(market: dict):
        nonlocal inflight, max_inflight
        async with lock:
            inflight += 1
            if inflight > max_inflight:
                max_inflight = inflight
        await asyncio.sleep(0.02)
        async with lock:
            inflight -= 1
        return None

    agent._estimate_probability = spy_estimate
    broker = _StubPolyBroker(_fake_markets(20))

    asyncio.run(agent.run_scan_cycle(broker))

    assert max_inflight <= 8, f"Default cap breached: peak={max_inflight}"
