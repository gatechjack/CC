"""Unit tests for CoinbaseBTCDonchianAgent.

Pin the agent's contract with the orchestrator: state machine
transitions, persistence, dedup, dust handling on reconciliation,
and ProposedOrder shape.

These tests construct an in-memory SQLite DB so persistence paths
exercise real code, not stubs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from trading_corp.agents.strategies.coinbase_btc_donchian_agent import (
    CoinbaseBTCDonchianAgent,
)
from trading_corp.agents.strategies.donchian_btc import State
from trading_corp.persistence.db import init_db, set_agent_state


def _strategies_yaml(tmp_path: Path) -> Path:
    """Write a minimal strategies.yaml so the agent has something
    to reload. Mirrors the production config block shape."""
    p = tmp_path / "strategies.yaml"
    p.write_text(
        yaml.safe_dump({
            "coinbase_btc_donchian": {
                "enabled": True,
                "auto_execute": False,
                "division": "coinbase_spot",
                "symbol": "BTC/USD",
                "starting_state": "auto",
                "donchian": {
                    "entry_lookback": 5,        # short lookbacks so unit
                    "exit_lookback": 3,         # tests with hand-rolled
                    "trend_filter_lookback": None,   # bar windows trigger
                    "granularity_seconds": 21600,
                },
                "audit": {"log_skip_decisions": True},
            },
        }),
        encoding="utf-8",
    )
    return p


def _db_url(tmp_path: Path) -> str:
    """Initialize a fresh on-disk SQLite (in-memory shares state
    poorly across separate connect() calls). Returns the url string."""
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"
    init_db(url)
    return url


def _bars(n: int, start_price: float = 80_000.0, step: float = 10.0,
          start_ts: datetime | None = None) -> list[dict]:
    """Make a sequence of n monotonically rising 6h bars where each
    bar's high = close (no upper wick). With monotonic +step closes,
    the LATEST bar strictly exceeds all prior bars' highs — that's
    the condition needed to trigger a Donchian breakout in tests."""
    start_ts = start_ts or datetime(2026, 5, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        price = start_price + step * i
        out.append({
            "ts": start_ts + timedelta(hours=6 * i),
            "open": price - step / 2,
            "high": price,                # high == close — no upper wick
            "low": price - step,
            "close": price,
            "volume": 100.0,
        })
    return out


# ── Disabled / config gates ────────────────────────────────────────


def test_disabled_strategy_returns_none(tmp_path):
    cfg = _strategies_yaml(tmp_path)
    raw = yaml.safe_load(cfg.read_text())
    raw["coinbase_btc_donchian"]["enabled"] = False
    cfg.write_text(yaml.safe_dump(raw))
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=cfg)
    order, reason = agent.on_bar_close(_bars(10), account_equity=10_000, held_btc=0.0)
    assert order is None
    assert "disabled" in reason


def test_no_bars_returns_none(tmp_path):
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    order, reason = agent.on_bar_close([], account_equity=10_000, held_btc=0.0)
    assert order is None
    assert "no bars" in reason


def test_warmup_returns_skip(tmp_path):
    """Fewer bars than entry_lookback → skip with 'warmup' reason
    surfaced from the donchian engine."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    # entry_lookback=5 → need 6 bars; supply 3
    order, reason = agent.on_bar_close(_bars(3), account_equity=10_000, held_btc=0.0)
    assert order is None
    assert "warmup" in reason


# ── Decision → ProposedOrder ───────────────────────────────────────


