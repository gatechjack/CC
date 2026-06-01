"""Tests for `resume_live_positions` — Session B Commit 5a (restart-resume).

Validates Phase 1b §4 cases:
  (a) match: broker has position, bot has matching live row → clean
  (b) orphan_on_broker: broker has position, bot has no row → halt + audit
  (c) deferred: bot has row, broker has no position → audit + halt + page
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    ORPHAN_BROKER_POSITION_ON_RESTART_KIND,
    RECONCILER_ACTOR,
    RESTART_RESUME_CASE_C_DEFERRED_KIND,
    RESTART_RESUME_EXECUTED_KIND,
    RestartResumeSummary,
    resume_live_positions,
)
from trading_corp.persistence import db
from trading_corp.persistence.models import Position


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "resume.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _seed_live(db_url: str, *, order_id: str, side: str = "buy",
               qty: float = 0.001, broker_order_id: str = "bx-1") -> None:
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
                "BTCUSDT", side, qty,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps({
                    "execution_mode": "live",
                    "broker_order_id": broker_order_id,
                }),
            ),
        )


def _pos(symbol: str, qty: float) -> Position:
    return Position(
        account="bitunix-futures",
        symbol=symbol, qty=qty, avg_price=80_000.0,
        opened_ts="2026-06-01T10:00:00+00:00",
        extra={"side": "LONG" if qty > 0 else "SHORT"},
    )


def _broker_stub(positions: list[Position]) -> MagicMock:
    broker = MagicMock()
    broker.get_pending_positions = AsyncMock(return_value=positions)
    broker._halt_new_orders = False
    broker._halt_reason = None
    return broker


# ─── Case (a) match ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_a_matched_no_halt(db_url):
    _seed_live(db_url, order_id="ord-a")
    broker = _broker_stub([_pos("BTCUSDT", 0.001)])
    s = await resume_live_positions(broker, db_url)
    assert isinstance(s, RestartResumeSummary)
    assert len(s.matched) == 1
    assert s.matched[0]["order_id"] == "ord-a"
    assert len(s.orphan_on_broker) == 0
    assert len(s.case_c_deferred) == 0
    assert broker._halt_new_orders is False
    with db.connect(db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor=?",
            (RECONCILER_ACTOR,),
        ).fetchall()]
    # Both reconcile_position_state's own audit AND restart-resume's
    # matched audit land.
    assert RESTART_RESUME_EXECUTED_KIND in kinds


# ─── Case (b) orphan on broker ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_b_orphan_halts_and_audits(db_url):
    # No tracked row; broker has position
    broker = _broker_stub([_pos("BTCUSDT", 0.005)])
    s = await resume_live_positions(broker, db_url)
    assert len(s.orphan_on_broker) == 1
    assert s.orphan_on_broker[0]["symbol"] == "BTCUSDT"
    assert s.has_orphan_or_case_c is True
    assert broker._halt_new_orders is True
    assert broker._halt_reason == "restart_resume_orphan_or_case_c"
    with db.connect(db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor=?",
            (RECONCILER_ACTOR,),
        ).fetchall()]
    assert ORPHAN_BROKER_POSITION_ON_RESTART_KIND in kinds


# ─── Case (c) deferred ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_c_deferred_halts_and_audits(db_url):
    _seed_live(db_url, order_id="ord-c")
    broker = _broker_stub([])  # broker has no positions
    s = await resume_live_positions(broker, db_url)
    assert len(s.case_c_deferred) == 1
    assert s.case_c_deferred[0]["order_id"] == "ord-c"
    assert broker._halt_new_orders is True
    with db.connect(db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor=?",
            (RECONCILER_ACTOR,),
        ).fetchall()]
    assert RESTART_RESUME_CASE_C_DEFERRED_KIND in kinds


# ─── notifier integration ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notifier_called_with_counts(db_url):
    _seed_live(db_url, order_id="ord-n1")
    broker = _broker_stub([_pos("BTCUSDT", 0.001)])
    notifier = MagicMock()
    notifier.notify_restart_resume_executed = AsyncMock()
    await resume_live_positions(broker, db_url, notifier=notifier)
    notifier.notify_restart_resume_executed.assert_awaited_once()
    call = notifier.notify_restart_resume_executed.await_args
    assert call.kwargs["matched_count"] == 1
    assert call.kwargs["orphan_count"] == 0
    assert call.kwargs["case_c_count"] == 0


# ─── halt opt-out ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_halt_on_orphan_false_skips_latch(db_url):
    broker = _broker_stub([_pos("BTCUSDT", 0.001)])  # orphan
    await resume_live_positions(
        broker, db_url, halt_on_orphan_or_case_c=False,
    )
    assert broker._halt_new_orders is False
