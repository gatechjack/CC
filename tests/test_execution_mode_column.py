"""E2·5 — execution_mode column on proposed_order + paper_trade_record.

Pins:
- the migration adds execution_mode (NOT NULL DEFAULT 'paper') to BOTH tables;
- a pre-existing (paper-era) row defaults to 'paper' on upgrade — no misclassify;
- ProposedOrder / PaperTradeRecord resolve execution_mode (field → extra tag →
  'paper') at to_db_row;
- data_exec.place() writes 'paper' for a paper broker and 'live' for a live broker
  (mocked; no real order), derived from broker.paper (the real path, not config).

Fundless, network-free, mocked.
"""
from __future__ import annotations

import sqlite3

import pytest

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence.db import (
    connect,
    init_db,
    insert_paper_trade_record,
    resolve_db_path,
)
from trading_corp.persistence.models import (
    FillEvent,
    PaperTradeRecord,
    ProposedOrder,
    _resolve_execution_mode,
)


def _columns(db_url: str, table: str) -> set[str]:
    path = resolve_db_path(db_url)
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# ── migration ────────────────────────────────────────────────────────────


def test_migration_adds_execution_mode_to_both_tables(tmp_db):
    init_db(tmp_db)
    for table in ("proposed_order", "paper_trade_record"):
        assert "execution_mode" in _columns(tmp_db, table), table


def test_existing_paper_era_row_defaults_to_paper_on_upgrade(tmp_db):
    # Simulate a long-lived DB whose proposed_order PREDATES the column: create
    # the table WITHOUT execution_mode, insert a row, then run init_db (which
    # ALTERs the column in via the idempotent _maybe_add_column with DEFAULT
    # 'paper'). The pre-existing row must read 'paper' — never misclassified.
    path = resolve_db_path(tmp_db)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE proposed_order ("
            "id TEXT PRIMARY KEY, ts TEXT, strategy TEXT, symbol TEXT, side TEXT, "
            "qty REAL, order_type TEXT, limit_price REAL, rationale TEXT, status TEXT, "
            "risk_reason TEXT, board_reason TEXT, fill_price REAL, fill_ts TEXT, extra_json TEXT)"
        )
        conn.execute(
            "INSERT INTO proposed_order (id, ts, strategy, symbol, side, qty, order_type, status) "
            "VALUES ('old1','2026-01-01T00:00:00+00:00','lord_otter','BTC/USD','buy',0.01,'market','filled')"
        )
        conn.commit()

    init_db(tmp_db)  # idempotent ADD COLUMN migration

    assert "execution_mode" in _columns(tmp_db, "proposed_order")
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT execution_mode FROM proposed_order WHERE id='old1'"
        ).fetchone()
    assert row["execution_mode"] == "paper"  # backfilled, not misclassified as live


def test_migration_is_idempotent(tmp_db):
    init_db(tmp_db)
    init_db(tmp_db)  # second run must not raise (column already exists)
    assert "execution_mode" in _columns(tmp_db, "proposed_order")
    assert "execution_mode" in _columns(tmp_db, "paper_trade_record")


# ── model resolution (field → extra tag → 'paper') ───────────────────────


def test_resolve_execution_mode_precedence():
    assert _resolve_execution_mode(None, {}) == "paper"                       # default
    assert _resolve_execution_mode("live", {}) == "live"                      # explicit field
    assert _resolve_execution_mode("paper", {}) == "paper"
    assert _resolve_execution_mode(None, {"execution_mode": "live"}) == "live"  # extra tag (bitunix)
    assert _resolve_execution_mode("bogus", {}) == "paper"                    # invalid field → paper
    assert _resolve_execution_mode(None, {"execution_mode": "x"}) == "paper"  # invalid tag → paper


def test_proposed_order_to_db_row_execution_mode():
    base = dict(strategy="s", symbol="X", side="buy", qty=1.0)
    assert ProposedOrder(**base).to_db_row()["execution_mode"] == "paper"
    assert ProposedOrder(**base, execution_mode="live").to_db_row()["execution_mode"] == "live"
    # honors the bitunix observer's existing extra['execution_mode']='live' tag
    assert ProposedOrder(**base, extra={"execution_mode": "live"}).to_db_row()["execution_mode"] == "live"


def test_paper_trade_record_defaults_paper_and_honors_extra(tmp_db):
    init_db(tmp_db)
    order = ProposedOrder(strategy="lord_otter", symbol="BTC/USD", side="buy", qty=0.01)
    rec = PaperTradeRecord.from_order(
        order, strategy="lord_otter", division="coinbase_spot", max_hold_seconds=86400,
    )
    # the would_have_placed PAPER path → 'paper'
    assert rec.to_db_row()["execution_mode"] == "paper"
    insert_paper_trade_record(rec.to_db_row(), db_url=tmp_db)
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT execution_mode FROM paper_trade_record WHERE order_id=?", (order.id,),
        ).fetchone()
    assert row["execution_mode"] == "paper"
    # the bitunix LIVE path tags record.extra['execution_mode']='live' → column reflects 'live'
    rec.extra["execution_mode"] = "live"
    assert rec.to_db_row()["execution_mode"] == "live"


# ── write path: data_exec.place() derives mode from the real broker ──────


class _FakeBroker:
    """Minimal broker for place(): carries `paper` + returns a FillEvent. No
    `_assert_snapshot_fresh` (duck-typed skip), no real network/order."""

    def __init__(self, paper: bool):
        self.paper = paper
        self.name = "fake-paper" if paper else "fake-live"

    async def place_order(self, order):
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            qty=float(order.qty), price=1.0, ts="2026-06-14T00:00:00+00:00",
            venue=self.name,
        )


def _place_and_read_mode(tmp_db, *, paper: bool) -> tuple[str, str]:
    init_db(tmp_db)
    agent = DataExecAgent(LoggerAgent(tmp_db))
    agent.register_broker("div", _FakeBroker(paper=paper))
    order = ProposedOrder(strategy="s", symbol="X", side="buy", qty=1.0, order_type="market")
    return agent, order


@pytest.mark.asyncio
async def test_place_paper_broker_writes_paper(tmp_db):
    agent, order = _place_and_read_mode(tmp_db, paper=True)
    await agent.place(order, division="div")
    assert order.execution_mode == "paper"
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT execution_mode FROM proposed_order WHERE id=?", (order.id,),
        ).fetchone()
    assert row["execution_mode"] == "paper"


@pytest.mark.asyncio
async def test_place_live_broker_writes_live(tmp_db):
    # Mock live broker (paper=False); no real order is routed.
    agent, order = _place_and_read_mode(tmp_db, paper=False)
    await agent.place(order, division="div")
    assert order.execution_mode == "live"
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT execution_mode FROM proposed_order WHERE id=?", (order.id,),
        ).fetchone()
    assert row["execution_mode"] == "live"