def test_buy_decision_emits_buy_order(tmp_path):
    """Monotonically rising bars → current close > 5-bar high →
    state=CASH agent emits a BUY for the full account equity."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    # State defaults to CASH. Need 6+ bars rising for breakout to fire.
    bars = _bars(7, start_price=80_000.0, step=100.0)
    order, reason = agent.on_bar_close(bars, account_equity=10_000.0, held_btc=0.0)
    assert order is not None
    assert order.side == "buy"
    assert order.strategy == "coinbase_btc_donchian"
    assert order.symbol == "BTC/USD"
    assert order.order_type == "market"
    assert order.limit_price == bars[-1]["close"]
    # qty = equity / current close
    expected_qty = 10_000.0 / bars[-1]["close"]
    assert order.qty == pytest.approx(expected_qty)
    # Audit-grade extra
    assert order.extra["decision"] == "buy"
    assert order.extra["donchian_high"] is not None
    assert order.extra["asset_type"] == "crypto"
    assert "breakout" in reason


def test_sell_decision_closes_full_btc_position(tmp_path):
    """State=BTC, breakdown fires → sell order for the FULL held BTC,
    realized P&L estimate populated against cost_basis."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    # Force agent into BTC state with cost_basis $80k
    agent.mark_filled(side="buy", fill_price=80_000.0)
    assert agent.get_state() == (State.BTC, 80_000.0)

    # Falling bars → close < exit_lookback low → SELL
    bars = []
    for i in range(7):
        # Initial 4 bars rise to set the channel high, then 3 bars fall hard
        if i < 4:
            price = 80_000.0 + 100.0 * i
        else:
            price = 80_000.0 - 200.0 * (i - 3)
        bars.append({
            "ts": datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(hours=6 * i),
            "open": price, "high": price + 50, "low": price - 50,
            "close": price, "volume": 100.0,
        })

    order, reason = agent.on_bar_close(bars, account_equity=8_000.0, held_btc=0.1)
    assert order is not None
    assert order.side == "sell"
    assert order.qty == 0.1
    assert order.extra["cost_basis"] == 80_000.0
    # Realized P&L: (final_close - 80_000) * 0.1
    expected_pnl = (bars[-1]["close"] - 80_000.0) * 0.1
    assert order.extra["realized_pnl_estimate"] == pytest.approx(expected_pnl)
    assert "breakdown" in reason


def test_buy_skipped_when_already_in_btc(tmp_path):
    """State=BTC + breakout signal: state-aware logic skips the buy."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    agent.mark_filled(side="buy", fill_price=80_000.0)
    bars = _bars(7, start_price=80_000.0, step=100.0)
    order, reason = agent.on_bar_close(bars, account_equity=8_000.0, held_btc=0.1)
    assert order is None
    assert "hold" in reason or "no breakdown" in reason or "no breakout" in reason


def test_sell_with_no_held_btc_skips(tmp_path):
    """Defensive: if state thinks we're BTC but broker reports
    held_btc=0, we don't emit a phantom SELL — caller should
    reconcile state via restore_from_broker first."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    agent.mark_filled(side="buy", fill_price=80_000.0)

    # Build bars that would trigger SELL
    bars = []
    for i in range(7):
        if i < 4:
            price = 80_000.0 + 100.0 * i
        else:
            price = 80_000.0 - 200.0 * (i - 3)
        bars.append({
            "ts": datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(hours=6 * i),
            "open": price, "high": price + 50, "low": price - 50,
            "close": price, "volume": 100.0,
        })

    order, reason = agent.on_bar_close(bars, account_equity=8_000.0, held_btc=0.0)
    assert order is None
    assert "held_btc" in reason or "broker drift" in reason


# ── Dedup ──────────────────────────────────────────────────────────


