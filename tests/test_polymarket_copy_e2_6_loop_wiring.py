"""E2·6 — PCT loop wiring: gated live placement, no-fill handling, partial-fill
write-back.

Fundless, fully mocked — no live SDK, no real order, no division flipped live.

Central property (mandatory): a synthesized-FAK PARTIAL fill (FillEvent.qty <
intended) must result in `_emit_exit` later selling the ACTUAL held qty, not the
intended size. See `test_partial_fill_writeback_then_exit_sells_actual`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.strategies.polymarket_copy_trader import (
    PolymarketCopyTraderAgent,
)
from trading_corp.brokers.polymarket_live import NoFillInWindow, OrderPlacementError
from trading_corp.data.polymarket_data_api_client import ActivityRow
from trading_corp.main import _handle_copy_order_placement
from trading_corp.persistence import db as _db
from trading_corp.persistence.db import connect, set_agent_state
from trading_corp.persistence.models import FillEvent, ProposedOrder


# ── shared setup (mirrors test_polymarket_copy_trader.py) ────────────────────


class _StubDataAPI:
    def __init__(self, by_wallet):
        self._by = by_wallet

    async def fetch_activity(self, wallet, *, limit=20, offset=0):
        return list(self._by.get(wallet, []))


@pytest.fixture
def strategy(tmp_path):
    db_path = tmp_path / "pme26.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "polymarket_copy_trader:\n  enabled: true\n  poll_interval_sec: 60\n"
    )
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("polymarket: {}\n")
    agent = PolymarketCopyTraderAgent(
        strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url,
    )
    return agent, db_url


def _act(condition_id, outcome_index, side="BUY", price=0.5, size=100.0, ts=1000,
         asset="TID"):
    return ActivityRow(
        proxy_wallet="0xW", timestamp=ts, condition_id=condition_id, type="TRADE",
        size=size, usdc_size=size * price, transaction_hash=f"tx-{condition_id}-{side}-{ts}",
        price=price, asset=asset, side=side, outcome_index=outcome_index,
        title="t", slug="", event_slug="",
        outcome="Yes" if outcome_index == 0 else "No", name="alice",
    )


async def _emit_entry_position(agent, db_url, *, cid="cidX", price=0.40):
    """Cold-start a whale, then emit one BUY → returns (entry_order, intended_qty).
    The optimistic position (intended size) is now persisted in whale_state."""
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice"}], db_url=db_url,
    )
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": []}))  # cold start
    entry_orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act(cid, 0, price=price, size=1250, ts=2000),
    ]}))
    assert len(entry_orders) == 1 and entry_orders[0].side == "buy"
    return entry_orders[0], entry_orders[0].qty


def _pos(agent, cid="cidX", oi=0):
    st = agent._load_whale_state("0xW") or {}
    return (st.get("our_positions") or {}).get(agent._position_key(cid, oi))


def _fill(order, *, qty, price):
    return FillEvent(order_id=order.id, symbol=order.symbol, side=order.side,
                     qty=qty, price=price, ts="2026-06-14T00:00:00+00:00",
                     venue="polymarket")


# ── (C) partial-fill write-back + the exit-sells-actual INVARIANT ────────────


@pytest.mark.asyncio
async def test_partial_fill_writeback_then_exit_sells_actual(strategy):
    """MANDATORY invariant: live entry → partial fill (qty < intended) → recorded
    position reflects the ACTUAL filled qty → the later exit sells the ACTUAL held
    lot, NOT the intended size."""
    agent, db_url = strategy
    entry, intended_qty = await _emit_entry_position(agent, db_url)

    # optimistic position == intended (120 * 0.00833 = 0.9996 USDC @ 0.40 = ~2.499)
    pos = _pos(agent)
    assert pos["copy_size_usdc"] == pytest.approx(0.9996)
    assert intended_qty == pytest.approx(0.9996 / 0.40)

    # ── synthesized-FAK PARTIAL fill: only 1.0 of ~2.499 filled, at 0.42 ──
    fill = _fill(entry, qty=1.0, price=0.42)
    assert fill.qty < intended_qty                       # partial
    agent.record_entry_fill(entry, fill)

    pos = _pos(agent)
    assert pos["actual_fill_qty"] == pytest.approx(1.0)  # the real lot
    assert pos["entry_price"] == pytest.approx(0.42)
    assert pos["copy_size_usdc"] == pytest.approx(1.0 * 0.42)
    assert pos["execution_mode"] == "live"

    # ── drive the EXIT: whale sells → _emit_exit must sell the ACTUAL held qty ──
    exit_orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cidX", 0, price=0.55, size=1250, ts=3000, side="SELL"),
    ]}))
    assert len(exit_orders) == 1 and exit_orders[0].side == "sell"
    # THE INVARIANT
    assert exit_orders[0].qty == pytest.approx(1.0)              # actual held
    assert exit_orders[0].qty != pytest.approx(intended_qty)     # NOT the intended ~2.499


@pytest.mark.asyncio
async def test_full_fill_writeback_exit_sells_intended(strategy):
    """Boundary: a FULL fill (qty == intended) records == intended and the exit
    sells the intended size — the write-back didn't break the normal case."""
    agent, db_url = strategy
    entry, intended_qty = await _emit_entry_position(agent, db_url)
    agent.record_entry_fill(entry, _fill(entry, qty=intended_qty, price=0.40))

    pos = _pos(agent)
    assert pos["actual_fill_qty"] == pytest.approx(intended_qty)

    exit_orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cidX", 0, price=0.55, size=1250, ts=3000, side="SELL"),
    ]}))
    assert exit_orders[0].qty == pytest.approx(intended_qty)


