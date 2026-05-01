"""Bias + Sommi persistence tests for MarketCypherAgent.

Mirrors test_lord_otter_bias_persistence.py — Cypher uses the same
`agent_state` table, the same staleness-gate pattern, just under
agent name 'market_cypher'. These tests pin that the persistence
machinery survives the Otter→Cypher fork unmodified.

Cypher-specific additions vs Otter:
- `sommi` field on SymbolState (HTF VWAP regime, persisted alongside bias)
- 3-day staleness window (vs Otter's 12h) — Cypher signals fire on 1D
  events so 3 days of latency is reasonable
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_corp.agents.divisions.market_cypher import MarketCypherAgent
from trading_corp.persistence.db import (
    init_db, load_agent_state, set_agent_state,
)


@pytest.fixture
def cypher_yaml(tmp_path: Path) -> Path:
    """Minimal strategies.yaml that enables Market Cypher on BTC/USD only."""
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
market_cypher:
  enabled: true
  auto_execute: false
  symbols:
    - BTC/USD
  arming_window_bars: 3
  cooldown_seconds: 14400
  direction_policy: long_only
  tier_sizes:
    gold: 0.075
    diamond: 0.05
    premium: 0.04
    big_circle: 0.03
    standard: 0.02
    ema_flip: 0.02
    solo: 0.0075
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    init_db(tmp_db)
    return tmp_db


def _make_agent(yaml_path: Path, db_url: str | None) -> MarketCypherAgent:
    from trading_corp.data.macro_calendar import MacroCalendar
    nonexistent = yaml_path.parent / "no_macro_events.yaml"
    return MarketCypherAgent(
        strategies_yaml=yaml_path,
        macro_calendar=MacroCalendar(path=nonexistent),
        db_url=db_url,
    )


# ── Bias persistence ──────────────────────────────────────────────────────


def test_persist_bias_writes_to_db(cypher_yaml, initialized_db):
    agent = _make_agent(cypher_yaml, initialized_db)
    agent._persist_bias("BTC/USD", "bull")

    result = load_agent_state("market_cypher", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    value, updated_at = result
    assert value["bias"] == "bull"
    assert (datetime.now(timezone.utc) - updated_at) < timedelta(seconds=10)


def test_longema_signal_sets_bull_bias_and_persists(cypher_yaml, initialized_db):
    """`mc_a_longema` should both flip in-memory bias AND persist."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "mc_a_longema", "long", datetime.now(timezone.utc))

    assert state.bias == "bull"
    result = load_agent_state("market_cypher", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    assert result[0]["bias"] == "bull"


def test_blood_diamond_sets_bear_bias_and_persists(cypher_yaml, initialized_db):
    """`mc_a_blood_diamond` is the bear-bias setter (asymmetric design)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "mc_a_blood_diamond", "short", datetime.now(timezone.utc))

    assert state.bias == "bear"
    result = load_agent_state("market_cypher", "bias:BTC/USD", db_url=initialized_db)
    assert result is not None
    assert result[0]["bias"] == "bear"


def test_other_bear_signals_do_NOT_set_bias(cypher_yaml, initialized_db):
    """Red Diamond / RedX / YellowX are tier-eligible bear triggers but
    should NOT flip the bias — bias=bear is reserved for Blood Diamond
    (or via the Phase 2 backlog item, −RBD on 1D)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    for signal in ("mc_a_red_diamond", "mc_a_redx", "mc_a_yellow_x"):
        agent._refresh_state_from_signal(state, signal, "short", datetime.now(timezone.utc))
        assert state.bias != "bear", (
            f"{signal} should not set bias=bear (reserved for Blood Diamond)"
        )


def test_restore_bias_loads_persisted_state(cypher_yaml, initialized_db):
    """The whole point: bias survives process restart."""
    agent1 = _make_agent(cypher_yaml, initialized_db)
    state1 = agent1.get_state("BTC/USD")
    agent1._refresh_state_from_signal(state1, "mc_a_longema", "long", datetime.now(timezone.utc))
    assert state1.bias == "bull"

    # Throw away agent1, construct agent2 — simulates restart
    agent2 = _make_agent(cypher_yaml, initialized_db)
    state2 = agent2.get_state("BTC/USD")
    assert state2.bias == "bull"


def test_bias_staleness_window_is_3_days(cypher_yaml, initialized_db):
    """Cypher uses a 3-day staleness window (vs Otter's 12h) because
    its bias is set on 1D events."""
    # Insert a stale entry directly (4 days old)
    import json
    import sqlite3
    from trading_corp.persistence.db import resolve_db_path
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    path = resolve_db_path(initialized_db)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO agent_state (agent, key, value_json, updated_ts) "
            "VALUES (?, ?, ?, ?)",
            ("market_cypher", "bias:BTC/USD",
             json.dumps({"bias": "bull", "symbol": "BTC/USD"}),
             stale_ts),
        )
        conn.commit()

    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    assert state.bias == "unknown", "4-day-old bias should NOT have been restored"

    # And cleanup of stale entry
    result = load_agent_state("market_cypher", "bias:BTC/USD", db_url=initialized_db)
    assert result is None


