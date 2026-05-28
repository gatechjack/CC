"""Tests for BitunixLifecycleNotifier.

Covers: TP1/TP2 fill notifications, close-out scenarios (TP3 win, SL loss,
pending PnL), live-mode prefix, and failure-handling (audit row written,
no raise on channel error).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from trading_corp.comms.bitunix_lifecycle_notifier import BitunixLifecycleNotifier
from trading_corp.persistence import db


# ---------------------------------------------------------------------------
# Stub channels
# ---------------------------------------------------------------------------


class _StubChannel:
    def __init__(self) -> None:
        self.pushed: list[str] = []

    async def push(self, text: str) -> None:
        self.pushed.append(text)


class _RaisingChannel:
    async def push(self, text: str) -> None:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notifier(channel, *, db_url=None, paper_mode=True):
    return BitunixLifecycleNotifier(channel, db_url=db_url, paper_mode=paper_mode)


# ---------------------------------------------------------------------------
# Test 1: notify_tp_fill — TP1
# ---------------------------------------------------------------------------


async def test_notify_tp1_fill_content():
    ch = _StubChannel()
    notifier = _make_notifier(ch)
    await notifier.notify_tp_fill(
        order_id="ord-001",
        symbol="BTCUSDT",
        side="sell",
        leg="tp1",
        entry_price=75189.80,
        leg_price=75054.46,
        r_so_far=0.5,
        old_sl=75444.69,
        new_sl=75189.80,
        new_sl_label="breakeven",
        percent_closed=50,
    )
    assert len(ch.pushed) == 1
    msg = ch.pushed[0]
    assert msg.startswith("📄 [PAPER]"), f"Expected paper prefix, got: {msg[:20]!r}"
    assert "TP1 filled" in msg
    assert "R so far: +0.5R" in msg
    assert "breakeven" in msg
    assert "50% closed" in msg
    # Check price formatting
    assert "$75,189.80" in msg
    assert "$75,054.46" in msg
    # Pct: raw = (75054.46 - 75189.80) / 75189.80 * 100 = -0.1800...%
    assert "-0.18%" in msg


# ---------------------------------------------------------------------------
# Test 2: notify_tp_fill — TP2
# ---------------------------------------------------------------------------


async def test_notify_tp2_fill_content():
    ch = _StubChannel()
    notifier = _make_notifier(ch)
    await notifier.notify_tp_fill(
        order_id="ord-002",
        symbol="BTCUSDT",
        side="sell",
        leg="tp2",
        entry_price=75189.80,
        leg_price=74900.00,
        r_so_far=1.2,
        old_sl=75189.80,
        new_sl=75050.00,
        new_sl_label="post-TP1 floor",
        percent_closed=75,
    )
    assert len(ch.pushed) == 1
    msg = ch.pushed[0]
    assert "TP2 filled" in msg
    assert "75% closed" in msg
    assert "post-TP1 floor" in msg
    # TP2 body must not contain the Entry line
    assert "Entry:" not in msg


# ---------------------------------------------------------------------------
# Test 3: notify_close_out — TP3 win
# ---------------------------------------------------------------------------


async def test_notify_close_out_tp3_win():
    ch = _StubChannel()
    notifier = _make_notifier(ch)
    path = [
        ("Entry", 75189.80, None),
        ("TP1", 75054.46, 0.18),
        ("TP2", 74900.00, 0.39),
        ("TP3", 74700.00, 0.65),
        ("Exit", None, None),
    ]
    await notifier.notify_close_out(
        order_id="ord-003",
        symbol="BTCUSDT",
        side="sell",
        result="win",
        entry_price=75189.80,
        exit_price=74700.00,
        exit_reason="TP3 hit",
        path=path,
        r_multiple=1.62,
        pnl_dollars=0.10,
        held_seconds=3725,
    )
    assert len(ch.pushed) == 1
    msg = ch.pushed[0]
    assert "CLOSED · TP3 filled (WIN)" in msg
    assert "R-multiple: +1.62R" in msg
    assert "PnL: +$0.10" in msg
    assert "Fees: not tracked in paper" in msg
    assert "Funding: not tracked in paper" in msg
    # 3725s = 1h 2m
    assert "Held: 1h 2m" in msg


# ---------------------------------------------------------------------------
# Test 4: notify_close_out — SL loss
# ---------------------------------------------------------------------------


async def test_notify_close_out_sl_loss():
    ch = _StubChannel()
    notifier = _make_notifier(ch)
    path = [
        ("Entry", 75189.80, None),
        ("Exit", None, None),
    ]
    await notifier.notify_close_out(
        order_id="ord-004",
        symbol="BTCUSDT",
        side="sell",
        result="loss",
        entry_price=75189.80,
        exit_price=75444.69,
        exit_reason="SL hit",
        path=path,
        r_multiple=-1.0,
        pnl_dollars=-0.27,
        held_seconds=600,
    )
    assert len(ch.pushed) == 1
    msg = ch.pushed[0]
    assert "STOPPED OUT (LOSS)" in msg
    assert "PnL: -$0.27" in msg


# ---------------------------------------------------------------------------
# Test 5: notify_close_out — pnl_dollars=None
# ---------------------------------------------------------------------------


async def test_notify_close_out_pnl_pending():
    ch = _StubChannel()
    notifier = _make_notifier(ch)
    path = [("Entry", 75189.80, None), ("Exit", None, None)]
    await notifier.notify_close_out(
        order_id="ord-005",
        symbol="BTCUSDT",
        side="buy",
        result="expired",
        entry_price=75189.80,
        exit_price=75189.80,
        exit_reason="max_hold",
        path=path,
        r_multiple=0.0,
        pnl_dollars=None,
        held_seconds=None,
    )
    assert len(ch.pushed) == 1
    msg = ch.pushed[0]
    assert "PnL: pending persistence" in msg


# ---------------------------------------------------------------------------
# Test 6: live-mode prefix
# ---------------------------------------------------------------------------


async def test_live_mode_prefix():
    ch = _StubChannel()
    notifier = _make_notifier(ch, paper_mode=False)
    await notifier.notify_tp_fill(
        order_id="ord-006",
        symbol="BTCUSDT",
        side="buy",
        leg="tp1",
        entry_price=75000.00,
        leg_price=75300.00,
        r_so_far=0.5,
        old_sl=74700.00,
        new_sl=75000.00,
        new_sl_label="breakeven",
        percent_closed=50,
    )
    assert len(ch.pushed) == 1
    assert ch.pushed[0].startswith("💸 [LIVE]")


# ---------------------------------------------------------------------------
# Test 7: failure handling — no raise + audit row written
# ---------------------------------------------------------------------------


async def test_failure_does_not_raise_and_writes_audit(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path}/x.db"
    db.init_db(db_url)

    ch = _RaisingChannel()
    notifier = _make_notifier(ch, db_url=db_url)

    # Must not raise
    await notifier.notify_tp_fill(
        order_id="ord-007",
        symbol="BTCUSDT",
        side="sell",
        leg="tp1",
        entry_price=75189.80,
        leg_price=75054.46,
        r_so_far=0.5,
        old_sl=75444.69,
        new_sl=75189.80,
        new_sl_label="breakeven",
        percent_closed=50,
    )

    # Verify audit row written via raw sqlite3
    db_path = str(tmp_path / "x.db")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT kind, payload_json FROM audit_event WHERE kind = ?",
        ("telegram_notification_failed",),
    ).fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected 1 audit row, got {len(rows)}"
    payload = json.loads(rows[0][1])
    assert payload["notification_type"] == "tp_fill_tp1"
    assert payload["order_id"] == "ord-007"
    assert "boom" in payload["failure_reason"]
