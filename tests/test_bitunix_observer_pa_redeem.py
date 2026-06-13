"""Tests for the deferred-fire PA redeem mechanism.

Plan: `i-need-to-work-gentle-honey.md`. When PA rejects a high-score
TV alert in enforce mode, the observer caches the payload instead of
discarding it. A 60s background loop (`run_pa_redeem_loop`) re-runs
the full pipeline against fresh bars until either:

  - PA finally passes → fire, `pa_validation_redeem` audit row written.
  - Score decays to SKIP → cache cleared.
  - Opposite side wins → cache cleared (null-and-void rule).

These tests pin the cache lifecycle, the redeem-audit emission, and
the loop's idle-cheap behavior.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions import bitunix_futures_observer as obs_mod
from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
    OrderProposal,
)
from trading_corp.agents.risk import RiskVerdict
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
from trading_corp.persistence.models import ProposedOrder


# ─── helpers ────────────────────────────────────────────────────────────


def _verdict(tier: Tier, side: Side, net_score: int = 5) -> BitUnixVerdict:
    return BitUnixVerdict(
        tier=tier,
        side=side,
        breakdown=ScoreBreakdown(
            buy_contributions=[("synth", 3)] if side == Side.BUY else [],
            sell_contributions=[("synth", 3)] if side == Side.SELL else [],
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


def _pa_reject() -> PAValidationResult:
    return PAValidationResult(
        decision=PAValidationDecision.REJECT,
        side="sell",
        passed=("vwap_alignment",),
        failed=("volume_confirmation", "structure_alignment"),
        rush_fall_triggered=None,
        reason="REJECT: require_all (passed 1/3); failed=['volume_confirmation', 'structure_alignment']",
    )


def _pa_pass(side: str = "sell") -> PAValidationResult:
    return PAValidationResult(
        decision=PAValidationDecision.PASS,
        side=side,
        passed=("vwap_alignment", "volume_confirmation", "structure_alignment"),
        failed=(),
        rush_fall_triggered=None,
        reason="PASS: require_all (passed 3/3)",
    )


def _minimal_scoring_config() -> BitUnixConfluenceConfig:
    """A scoring_config that makes `scoring_config.enabled` True and
    `_max_ttl_minutes` non-zero, but is otherwise inert. We monkeypatch
    `evaluate_confluence_futures` in the tests so the factors here
    don't actually drive any decision."""
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
        enabled=True,
        require_all=True,
        min_validators_passed=0,
        validators=("vwap_alignment", "volume_confirmation", "structure_alignment"),
        rush_fall_enabled=False,
    )


@pytest.fixture
def wired_observer(tmp_path: Path):
    """Observer with mocked deps + scoring_config + pa_config + enforce
    mode, ready to drive `_score_and_maybe_propose`. The scorer + PA
    evaluator are NOT patched here; tests use monkeypatch to install
    per-test behavior."""
    db_path = tmp_path / "redeem.db"
    db.init_db(f"sqlite:///{db_path}")

    risk_agent = MagicMock()
    risk_verdict = MagicMock()
    risk_verdict.verdict = "approve"
    risk_verdict.reason = "ok"
    risk_verdict.new_qty = None
    # Realistic verdict: a normal approve/reject does NOT flatten. Without
    # this, the bare MagicMock's `.flatten_account` is a truthy auto-child,
    # which (post the D1/D2 score-path flatten dispatch) would spuriously
    # trigger `data_exec.flatten_division`. Mirrors production RiskVerdict
    # (flatten_account defaults False).
    risk_verdict.flatten_account = False
    risk_agent.evaluate.return_value = risk_verdict

    snap = MagicMock()
    snap.equity = 5_000.0
    snap.positions = []
    broker = MagicMock()
    broker.snapshot = AsyncMock(return_value=snap)

    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    # Awaitable so a flatten dispatch (when a test opts into flatten_account)
    # doesn't choke on a non-awaitable MagicMock; matches the Phase-3.1 fixture.
    data_exec.flatten_division = AsyncMock()

    logger_agent = MagicMock()

    obs = BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}",
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        scoring_config=_minimal_scoring_config(),
        pa_config=_minimal_pa_config(),
        htf_gate_mode="enforce",
    )
    return obs