def test_same_bar_evaluated_twice_returns_none(tmp_path):
    """Orchestrator-level double-call protection: same bar ts twice =
    second call returns None with a 'already evaluated' reason."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    bars = _bars(7, start_price=80_000.0, step=100.0)
    o1, _ = agent.on_bar_close(bars, account_equity=10_000.0, held_btc=0.0)
    assert o1 is not None    # first call fires
    o2, r2 = agent.on_bar_close(bars, account_equity=10_000.0, held_btc=0.0)
    assert o2 is None
    assert "already evaluated" in r2


# ── Persistence ────────────────────────────────────────────────────


def test_state_persists_across_agent_instances(tmp_path):
    """Two agent instances pointing at the same DB share state. After
    one instance does mark_filled(buy), the other's get_state should
    show BTC."""
    cfg = _strategies_yaml(tmp_path)
    db = _db_url(tmp_path)
    a1 = CoinbaseBTCDonchianAgent(strategies_yaml=cfg, db_url=db)
    a1.mark_filled(side="buy", fill_price=80_000.0)
    assert a1.get_state() == (State.BTC, 80_000.0)

    # New instance — should restore
    a2 = CoinbaseBTCDonchianAgent(strategies_yaml=cfg, db_url=db)
    state, cost_basis = a2.get_state()
    assert state == State.BTC
    assert cost_basis == 80_000.0


def test_stale_persisted_state_is_discarded(tmp_path):
    """Persisted state older than STATE_MAX_AGE (default 7d) is
    deleted on restore — caller is expected to reconcile via
    restore_from_broker after instantiation."""
    cfg = _strategies_yaml(tmp_path)
    db = _db_url(tmp_path)
    # Manually write a state row with a timestamp 30 days old by
    # writing now and then... actually we can't backdate via the
    # public helper. Use direct DB manipulation.
    import sqlite3
    db_path = tmp_path / "test.db"
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agent_state (agent, key, value_json, updated_ts) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(agent, key) DO UPDATE SET "
            "  value_json=excluded.value_json, updated_ts=excluded.updated_ts",
            ("coinbase_btc_donchian", "state",
             '{"state":"btc","cost_basis":80000.0}', old_ts),
        )

    agent = CoinbaseBTCDonchianAgent(strategies_yaml=cfg, db_url=db)
    state, cost_basis = agent.get_state()
    # Stale → fell back to cash default
    assert state == State.CASH
    assert cost_basis is None


# ── Broker reconciliation ──────────────────────────────────────────


def test_restore_from_broker_seeds_btc_state_at_current_price(tmp_path):
    """Per Board direction: if the broker reports BTC held on
    startup, agent state becomes BTC with cost_basis = current
    market price (not historical entry)."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    agent.restore_from_broker(
        account_equity=10_000.0, held_btc=0.05, current_price=80_000.0,
    )
    state, cost_basis = agent.get_state()
    assert state == State.BTC
    assert cost_basis == 80_000.0


def test_restore_from_broker_treats_dust_as_cash(tmp_path):
    """Sub-$1 BTC dust shouldn't pin agent into BTC state. Protects
    against rounding / unfilled-fragment scenarios where 0.0000001
    BTC sits in the account."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    # 0.000001 BTC × $80k = $0.08 → below dust threshold
    agent.restore_from_broker(
        account_equity=10_000.0, held_btc=0.000001, current_price=80_000.0,
    )
    state, cost_basis = agent.get_state()
    assert state == State.CASH
    assert cost_basis is None


def test_restore_from_broker_with_zero_held_is_cash(tmp_path):
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    # Force into BTC state first
    agent.mark_filled(side="buy", fill_price=80_000.0)
    # Then reconcile with broker reporting 0 — should flip back to CASH
    agent.restore_from_broker(
        account_equity=10_000.0, held_btc=0.0, current_price=80_000.0,
    )
    state, cost_basis = agent.get_state()
    assert state == State.CASH
    assert cost_basis is None


# ── State transitions via mark_filled ──────────────────────────────


def test_mark_filled_buy_transitions_to_btc(tmp_path):
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    assert agent.get_state() == (State.CASH, None)
    agent.mark_filled(side="buy", fill_price=75_500.0)
    assert agent.get_state() == (State.BTC, 75_500.0)


def test_mark_filled_sell_transitions_to_cash(tmp_path):
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    agent.mark_filled(side="buy", fill_price=75_500.0)
    agent.mark_filled(side="sell", fill_price=82_000.0)
    assert agent.get_state() == (State.CASH, None)


def test_mark_filled_unknown_side_is_noop(tmp_path):
    """Defensive: unknown side string doesn't corrupt state."""
    agent = CoinbaseBTCDonchianAgent(strategies_yaml=_strategies_yaml(tmp_path))
    before = agent.get_state()
    agent.mark_filled(side="frobnicate", fill_price=1.0)
    assert agent.get_state() == before