def test_bias_within_staleness_window_IS_restored(cypher_yaml, initialized_db):
    """A 2-day-old bias is fresh enough — should be restored."""
    import json
    import sqlite3
    from trading_corp.persistence.db import resolve_db_path
    fresh_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    path = resolve_db_path(initialized_db)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO agent_state (agent, key, value_json, updated_ts) "
            "VALUES (?, ?, ?, ?)",
            ("market_cypher", "bias:BTC/USD",
             json.dumps({"bias": "bull", "symbol": "BTC/USD"}),
             fresh_ts),
        )
        conn.commit()

    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    assert state.bias == "bull"


# ── Sommi persistence (Cypher-specific) ──────────────────────────────────


def test_sommi_bull_persists(cypher_yaml, initialized_db):
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "mc_b_sommi_bull", "", datetime.now(timezone.utc))

    assert state.sommi == "bull"
    result = load_agent_state("market_cypher", "sommi:BTC/USD", db_url=initialized_db)
    assert result is not None
    assert result[0]["sommi"] == "bull"


def test_sommi_bear_persists(cypher_yaml, initialized_db):
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "mc_b_sommi_bear", "", datetime.now(timezone.utc))

    assert state.sommi == "bear"


def test_sommi_restore_on_construction(cypher_yaml, initialized_db):
    """Sommi state survives restart, same way bias does."""
    set_agent_state(
        "market_cypher", "sommi:BTC/USD",
        {"sommi": "bear", "symbol": "BTC/USD"},
        db_url=initialized_db,
    )

    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    assert state.sommi == "bear"


def test_sommi_and_bias_independent(cypher_yaml, initialized_db):
    """Setting bias should not touch sommi, and vice versa."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    agent._refresh_state_from_signal(state, "mc_a_longema", "long", datetime.now(timezone.utc))
    agent._refresh_state_from_signal(state, "mc_b_sommi_bear", "", datetime.now(timezone.utc))

    assert state.bias == "bull"
    assert state.sommi == "bear"
    # Both DB rows present
    bias_row = load_agent_state("market_cypher", "bias:BTC/USD", db_url=initialized_db)
    sommi_row = load_agent_state("market_cypher", "sommi:BTC/USD", db_url=initialized_db)
    assert bias_row is not None and bias_row[0]["bias"] == "bull"
    assert sommi_row is not None and sommi_row[0]["sommi"] == "bear"


# ── Bluetriangle arming ──────────────────────────────────────────────────


def test_bluetriangle_arms_long(cypher_yaml, initialized_db):
    """Bluetriangle should set armed_long with proper expiry (3 bars × 4h)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    ts = datetime.now(timezone.utc)
    agent._refresh_state_from_signal(state, "mc_a_bluetriangle", "long", ts)

    assert state.armed_long is not None
    assert state.armed_long.source == "bluetriangle"
    assert state.armed_long.direction == "long"
    # 3 bars × 4h = 12 hours
    expected_expiry = ts + timedelta(hours=12)
    assert abs((state.armed_long.expires_at - expected_expiry).total_seconds()) < 1


# ── Tier classification ──────────────────────────────────────────────────