def _payload_btc(price: float = 80_000.0, signal: str = "mc_a_red_diamond") -> dict:
    return {
        "symbol": "BTCUSDT",
        "signal": signal,
        "price": price,
        "interval": "3",
        "time": "2026-05-17T03:00:00Z",
    }


# ─── _clear_pending_pa ──────────────────────────────────────────────────


def test_clear_pending_pa_resets_all_three_attrs(wired_observer):
    wired_observer._pending_pa_payload = {"x": 1}
    wired_observer._pending_pa_side = "buy"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)
    wired_observer._clear_pending_pa()
    assert wired_observer._pending_pa_payload is None
    assert wired_observer._pending_pa_side is None
    assert wired_observer._pending_pa_cached_at_ts is None


def test_clear_pending_pa_is_idempotent_when_empty(wired_observer):
    wired_observer._clear_pending_pa()
    wired_observer._clear_pending_pa()  # no exception
    assert wired_observer._pending_pa_payload is None


# ─── cache lifecycle through the pipeline ───────────────────────────────


@pytest.mark.asyncio
async def test_pa_reject_in_enforce_caches_payload(wired_observer, monkeypatch):
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_reject(),
    )

    payload = _payload_btc()
    await wired_observer._score_and_maybe_propose(payload, source="lord_otter")

    assert wired_observer._pending_pa_payload is not None
    assert wired_observer._pending_pa_payload["signal"] == "mc_a_red_diamond"
    assert wired_observer._pending_pa_side == "sell"
    assert wired_observer._pending_pa_cached_at_ts is not None


@pytest.mark.asyncio
async def test_pa_reject_in_shadow_does_not_cache(tmp_path, monkeypatch):
    """Shadow mode is audit-only; cache MUST stay clean so the redeem
    loop doesn't re-eval signals the production gate never blocked."""
    db_path = tmp_path / "shadow.db"
    db.init_db(f"sqlite:///{db_path}")
    obs = BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}",
        risk_agent=MagicMock(),
        data_exec=MagicMock(brokers={}),
        logger_agent=MagicMock(),
        scoring_config=_minimal_scoring_config(),
        pa_config=_minimal_pa_config(),
        htf_gate_mode="shadow",
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_reject(),
    )

    await obs._score_and_maybe_propose(_payload_btc(), source="lord_otter")
    assert obs._pending_pa_payload is None
    assert obs._pending_pa_side is None


@pytest.mark.asyncio
async def test_score_skip_clears_cache(wired_observer, monkeypatch):
    wired_observer._pending_pa_payload = _payload_btc()
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.SKIP, Side.FLAT, net_score=0),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    assert wired_observer._pending_pa_payload is None
    assert wired_observer._pending_pa_side is None
    assert wired_observer._pending_pa_cached_at_ts is None


@pytest.mark.asyncio
async def test_opposite_side_win_clears_prior_cache(wired_observer, monkeypatch):
    """Cache is set on SELL. A BUY-side score arrives → prior SELL
    cache must be cleared (Jack's null-and-void rule). The new BUY-side
    PA REJECT then re-populates cache on the buy side."""
    wired_observer._pending_pa_payload = _payload_btc(signal="prior_sell_signal")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.BUY),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_reject(),
    )

    new_payload = _payload_btc(signal="mc_b_buy_circle")
    await wired_observer._score_and_maybe_propose(
        new_payload, source="lord_otter",
    )

    # Prior sell-side payload is gone; buy-side is now cached
    assert wired_observer._pending_pa_side == "buy"
    assert wired_observer._pending_pa_payload["signal"] == "mc_b_buy_circle"


