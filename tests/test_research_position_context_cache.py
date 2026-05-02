"""Unit tests for the PositionContext pre-emptive cache (Phase 1d, Q7).

Cache contract:
- write → read roundtrip returns the same product
- read past TTL returns None and lazy-evicts
- read on a missing key returns None
- second write replaces (no append)
- bad JSON / schema-drift returns None (not a raise)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.agents.research import schemas
from trading_corp.agents.research.position_context_cache import (
    cache_key,
    evict_position_context,
    read_position_context,
    ttl_seconds_for,
    write_position_context,
)
from trading_corp.persistence.db import (
    init_db, load_agent_state, set_agent_state,
)


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    init_db(tmp_db)
    return tmp_db


def _pc(symbol: str = "AAPL", horizon: int = 4) -> schemas.PositionContext:
    return schemas.PositionContext(
        engagement_id="eng-1",
        requesting_division="lord_otter",
        symbol=symbol,
        time_horizon_hours=horizon,
        macro_summary="m",
        sentiment_summary="s",
        risk_flags=["bear_macro"],
        confidence_score=0.6,
        expert_audit_row_ids=[1, 2],
    )


def test_cache_key_format():
    assert cache_key("AAPL", 4) == "position_context:AAPL:4h"
    assert cache_key("BTC/USD", 24) == "position_context:BTC/USD:24h"


def test_ttl_per_division_from_config():
    """The repo config/research.yaml has lord_otter=3600 + market_cypher=14400.
    Unknown divisions fall back to default 3600."""
    assert ttl_seconds_for("lord_otter") == 3600
    assert ttl_seconds_for("market_cypher") == 14400
    assert ttl_seconds_for("totally_made_up_division") == 3600


def test_write_then_read_roundtrip(initialized_db):
    pc = _pc()
    write_position_context("lord_otter", pc, db_url=initialized_db)
    got = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    assert got is not None
    assert got.symbol == "AAPL"
    assert got.time_horizon_hours == 4
    assert got.confidence_score == 0.6
    assert got.risk_flags == ["bear_macro"]


def test_read_missing_key_returns_none(initialized_db):
    got = read_position_context(
        "lord_otter", "NEVER_WRITTEN", 4, db_url=initialized_db,
    )
    assert got is None


def test_read_past_ttl_returns_none_and_evicts(initialized_db):
    """Stale entries are lazy-deleted on read so the next prime overwrites
    cleanly without leaving zombie rows."""
    pc = _pc()
    write_position_context("lord_otter", pc, db_url=initialized_db)
    far_future = datetime.now(timezone.utc) + timedelta(seconds=3601)
    got = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db, now=far_future,
    )
    assert got is None
    # Lazy GC: the row is gone after the stale read.
    raw = load_agent_state(
        "lord_otter", cache_key("AAPL", 4), db_url=initialized_db,
    )
    assert raw is None


def test_second_write_replaces(initialized_db):
    write_position_context("lord_otter", _pc(symbol="AAPL"), db_url=initialized_db)
    pc2 = _pc(symbol="AAPL")
    pc2 = pc2.model_copy(update={"confidence_score": 0.9, "risk_flags": []})
    write_position_context("lord_otter", pc2, db_url=initialized_db)
    got = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    assert got is not None
    assert got.confidence_score == 0.9
    assert got.risk_flags == []


def test_distinct_divisions_have_separate_keyspaces(initialized_db):
    """Otter writing AAPL must not be visible to Cypher and vice versa."""
    write_position_context(
        "lord_otter", _pc(symbol="AAPL"), db_url=initialized_db,
    )
    got_cypher = read_position_context(
        "market_cypher", "AAPL", 4, db_url=initialized_db,
    )
    assert got_cypher is None
    got_otter = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    assert got_otter is not None


def test_distinct_horizons_are_separate_entries(initialized_db):
    """horizon_hours is part of the key; 4h read shouldn't see a 24h write."""
    write_position_context(
        "lord_otter", _pc(symbol="AAPL", horizon=24), db_url=initialized_db,
    )
    got_4h = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    assert got_4h is None
    got_24h = read_position_context(
        "lord_otter", "AAPL", 24, db_url=initialized_db,
    )
    assert got_24h is not None


def test_schema_drift_returns_none_not_raise(initialized_db):
    """If the cache row was written by an older code version that's
    missing a now-required field, the read must fail-soft (treat as
    miss) rather than raising — alert path can't tolerate exceptions."""
    set_agent_state(
        "lord_otter",
        cache_key("AAPL", 4),
        {"engagement_id": "old", "symbol": "AAPL"},  # missing required fields
        db_url=initialized_db,
    )
    got = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    assert got is None


def test_evict_removes_entry(initialized_db):
    write_position_context("lord_otter", _pc(), db_url=initialized_db)
    evict_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    got = read_position_context(
        "lord_otter", "AAPL", 4, db_url=initialized_db,
    )
    assert got is None