@pytest.mark.asyncio
async def test_discard_entry_removes_optimistic_position(strategy):
    """A no-fill must leave NO position: discard_entry removes the optimistically
    recorded lot, and a later whale SELL then closes nothing."""
    agent, db_url = strategy
    entry, _ = await _emit_entry_position(agent, db_url)
    assert _pos(agent) is not None                       # optimistic position present

    agent.discard_entry(entry)
    assert _pos(agent) is None                            # removed — we hold nothing

    exit_orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cidX", 0, price=0.55, size=1250, ts=3000, side="SELL"),
    ]}))
    assert exit_orders == []                              # nothing to close


@pytest.mark.asyncio
async def test_record_entry_fill_noop_on_empty_fill(strategy):
    # A zero/empty fill must NOT corrupt the recorded position (the loop discards
    # no-fills via discard_entry separately); record_entry_fill is a no-op here.
    agent, db_url = strategy
    entry, intended_qty = await _emit_entry_position(agent, db_url)
    before = dict(_pos(agent))
    agent.record_entry_fill(entry, _fill(entry, qty=0.0, price=0.0))
    assert _pos(agent) == before                         # unchanged — still intended


# ── (A)/(B) loop helper: gated placement + no-fill vs real failure (mocked) ──


def _live_order(is_entry=True):
    return ProposedOrder(
        strategy="polymarket_copy_trader",
        symbol="cidX:Yes", side="buy" if is_entry else "sell",
        qty=2.5, order_type="market", limit_price=0.40,
        extra={"is_entry": is_entry, "whale_wallet": "0xW", "condition_id": "cidX",
               "outcome_index": 0, "market_title": "M", "copy_size_usdc": 1.0,
               "whale_user_name": "alice"},
    )


def _mocks():
    agent = MagicMock()
    agent.name = "polymarket_copy_trader"
    agent.division = "polymarket_copy_trading"
    data_exec = MagicMock()
    logger_agent = MagicMock()
    channel = MagicMock()
    channel.push = AsyncMock()
    verdict = SimpleNamespace(verdict="approve", reason="")
    return agent, data_exec, logger_agent, channel, verdict


def _logged_kinds(logger_agent):
    return [c.args[1] for c in logger_agent.log_event.call_args_list]


@pytest.mark.asyncio
async def test_live_armed_calls_place_and_records_fill():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_order(is_entry=True)
    fill = _fill(order, qty=1.0, price=0.42)
    data_exec.place = AsyncMock(return_value=fill)

    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel,
        base_payload={},
    )
    data_exec.place.assert_awaited_once_with(order, division="polymarket_copy_trading")
    agent.record_entry_fill.assert_called_once_with(order, fill)
    agent.discard_entry.assert_not_called()
    assert "would_have_placed" not in _logged_kinds(logger_agent)  # live path, not paper
    channel.push.assert_awaited()   # Phase 2a: a LIVE placement STILL pushes a Telegram card