@pytest.mark.asyncio
async def test_pa_pass_clears_cache(wired_observer, monkeypatch):
    """When PA finally passes (whether immediate or from a redeem tick),
    the cache is cleared even if downstream gates (HTF, risk) reject."""
    wired_observer._pending_pa_payload = _payload_btc()
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    assert wired_observer._pending_pa_payload is None
    assert wired_observer._pending_pa_side is None


@pytest.mark.asyncio
async def test_pa_validation_redeem_audit_written_only_on_redeem_source(
    wired_observer, monkeypatch,
):
    """`pa_validation_redeem` row fires ONLY when source='bar_tick_redeem'
    AND a cached payload exists. Immediate PA-pass fires (source from a
    TV webhook) MUST NOT write the redeem audit."""
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )

    # Case A: no cache, immediate PA pass via TV alert → no redeem row
    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )
    with db.connect(wired_observer.db_url) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_event "
            "WHERE kind='pa_validation_redeem'"
        ).fetchone()
    assert rows["n"] == 0

    # Case B: prior PA reject cached the payload; redeem tick fires
    wired_observer._pending_pa_payload = _payload_btc(signal="mc_a_red_diamond")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)

    await wired_observer._score_and_maybe_propose(
        _payload_btc(signal="mc_a_red_diamond"),
        source="bar_tick_redeem",
    )

    with db.connect(wired_observer.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_redeem'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["strategy"] == "bitunix_futures"
    assert p["division"] == "bitunix_futures"
    assert p["final_side"] == "sell"
    assert p["final_tier"] == "STANDARD"
    assert p["trigger_signal"] == "mc_a_red_diamond"
    assert "vwap_alignment" in p["final_passed"]
    assert "original_cached_at" in p
    assert "redeem_ts" in p
    assert p["bars_waited"] >= 0
    # Gap 3 close: seconds_waited preserves sub-bar precision
    assert "seconds_waited" in p
    assert p["seconds_waited"] >= 0

    # And the cache is now cleared
    assert wired_observer._pending_pa_payload is None


# ─── closed gaps: pa_validation_expired + order_id backfill ─────────────


@pytest.mark.asyncio
async def test_pa_validation_expired_written_on_score_decay(
    wired_observer, monkeypatch,
):
    """Gap 2 close: when a cached PA-rejected payload is dropped because
    the score evaluation now returns SKIP, `pa_validation_expired` is
    written with `reason='score_decay'`. Lets backtests compute
    redemption-failure rate by cause."""
    cached_at = datetime.now(timezone.utc).replace(microsecond=0)
    cached_at = cached_at.fromtimestamp(
        cached_at.timestamp() - 540, tz=timezone.utc,
    )  # 9 min ago = 3 bars
    wired_observer._pending_pa_payload = _payload_btc(signal="mc_a_red_diamond")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = cached_at

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.SKIP, Side.FLAT, net_score=0),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    with db.connect(wired_observer.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_expired'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["reason"] == "score_decay"
    assert p["cached_side"] == "sell"
    assert p["trigger_signal"] == "mc_a_red_diamond"
    assert p["bars_waited"] == 3
    assert p["seconds_waited"] >= 540


@pytest.mark.asyncio
async def test_pa_validation_expired_written_on_opposite_side(
    wired_observer, monkeypatch,
):
    """Gap 2 close: opposite-side win invalidates the cached payload;
    `pa_validation_expired` is written with `reason='opposite_side'`."""
    cached_at = datetime.now(timezone.utc).replace(microsecond=0)
    cached_at = cached_at.fromtimestamp(
        cached_at.timestamp() - 120, tz=timezone.utc,
    )
    wired_observer._pending_pa_payload = _payload_btc(signal="prior_sell_signal")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = cached_at

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.BUY),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_reject(),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(signal="mc_b_buy_circle"), source="lord_otter",
    )

    with db.connect(wired_observer.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_expired'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["reason"] == "opposite_side"
    assert p["cached_side"] == "sell"
    assert p["trigger_signal"] == "prior_sell_signal"


@pytest.mark.asyncio
async def test_pa_validation_expired_NOT_written_when_cache_empty(
    wired_observer, monkeypatch,
):
    """When SKIP happens but there was no cached payload, no
    `pa_validation_expired` row is emitted (would be misleading
    noise)."""
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.SKIP, Side.FLAT, net_score=0),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    with db.connect(wired_observer.db_url) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_event "
            "WHERE kind='pa_validation_expired'"
        ).fetchone()["n"]
    assert n == 0


