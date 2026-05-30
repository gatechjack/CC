"""Tests for the StrategyState persistence primitive.

Commit 5 of Stage-1 Session N+1. Primitive in isolation — no site
swaps yet (those land in commit 6). Covers:

- `StrategyState.from_persistence(strategy, db_url)` round-trips with
  `set_agent_state` / `load_agent_state`.
- `StrategyState.persist_halt(strategy, reason, db_url)` writes the
  expected row.
- `StrategyState.clear_halt(strategy, db_url)` reverses it.
- RiskAgent's daily-loss-cap branch CALLS persist_halt with db_url so
  the halt survives observer re-instantiation.
- Read of an absent row → default `StrategyState(strategy=X)` with
  `halted=False`.
- Read of a malformed row → degrades to default (does not raise).
- Persistence test (the brief's explicit requirement): write halted=True,
  re-read via from_persistence in a "fresh process equivalent"
  (separate DB connection / fresh helper invocation), get halted=True.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_corp.agents.risk import RiskAgent, RiskVerdict
from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState, ProposedOrder, StrategyState,
)


# ─── round-trip primitive ──────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "ss_persist.db"
    db.init_db(f"sqlite:///{p}")
    return f"sqlite:///{p}"


def test_from_persistence_absent_returns_default(db_url):
    """No row → fresh default StrategyState(halted=False)."""
    s = StrategyState.from_persistence("my_strategy", db_url=db_url)
    assert s.strategy == "my_strategy"
    assert s.halted is False
    assert s.halt_reason is None


def test_persist_halt_then_from_persistence_round_trip(db_url):
    """The brief's load-bearing assertion: write → re-read via the
    primitive → halted=True."""
    StrategyState.persist_halt(
        "my_strategy", "test halt", db_url=db_url,
    )
    s = StrategyState.from_persistence("my_strategy", db_url=db_url)
    assert s.strategy == "my_strategy"
    assert s.halted is True
    assert s.halt_reason == "test halt"


def test_clear_halt_reverses(db_url):
    StrategyState.persist_halt(
        "my_strategy", "test halt", db_url=db_url,
    )
    assert StrategyState.from_persistence("my_strategy", db_url=db_url).halted is True
    StrategyState.clear_halt("my_strategy", db_url=db_url)
    s = StrategyState.from_persistence("my_strategy", db_url=db_url)
    assert s.halted is False
    assert s.halt_reason is None


# ─── fresh process equivalent ──────────────────────────────────────────


def test_from_persistence_survives_process_restart_equivalent(db_url):
    """Simulate the 'fresh process equivalent' scenario: persist, then
    re-read in a separate function frame (separate DB connect each
    time per the helper's contract). This is the cross-process
    persistence claim the rest of N+1 depends on."""
    StrategyState.persist_halt(
        "bitunix_futures", "daily loss cap reached", db_url=db_url,
    )

    def _fresh_read():
        # Call returns a new StrategyState — no shared in-memory state.
        return StrategyState.from_persistence("bitunix_futures", db_url=db_url)

    s = _fresh_read()
    assert s.halted is True
    assert s.halt_reason == "daily loss cap reached"
    s2 = _fresh_read()  # second call still sees it
    assert s2.halted is True


# ─── robustness ────────────────────────────────────────────────────────


def test_from_persistence_malformed_value_degrades_to_default(db_url):
    """A non-dict agent_state value (legacy or corrupt row) must NOT
    raise; degrade to the default state."""
    db.set_agent_state(
        StrategyState._AGENT_STATE_ACTOR, "weird_strategy",
        "not a dict",  # type: ignore
        db_url=db_url,
    )
    s = StrategyState.from_persistence("weird_strategy", db_url=db_url)
    assert s.halted is False
    assert s.halt_reason is None


def test_from_persistence_missing_keys_fills_safe_defaults(db_url):
    """Dict without `halted` key → default False (not raise)."""
    db.set_agent_state(
        StrategyState._AGENT_STATE_ACTOR, "partial_strategy",
        {"halt_reason": "should not matter without halted=True"},
        db_url=db_url,
    )
    s = StrategyState.from_persistence("partial_strategy", db_url=db_url)
    assert s.halted is False


# ─── RiskAgent integration: writer at halt-mutation site ───────────────


def test_risk_agent_daily_loss_cap_persists_halt(tmp_path):
    """When RiskAgent.evaluate returns halt_strategy=True (daily-loss
    cap branch), it must ALSO call persist_halt so the next eval after
    a restart still rejects. This is the 'writer at halt-mutation site'
    the brief calls out."""
    db_path = tmp_path / "risk_persist.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    # Construct a RiskAgent against the real risk.yaml on disk
    risk = RiskAgent(
        risk_yaml=Path("config/risk.yaml"),
        narrator_enabled=False,
    )
    order = ProposedOrder(
        strategy="bitunix_futures",
        symbol="BTC/USDT.P",
        side="buy",
        qty=0.001,
        order_type="market",
    )
    # equity=5000, realized_pnl=-200 → -200/5000 = 4% > 3% default cap
    account = AccountState(account="bitunix_futures", equity=5_000.0,
                            peak_equity=5_000.0)
    strategy_state = StrategyState(
        strategy="bitunix_futures", realized_pnl=-200.0,
    )

    verdict = risk.evaluate(order, account, strategy_state, db_url=db_url)
    assert verdict.verdict == "reject"
    assert verdict.halt_strategy is True

    # Persistence side effect — halt is now durable.
    fresh = StrategyState.from_persistence("bitunix_futures", db_url=db_url)
    assert fresh.halted is True
    assert "daily loss cap" in (fresh.halt_reason or "")


def test_risk_agent_daily_loss_cap_does_not_write_when_no_db_url(tmp_path):
    """db_url=None (legacy tests / no-persistence callers) → in-process
    halt verdict still works; just no persistence side effect. The
    StrategyState.persist_halt helper is robust to set_agent_state
    failures but the writer call is gated on db_url being truthy."""
    risk = RiskAgent(
        risk_yaml=Path("config/risk.yaml"),
        narrator_enabled=False,
    )
    order = ProposedOrder(
        strategy="some_legacy_strategy",
        symbol="BTC/USDT.P",
        side="buy",
        qty=0.001,
        order_type="market",
    )
    account = AccountState(account="x", equity=5_000.0, peak_equity=5_000.0)
    strategy_state = StrategyState(
        strategy="some_legacy_strategy", realized_pnl=-200.0,
    )
    verdict = risk.evaluate(order, account, strategy_state, db_url=None)
    assert verdict.verdict == "reject"
    assert verdict.halt_strategy is True
    # No assertion on persistence — the test confirms no crash when
    # db_url is missing.


def test_risk_agent_below_daily_cap_does_not_persist_halt(tmp_path):
    """Sanity: an order under the daily-loss cap → no halt verdict,
    no persistence side-effect."""
    db_path = tmp_path / "risk_no_persist.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    risk = RiskAgent(
        risk_yaml=Path("config/risk.yaml"),
        narrator_enabled=False,
    )
    order = ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P",
        side="buy", qty=0.001, order_type="market",
    )
    account = AccountState(account="x", equity=5_000.0, peak_equity=5_000.0)
    strategy_state = StrategyState(
        strategy="bitunix_futures", realized_pnl=-10.0,  # 0.2% < 3%
    )
    risk.evaluate(order, account, strategy_state, db_url=db_url)
    fresh = StrategyState.from_persistence("bitunix_futures", db_url=db_url)
    assert fresh.halted is False
