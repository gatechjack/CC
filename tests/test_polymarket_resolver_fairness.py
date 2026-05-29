"""Resolver per-actor fairness (Group A #3).

Network-free. Proves the market-settle pass no longer lets one division's
backlog starve the other out of the scan window — the bug that left ~1,650
copy-trader positions unresolved (≥578 on already-settled markets) while
arbitrage's ~121 long-horizon rows saturated the old global 100-row cap.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from trading_corp.agents.polymarket_resolver import (
    _fetch_unresolved_orders,
    resolve_pending_round_trips,
)
from trading_corp.persistence.db import init_db


def _init_db(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tc.db'}"
    init_db(db_url=db_url)
    return db_url, tmp_path / "tc.db"


def _ins_buy(db_path, *, actor, order_id, condition_id, outcome="yes",
             qty=20.0, price=0.5, resolves_at=None, ts=None):
    payload = {
        "strategy": actor, "order_id": order_id, "condition_id": condition_id,
        "side": "buy", "qty": qty, "limit_price": price, "outcome": outcome,
    }
    if actor == "polymarket_copy_trader":
        payload["division"] = "polymarket_copy_trading"
    if resolves_at:
        payload["resolves_at"] = resolves_at
    ts = ts or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?, ?, 'would_have_placed', ?)",
            (ts, actor, json.dumps(payload)),
        )


class _StubBroker:
    """get_market_resolution returns resolved for `resolved` condition_ids,
    pending otherwise."""
    def __init__(self, resolved):
        self.resolved = set(resolved)

    async def get_market_resolution(self, *, condition_id=None, slug=None):
        if condition_id in self.resolved:
            return {"status": "resolved", "yes_won": True}
        return {"status": "pending"}


def _round_trip_order_ids(db_path):
    with sqlite3.connect(db_path) as c:
        return {r[0] for r in c.execute(
            "SELECT order_id FROM polymarket_round_trips").fetchall()}


def test_pct_not_starved_by_arb_backlog(tmp_path):
    """120 arbitrage rows (all carrying resolves_at, all pending) + 1 copy-
    trader BUY on a resolved market. Even at per_actor_limit=100 (the OLD
    global cap), the PCT BUY must resolve — because it gets its OWN budget,
    not the leftovers behind 120 arb rows. (Old global-100 behavior: arb fills
    the window, PCT never scanned, PCT never resolves.)"""
    db_url, db_path = _init_db(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    for i in range(120):
        _ins_buy(db_path, actor="polymarket_arbitrage", order_id=f"arb-{i}",
                 condition_id=f"0xARB{i}", resolves_at=future)
    _ins_buy(db_path, actor="polymarket_copy_trader", order_id="pct-1",
             condition_id="0xRESOLVED")
    broker = _StubBroker(resolved={"0xRESOLVED"})
    counts = asyncio.run(
        resolve_pending_round_trips(db_url, broker, per_actor_limit=100))
    rt = _round_trip_order_ids(db_path)
    assert "pct-1" in rt, f"PCT starved by arb backlog; counts={counts}"
    assert counts["resolved"] >= 1


def test_backlog_drains_past_old_global_cap(tmp_path):
    """150 copy-trader BUYs all on resolved markets drain in one pass with
    per_actor_limit=1000 (the old global cap of 100 would leave 50 stuck)."""
    db_url, db_path = _init_db(tmp_path)
    for i in range(150):
        _ins_buy(db_path, actor="polymarket_copy_trader", order_id=f"pct-{i}",
                 condition_id=f"0xR{i}")
    broker = _StubBroker(resolved={f"0xR{i}" for i in range(150)})
    counts = asyncio.run(
        resolve_pending_round_trips(db_url, broker, per_actor_limit=1000))
    assert counts["resolved"] == 150, counts
    assert len(_round_trip_order_ids(db_path)) == 150


def test_per_actor_fetch_caps_each_actor_independently(tmp_path):
    """_fetch_unresolved_orders caps EACH actor at per_actor_limit (not a
    shared global budget)."""
    db_url, db_path = _init_db(tmp_path)
    for i in range(10):
        _ins_buy(db_path, actor="polymarket_arbitrage", order_id=f"a{i}",
                 condition_id=f"0xA{i}")
    for i in range(10):
        _ins_buy(db_path, actor="polymarket_copy_trader", order_id=f"p{i}",
                 condition_id=f"0xP{i}")
    rows = _fetch_unresolved_orders(db_url, per_actor_limit=3)
    actors = [r["_actor"] for r in rows]
    assert actors.count("polymarket_arbitrage") == 3
    assert actors.count("polymarket_copy_trader") == 3