@pytest.mark.asyncio
async def test_redeem_audit_order_id_backfilled_after_placement(
    wired_observer, monkeypatch,
):
    """Gap 1 close: after a redeemed fire reaches placement, the
    `pa_validation_redeem` row's `order_id` field is back-filled via
    UPDATE so backtests can one-hop join `pa_validation_redeem` →
    `paper_trade_record` by order_id."""
    cached_at = datetime.now(timezone.utc).replace(microsecond=0)
    cached_at = cached_at.fromtimestamp(
        cached_at.timestamp() - 360, tz=timezone.utc,
    )
    wired_observer._pending_pa_payload = _payload_btc(signal="mc_a_red_diamond")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = cached_at

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )
    monkeypatch.setattr(
        wired_observer, "_build_proposal",
        lambda **kwargs: _synth_proposal(side="sell"),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(signal="mc_a_red_diamond"),
        source="bar_tick_redeem",
    )

    # Read back the redeem audit row + the paper_trade_record row;
    # confirm the order_id matches between them.
    with db.connect(wired_observer.db_url) as conn:
        redeem_p = json.loads(conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_redeem'"
        ).fetchone()["payload_json"])
        ptr = conn.execute(
            "SELECT order_id FROM paper_trade_record "
            "WHERE division='bitunix_futures'"
        ).fetchone()

    assert redeem_p["order_id"] is not None, (
        "order_id should be back-filled after placement"
    )
    assert redeem_p["order_id"] == ptr["order_id"]


@pytest.mark.asyncio
async def test_redeem_audit_order_id_stays_none_when_post_pa_gate_rejects(
    wired_observer, monkeypatch,
):
    """If PA passes via redeem but a downstream gate (here: risk) rejects,
    the redeem audit row's `order_id` should remain None — the trade
    never fired. Backtests treat `order_id IS NULL` as the "PA
    redeemed but post-PA gate killed it" signal."""
    cached_at = datetime.now(timezone.utc)
    wired_observer._pending_pa_payload = _payload_btc(signal="mc_a_red_diamond")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = cached_at

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )
    monkeypatch.setattr(
        wired_observer, "_build_proposal",
        lambda **kwargs: _synth_proposal(side="sell"),
    )
    # Force risk rejection
    wired_observer.risk_agent.evaluate.return_value.verdict = "reject"
    wired_observer.risk_agent.evaluate.return_value.reason = "synthetic-block"

    await wired_observer._score_and_maybe_propose(
        _payload_btc(signal="mc_a_red_diamond"),
        source="bar_tick_redeem",
    )

    with db.connect(wired_observer.db_url) as conn:
        redeem_p = json.loads(conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_redeem'"
        ).fetchone()["payload_json"])
    assert redeem_p["order_id"] is None
    # No paper_trade_record was written (trade never placed)
    with db.connect(wired_observer.db_url) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_trade_record"
        ).fetchone()["n"]
    assert n == 0


# ─── run_pa_redeem_loop ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pa_redeem_loop_is_no_op_when_cache_empty(wired_observer):
    called = []
    async def _spy(payload, *, source):
        called.append((dict(payload), source))
    wired_observer._score_and_maybe_propose = _spy

    task = asyncio.create_task(
        wired_observer.run_pa_redeem_loop(interval_s=0.02)
    )
    await asyncio.sleep(0.10)        # ~5 ticks
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert called == []


