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
    _count_open_entries_by_condition_id,
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


# ── Per-condition_id position cap (Board-approved 2026-05-21) ──────────


def _init_test_db(tmp_path):
    """Create a temp SQLite with the audit_event + polymarket_round_trips
    schema. Returns (db_url, db_path)."""
    from trading_corp.persistence.db import init_db
    db_url = f"sqlite:///{tmp_path / 'tc.db'}"
    init_db(db_url=db_url)
    return db_url, tmp_path / "tc.db"


def _insert_open_audit(db_path, *, condition_id: str, order_id: str):
    """Insert a `would_have_placed` audit row for polymarket_arbitrage."""
    import json as _json
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone
    payload = {
        "strategy": "polymarket_arbitrage",
        "division": "polymarket_arbitrage",
        "condition_id": condition_id,
        "order_id": order_id,
        "slug": "test-slug",
        "side": "buy", "qty": 20.0, "limit_price": 0.05,
        "outcome": "no",
    }
    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?, 'polymarket_arbitrage', 'would_have_placed', ?)",
            (datetime.now(timezone.utc).isoformat(), _json.dumps(payload)),
        )


def _insert_resolved_round_trip(db_path, *, order_id: str, condition_id: str):
    """Mark an `order_id` as resolved by inserting into polymarket_round_trips."""
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO polymarket_round_trips "
            "(order_id, condition_id, outcome_bet, qty, entry_price, notional, "
            " entry_ts, resolved_ts, yes_won, won, realized_pnl, roi_pct, "
            " category, division) "
            "VALUES (?, ?, 'no', 20.0, 0.05, 1.0, ?, ?, 0, 1, 0.95, 95.0, "
            " 'other', 'polymarket_arbitrage')",
            (order_id, condition_id, now_iso, now_iso),
        )


def test_count_open_entries_returns_only_unresolved(tmp_path):
    """3 audit rows: 2 unresolved, 1 with matching round_trip → counts {cid: 2}."""
    db_url, db_path = _init_test_db(tmp_path)
    _insert_open_audit(db_path, condition_id="0xAAA", order_id="ord-1")
    _insert_open_audit(db_path, condition_id="0xAAA", order_id="ord-2")
    _insert_open_audit(db_path, condition_id="0xAAA", order_id="ord-3")
    _insert_resolved_round_trip(db_path, order_id="ord-3", condition_id="0xAAA")
    counts = _count_open_entries_by_condition_id(db_url, ["0xAAA"])
    assert counts == {"0xAAA": 2}


def test_count_open_entries_empty_db_returns_empty(tmp_path):
    db_url, _ = _init_test_db(tmp_path)
    assert _count_open_entries_by_condition_id(db_url, ["0xAAA"]) == {}


def test_count_open_entries_ignores_other_actors(tmp_path):
    """Audit rows for other actors must not count."""
    db_url, db_path = _init_test_db(tmp_path)
    import json as _json, sqlite3 as _sqlite3
    from datetime import datetime, timezone
    payload = {"condition_id": "0xAAA", "order_id": "other-1"}
    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?, 'polymarket_copy_trader', 'would_have_placed', ?)",
            (datetime.now(timezone.utc).isoformat(), _json.dumps(payload)),
        )
    assert _count_open_entries_by_condition_id(db_url, ["0xAAA"]) == {}


def test_count_open_entries_handles_missing_db_url():
    assert _count_open_entries_by_condition_id("", ["0xAAA"]) == {}
    assert _count_open_entries_by_condition_id("sqlite:///nonexistent.db", []) == {}


def _yamls_with_cap(tmp_path, *, max_open=1):
    """Strategy + risk YAML pair with cap configured."""
    strat = tmp_path / "strategies.yaml"
    strat.write_text(
        "polymarket_arbitrage:\n"
        "  enabled: true\n"
        "  division: polymarket_arbitrage\n"
        "  k_markets_per_cycle: 10\n"
        "  market_cooldown_hours: 6\n"
        "  min_divergence_pct: 10.0\n"
        "  time_horizon_max_days: 7\n"
        "  llm_concurrency: 8\n"
        f"  max_open_per_condition_id: {max_open}\n"
        "  sizing: {mode: fixed_usdc, fixed_amount: 1.0}\n"
    )
    risk = tmp_path / "risk.yaml"
    risk.write_text(
        "polymarket: {min_implied_probability: 0.05, max_implied_probability: 0.95,\n"
        "             min_market_24h_volume_usd: 0, max_spread_cents: 99,\n"
        "             min_hours_to_resolution: 0}\n"
    )
    return strat, risk


class _RecordingLogger:
    """Captures audit events; mirrors LoggerAgent.log_event signature."""
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def log_event(self, actor, kind, payload):
        self.events.append((actor, kind, dict(payload)))


