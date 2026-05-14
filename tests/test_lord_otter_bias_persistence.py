"""Bias persistence regression tests for LordOtterAgent.

Origin: 2026-04-30. Tonight we ran ~5 trading-corp restarts in quick
succession (xvfb fix, .tokens dir fix, P&L deploy, etc.) and discovered
that every restart wiped Lord Otter's in-memory `state.bias` latch. Since
`bias_bull` is a transition signal that only fires on regime-change
crosses (not every bar), the strategy went silent for 3+ hours after
the bias was lost — every spoon_bull / cvd_bull_flip arrived to a
`bias=None` agent and got dismissed for "no bias alignment".

Fix: persist the bias latch in the `agent_state` SQLite table, restore
on construction with a 12h staleness gate.

These tests pin that behavior. If anyone later refactors the bias-handling
path (or removes persistence), these tests catch the regression before
the strategy goes mute in production.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_corp.agents.strategies.lord_otter import LordOtterAgent
from trading_corp.persistence.db import (
    init_db, load_agent_state, set_agent_state,
)


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def lord_otter_yaml(tmp_path: Path) -> Path:
    """Minimal strategies.yaml that enables Lord Otter on BTC/USD only."""
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
lord_otter:
  enabled: true
  auto_execute: false
  symbols:
    - BTC/USD
  arming_window_bars: 5
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    """tmp_db with the schema applied (LordOtterAgent's persist will need
    the agent_state table to exist)."""
    init_db(tmp_db)
    return tmp_db


def _make_agent(yaml_path: Path, db_url: str | None) -> LordOtterAgent:
    """Construct a LordOtterAgent with no live macro calendar coupling
    (we don't care about news halts in these tests). Pointing the
    calendar at a non-existent path makes it load zero events with no
    side effects.
    """
    from trading_corp.data.macro_calendar import MacroCalendar
    nonexistent = yaml_path.parent / "no_macro_events.yaml"
    return LordOtterAgent(
        strategies_yaml=yaml_path,
        macro_calendar=MacroCalendar(path=nonexistent),
        db_url=db_url,
    )


# ── _persist_bias direct ─────────────────────────────────────────────────


def test_persist_bias_writes_to_db(lord_otter_yaml, initialized_db):
    """Direct call: _persist_bias should upsert into agent_state."""
    agent = _make_agent(lord_otter_yaml, initialized_db)
    agent._persist_bias("BTC/USD", "bull")

    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    value, updated_at = result
    assert value["bias"] == "bull"
    assert value["symbol"] == "BTC/USD"
    # updated_at should be very fresh
    assert (datetime.now(timezone.utc) - updated_at) < timedelta(seconds=10)


def test_persist_bias_overwrites_previous(lord_otter_yaml, initialized_db):
    """Bull → bear flip should overwrite the existing entry (not append)."""
    agent = _make_agent(lord_otter_yaml, initialized_db)
    agent._persist_bias("BTC/USD", "bull")
    agent._persist_bias("BTC/USD", "bear")

    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    value, _ = result
    assert value["bias"] == "bear"


def test_persist_bias_no_db_url_is_no_op(lord_otter_yaml, initialized_db):
    """An agent constructed with db_url=None should never touch the DB.

    This is the test-mode + ad-hoc-CLI path. We assert nothing got
    persisted by checking the DB is empty after the call.
    """
    agent = _make_agent(lord_otter_yaml, db_url=None)
    agent._persist_bias("BTC/USD", "bull")

    # The DB exists but should have no rows for lord_otter
    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is None


# ── persist via _refresh_state_from_signal (the production path) ─────────


def test_bias_bull_signal_triggers_persistence(lord_otter_yaml, initialized_db):
    """`bias_bull` signal should both update in-memory state AND persist."""
    agent = _make_agent(lord_otter_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "bias_bull", "long", datetime.now(timezone.utc))

    # In-memory
    assert state.bias == "bull"
    # Persisted
    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    assert result[0]["bias"] == "bull"


def test_bias_bear_signal_triggers_persistence(lord_otter_yaml, initialized_db):
    agent = _make_agent(lord_otter_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "bias_bear", "short", datetime.now(timezone.utc))

    assert state.bias == "bear"
    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    assert result[0]["bias"] == "bear"


def test_non_bias_signal_does_not_persist(lord_otter_yaml, initialized_db):
    """Arming signals like spoon_bull don't update bias and shouldn't write."""
    agent = _make_agent(lord_otter_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "spoon_bull", "long", datetime.now(timezone.utc))

    # No bias change
    assert state.bias == "unknown"
    # No persistence
    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is None


# ── restore on construction ──────────────────────────────────────────────


def test_restore_loads_persisted_bias_on_new_agent(lord_otter_yaml, initialized_db):
    """The whole point of this work: write bias, construct fresh agent,
    bias is back."""
    # First agent — sets the bias
    agent1 = _make_agent(lord_otter_yaml, initialized_db)
    state = agent1.get_state("BTC/USD")
    agent1._refresh_state_from_signal(state, "bias_bull", "long", datetime.now(timezone.utc))
    assert state.bias == "bull"

    # Throw away agent1, construct agent2 — simulates a process restart.
    agent2 = _make_agent(lord_otter_yaml, initialized_db)
    restored_state = agent2.get_state("BTC/USD")
    assert restored_state.bias == "bull"


def test_restore_skips_stale_bias(lord_otter_yaml, initialized_db):
    """A bias older than BIAS_STATE_MAX_AGE (12h) should be discarded."""
    # Manually insert a stale entry directly into the DB (older than 12h).
    # We can't use _persist_bias for this because it always sets ts=now.
    import json
    import sqlite3
    from trading_corp.persistence.db import resolve_db_path
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    path = resolve_db_path(initialized_db)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO agent_state (agent, key, value_json, updated_ts) "
            "VALUES (?, ?, ?, ?)",
            ("lord_otter", "bias:BTC/USD",
             json.dumps({"bias": "bull", "symbol": "BTC/USD"}),
             stale_ts),
        )
        conn.commit()

    # Construct a fresh agent — restore should skip stale entry
    agent = _make_agent(lord_otter_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    assert state.bias == "unknown", "stale bias should NOT have been restored"

    # Stale entry should also have been cleaned up so next boot doesn't
    # re-evaluate it
    result = load_agent_state("lord_otter", "bias:BTC/USD", db_url=initialized_db)
    assert result is None, "stale entry should have been deleted"


def test_restore_only_loads_configured_symbols(lord_otter_yaml, initialized_db):
    """Bias for an unconfigured symbol should be ignored during restore."""
    # Write bias for ETH/USD (not in config which only has BTC/USD)
    set_agent_state(
        "lord_otter", "bias:ETH/USD",
        {"bias": "bull", "symbol": "ETH/USD"},
        db_url=initialized_db,
    )

    agent = _make_agent(lord_otter_yaml, initialized_db)
    # ETH/USD shouldn't be in _states because it's not configured
    assert "ETH/USD" not in agent._states


def test_restore_with_no_db_url_does_nothing(lord_otter_yaml, initialized_db):
    """db_url=None should make the restore path a no-op even if the DB
    has bias data — used by tests + ad-hoc CLI to disable persistence."""
    set_agent_state(
        "lord_otter", "bias:BTC/USD",
        {"bias": "bull", "symbol": "BTC/USD"},
        db_url=initialized_db,
    )

    # Construct WITHOUT db_url
    agent = _make_agent(lord_otter_yaml, db_url=None)
    state = agent.get_state("BTC/USD")
    # Even though the DB has bias=bull, agent shouldn't have loaded it
    assert state.bias == "unknown"