@pytest.mark.asyncio
async def test_run_pa_redeem_loop_calls_score_when_cache_set(wired_observer):
    cached = _payload_btc(signal="mc_a_red_diamond")
    wired_observer._pending_pa_payload = cached
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)

    called = []
    async def _spy(payload, *, source):
        called.append((dict(payload), source))
        # Simulate the cache being cleared after a successful tick so
        # the loop doesn't keep recalling. In real flow this happens
        # via the SKIP / PASS branches of _score_and_maybe_propose.
        wired_observer._pending_pa_payload = None
    wired_observer._score_and_maybe_propose = _spy

    task = asyncio.create_task(
        wired_observer.run_pa_redeem_loop(interval_s=0.02)
    )
    await asyncio.sleep(0.10)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(called) == 1
    payload, source = called[0]
    assert source == "bar_tick_redeem"
    assert payload["signal"] == "mc_a_red_diamond"


# ─── data storage gaps for backtests ────────────────────────────────────


def _synth_proposal(side: str = "sell", qty: float = 0.1) -> OrderProposal:
    """Build a synthetic OrderProposal that passes the sizing-rejected
    short-circuit, so the placement section runs and writes the
    paper_trade_record."""
    order = ProposedOrder(
        strategy="bitunix_futures",
        symbol="BTCUSDT",
        side=side,
        qty=qty,
        order_type="market",
        rationale="synth",
        extra={
            # Mimic what `_build_proposal` stamps on `order.extra`:
            "tier": "STANDARD",
            "source_signal": "mc_a_red_diamond",
            "entry_reference_price": 80_000.0,
            "stop_price": 80_500.0 if side == "sell" else 79_500.0,
            "take_profit_price": 79_000.0 if side == "sell" else 81_000.0,
            "tp_r_multiple": 2.0,
            "max_dollar_risk": 50.0,
            "expected_gain_if_tp_hit": 100.0,
        },
    )
    return OrderProposal(
        proposed_order=order,
        reason="synth",
        effective_risk_pct=0.01,
        target_size_pct=0.02,
        leverage=5.0,
        stop_distance_pct=0.6,
        stop_price=80_500.0 if side == "sell" else 79_500.0,
        tp_price=79_000.0 if side == "sell" else 81_000.0,
        rr_ratio=2.0,
    )


@pytest.mark.asyncio
async def test_immediate_fire_stamps_redeemed_false_in_storage(
    wired_observer, monkeypatch,
):
    """Backtest data gap fix: every fire writes `redeemed` + `bars_waited`
    in both the would_have_placed event and the paper_trade_record's
    extra_json, so backtests can segment redeemed vs immediate fires."""
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )
    monkeypatch.setattr(
        wired_observer, "_build_proposal",
        lambda **kwargs: _synth_proposal(side="sell"),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    # would_have_placed event carries redeemed=False / bars_waited=None
    log_event_calls = [
        c for c in wired_observer.logger_agent.log_event.call_args_list
        if c.kwargs.get("kind") == "would_have_placed"
    ]
    assert len(log_event_calls) == 1
    p = log_event_calls[0].kwargs["payload"]
    assert p["redeemed"] is False
    assert p["bars_waited"] is None
    assert p["via"] == "bitunix_score"

    # paper_trade_record.extra_json carries `redeemed=False` (no key for
    # `bars_waited` / `original_cached_at` since redeem_metadata was None)
    with db.connect(wired_observer.db_url) as conn:
        rows = conn.execute(
            "SELECT extra_json FROM paper_trade_record "
            "WHERE division='bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"])
    assert extra["score_path"] is True
    assert "net_score" in extra
    assert extra.get("redeemed", False) is False
    assert "bars_waited" not in extra
    assert "original_cached_at" not in extra


