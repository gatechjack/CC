"""Integration tests: observer bracket placement + reconciler SL-move.

Mocked brokers / temp DB — no venue I/O. Covers _place_bracket_exits (TP ladder
placement via the native place_tpsl_order path, degradation, fail-soft) and
move_bracket_sls (SL-move-on-TP-fill, price-only, failure-tolerant).

Updated 2026-06-18: bracket now uses place_tpsl_order (native /tpsl/place_order)
instead of place_resting_reduce_only_limit; positionId sourced from
get_pending_positions and threaded to each leg.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.divisions.bitunix_position_reconciler import (
    move_bracket_sls,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import (
    PaperTradeRecord,
    Position,
    ProposedOrder,
)

SHORT_TP_PLAN = [
    {"leg": "tp1", "fraction": 0.25, "price": 65928.22, "stop_action": "move_to_breakeven"},
    {"leg": "tp2", "fraction": 0.50, "price": 65801.10, "stop_action": "move_to_tp1"},
    {"leg": "tp3", "fraction": 0.25, "price": 65482.04, "stop_action": "trail_atr"},
]

_PRICES_TO_LEG = {65928.22: "tp1", 65801.10: "tp2", 65482.04: "tp3"}


def _read_extra(url: str, order_id: str) -> dict:
    path = db.resolve_db_path(url)
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return json.loads(row["extra_json"]) if row and row["extra_json"] else {}
    finally:
        conn.close()


def _last_kind(url: str, kind: str) -> dict | None:
    path = db.resolve_db_path(url)
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None
    finally:
        conn.close()


# ── bracket placement ───────────────────────────────────────────────────────

class FakeTpslBroker:
    """Broker fake supporting the native tpsl path (place_tpsl_order +
    get_pending_positions). Replaces the old FakeRestingBroker which had
    place_resting_reduce_only_limit (the pre-rebuild path)."""
    def __init__(self, position_id="pos-int-123", fail_legs=()):
        self.placed: list[dict] = []
        self.fail_legs = set(fail_legs)
        self._position_id = position_id
        self._positions: list[Position] = []

    def set_positions(self, positions):
        self._positions = positions

    async def get_pending_positions(self):
        return self._positions

    async def place_tpsl_order(self, *, symbol, position_id, tp_price, tp_qty,
                                tp_stop_type="MARK_PRICE", tp_order_type="LIMIT"):
        leg = None
        for price, label in _PRICES_TO_LEG.items():
            if abs(tp_price - price) < 0.01:
                leg = label
                break
        if leg in self.fail_legs:
            raise RuntimeError(f"tpsl fail for {leg}")
        self.placed.append({
            "symbol": symbol, "position_id": position_id,
            "tp_price": tp_price, "tp_qty": tp_qty,
            "tp_stop_type": tp_stop_type, "tp_order_type": tp_order_type,
            "exit_kind": leg,
        })
        return f"venue-tpsl-{leg}"


class FakeDataExec:
    def __init__(self, broker):
        self.brokers = {"bitunix_futures": broker}


def _entry_order(qty: float, side: str = "sell", oid: str = "ord-1") -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side=side, qty=qty,
        order_type="market", id=oid,
        extra={"tp_plan": SHORT_TP_PLAN, "stop_price": 66273.0,
               "tp1_price": 65928.22, "entry_reference_price": 66047.0,
               "execution_mode": "live"},
    )


def _short_pos_with_id(qty_abs: float, pos_id: str = "pos-int-123") -> Position:
    return Position(
        account="bitunix-futures", symbol="BTCUSDT", qty=-qty_abs,
        avg_price=66047.0, opened_ts="2026-06-18T12:00:00",
        extra={"side": "SHORT", "positionId": pos_id,
               "leverage": "10", "marginMode": "ISOLATED",
               "unrealizedPNL": "0", "liqPrice": "70000"},
    )


def _observer(tmp_path, broker):
    url = f"sqlite:///{tmp_path / 'obs.db'}"
    init_db(url)
    obs = BitunixFuturesObserver(
        db_url=url, logger_agent=LoggerAgent(db_url=url), data_exec=FakeDataExec(broker),
    )
    return obs, url


async def _persist_entry(url, order):
    rec = PaperTradeRecord.from_order(
        order, strategy="bitunix_futures", division="bitunix_futures",
        max_hold_seconds=3600,
    )
    rec.extra = dict(order.extra)
    rec.extra["execution_mode"] = "live"
    db.insert_paper_trade_record(rec.to_db_row(), db_url=url)
    return rec


@pytest.mark.asyncio
async def test_bracket_places_three_legs(tmp_path):
    broker = FakeTpslBroker(position_id="pos-int-123")
    broker.set_positions([_short_pos_with_id(0.0016, "pos-int-123")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    assert [p["exit_kind"] for p in broker.placed] == ["tp1", "tp2", "tp3"]
    # positionId threaded to all legs
    assert all(p["position_id"] == "pos-int-123" for p in broker.placed)
    assert broker.placed[0]["tp_price"] == 65928.22
    assert broker.placed[0]["tp_qty"] == pytest.approx(0.0004)   # 0.25
    assert broker.placed[1]["tp_qty"] == pytest.approx(0.0008)   # 0.50
    extra = _read_extra(url, order.id)
    assert set(extra["bracket_tp_order_ids"]) == {"tp1", "tp2", "tp3"}
    assert extra["bracket_entry_qty"] == pytest.approx(0.0016)
    assert extra["current_sl"] == pytest.approx(66273.0)
    assert extra["bracket_position_id"] == "pos-int-123"
    assert _last_kind(url, "bracket_placed")["legs_placed"] == 3


@pytest.mark.asyncio
async def test_bracket_degrades_to_one_leg(tmp_path):
    broker = FakeTpslBroker(position_id="pos-int-123")
    broker.set_positions([_short_pos_with_id(0.0004, "pos-int-123")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0004)  # < 2*min → single leg
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    assert [p["exit_kind"] for p in broker.placed] == ["tp1"]
    assert broker.placed[0]["tp_qty"] == pytest.approx(0.0004)
    assert "degrade" in (_last_kind(url, "bracket_placed")["degrade_note"] or "").lower()


@pytest.mark.asyncio
async def test_bracket_leg_failure_failsoft(tmp_path):
    broker = FakeTpslBroker(position_id="pos-int-123", fail_legs={"tp2"})
    broker.set_positions([_short_pos_with_id(0.0016, "pos-int-123")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)  # must NOT raise

    assert [p["exit_kind"] for p in broker.placed] == ["tp1", "tp3"]  # tp2 failed, rest placed
    assert _last_kind(url, "bracket_tp_leg_failed")["leg"] == "tp2"
    extra = _read_extra(url, order.id)
    assert "tp2" not in extra["bracket_tp_order_ids"]
    assert set(extra["bracket_tp_order_ids"]) == {"tp1", "tp3"}


# ── SL-move-on-TP-fill ───────────────────────────────────────────────────────

class FakePosBroker:
    def __init__(self, positions, modify_ok=True):
        self._positions = positions
        self.modify_calls: list[tuple] = []
        self._ok = modify_ok

    async def get_pending_positions(self):
        return self._positions

    async def modify_position_sl(self, symbol, new_sl, **kw):
        self.modify_calls.append((symbol, new_sl))
        return self._ok


def _insert_bracket_row(url, *, oid, side, entry_qty, current_sl, entry, tp1):
    order = ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side=side, qty=entry_qty,
        order_type="market", id=oid,
    )
    rec = PaperTradeRecord.from_order(
        order, strategy="bitunix_futures", division="bitunix_futures",
        max_hold_seconds=3600,
    )
    rec.extra = {
        "execution_mode": "live", "bracket_entry_qty": entry_qty,
        "current_sl": current_sl, "entry_reference_price": entry, "tp1_price": tp1,
    }
    db.insert_paper_trade_record(rec.to_db_row(), db_url=url)


def _short_pos(qty_abs: float) -> Position:
    return Position(account="bx", symbol="BTCUSDT", qty=-qty_abs,
                    avg_price=66047.0, opened_ts="2026-06-16T13:48:00")


@pytest.mark.asyncio
async def test_sl_moves_to_breakeven_on_tp1_fill(tmp_path):
    url = f"sqlite:///{tmp_path / 'slm.db'}"
    init_db(url)
    _insert_bracket_row(url, oid="o1", side="sell", entry_qty=0.0016,
                        current_sl=66273.0, entry=66047.0, tp1=65928.22)
    # broker shows the short reduced 0.0016 → 0.0012 (25% closed = TP1).
    broker = FakePosBroker(positions=[_short_pos(0.0012)])

    await move_bracket_sls(broker, url)

    assert broker.modify_calls == [("BTC/USDT.P", pytest.approx(66047.0))]
    assert _read_extra(url, "o1")["current_sl"] == pytest.approx(66047.0)
    audit = _last_kind(url, "position_sl_update")
    assert audit["moved"] is True and audit["new_sl"] == pytest.approx(66047.0)


@pytest.mark.asyncio
async def test_sl_no_move_when_position_full(tmp_path):
    url = f"sqlite:///{tmp_path / 'slm2.db'}"
    init_db(url)
    _insert_bracket_row(url, oid="o2", side="sell", entry_qty=0.0016,
                        current_sl=66273.0, entry=66047.0, tp1=65928.22)
    broker = FakePosBroker(positions=[_short_pos(0.0016)])  # no fill

    await move_bracket_sls(broker, url)

    assert broker.modify_calls == []
    assert _read_extra(url, "o2")["current_sl"] == pytest.approx(66273.0)


@pytest.mark.asyncio
async def test_sl_move_failsoft_not_persisted_on_modify_failure(tmp_path):
    url = f"sqlite:///{tmp_path / 'slm3.db'}"
    init_db(url)
    _insert_bracket_row(url, oid="o3", side="sell", entry_qty=0.0016,
                        current_sl=66273.0, entry=66047.0, tp1=65928.22)
    broker = FakePosBroker(positions=[_short_pos(0.0012)], modify_ok=False)

    await move_bracket_sls(broker, url)  # must NOT raise

    assert broker.modify_calls  # attempted
    # modify failed → SL NOT advanced in state (retries next tick).
    assert _read_extra(url, "o3")["current_sl"] == pytest.approx(66273.0)
    assert _last_kind(url, "position_sl_update")["moved"] is False


@pytest.mark.asyncio
async def test_sl_move_ignores_non_bracket_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'slm4.db'}"
    init_db(url)
    # a live row WITHOUT bracket_entry_qty → must be ignored.
    order = ProposedOrder(strategy="bitunix_futures", symbol="BTC/USDT.P",
                          side="sell", qty=0.0016, order_type="market", id="o4")
    rec = PaperTradeRecord.from_order(order, strategy="bitunix_futures",
                                      division="bitunix_futures", max_hold_seconds=3600)
    rec.extra = {"execution_mode": "live"}  # no bracket state
    db.insert_paper_trade_record(rec.to_db_row(), db_url=url)
    broker = FakePosBroker(positions=[_short_pos(0.0008)])

    await move_bracket_sls(broker, url)

    assert broker.modify_calls == []  # non-bracket row untouched