@pytest.mark.asyncio
async def test_no_fill_skips_discards_and_continues():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_order(is_entry=True)
    data_exec.place = AsyncMock(
        side_effect=NoFillInWindow("order did not fill within 5s; no fill recorded")
    )
    # returns NORMALLY (no raise) → loop continues to next order, batch not abandoned
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel,
        base_payload={},
    )
    agent.discard_entry.assert_called_once_with(order)   # optimistic position dropped
    agent.record_entry_fill.assert_not_called()          # NO position recorded
    kinds = _logged_kinds(logger_agent)
    assert "polymarket_copy_no_fill" in kinds            # benign audit (not the loud handler)
    assert "would_have_placed" not in kinds


@pytest.mark.asyncio
async def test_real_placement_failure_propagates():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_order(is_entry=True)
    data_exec.place = AsyncMock(
        side_effect=OrderPlacementError("polymarket post_order failed: insufficient balance")
    )
    # plain OrderPlacementError is NOT a NoFillInWindow → propagates to the loud handler
    with pytest.raises(OrderPlacementError):
        await _handle_copy_order_placement(
            agent=agent, order=order, verdict=verdict, is_live_armed=True,
            data_exec=data_exec, logger_agent=logger_agent, channel=channel,
            base_payload={},
        )
    agent.discard_entry.assert_not_called()              # not treated as benign
    agent.record_entry_fill.assert_not_called()


@pytest.mark.asyncio
async def test_paper_branch_logs_would_have_placed_and_never_places():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    data_exec.place = AsyncMock()
    order = _live_order(is_entry=True)
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=False,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel,
        base_payload={},
    )
    data_exec.place.assert_not_awaited()                 # paper NEVER calls place()
    assert "would_have_placed" in _logged_kinds(logger_agent)   # Phase 2a: audit rail RETAINED
    channel.push.assert_not_awaited()                    # Phase 2a: paper farm is SILENCED on Telegram
    agent.record_entry_fill.assert_not_called()
    agent.discard_entry.assert_not_called()


# ── the live-armed GATE is E2·4 placement-legal, NOT broker.paper ────────────


def test_live_armed_gate_uses_placement_legal_not_broker_paper():
    from trading_corp.brokers.base import Broker
    from trading_corp.brokers.polymarket import PolymarketBroker
    from trading_corp.brokers.polymarket_live import PolymarketLiveBroker

    live = PolymarketLiveBroker(
        private_key="0xk", funder_address="0xf", polygon_rpc_url="http://rpc",
    )
    paper = PolymarketBroker(funder_address="0xf", polygon_rpc_url="http://rpc")
    # E2·6 gate: isinstance(broker, Broker)
    assert isinstance(live, Broker) is True              # live-armed
    assert isinstance(paper, Broker) is False             # paper read-only — NOT armed
    # crucially NOT broker.paper: the read-only adapter has paper=False yet is the
    # paper path — gating on broker.paper would mis-arm it live.
    assert paper.paper is False


# ── (B+E2·5) integration: real data_exec.place sets execution_mode='live' ────


@pytest.mark.asyncio
async def test_live_place_sets_execution_mode_live_end_to_end(tmp_db):
    from trading_corp.agents.data_exec import DataExecAgent
    from trading_corp.agents.logger import LoggerAgent

    _db.init_db(tmp_db)

    class _LiveBroker:
        paper = False
        name = "polymarket-live-mock"

        async def place_order(self, order):
            return FillEvent(order_id=order.id, symbol=order.symbol, side=order.side,
                             qty=1.0, price=0.42, ts="2026-06-14T00:00:00+00:00",
                             venue="polymarket")

    dex = DataExecAgent(LoggerAgent(tmp_db))
    dex.register_broker("polymarket_copy_trading", _LiveBroker())

    agent = MagicMock()
    agent.name = "polymarket_copy_trader"
    agent.division = "polymarket_copy_trading"
    channel = MagicMock()
    channel.push = AsyncMock()
    order = _live_order(is_entry=True)

    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=SimpleNamespace(verdict="approve", reason=""),
        is_live_armed=True, data_exec=dex, logger_agent=LoggerAgent(tmp_db),
        channel=channel, base_payload={},
    )
    assert order.execution_mode == "live"                # E2·5 set-point via broker.paper=False
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT execution_mode FROM proposed_order WHERE id=?", (order.id,),
        ).fetchone()
    assert row["execution_mode"] == "live"
    agent.record_entry_fill.assert_called_once()         # fill written back (entry)
