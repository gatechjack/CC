"""Tests for the flip-opportunity detection audit (observe-only).

Scope: when a PREMIUM opposite-side signal scores while an open paper
position exists, the observer writes one `flip_opportunity_detected`
audit row capturing the open position state + the opposing signal.
The path is purely observational — it must NEVER close, modify, or
otherwise touch the open position. It is instrumentation to measure
how often (and at what R) the no-close-on-opposite gap actually fires
in practice, gating any future close-on-opposite-PREMIUM build on
observed data.

Test matrix:
  - open BUY + PREMIUM SELL → 1 row written, R captured (long math)
  - open SELL + PREMIUM BUY → 1 row written, R captured (short math)
  - open BUY + STANDARD SELL → 0 rows (PREMIUM-only gate)
  - open BUY + PREMIUM BUY  → 0 rows (same side)
  - no open position + PREMIUM SELL → 0 rows
  - helper raises → trading pipeline still completes (fail-safe)
  - SKIP-tier verdict never reaches the detector (gated by earlier
    SKIP return path in `_score_and_maybe_propose_locked`).
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


# ─── helpers ────────────────────────────────────────────────────────────


def _verdict(tier: Tier, side: Side, net_score: int = 10) -> BitUnixVerdict:
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


def _pa_reject() -> PAValidationResult:
    return PAValidationResult(
        decision=PAValidationDecision.REJECT,
        side="sell",
        passed=(),
        failed=("vwap_alignment",),
        rush_fall_triggered=None,
        reason="REJECT (test fixture)",
    )


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
        enabled=True,
        require_all=True,
        min_validators_passed=0,
        validators=("vwap_alignment",),
        rush_fall_enabled=False,
    )


@pytest.fixture
def wired_observer(tmp_path: Path):
    db_path = tmp_path / "flip.db"
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

    obs = BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}",
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=MagicMock(),
        scoring_config=_minimal_scoring_config(),
        pa_config=_minimal_pa_config(),
        htf_gate_mode="enforce",
    )
    return obs


def _insert_open_trade(
    db_url: str,
    *,
    order_id: str,
    side: str,
    entry: float,
    stop: float,
    ts: str = "2026-05-23T10:00:00+00:00",
) -> None:
    """Insert one open paper_trade_record (result IS NULL) for the
    bitunix_futures strategy, mimicking what the v2 placement path
    writes after a `would_have_placed` audit."""
    with db.connect(db_url) as conn:
        conn.execute(
            """
            INSERT INTO paper_trade_record (
              order_id, ts, strategy, division, symbol, side, qty,
              tier, source_signal, entry_reference_price, stop_price,
              tp_price, tp_r_multiple, expected_loss, expected_gain,
              rr_ratio, max_hold_seconds
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                order_id, ts, "bitunix_futures", "bitunix_futures",
                "BTCUSDT", side, 0.01,
                "STANDARD", "test_open", entry, stop,
                None, None, None, None, None, 24 * 3600,
            ),
        )


def _count_flip_audits(db_url: str) -> int:
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_event WHERE kind = ?",
            ("flip_opportunity_detected",),
        ).fetchone()
    return int(row["c"])


def _latest_flip_payload(db_url: str) -> dict:
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind = ? ORDER BY id DESC LIMIT 1",
            ("flip_opportunity_detected",),
        ).fetchone()
    assert row is not None
    return json.loads(row["payload_json"])


def _payload_btc(price: float, signal: str) -> dict:
    return {
        "symbol": "BTCUSDT",
        "signal": signal,
        "price": price,
        "interval": "3",
        "time": "2026-05-23T10:03:00Z",
    }


# ─── happy paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_buy_with_premium_sell_writes_audit_row(
    wired_observer, monkeypatch,
):
    """Open long, an opposing PREMIUM sell scores → one audit row with
    open trade id, side, entry, current price, and R captured."""
    _insert_open_trade(
        wired_observer.db_url,
        order_id="open_long_42",
        side="buy",
        entry=80_000.0,
        stop=79_200.0,  # 800 risk per 1 BTC
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.SELL, net_score=11),
    )
    # PA rejects so we don't go through the full propose path; flip
    # detection must fire BEFORE the PA gate.
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    # Current price 79_600 → unrealized R for the long:
    #   (79_600 - 80_000) / (80_000 - 79_200) = -400 / 800 = -0.5
    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=79_600.0, signal="mc_a_red_diamond"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 1
    p = _latest_flip_payload(wired_observer.db_url)
    assert p["open_order_id"] == "open_long_42"
    assert p["open_side"] == "buy"
    assert p["opposing_side"] == "sell"
    assert p["opposing_tier"] == "PREMIUM"
    assert p["current_price"] == 79_600.0
    assert p["current_r"] == pytest.approx(-0.5, abs=1e-6)
    assert p["opposing_signal"] == "mc_a_red_diamond"