@pytest.mark.asyncio
async def test_redeemed_fire_stamps_redeem_metadata_in_storage(
    wired_observer, monkeypatch,
):
    """The redeem path stamps `redeemed=True`, `bars_waited`, and
    `original_cached_at` onto both audit and paper_trade_record.extra_json.
    This is the data backtests need to compare redeemed vs immediate
    fire performance without joining audit timestamps."""
    # Pre-populate cache as if PA had rejected ~6 minutes ago (2 bars).
    cached_at = datetime.now(timezone.utc).replace(microsecond=0)
    cached_at = cached_at.fromtimestamp(
        cached_at.timestamp() - 360, tz=timezone.utc,
    )
    wired_observer._pending_pa_payload = _payload_btc(signal="mc_a_red_diamond")
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = cached_at

    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )
    monkeypatch.setattr(
        wired_observer, "_build_proposal",
        lambda **kwargs: _synth_proposal(side="sell"),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(signal="mc_a_red_diamond"),
        source="bar_tick_redeem",
    )

    # would_have_placed event carries redeemed=True + bars_waited
    log_event_calls = [
        c for c in wired_observer.logger_agent.log_event.call_args_list
        if c.kwargs.get("kind") == "would_have_placed"
    ]
    assert len(log_event_calls) == 1
    p = log_event_calls[0].kwargs["payload"]
    assert p["redeemed"] is True
    assert p["bars_waited"] == 2

    # paper_trade_record.extra_json carries all three redeem fields
    with db.connect(wired_observer.db_url) as conn:
        rows = conn.execute(
            "SELECT extra_json FROM paper_trade_record "
            "WHERE division='bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"])
    assert extra["redeemed"] is True
    assert extra["bars_waited"] == 2
    assert extra["original_cached_at"] == cached_at.isoformat()
    # Plus the pre-existing extras still flow through
    assert extra["score_path"] is True
    assert "net_score" in extra


@pytest.mark.asyncio
async def test_run_pa_redeem_loop_swallows_tick_exceptions(wired_observer):
    """A single failing tick must not kill the loop. The loop should
    log + continue."""
    wired_observer._pending_pa_payload = _payload_btc()
    wired_observer._pending_pa_side = "sell"
    wired_observer._pending_pa_cached_at_ts = datetime.now(timezone.utc)

    call_count = {"n": 0}
    async def _flaky(payload, *, source):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic tick failure")
        # On second tick, clear cache so loop becomes a no-op
        wired_observer._pending_pa_payload = None
    wired_observer._score_and_maybe_propose = _flaky

    task = asyncio.create_task(
        wired_observer.run_pa_redeem_loop(interval_s=0.02)
    )
    await asyncio.sleep(0.12)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # The loop survived the first-tick exception and ran at least twice
    assert call_count["n"] >= 2


# ─── D2: flatten dispatch on the SCORE path ─────────────────────────────
# Before the fix, _score_and_maybe_propose_locked went straight from the
# risk eval to the reject handler with NO flatten dispatch, so a
# flatten_account verdict arriving via the score path was logged as a plain
# reject and the account never flattened. These pin the dispatch (and its
# specificity — a normal reject must NOT flatten).


@pytest.mark.asyncio
async def test_score_path_flatten_account_verdict_dispatches_flatten(
    wired_observer, monkeypatch,
):
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )
    monkeypatch.setattr(
        wired_observer, "_build_proposal",
        lambda **kwargs: _synth_proposal(side="sell"),
    )
    wired_observer.risk_agent.evaluate.return_value = RiskVerdict(
        verdict="reject",
        reason="account drawdown 16.0% ≥ 15.0% cap — flatten and halt",
        flatten_account=True,
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    wired_observer.data_exec.flatten_division.assert_awaited_once_with(
        "bitunix_futures",
    )


@pytest.mark.asyncio
async def test_score_path_normal_reject_does_not_flatten(
    wired_observer, monkeypatch,
):
    """Specificity: a non-flatten reject must NOT flatten the account."""
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **kwargs: _verdict(Tier.STANDARD, Side.SELL),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation",
        lambda **kwargs: _pa_pass(side="sell"),
    )
    monkeypatch.setattr(
        wired_observer, "_build_proposal",
        lambda **kwargs: _synth_proposal(side="sell"),
    )
    wired_observer.risk_agent.evaluate.return_value = RiskVerdict(
        verdict="reject", reason="per-trade risk cap", flatten_account=False,
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(), source="lord_otter",
    )

    wired_observer.data_exec.flatten_division.assert_not_awaited()