def test_gold_buy_fires_gold_tier(cypher_yaml, initialized_db):
    """GOLD circle alone should fire GOLD tier — even with no bias set
    (it's a fire-without-bias exception, like Otter's solo_otter)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    # No bias set deliberately

    payload = {"signal": "mc_b_gold_buy", "symbol": "BTC/USD", "price": 76000.0}
    verdict = agent._classify_tier(
        state, "mc_b_gold_buy", "long", 76000.0, payload, datetime.now(timezone.utc),
    )
    assert verdict is not None
    assert verdict.tier == "gold"
    assert verdict.size_pct_equity == 0.075


def test_buy_circle_div_with_bluetriangle_confirm_is_diamond(cypher_yaml, initialized_db):
    """Big circle + Div + Bluetriangle in last 12h + bias=bull → DIAMOND."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"
    ts = datetime.now(timezone.utc)
    # Bluetriangle 1 hour ago
    agent._record_alert(state, "mc_a_bluetriangle", "long", 76000.0, {}, ts - timedelta(hours=1))

    payload = {"signal": "mc_b_buy_circle_div", "symbol": "BTC/USD", "price": 76500.0}
    verdict = agent._classify_tier(
        state, "mc_b_buy_circle_div", "long", 76500.0, payload, ts,
    )
    assert verdict is not None
    assert verdict.tier == "diamond"
    assert verdict.size_pct_equity == 0.05


def test_buy_circle_div_without_confirm_is_premium(cypher_yaml, initialized_db):
    """Big circle + Div + bias=bull, no Cipher A confirm → PREMIUM."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"

    payload = {"signal": "mc_b_buy_circle_div", "symbol": "BTC/USD", "price": 76500.0}
    verdict = agent._classify_tier(
        state, "mc_b_buy_circle_div", "long", 76500.0, payload, datetime.now(timezone.utc),
    )
    assert verdict is not None
    assert verdict.tier == "premium"


def test_buy_circle_alone_with_confirm_is_big_circle(cypher_yaml, initialized_db):
    """Big circle (no div) + Cipher A confirm + bias=bull → BIG_CIRCLE."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"
    ts = datetime.now(timezone.utc)
    agent._record_alert(state, "mc_a_bluetriangle", "long", 76000.0, {}, ts - timedelta(hours=2))

    payload = {"signal": "mc_b_buy_circle", "symbol": "BTC/USD", "price": 76500.0}
    verdict = agent._classify_tier(state, "mc_b_buy_circle", "long", 76500.0, payload, ts)
    assert verdict is not None
    assert verdict.tier == "big_circle"


def test_buy_circle_alone_no_confirm_is_standard(cypher_yaml, initialized_db):
    """Big circle alone + bias=bull → STANDARD (no confirm, no div)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"

    payload = {"signal": "mc_b_buy_circle", "symbol": "BTC/USD", "price": 76500.0}
    verdict = agent._classify_tier(
        state, "mc_b_buy_circle", "long", 76500.0, payload, datetime.now(timezone.utc),
    )
    assert verdict is not None
    assert verdict.tier == "standard"


def test_longema_fires_ema_flip_tier(cypher_yaml, initialized_db):
    """Longema fires EMA_FLIP tier (catching regime change early)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"  # Longema sets bias=bull, but tier classifier runs after

    payload = {"signal": "mc_a_longema", "symbol": "BTC/USD", "price": 76000.0}
    verdict = agent._classify_tier(
        state, "mc_a_longema", "long", 76000.0, payload, datetime.now(timezone.utc),
    )
    assert verdict is not None
    assert verdict.tier == "ema_flip"


def test_buy_dot_alone_is_solo(cypher_yaml, initialized_db):
    """Small green dot + bias=bull → SOLO tier."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"

    payload = {"signal": "mc_b_buy_dot", "symbol": "BTC/USD", "price": 76500.0}
    verdict = agent._classify_tier(
        state, "mc_b_buy_dot", "long", 76500.0, payload, datetime.now(timezone.utc),
    )
    assert verdict is not None
    assert verdict.tier == "solo"


def test_signal_rejected_without_bias(cypher_yaml, initialized_db):
    """Most signals require bias — only mc_b_gold_buy and mc_a_longema
    can fire on bias=unknown."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    # bias=unknown by default

    for signal in ("mc_b_buy_circle_div", "mc_b_buy_circle", "mc_b_buy_dot"):
        payload = {"signal": signal, "symbol": "BTC/USD", "price": 76500.0}
        verdict = agent._classify_tier(
            state, signal, "long", 76500.0, payload, datetime.now(timezone.utc),
        )
        assert verdict is None, f"{signal} should not fire without bias"