def test_dedupe_skips_when_open_count_at_cap(tmp_path):
    """Cap=1, 1 open entry on cid-A → cid-A skipped, cid-B kept."""
    db_url, db_path = _init_test_db(tmp_path)
    _insert_open_audit(db_path, condition_id="cid-A", order_id="ord-A1")

    strat, risk = _yamls_with_cap(tmp_path, max_open=1)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    seen: list[str] = []
    async def spy_estimate(market):
        seen.append(market.get("conditionId"))
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-A", "slug": "mkt-a", "question": "Q-A", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
        {"conditionId": "cid-B", "slug": "mkt-b", "question": "Q-B", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
    ]
    logger = _RecordingLogger()
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=logger))

    # Only cid-B should have been LLM-called.
    assert seen == ["cid-B"]
    # A dedupe_skipped audit must exist for cid-A.
    skipped = [e for e in logger.events if e[1] == "polymarket_dedupe_skipped"]
    assert len(skipped) == 1
    assert skipped[0][2]["condition_id"] == "cid-A"
    assert skipped[0][2]["current_open_count"] == 1
    assert skipped[0][2]["cap"] == 1


def test_dedupe_disabled_when_max_is_zero(tmp_path):
    """Cap=0 → no skip even when open count is high."""
    db_url, db_path = _init_test_db(tmp_path)
    for i in range(10):
        _insert_open_audit(db_path, condition_id="cid-A", order_id=f"ord-{i}")

    strat, risk = _yamls_with_cap(tmp_path, max_open=0)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    seen: list[str] = []
    async def spy_estimate(market):
        seen.append(market.get("conditionId"))
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-A", "slug": "mkt-a", "question": "Q-A", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
    ]
    logger = _RecordingLogger()
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=logger))

    assert seen == ["cid-A"]
    assert [e for e in logger.events if e[1] == "polymarket_dedupe_skipped"] == []


def test_dedupe_cap_default_is_one(tmp_path):
    """When max_open_per_condition_id is unset in YAML, default=1 applies."""
    db_url, db_path = _init_test_db(tmp_path)
    _insert_open_audit(db_path, condition_id="cid-A", order_id="ord-A1")

    # YAML missing max_open_per_condition_id key.
    strat = tmp_path / "strategies.yaml"
    strat.write_text(
        "polymarket_arbitrage:\n"
        "  enabled: true\n"
        "  k_markets_per_cycle: 10\n"
        "  market_cooldown_hours: 6\n"
        "  min_divergence_pct: 10.0\n"
        "  time_horizon_max_days: 7\n"
        "  sizing: {mode: fixed_usdc, fixed_amount: 1.0}\n"
    )
    risk = tmp_path / "risk.yaml"
    risk.write_text(
        "polymarket: {min_implied_probability: 0.05, max_implied_probability: 0.95,\n"
        "             min_market_24h_volume_usd: 0, max_spread_cents: 99,\n"
        "             min_hours_to_resolution: 0}\n"
    )
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    seen: list[str] = []
    async def spy_estimate(market):
        seen.append(market.get("conditionId"))
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-A", "slug": "mkt-a", "question": "Q-A", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
    ]
    logger = _RecordingLogger()
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=logger))

    # Default cap=1 → cid-A skipped, no LLM call.
    assert seen == []
    assert [e for e in logger.events if e[1] == "polymarket_dedupe_skipped"]


def test_dedupe_cap_at_three_allows_partial_stacking(tmp_path):
    """Cap=3, 2 open entries → emit (under cap). 3 open → skip."""
    db_url, db_path = _init_test_db(tmp_path)
    # cid-A has 2 open (under cap=3). cid-B has 3 open (at cap=3).
    _insert_open_audit(db_path, condition_id="cid-A", order_id="A-1")
    _insert_open_audit(db_path, condition_id="cid-A", order_id="A-2")
    _insert_open_audit(db_path, condition_id="cid-B", order_id="B-1")
    _insert_open_audit(db_path, condition_id="cid-B", order_id="B-2")
    _insert_open_audit(db_path, condition_id="cid-B", order_id="B-3")

    strat, risk = _yamls_with_cap(tmp_path, max_open=3)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    seen: list[str] = []
    async def spy_estimate(market):
        seen.append(market.get("conditionId"))
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-A", "slug": "mkt-a", "question": "Q-A", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
        {"conditionId": "cid-B", "slug": "mkt-b", "question": "Q-B", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
    ]
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=None))

    # cid-A (n=2, cap=3) emits; cid-B (n=3, cap=3) skips.
    assert seen == ["cid-A"]


def test_dedupe_resolved_entries_dont_count_toward_cap(tmp_path):
    """An entry whose order_id is in polymarket_round_trips is RESOLVED →
    not counted as open → does not contribute to the cap."""
    db_url, db_path = _init_test_db(tmp_path)
    _insert_open_audit(db_path, condition_id="cid-A", order_id="A-1")
    _insert_resolved_round_trip(db_path, order_id="A-1", condition_id="cid-A")

    strat, risk = _yamls_with_cap(tmp_path, max_open=1)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    seen: list[str] = []
    async def spy_estimate(market):
        seen.append(market.get("conditionId"))
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-A", "slug": "mkt-a", "question": "Q-A", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
    ]
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=None))

    # cid-A's only prior entry is resolved → open count = 0 → emit allowed.
    assert seen == ["cid-A"]


