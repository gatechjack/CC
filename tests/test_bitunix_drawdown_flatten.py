"""D1/D2 — account-drawdown auto-flatten breaker.

Before this fix the 15% account-drawdown auto-flatten was a PLACEHOLDER that
NEVER fired: both observer risk-eval call sites constructed
`AccountState(peak_equity=current_equity)`, so `drawdown_pct()` was always 0,
so the `flatten_account` verdict was never produced. (See
`reports/2026-06-11_bitunix_hitl_removal_for_autonomous_live.md` §3 D1.)

This module pins the breaker as REAL:
  - `_tracked_peak_equity` maintains a persisted account high-water-mark that
    ratchets up only and survives restart (the bug recreates if peak resets to
    current on reload).
  - A peak-then-15%-drop produces a real (non-zero) drawdown and a
    `flatten_account` verdict from the real RiskAgent.
  - Boundary behavior: just-below 15% does NOT flatten; exactly-at and
    just-above DO.

The D2 dispatch (flatten_division fires on BOTH the score path and the
Phase-3.1 path) is pinned next to the existing path harnesses, in
`test_bitunix_observer_pa_redeem.py` (score path) and
`test_bitunix_futures_observer.py` (Phase-3.1 path).
"""
from __future__ import annotations

import pytest

from trading_corp.agents.divisions import bitunix_futures_observer as obs_mod
from trading_corp.agents.divisions.bitunix_futures_observer import (
    PEAK_EQUITY_AGENT_STATE_KEY,
    BitunixFuturesObserver,
)
from trading_corp.agents.risk import RiskAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState,
    ProposedOrder,
    StrategyState,
)


# ─── fixtures / helpers ─────────────────────────────────────────────────


@pytest.fixture
def observer(tmp_path):
    db_path = tmp_path / "drawdown.db"
    db.init_db(f"sqlite:///{db_path}")
    return BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")


def _order() -> ProposedOrder:
    return ProposedOrder(
        strategy="demo", symbol="SPY", side="buy", qty=10,
        order_type="limit", limit_price=500.0,
    )


def _strategy() -> StrategyState:
    return StrategyState(strategy="demo", halted=False, realized_pnl=0.0)


def _stored_peak(db_url: str) -> float | None:
    loaded = db.load_agent_state(
        "bitunix_futures", PEAK_EQUITY_AGENT_STATE_KEY, db_url=db_url,
    )
    if loaded is None:
        return None
    return loaded[0]["peak"]


# ─── _tracked_peak_equity: high-water-mark mechanics (D1 core) ───────────


def test_first_eval_initializes_peak_to_current(observer):
    """Fresh account (no stored peak) → peak == current, persisted. With
    peak == current the drawdown is 0, so the first eval can never produce a
    false flatten — identical to today's below-threshold behavior."""
    assert observer._tracked_peak_equity(100_000.0) == 100_000.0
    assert _stored_peak(observer.db_url) == 100_000.0


def test_peak_ratchets_up_only(observer):
    """A new high raises the peak; a dip NEVER lowers it. This is the whole
    point — the peak must be a true high-water-mark, not the per-call equity
    (the old bug fed current as the peak)."""
    assert observer._tracked_peak_equity(100_000.0) == 100_000.0  # init
    assert observer._tracked_peak_equity(120_000.0) == 120_000.0  # new high
    assert observer._tracked_peak_equity(110_000.0) == 120_000.0  # dip: held
    assert observer._tracked_peak_equity(130_000.0) == 130_000.0  # new high
    assert _stored_peak(observer.db_url) == 130_000.0


def test_peak_survives_restart(tmp_path):
    """The persisted peak must survive observer / process re-instantiation.
    If a restart reset the peak to current equity, the bug would silently
    return: a post-restart drop would compute drawdown against the depressed
    equity and never flatten."""
    db_path = tmp_path / "restart.db"
    db.init_db(f"sqlite:///{db_path}")

    first = BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    assert first._tracked_peak_equity(120_000.0) == 120_000.0

    # Fresh instance, same DB — simulates a process restart.
    second = BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    # Equity has since dropped to 100k; the peak must NOT reset to 100k.
    assert second._tracked_peak_equity(100_000.0) == 120_000.0


def test_read_failure_falls_back_to_current(observer, monkeypatch):
    """FAIL-SAFE: if the persisted peak can't be read, fall back to current
    equity (== pre-fix behavior, drawdown 0). A persistence hiccup must NEVER
    manufacture a false flatten of the account."""
    def _boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(obs_mod.db, "load_agent_state", _boom)
    # Returns current, no exception propagates.
    assert observer._tracked_peak_equity(100_000.0) == 100_000.0


# ─── tracked peak → real drawdown → flatten verdict (D1, §7 criterion) ───


def test_tracked_peak_yields_real_drawdown_and_flatten(observer, tmp_risk_yaml):
    """End-to-end D1: a peak of 100k followed by a drop to 85k yields a REAL
    15% drawdown (not 0) and the real RiskAgent produces a flatten_account
    verdict — the exact path that was dead before the fix."""
    observer._tracked_peak_equity(100_000.0)            # establish the peak
    peak = observer._tracked_peak_equity(85_000.0)      # dip → peak held at 100k
    assert peak == 100_000.0

    acct = AccountState(account="bitunix_futures", equity=85_000.0, peak_equity=peak)
    assert acct.drawdown_pct() == pytest.approx(0.15)   # real, not 0

    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    v = risk.evaluate(_order(), acct, _strategy())
    assert v.verdict == "reject"
    assert v.flatten_account is True


@pytest.mark.parametrize(
    "equity,expect_flatten",
    [
        (85_500.0, False),   # drawdown 14.5% — just below the 15% cap
        (85_000.0, True),    # drawdown exactly 15.0% — at the cap (>=)
        (84_000.0, True),    # drawdown 16.0% — just above the cap
    ],
)
def test_drawdown_boundary(observer, tmp_risk_yaml, equity, expect_flatten):
    """Boundary: the breaker fires at and above 15%, and NOT just below.
    Peak is fed through the tracked high-water-mark so this exercises the
    real integration (tracked peak → AccountState → RiskAgent)."""
    observer._tracked_peak_equity(100_000.0)
    peak = observer._tracked_peak_equity(equity)
    assert peak == 100_000.0

    acct = AccountState(account="bitunix_futures", equity=equity, peak_equity=peak)
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    v = risk.evaluate(_order(), acct, _strategy())
    assert v.flatten_account is expect_flatten
