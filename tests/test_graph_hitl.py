"""End-to-end test of the trade-flow graph including HITL interrupt + resume."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.risk import RiskAgent
from trading_corp.brokers.paper import PaperBroker
from trading_corp.graph.ceo_graph import build_trade_graph
from trading_corp.persistence import db
from trading_corp.persistence.models import ProposedOrder


@pytest.fixture
def setup_pieces(tmp_db, tmp_risk_yaml):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    de = DataExecAgent(logger)
    return tmp_db, logger, risk, de


@pytest.mark.asyncio
async def test_risk_rejected_path_does_not_interrupt(setup_pieces):
    """If risk rejects, the graph runs to END without ever calling interrupt()."""
    tmp_db, logger, risk, de = setup_pieces
    paper = PaperBroker(account="paper-test", starting_equity=10_000.0)
    de.register_broker("default", paper)
    await de.connect_all()

    from langgraph.checkpoint.memory import MemorySaver  # type: ignore
    saver = MemorySaver()
    graph = build_trade_graph(risk, de, logger, checkpointer=saver)

    # Order that violates per-trade cap so badly that resize would be 0:
    # equity=10k * 0.015 = $150 risk cap; 1 share @ $1000 = $1000 → resize to 0.15.
    # That's still fine (resize, not reject). To force reject, halt the strategy.
    order = ProposedOrder(strategy="demo", symbol="SPY", side="buy",
                          qty=1, order_type="limit", limit_price=1000.0)
    state = {
        "proposed_order": order.to_db_row() | {"extra": order.extra},
        "division": "default",
        "regime": "uptrend",
        "strategy_state": {"strategy": "demo", "halted": True, "halt_reason": "test"},
        "account": {"account": "paper-test", "equity": 10_000.0, "peak_equity": 10_000.0},
    }
    config = {"configurable": {"thread_id": order.id}}
    result = await graph.ainvoke(state, config=config)
    assert result["final_status"] == "risk_rejected"


@pytest.mark.asyncio
async def test_approval_interrupt_then_resume_approve(setup_pieces):
    """Risk-approved order pauses at the Board approval gate; resume('approve')
    routes to executor and produces a paper fill."""
    tmp_db, logger, risk, de = setup_pieces
    paper = PaperBroker(account="paper-test", starting_equity=100_000.0)
    de.register_broker("default", paper)
    await de.connect_all()

    from langgraph.checkpoint.memory import MemorySaver  # type: ignore
    from langgraph.types import Command  # type: ignore
    saver = MemorySaver()
    graph = build_trade_graph(risk, de, logger, checkpointer=saver)

    order = ProposedOrder(strategy="demo", symbol="SPY", side="buy",
                          qty=1, order_type="limit", limit_price=500.0)
    state = {
        "proposed_order": order.to_db_row() | {"extra": order.extra},
        "division": "default",
        "regime": "uptrend",
        "strategy_state": {"strategy": "demo", "halted": False},
        "account": {"account": "paper-test", "equity": 100_000.0, "peak_equity": 100_000.0},
    }
    config = {"configurable": {"thread_id": order.id}}

    result = await graph.ainvoke(state, config=config)
    # The graph must have paused at the approval gate.
    assert "__interrupt__" in result, f"expected interrupt; got keys={list(result)}"
    interrupts = result["__interrupt__"]
    assert len(interrupts) == 1
    payload = getattr(interrupts[0], "value", interrupts[0])
    assert payload["order_id"] == order.id

    # Resume with approve.
    final = await graph.ainvoke(
        Command(resume={"decision": "approve", "reason": "test"}),
        config=config,
    )
    assert final["final_status"] == "filled"
    assert final["fill"]["price"] == 500.0


@pytest.mark.asyncio
async def test_approval_interrupt_then_resume_reject(setup_pieces):
    """Resume with 'reject' routes to end_rejected; no fill placed."""
    tmp_db, logger, risk, de = setup_pieces
    paper = PaperBroker(account="paper-test", starting_equity=100_000.0)
    de.register_broker("default", paper)
    await de.connect_all()

    from langgraph.checkpoint.memory import MemorySaver  # type: ignore
    from langgraph.types import Command  # type: ignore
    saver = MemorySaver()
    graph = build_trade_graph(risk, de, logger, checkpointer=saver)

    order = ProposedOrder(strategy="demo", symbol="SPY", side="buy",
                          qty=1, order_type="limit", limit_price=500.0)
    state = {
        "proposed_order": order.to_db_row() | {"extra": order.extra},
        "division": "default",
        "regime": "uptrend",
        "strategy_state": {"strategy": "demo", "halted": False},
        "account": {"account": "paper-test", "equity": 100_000.0, "peak_equity": 100_000.0},
    }
    config = {"configurable": {"thread_id": order.id}}

    await graph.ainvoke(state, config=config)
    final = await graph.ainvoke(
        Command(resume={"decision": "reject", "reason": "no thanks"}),
        config=config,
    )
    assert final["final_status"] == "board_rejected"
    # PaperBroker should still have no positions.
    snap = await paper.snapshot()
    assert len(snap.positions) == 0


@pytest.mark.asyncio
async def test_audit_log_written_on_full_flow(setup_pieces):
    """An approve→fill should write at least one 'filled' audit_event row."""
    tmp_db, logger, risk, de = setup_pieces
    paper = PaperBroker(account="paper-test", starting_equity=100_000.0)
    de.register_broker("default", paper)
    await de.connect_all()

    from langgraph.checkpoint.memory import MemorySaver  # type: ignore
    from langgraph.types import Command  # type: ignore
    saver = MemorySaver()
    graph = build_trade_graph(risk, de, logger, checkpointer=saver)

    order = ProposedOrder(strategy="demo", symbol="SPY", side="buy",
                          qty=1, order_type="limit", limit_price=500.0)
    state = {
        "proposed_order": order.to_db_row() | {"extra": order.extra},
        "division": "default",
        "regime": "uptrend",
        "strategy_state": {"strategy": "demo", "halted": False},
        "account": {"account": "paper-test", "equity": 100_000.0, "peak_equity": 100_000.0},
    }
    config = {"configurable": {"thread_id": order.id}}
    await graph.ainvoke(state, config=config)
    await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)

    # Read audit log directly.
    db_path = Path(tmp_db.replace("sqlite:///", ""))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT actor, kind FROM audit_event ORDER BY id"
        ).fetchall()
    kinds = {(actor, kind) for actor, kind in rows}
    assert ("risk", "risk_approved") in kinds
    assert ("board", "board_approved") in kinds
    assert ("data_exec", "filled") in kinds