@pytest.mark.asyncio
async def test_open_sell_with_premium_buy_writes_audit_row(
    wired_observer, monkeypatch,
):
    """Mirror: open short, opposing PREMIUM buy → row with short R math."""
    _insert_open_trade(
        wired_observer.db_url,
        order_id="open_short_7",
        side="sell",
        entry=80_000.0,
        stop=80_800.0,  # 800 risk
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.BUY, net_score=12),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    # Current price 80_400 → unrealized R for the short:
    #   (80_000 - 80_400) / (80_800 - 80_000) = -400 / 800 = -0.5
    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=80_400.0, signal="mc_b_gold_buy"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 1
    p = _latest_flip_payload(wired_observer.db_url)
    assert p["open_order_id"] == "open_short_7"
    assert p["open_side"] == "sell"
    assert p["opposing_side"] == "buy"
    assert p["opposing_tier"] == "PREMIUM"
    assert p["current_r"] == pytest.approx(-0.5, abs=1e-6)


# ─── negative paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_standard_opposite_does_not_write(wired_observer, monkeypatch):
    _insert_open_trade(
        wired_observer.db_url,
        order_id="open_long_99",
        side="buy",
        entry=80_000.0,
        stop=79_200.0,
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.STANDARD, Side.SELL, net_score=6),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=79_600.0, signal="mc_a_red_diamond"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 0


@pytest.mark.asyncio
async def test_same_side_premium_does_not_write(wired_observer, monkeypatch):
    _insert_open_trade(
        wired_observer.db_url,
        order_id="open_long_100",
        side="buy",
        entry=80_000.0,
        stop=79_200.0,
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.BUY, net_score=12),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=80_500.0, signal="mc_b_gold_buy"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 0


@pytest.mark.asyncio
async def test_no_open_position_does_not_write(wired_observer, monkeypatch):
    # No paper_trade_record inserted.
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.SELL, net_score=11),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=79_600.0, signal="mc_a_red_diamond"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 0


@pytest.mark.asyncio
async def test_closed_position_does_not_count_as_open(
    wired_observer, monkeypatch,
):
    """A paper_trade_record with non-NULL `result` is closed; the
    detector must ignore it."""
    with db.connect(wired_observer.db_url) as conn:
        conn.execute(
            """
            INSERT INTO paper_trade_record (
              order_id, ts, strategy, division, symbol, side, qty,
              tier, source_signal, entry_reference_price, stop_price,
              tp_price, tp_r_multiple, expected_loss, expected_gain,
              rr_ratio, max_hold_seconds, result, result_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "closed_long_1", "2026-05-20T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTCUSDT", "buy", 0.01,
                "STANDARD", "test_closed", 80_000.0, 79_200.0,
                None, None, None, None, None, 24 * 3600,
                "win", "2026-05-20T12:00:00+00:00",
            ),
        )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.SELL, net_score=11),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=79_600.0, signal="mc_a_red_diamond"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 0


# ─── fail-safe ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detector_exception_does_not_break_pipeline(
    wired_observer, monkeypatch,
):
    """If the detection helper raises (e.g., DB hiccup), the rest of
    the score path must still execute. Detection is observe-only and
    must not break placement."""
    _insert_open_trade(
        wired_observer.db_url,
        order_id="open_long_x",
        side="buy",
        entry=80_000.0,
        stop=79_200.0,
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.SELL, net_score=11),
    )
    # Force the detector to blow up.
    def _boom(*_a, **_kw):
        raise RuntimeError("synthetic DB failure")
    monkeypatch.setattr(
        wired_observer, "_detect_flip_opportunity", _boom,
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    # Should not raise. PA reject below the detector still runs and
    # caches the payload — that's how we know the rest of the pipeline
    # was reached.
    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=79_600.0, signal="mc_a_red_diamond"),
        source="lord_otter",
    )

    assert wired_observer._pending_pa_payload is not None


# ─── R-math edge cases ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_risk_distance_records_null_r(
    wired_observer, monkeypatch,
):
    """If stop == entry (degenerate), record R as None rather than
    raising or recording inf."""
    _insert_open_trade(
        wired_observer.db_url,
        order_id="degenerate_1",
        side="buy",
        entry=80_000.0,
        stop=80_000.0,
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_confluence_futures",
        lambda **_: _verdict(Tier.PREMIUM, Side.SELL, net_score=11),
    )
    monkeypatch.setattr(
        obs_mod, "evaluate_pa_validation", lambda **_: _pa_reject(),
    )

    await wired_observer._score_and_maybe_propose(
        _payload_btc(price=79_900.0, signal="mc_a_red_diamond"),
        source="lord_otter",
    )

    assert _count_flip_audits(wired_observer.db_url) == 1
    p = _latest_flip_payload(wired_observer.db_url)
    assert p["current_r"] is None