def test_non_skipped_path_cooldown_unchanged_after_init_move(tmp_path):
    """REGRESSION: the only edit to existing logic is moving the
    `new_cooldowns` / `cooldown_until` initialization upward (out of the
    LLM-fan block, above the dedupe filter). For a market that is NOT
    skipped by dedupe, the cooldown must be written by the existing
    result-loop code path with the same value it would have used before
    the move.

    Setup: cap=1, fresh DB (no open entries), one market survives. The
    market should pass the dedupe filter, go through the LLM fan, and
    receive its cooldown via the result loop — identical to pre-move
    behavior. Verified by checking that the persisted cooldown for the
    market exists and is parseable as a future ISO timestamp."""
    from datetime import datetime, timezone
    db_url, _db_path = _init_test_db(tmp_path)
    # NO pre-existing audits — every survivor's open_count is 0.

    strat, risk = _yamls_with_cap(tmp_path, max_open=1)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    async def spy_estimate(market):
        return None  # no order emitted, but result loop still sets cooldown
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "fresh-cid", "slug": "fresh", "question": "Fresh?",
         "lastTradePrice": 0.5, "events": [], "category": "other"},
    ]
    logger = _RecordingLogger()
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=logger))

    # No dedupe skip should have happened.
    assert [e for e in logger.events if e[1] == "polymarket_dedupe_skipped"] == []

    # Cooldown for fresh-cid must be persisted by the result loop,
    # exactly as the pre-move code did.
    from trading_corp.persistence.db import load_agent_state
    row = load_agent_state("polymarket_arbitrage", "market_cooldowns", db_url=db_url)
    assert row is not None
    cooldowns_dict, _ = row
    assert "fresh-cid" in cooldowns_dict
    until_iso = cooldowns_dict["fresh-cid"]
    until_dt = datetime.fromisoformat(until_iso)
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)
    assert until_dt > datetime.now(timezone.utc), \
        "cooldown should be a future timestamp (market_cooldown_hours=6)"


def test_cap_bites_against_pre_existing_overhang(tmp_path):
    """REGRESSION: the cap must apply to UNRESOLVED audit rows regardless
    of when they were created. On deploy, condition_ids with N already-
    open entries (e.g. the 18-row Iran stack, 14-row WTI HIGH $110 stack
    that exist in prod 2026-05-21) must be skipped immediately, not
    grandfathered.

    Setup: insert 18 audit rows for cid-IRAN (mimicking the in-flight
    Iran peace-deal stack). Run scan. Cap=1 → cid-IRAN must be skipped
    on the very first cycle, with the open_count reflecting all 18
    prior entries."""
    db_url, db_path = _init_test_db(tmp_path)
    for i in range(18):
        _insert_open_audit(
            db_path,
            condition_id="cid-IRAN",
            order_id=f"iran-{i:02d}",
        )

    strat, risk = _yamls_with_cap(tmp_path, max_open=1)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    seen: list[str] = []
    async def spy_estimate(market):
        seen.append(market.get("conditionId"))
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-IRAN", "slug": "iran-peace", "question": "Iran peace deal?",
         "lastTradePrice": 0.05, "events": [], "category": "geopolitics"},
    ]
    logger = _RecordingLogger()
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=logger))

    # No LLM call — cap bit immediately on the existing overhang.
    assert seen == []
    skipped = [e for e in logger.events if e[1] == "polymarket_dedupe_skipped"]
    assert len(skipped) == 1
    # The audit must reflect ALL 18 prior unresolved entries, not just
    # a subset filtered by recency.
    assert skipped[0][2]["current_open_count"] == 18
    assert skipped[0][2]["cap"] == 1
    assert skipped[0][2]["condition_id"] == "cid-IRAN"


def test_dedupe_skipped_market_gets_cooldown_advanced(tmp_path):
    """Skipped markets must have their cooldown advanced so we don't
    re-evaluate them every cycle."""
    db_url, db_path = _init_test_db(tmp_path)
    _insert_open_audit(db_path, condition_id="cid-A", order_id="A-1")

    strat, risk = _yamls_with_cap(tmp_path, max_open=1)
    agent = PolymarketArbitrageAgent(strategies_yaml=strat, risk_yaml=risk, db_url=db_url)

    async def spy_estimate(market):
        return None
    agent._estimate_probability = spy_estimate

    markets = [
        {"conditionId": "cid-A", "slug": "mkt-a", "question": "Q-A", "lastTradePrice": 0.5,
         "events": [], "category": "other"},
    ]
    asyncio.run(agent.run_scan_cycle(_StubPolyBroker(markets), logger_agent=None))

    # Cooldown should now contain cid-A.
    from trading_corp.persistence.db import load_agent_state
    row = load_agent_state("polymarket_arbitrage", "market_cooldowns", db_url=db_url)
    assert row is not None
    cooldowns_dict, _ = row
    assert "cid-A" in cooldowns_dict
