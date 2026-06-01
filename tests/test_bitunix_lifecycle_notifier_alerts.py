"""Tests for the 8 new lifecycle-notifier alert methods (Session B 5c)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.comms.bitunix_lifecycle_notifier import BitunixLifecycleNotifier


def _make_notifier(*, paper_mode: bool = True) -> tuple[BitunixLifecycleNotifier, MagicMock]:
    channel = MagicMock()
    channel.push = AsyncMock(return_value=True)
    n = BitunixLifecycleNotifier(channel, paper_mode=paper_mode)
    return n, channel


def _pushed(channel: MagicMock) -> str:
    assert channel.push.await_count == 1
    args, kwargs = channel.push.await_args
    return args[0], kwargs


@pytest.mark.asyncio
async def test_exit_order_placed():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_exit_order_placed(
        order_id="bx-exit-1", parent_order_id="ord-1",
        symbol="BTCUSDT", side="buy", exit_kind="tp1", qty=0.001,
    )
    body, kwargs = _pushed(ch)
    assert "BTCUSDT" in body and "exit:tp1" in body and "PLACED" in body
    assert "[LIVE]" in body
    assert kwargs["audit_path"] == "lifecycle_exit_order_placed"


@pytest.mark.asyncio
async def test_exit_order_filled_no_counter():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_exit_order_filled(
        order_id="bx-exit-2", parent_order_id="ord-2",
        symbol="BTCUSDT", side="buy", exit_kind="sl",
        real_fill_price=79_500.0, real_qty=0.001, real_fee_usd=0.0345,
    )
    body, _ = _pushed(ch)
    assert "FILLED" in body and "$79,500.00" in body and "$0.0345" in body


@pytest.mark.asyncio
async def test_exit_order_filled_with_counter_suffix():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_exit_order_filled(
        order_id="bx-exit-3", parent_order_id="ord-3",
        symbol="BTCUSDT", side="buy", exit_kind="tp2",
        real_fill_price=81_500.0, real_qty=0.0005, real_fee_usd=0.0211,
        live_exit_counter=3, live_exit_counter_total=10,
    )
    body, _ = _pushed(ch)
    assert "(exit #3/10)" in body


@pytest.mark.asyncio
async def test_exit_order_rejected():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_exit_order_rejected(
        order_id="bx-exit-4", parent_order_id="ord-4",
        symbol="BTCUSDT", exit_kind="sl",
        bitunix_code="30038", bitunix_msg="TP/SL amount > position size",
    )
    body, _ = _pushed(ch)
    assert "EXIT REJECTED" in body and "30038" in body


@pytest.mark.asyncio
async def test_exit_partial_fill():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_exit_partial_fill(
        order_id="bx-exit-5", parent_order_id="ord-5",
        symbol="BTCUSDT", exit_kind="tp1",
        expected_qty=0.001, actual_qty=0.0006,
    )
    body, _ = _pushed(ch)
    assert "EXIT PARTIAL" in body and "0.0006" in body


@pytest.mark.asyncio
async def test_position_closed_with_pnl():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_position_closed_with_pnl(
        order_id="ord-close-1", symbol="BTCUSDT", side="buy",
        result="win",
        gross_pnl_usd=12.5, total_fee_usd=0.085,
        total_funding_usd=-0.012, net_pnl_usd=12.403,
    )
    body, _ = _pushed(ch)
    assert "CLOSED (WIN)" in body
    assert "Gross PnL: +$12.50" in body
    assert "Fees: $0.0850" in body
    assert "Net PnL: +$12.40" in body


@pytest.mark.asyncio
async def test_reconciliation_divergence():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_reconciliation_divergence(
        order_id="ord-div-1", symbol="BTCUSDT",
        kind="missing_on_broker",
        detail="bot tracks open row but broker has no matching position",
    )
    body, _ = _pushed(ch)
    assert "RECON DIVERGENCE" in body and "missing_on_broker" in body
    assert "halt_new_orders=True" in body


@pytest.mark.asyncio
async def test_cost_accrual_recorded():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_cost_accrual_recorded(
        order_id="ord-cost-1", symbol="BTCUSDT",
        fee_usd=0.012, funding_usd=-0.005,
        cumulative_fee_usd=0.080, cumulative_funding_usd=-0.030,
    )
    body, _ = _pushed(ch)
    assert "cost accrual" in body and "$0.0120" in body


@pytest.mark.asyncio
async def test_restart_resume_executed():
    n, ch = _make_notifier(paper_mode=False)
    await n.notify_restart_resume_executed(
        matched_count=3, orphan_count=1, case_c_count=0,
    )
    body, _ = _pushed(ch)
    assert "RESTART RESUME" in body
    assert "matched: 3" in body
    assert "orphan on broker: 1" in body
