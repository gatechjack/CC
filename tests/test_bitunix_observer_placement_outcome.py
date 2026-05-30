"""Byte-identity tests for the canonical `_record_placement_outcome` helper.

Commit 1 of Stage-1 Session N+1: the two parallel wire sites
(`_maybe_propose` trigger-path and `_score_and_maybe_propose_locked`
score-path) get extracted into one canonical helper. This file locks
down the contract:

  * The helper exists on `BitunixFuturesObserver`.
  * Both call sites route through it.
  * Paper-mode behavior is byte-identical pre- and post-refactor:
    - `audit_event` rows: same kind, same payload fields
    - `paper_trade_record` row: same typed columns + same
      `extra_json` carry asymmetry (score-path carries
      `order.extra`; trigger-path does not — preserved here for
      byte-identity; future commit may unify, separately).
    - `log_proposed_order` called with status='would_have_placed'.
    - `_record_daily_risk` invoked with the same args.

This is the foundation for commit 3 (live branch INSIDE the helper).
After commit 3 the helper's behavior diverges by `execution_mode`;
this file's tests still hold because they pin paper-mode (the default).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions import bitunix_futures_observer as obs_mod
from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.strategies.bitunix_confluence import (
    BitUnixConfluenceConfig,
    BitUnixVerdict,
    FactorConfig,
    GuardConfig,
    ScoreBreakdown,
    Side,
    Tier,
)
from trading_corp.agents.strategies.bitunix_pa_validation import (
    PAValidationConfig,
    PAValidationDecision,
    PAValidationResult,
)
from trading_corp.persistence import db


# ─── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def wired_observer(tmp_path: Path):
    """Observer wired with mocked deps; both trigger-path and score-path
    can be exercised by manipulating the bias/CVD state and the scoring
    config / monkeypatch."""
    db_path = tmp_path / "placement.db"
    db.init_db(f"sqlite:///{db_path}")

    risk_agent = MagicMock()
    risk_verdict = MagicMock()
    risk_verdict.verdict = "approve"
    risk_verdict.reason = "ok"
    risk_verdict.new_qty = None
    risk_agent.evaluate.return_value = risk_verdict

    snap = MagicMock()
    snap.equity = 5_000.0
    snap.positions = []
    broker = MagicMock()
    broker.snapshot = AsyncMock(return_value=snap)
    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.flatten_division = AsyncMock()

    logger_agent = MagicMock()
    telegram_channel = MagicMock()
    telegram_channel.push = AsyncMock()

    obs = BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}",
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram_channel,
    )
    return obs, risk_agent, data_exec, logger_agent, telegram_channel


def _minimal_scoring_config() -> BitUnixConfluenceConfig:
    f = FactorConfig(name="synth", side="buy", weight=3, ttl_minutes=30)
    return BitUnixConfluenceConfig(
        enabled=True,
        min_score_to_fire=5,
        premium_threshold=10,
        standard_threshold=5,
        weak_threshold=3,
        cooldown_seconds=1800,
        dedupe_within_ttl=True,
        factors={"synth": f},
        sell_on_rush=GuardConfig(window_minutes=60, brackets=()),
        buy_on_fall=GuardConfig(window_minutes=60, brackets=()),
        score_timeframes=("3m", "15m", "30m"),
        factor_ttl_per_tf={},
        pa_factors_in_score=True,
        guards_in_score=True,
    )


def _minimal_pa_config() -> PAValidationConfig:
    return PAValidationConfig(
        enabled=False,  # disable PA so score-path proceeds to placement
        require_all=True,
        min_validators_passed=0,
        validators=(),
        rush_fall_enabled=False,
    )


def _verdict(tier: Tier, side: Side, net_score: int = 12) -> BitUnixVerdict:
    return BitUnixVerdict(
        tier=tier,
        side=side,
        breakdown=ScoreBreakdown(
            buy_contributions=[("synth", net_score)] if side == Side.BUY else [],
            sell_contributions=[("synth", net_score)] if side == Side.SELL else [],
            raw_buy_score=net_score if side == Side.BUY else 0,
            raw_sell_score=net_score if side == Side.SELL else 0,
            final_buy_score=net_score if side == Side.BUY else 0,
            final_sell_score=net_score if side == Side.SELL else 0,
            net_score=net_score if side == Side.BUY else -net_score,
            winning_side=side,
        ),
        reason=f"synth {tier.value} {side.value}",
        cooldown_blocked=False,
    )


# ─── helper exists + signature ──────────────────────────────────────────


def test_record_placement_outcome_method_exists(wired_observer):
    """The canonical helper must exist on the observer."""
    obs, *_ = wired_observer
    assert hasattr(obs, "_record_placement_outcome"), (
        "BitunixFuturesObserver must expose `_record_placement_outcome` "
        "as the canonical post-risk-approve writer (paper today, live in "
        "commit 3)."
    )


# ─── trigger-path: helper invocation + byte-identity ────────────────────


@pytest.mark.asyncio
async def test_trigger_path_calls_helper_once(wired_observer, monkeypatch):
    """The trigger-path (_maybe_propose) must route the placement
    side-effects through `_record_placement_outcome`, not duplicate them
    inline."""
    obs, risk_agent, data_exec, logger_agent, telegram_channel = wired_observer

    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")

    calls = {"n": 0, "args": None}
    original = obs._record_placement_outcome

    async def spy(*args, **kwargs):
        calls["n"] += 1
        calls["args"] = kwargs
        return await original(*args, **kwargs)

    monkeypatch.setattr(obs, "_record_placement_outcome", spy)

    payload = {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }
    await obs.observe_and_decide(payload, source="lord_otter")

    assert calls["n"] == 1, "trigger-path must call the helper exactly once"
    # carry_order_extra_to_record=False preserves trigger-path's
    # pre-refactor behavior (extra_json stays NULL).
    assert calls["args"].get("carry_order_extra_to_record") is False


@pytest.mark.asyncio
async def test_trigger_path_byte_identity(wired_observer):
    """Trigger-path placement produces the same audit kind, payload
    fields, and paper_trade_record shape as before extraction.

    logger_agent is a MagicMock (does not write to audit_event), so we
    inspect would_have_placed via call_args_list. paper_trade_record IS
    written to DB directly via db.insert_paper_trade_record, so we
    query it there."""
    obs, risk_agent, data_exec, logger_agent, telegram_channel = wired_observer

    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")

    payload = {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }
    await obs.observe_and_decide(payload, source="lord_otter")

    # would_have_placed audit: inspect via mock call_args_list
    wp_calls = [c for c in logger_agent.log_event.call_args_list
                if c.kwargs.get("kind") == "would_have_placed"]
    assert len(wp_calls) == 1, "exactly one would_have_placed audit per trigger placement"
    p = wp_calls[0].kwargs["payload"]
    # Required tagging keys (CLAUDE.md §state+audit)
    assert p["strategy"] == "bitunix_futures"
    assert p["division"] == "bitunix_futures"
    # Trigger-path-specific payload shape: no `via` key, no `redeemed`
    assert "via" not in p
    assert "redeemed" not in p
    assert "net_score" not in p
    # Required content
    assert p["tier"] == "PREMIUM"
    assert p["trigger_signal"] == "spoon_bull"
    assert p["entry_price"] == 80_000.0

    # paper_trade_record row: extra_json stays NULL for trigger-path
    # (asymmetry preserved — score-path carries, trigger-path does not).
    with db.connect(obs.db_url) as conn:
        ptr_row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE strategy='bitunix_futures'"
        ).fetchone()
    assert ptr_row is not None
    assert ptr_row["extra_json"] is None, (
        "trigger-path's extra_json must remain NULL post-refactor "
        "(byte-identical — score-path is the only carrier)"
    )

    # log_proposed_order called with status='would_have_placed'
    assert logger_agent.log_proposed_order.called
    order_arg = logger_agent.log_proposed_order.call_args.args[0]
    assert order_arg.status == "would_have_placed"


# ─── score-path: helper invocation + byte-identity ──────────────────────


@pytest.fixture
def score_wired_observer(tmp_path: Path, monkeypatch):
    """Score-path-ready observer with scoring + PA disabled (no PA gate),
    monkeypatch evaluate_confluence_futures to return a PREMIUM verdict."""
    db_path = tmp_path / "score_placement.db"
    db.init_db(f"sqlite:///{db_path}")

    risk_agent = MagicMock()
    risk_verdict = MagicMock()
    risk_verdict.verdict = "approve"
    risk_verdict.reason = "ok"
    risk_verdict.new_qty = None
    risk_agent.evaluate.return_value = risk_verdict

    snap = MagicMock()
    snap.equity = 5_000.0
    snap.positions = []
    broker = MagicMock()
    broker.snapshot = AsyncMock(return_value=snap)
    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.flatten_division = AsyncMock()

    logger_agent = MagicMock()
    telegram_channel = MagicMock()
    telegram_channel.push = AsyncMock()

    obs = BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}",
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram_channel,
        scoring_config=_minimal_scoring_config(),
        pa_config=_minimal_pa_config(),
        htf_gate_mode="off",
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.BUY, net_score=12),
    )
    return obs, risk_agent, data_exec, logger_agent, telegram_channel


@pytest.mark.asyncio
async def test_score_path_calls_helper_once(score_wired_observer, monkeypatch):
    obs, *_ = score_wired_observer
    calls = {"n": 0, "args": None}
    original = obs._record_placement_outcome

    async def spy(*args, **kwargs):
        calls["n"] += 1
        calls["args"] = kwargs
        return await original(*args, **kwargs)

    monkeypatch.setattr(obs, "_record_placement_outcome", spy)

    payload = {
        "signal": "mc_b_gold_buy", "symbol": "BTCUSDT",
        "price": 80_000.0, "interval": "3",
        "time": "2026-05-23T10:03:00Z",
    }
    await obs._score_and_maybe_propose(payload, source="lord_otter")

    assert calls["n"] == 1, "score-path must call the helper exactly once"
    assert calls["args"].get("carry_order_extra_to_record") is True, (
        "score-path must carry order.extra into record.extra "
        "(pre-refactor behavior, required for backtest reconstruction)"
    )


@pytest.mark.asyncio
async def test_score_path_byte_identity(score_wired_observer):
    """Score-path placement produces the same audit kind, payload fields
    (including via='bitunix_score', net_score, redeemed=False), and
    paper_trade_record.extra_json carry."""
    obs, risk_agent, data_exec, logger_agent, telegram_channel = score_wired_observer

    payload = {
        "signal": "mc_b_gold_buy", "symbol": "BTCUSDT",
        "price": 80_000.0, "interval": "3",
        "time": "2026-05-23T10:03:00Z",
    }
    await obs._score_and_maybe_propose(payload, source="lord_otter")

    # Same mocked-logger inspection as the trigger-path test.
    wp_calls = [c for c in logger_agent.log_event.call_args_list
                if c.kwargs.get("kind") == "would_have_placed"]
    assert len(wp_calls) == 1, "exactly one would_have_placed audit per score placement"
    p = wp_calls[0].kwargs["payload"]
    assert p["strategy"] == "bitunix_futures"
    assert p["division"] == "bitunix_futures"
    # Score-path-specific tagging
    assert p["via"] == "bitunix_score"
    assert p["net_score"] == 12
    assert p["redeemed"] is False
    assert p["bars_waited"] is None
    assert p["tier"] == "PREMIUM"

    # paper_trade_record row: extra_json populated with order.extra
    # (score-path-only carry — backtests need score_path/net_score/etc.)
    with db.connect(obs.db_url) as conn:
        ptr = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE strategy='bitunix_futures'"
        ).fetchone()
    assert ptr is not None
    assert ptr["extra_json"] is not None, (
        "score-path must populate extra_json (carries score_path/net_score/etc.)"
    )
    extra = json.loads(ptr["extra_json"])
    assert extra.get("score_path") is True
    assert extra.get("net_score") == 12

    # log_proposed_order called with status='would_have_placed' AND
    # rationale prefixed with [score]
    assert logger_agent.log_proposed_order.called
    order_arg = logger_agent.log_proposed_order.call_args.args[0]
    assert order_arg.status == "would_have_placed"
    assert order_arg.rationale.startswith("[score]"), (
        "score-path mutates rationale BEFORE log_proposed_order; "
        "byte-identical post-refactor"
    )


# ─── helper internals: paper-mode default behavior ──────────────────────


@pytest.mark.asyncio
async def test_helper_paper_mode_does_not_call_data_exec_place(wired_observer):
    """Paper-mode (default) helper invocation must NOT route to
    data_exec.place(). This is the load-bearing isolation property —
    commit 3 adds the live branch INSIDE the helper, but paper mode
    stays unchanged.

    Important: data_exec.place is the canary; we assert it is never
    called from the helper directly under paper-mode."""
    obs, risk_agent, data_exec, logger_agent, telegram_channel = wired_observer
    # data_exec is a MagicMock; arm `.place` so we can detect calls
    data_exec.place = AsyncMock()

    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")

    payload = {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }
    await obs.observe_and_decide(payload, source="lord_otter")

    data_exec.place.assert_not_called()


@pytest.mark.asyncio
async def test_helper_records_daily_risk_with_proposal_pct(wired_observer):
    """_record_daily_risk must be called from the helper with the
    proposal's effective_risk_pct (or 0.0 fallback)."""
    obs, risk_agent, data_exec, logger_agent, telegram_channel = wired_observer

    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")

    today = datetime.now(timezone.utc).date().isoformat()
    cum_before, n_before = obs._read_daily_risk(today)

    payload = {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }
    await obs.observe_and_decide(payload, source="lord_otter")

    cum_after, n_after = obs._read_daily_risk(today)
    assert n_after == n_before + 1
    assert cum_after > cum_before