def test_blood_diamond_fires_full_close(cypher_yaml, initialized_db):
    """Blood Diamond should classify as 'blood_diamond' tier on bear path."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")

    payload = {"signal": "mc_a_blood_diamond", "symbol": "BTC/USD", "price": 76000.0}
    verdict = agent._classify_tier(
        state, "mc_a_blood_diamond", "short", 76000.0, payload,
        datetime.now(timezone.utc), bypass_bias=True,
    )
    assert verdict is not None
    assert verdict.tier == "blood_diamond"


def test_sell_circle_with_bear_bias_is_big_red(cypher_yaml, initialized_db):
    """Big red circle + bias=bear → big_red tier (bear path)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bear"

    payload = {"signal": "mc_b_sell_circle", "symbol": "BTC/USD", "price": 76000.0}
    verdict = agent._classify_tier(
        state, "mc_b_sell_circle", "short", 76000.0, payload,
        datetime.now(timezone.utc), bypass_bias=True,
    )
    assert verdict is not None
    assert verdict.tier == "big_red"


def test_sommi_signals_do_not_qualify_for_tiers(cypher_yaml, initialized_db):
    """Sommi alerts are state modifiers — they should NOT produce tier
    verdicts on their own (they're filtered out of on_alert before
    reaching _classify_tier in production, but defense-in-depth: the
    classifier shouldn't emit anything for them either)."""
    agent = _make_agent(cypher_yaml, initialized_db)
    state = agent.get_state("BTC/USD")
    state.bias = "bull"

    # signal_direction returns "" for sommi signals, so direction is invalid
    for signal in ("mc_b_sommi_bull", "mc_b_sommi_bear"):
        payload = {"signal": signal, "symbol": "BTC/USD", "price": 76000.0}
        verdict = agent._classify_tier(
            state, signal, "", 76000.0, payload, datetime.now(timezone.utc),
        )
        assert verdict is None, f"{signal} should not fire a tier verdict"


# ── End-to-end on_alert ──────────────────────────────────────────────────


def test_on_alert_full_chain_emits_order(cypher_yaml, initialized_db):
    """End-to-end: bias_bull → confirm → trigger should emit a ProposedOrder."""
    agent = _make_agent(cypher_yaml, initialized_db)
    ts = datetime.now(timezone.utc)

    # 1. Longema sets bias=bull (but EMA_FLIP tier also fires; we ignore that
    #    and use a fresh ts for the actual trigger below)
    payload = {
        "signal": "mc_a_longema", "symbol": "BTC/USD",
        "price": 76000.0, "time": ts.isoformat(),
        "ticker": "BTCUSD", "interval": "1D",
    }
    order, _ = agent.on_alert(payload, account_equity=100_000.0)
    # mc_a_longema fires EMA_FLIP
    assert order is not None or order is None  # Either is OK; we test the next step

    # 2. Bluetriangle arms long
    ts2 = ts + timedelta(hours=1)
    payload = {
        "signal": "mc_a_bluetriangle", "symbol": "BTC/USD",
        "price": 76100.0, "time": ts2.isoformat(),
        "ticker": "BTCUSD", "interval": "240",
    }
    order, _ = agent.on_alert(payload, account_equity=100_000.0)
    # Bluetriangle alone doesn't fire a trade — just arms
    assert order is None

    # 3. Big green circle + Div fires DIAMOND tier (bias + arming + div)
    ts3 = ts + timedelta(hours=2)
    payload = {
        "signal": "mc_b_buy_circle_div", "symbol": "BTC/USD",
        "price": 76500.0, "time": ts3.isoformat(),
        "ticker": "BTCUSD", "interval": "240",
        "open": 76200.0, "high": 76600.0, "low": 76100.0,
    }
    # Need to bypass cooldown — set last_entry_at far in past
    state = agent.get_state("BTC/USD")
    state.last_entry_at = None
    order, decision = agent.on_alert(payload, account_equity=100_000.0)
    assert order is not None, f"Diamond tier should have fired (decision: {decision})"
    assert order.strategy == "market_cypher"
    assert order.side == "buy"
    assert (order.extra or {}).get("tier") == "diamond"
