"""Tests for the PositionContext startup-of-day prime (Phase 1d, Q7)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import ResearchFirmDeps
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.agents.research.position_context_cache import (
    cache_key, read_position_context,
)
from trading_corp.agents.research.prime import (
    prime_all_division_position_contexts,
    prime_division_position_contexts,
)
from trading_corp.persistence.db import init_db, load_agent_state


# Reuse the fake experts from the e2e test module.
from tests.test_research_engagement_e2e import (
    FakeMacroExpert, FakeSentimentExpert,
)


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    init_db(tmp_db)
    return tmp_db


@pytest.fixture
def deps(initialized_db: str) -> ResearchFirmDeps:
    logger_agent = LoggerAgent(initialized_db)
    experts = {"macro": FakeMacroExpert(), "sentiment": FakeSentimentExpert()}
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


async def test_prime_writes_one_per_symbol(deps: ResearchFirmDeps, initialized_db):
    results = await prime_division_position_contexts(
        division_slug="lord_otter",
        asset_class="crypto_spot",
        symbols=["BTC/USD", "ETH/USD"],
        horizon_hours=4,
        research_firm=deps,
        db_url=initialized_db,
    )
    assert results == {"BTC/USD": True, "ETH/USD": True}

    btc = read_position_context(
        "lord_otter", "BTC/USD", 4, db_url=initialized_db,
    )
    eth = read_position_context(
        "lord_otter", "ETH/USD", 4, db_url=initialized_db,
    )
    assert btc is not None
    assert eth is not None
    assert btc.symbol == "BTC/USD"
    assert eth.symbol == "ETH/USD"


async def test_prime_no_db_url_is_noop(deps: ResearchFirmDeps):
    results = await prime_division_position_contexts(
        division_slug="lord_otter",
        asset_class="crypto_spot",
        symbols=["BTC/USD"],
        horizon_hours=4,
        research_firm=deps,
        db_url=None,
    )
    # In-memory mode = no cache to populate; reported as not-primed.
    assert results == {"BTC/USD": False}


async def test_prime_empty_symbol_list_is_noop(deps: ResearchFirmDeps, initialized_db):
    results = await prime_division_position_contexts(
        division_slug="lord_otter",
        asset_class="crypto_spot",
        symbols=[],
        horizon_hours=4,
        research_firm=deps,
        db_url=initialized_db,
    )
    assert results == {}


async def test_prime_swallows_per_symbol_engagement_failure(
    deps: ResearchFirmDeps, initialized_db, monkeypatch,
):
    """If run_engagement raises for one symbol, others still prime."""
    from trading_corp.agents.research import prime as prime_mod

    real_run = prime_mod.run_engagement
    calls: list[str] = []

    async def flaky_run(spec, *, deps):
        calls.append(spec.scope.symbol)
        if spec.scope.symbol == "BAD":
            raise RuntimeError("simulated yfinance outage")
        return await real_run(spec, deps=deps)

    monkeypatch.setattr(prime_mod, "run_engagement", flaky_run)

    results = await prime_division_position_contexts(
        division_slug="lord_otter",
        asset_class="crypto_spot",
        symbols=["BTC/USD", "BAD", "ETH/USD"],
        horizon_hours=4,
        research_firm=deps,
        db_url=initialized_db,
    )
    assert results == {"BTC/USD": True, "BAD": False, "ETH/USD": True}
    assert calls == ["BTC/USD", "BAD", "ETH/USD"]
    # The two healthy symbols are in the cache; the bad one isn't.
    raw_bad = load_agent_state(
        "lord_otter", cache_key("BAD", 4), db_url=initialized_db,
    )
    assert raw_bad is None


async def test_prime_all_runs_multiple_divisions(deps: ResearchFirmDeps, initialized_db):
    await prime_all_division_position_contexts(
        research_firm=deps,
        db_url=initialized_db,
        divisions=[
            {
                "slug": "lord_otter",
                "asset_class": "crypto_spot",
                "symbols": ["BTC/USD"],
                "horizon_hours": 4,
            },
            {
                "slug": "market_cypher",
                "asset_class": "crypto_spot",
                "symbols": ["BTC/USD"],
                "horizon_hours": 24,
            },
        ],
    )
    otter = read_position_context(
        "lord_otter", "BTC/USD", 4, db_url=initialized_db,
    )
    cypher = read_position_context(
        "market_cypher", "BTC/USD", 24, db_url=initialized_db,
    )
    assert otter is not None
    assert cypher is not None
    # Same symbol but separated by (division, horizon) keyspace.
    assert otter.requesting_division == "lord_otter"
    assert cypher.requesting_division == "market_cypher"
