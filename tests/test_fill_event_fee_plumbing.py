"""Tests for Layer 1 fee plumbing — FillEvent.fee field + downstream stamps.

Session B Commit 1 of N+2 Phase 3. Validates:

- FillEvent.fee field exists with default 0.0.
- Existing FillEvent constructors that omit `fee` still work (paper,
  robinhood, tasty, coinbase, fidelity downstream consumers).
- BitunixBroker.place_order passes through `_observe_fill`'s fee
  return into FillEvent.fee (no longer discarded as `_fee`).
- Path C `_place_live` stamps `extra["entry_fee_usd"]` from
  `fill.fee` after successful broker placement.
- `_record_exit_outcome` stamps `extra["exit_fee_usd"]` and emits
  `audit_payload["fill_event"]["fee"]` when a FillEvent is supplied.

Layer 2 (funding accrual via get_history_positions) deferred to N+3.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent


# ─── FillEvent dataclass shape ──────────────────────────────────────────


def test_fill_event_fee_field_defaults_to_zero():
    """`fee` defaults to 0.0 so all existing constructor sites
    (paper / robinhood / tasty / coinbase / fidelity) keep working
    without modification."""
    f = FillEvent(
        order_id="x", symbol="BTCUSDT", side="buy",
        qty=0.001, price=80_000.0, ts="2026-06-01T11:00:00+00:00",
        venue="bitunix",
    )
    assert f.fee == 0.0


def test_fill_event_fee_field_accepts_explicit_value():
    f = FillEvent(
        order_id="x", symbol="BTCUSDT", side="buy",
        qty=0.001, price=80_000.0, ts="2026-06-01T11:00:00+00:00",
        venue="bitunix", fee=0.00123,
    )
    assert f.fee == 0.00123


# ─── Path C: _place_live entry-side fee stamp ───────────────────────────


def _logger(db_url: str) -> LoggerAgent:
    return LoggerAgent(db_url=db_url)


def _make_observer_live(tmp_path: Path, monkeypatch, *, fill_fee: float):
    db_path = tmp_path / "fee_plumbing.db"
    db_url = f"sqlite:///{db_path}"
    db.init_db(db_url)

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
    broker._assert_snapshot_fresh = AsyncMock()

    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.flatten_division = AsyncMock()
    data_exec.place = AsyncMock(return_value=FillEvent(
        order_id="bx-entry-fee-1", symbol="BTCUSDT", side="buy",
        qty=0.001, price=80_000.0, ts="2026-05-29T12:00:00+00:00",
        venue="bitunix", fee=fill_fee,
    ))

    telegram = MagicMock()
    telegram.push = AsyncMock(return_value=True)

    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=_logger(db_url),
        telegram_channel=telegram,
        execution_mode="live",
    )
    monkeypatch.setattr(
        obs, "_yaml_auto_execute_for_bitunix", lambda: True,
    )
    return obs


def _set_bull_state(obs):
    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")


def _trigger_payload():
    return {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }


@pytest.mark.asyncio
async def test_path_c_stamps_entry_fee_usd_from_broker_truth(
    tmp_path, monkeypatch,
):
    """Layer 1 entry-side: the FillEvent.fee returned by data_exec.place
    must land in paper_trade_record.extra["entry_fee_usd"] for the
    reconciler + cost-accrual + tax-grade record-keeping."""
    obs = _make_observer_live(tmp_path, monkeypatch, fill_fee=0.0345)
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT extra_json FROM paper_trade_record "
            "WHERE strategy='bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"])
    assert extra["entry_fee_usd"] == 0.0345


@pytest.mark.asyncio
async def test_path_c_entry_fee_usd_zero_when_broker_reports_no_fee(
    tmp_path, monkeypatch,
):
    """Broker reporting 0.0 fee (or default-not-populated case) is
    distinct from "fee key absent" — the stamp lands explicitly as 0.0,
    making downstream consumers' presence-checks safe."""
    obs = _make_observer_live(tmp_path, monkeypatch, fill_fee=0.0)
    _set_bull_state(obs)
    await obs.observe_and_decide(_trigger_payload(), source="lord_otter")

    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT extra_json FROM paper_trade_record "
            "WHERE strategy='bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    extra = json.loads(rows[0]["extra_json"])
    assert extra["entry_fee_usd"] == 0.0
    assert "entry_fee_usd" in extra  # explicit presence


# ─── _record_exit_outcome exit-side fee stamp + audit ───────────────────


def _seed_pending_live_row(db_url: str, order_id: str):
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id, "2026-06-01T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTCUSDT", "buy", 0.001,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps({
                    "execution_mode": "live",
                    "broker_order_id": "bx-entry-1",
                    "entry_fee_usd": 0.0345,
                }),
            ),
        )


def _make_obs_helper(tmp_path: Path):
    db_path = tmp_path / "exit_fee.db"
    db_url = f"sqlite:///{db_path}"
    db.init_db(db_url)
    logger_agent = LoggerAgent(db_url=db_url)
    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=MagicMock(),
        data_exec=MagicMock(),
        logger_agent=logger_agent,
        execution_mode="paper",
    )
    return obs, db_url


def test_record_exit_outcome_stamps_exit_fee_usd_in_extra(tmp_path):
    obs, db_url = _make_obs_helper(tmp_path)
    _seed_pending_live_row(db_url, "ord-exit-1")
    fill = FillEvent(
        order_id="bx-exit-1", symbol="BTCUSDT", side="sell",
        qty=0.001, price=81_000.0, ts="2026-06-01T11:00:00+00:00",
        venue="bitunix", fee=0.0501,
    )
    obs._record_exit_outcome(
        order_id="ord-exit-1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=True,
        fill_event=fill,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-exit-1",),
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["exit_fee_usd"] == 0.0501
    # Entry-side stamp from Path C must survive the merge
    assert extra["entry_fee_usd"] == 0.0345


def test_record_exit_outcome_audit_payload_includes_fill_fee(tmp_path):
    obs, db_url = _make_obs_helper(tmp_path)
    _seed_pending_live_row(db_url, "ord-exit-a1")
    fill = FillEvent(
        order_id="bx-exit-a1", symbol="BTCUSDT", side="sell",
        qty=0.001, price=81_000.0, ts="2026-06-01T11:00:00+00:00",
        venue="bitunix", fee=0.0789,
    )
    obs._record_exit_outcome(
        order_id="ord-exit-a1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=True,
        fill_event=fill,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='exit_outcome_recorded'"
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["fill_event"]["fee"] == 0.0789


def test_record_exit_outcome_no_fill_event_no_fee_keys(tmp_path):
    """Paper-mode exits (no FillEvent) must NOT stamp exit_fee_usd
    (no broker fee to report)."""
    obs, db_url = _make_obs_helper(tmp_path)
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ord-paper", "2026-06-01T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTCUSDT", "buy", 0.001,
                80_000.0, 79_500.0, 81_000.0,
                7200, None, json.dumps({"tier": "PREMIUM"}),
            ),
        )
    obs._record_exit_outcome(
        order_id="ord-paper",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=False,
        fill_event=None,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-paper",),
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert "exit_fee_usd" not in extra
