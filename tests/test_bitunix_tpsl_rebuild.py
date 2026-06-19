"""Tests for the native /tpsl/ bracket rebuild (steps 1-4).

Covers:
1. positionId parsed into Position.extra from get_pending_positions
2. place_tpsl_order — body shape, error paths, idempotent codes
3. _place_bracket_exits wired to place_tpsl_order — 3/2/1-leg degrade,
   bracket_placed audit shape, positionId threading, fail-soft on unresolved
   positionId
4. modify_position_sl — correct path, mandatory positionId (no-op/False when
   absent), move_bracket_sls threads positionId from broker Position.extra
5. decide_sl_move integration unchanged (re-verified after reconciler refactor)
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.brokers.bitunix import (
    BitunixAPIError,
    BitunixBroker,
    _extract_tpsl_order_id,
)
from trading_corp.brokers.bitunix_exceptions import BitunixUntrackedTpslOrder
from trading_corp.persistence.models import Position, ProposedOrder, PaperTradeRecord
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.agents.divisions.bitunix_futures_observer import BitunixFuturesObserver
from trading_corp.agents.divisions.bitunix_position_reconciler import move_bracket_sls
from trading_corp.agents.logger import LoggerAgent

# ── helpers ───────────────────────────────────────────────────────────────────

SHORT_TP_PLAN = [
    {"leg": "tp1", "fraction": 0.25, "price": 65928.22, "stop_action": "move_to_breakeven"},
    {"leg": "tp2", "fraction": 0.50, "price": 65801.10, "stop_action": "move_to_tp1"},
    {"leg": "tp3", "fraction": 0.25, "price": 65482.04, "stop_action": "trail_atr"},
]


def _make_broker(
    *,
    request_returns=None,
    request_raises: Exception | None = None,
) -> BitunixBroker:
    broker = BitunixBroker(api_key="test_key", api_secret="test_secret")
    broker._client = MagicMock()
    if request_raises is not None:
        broker._request = AsyncMock(side_effect=request_raises)
    else:
        broker._request = AsyncMock(return_value=request_returns)
    return broker


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


# ════════════════════════════════════════════════════════════════════════════════
# 1. positionId sourcing — get_pending_positions captures it into Position.extra
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_position_id_captured_in_extra():
    """positionId from the venue response must appear in Position.extra."""
    broker = _make_broker(request_returns=[
        {
            "symbol": "BTCUSDT",
            "qty": "0.0016",
            "avgOpenPrice": "66047.0",
            "ctime": "1717200000000",
            "side": "SHORT",
            "positionId": "pos-abc-123",
            "leverage": "10",
            "marginMode": "ISOLATED",
            "unrealizedPNL": "-0.05",
            "liqPrice": "70000.0",
        },
    ])
    positions = await broker.get_pending_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.extra["positionId"] == "pos-abc-123"
    # Other existing extra fields must still be present
    assert p.extra["leverage"] == "10"
    assert p.extra["marginMode"] == "ISOLATED"
    assert p.extra["side"] == "SHORT"


@pytest.mark.asyncio
async def test_position_id_none_when_absent_from_response():
    """If the venue omits positionId (flat/old firmware), extra carries None gracefully."""
    broker = _make_broker(request_returns=[
        {
            "symbol": "BTCUSDT",
            "qty": "0.0016",
            "avgOpenPrice": "66047.0",
            "ctime": "1717200000000",
            "side": "LONG",
        },
    ])
    positions = await broker.get_pending_positions()
    assert len(positions) == 1
    # key present, value is None (not a KeyError)
    assert "positionId" in positions[0].extra
    assert positions[0].extra["positionId"] is None


# ════════════════════════════════════════════════════════════════════════════════
# 2. place_tpsl_order — body shape + error paths
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_place_tpsl_order_body_shape():
    """place_tpsl_order must build the correct /tpsl/place_order body."""
    broker = _make_broker(request_returns={"orderId": "tpsl-venue-001"})
    result = await broker.place_tpsl_order(
        symbol="BTC/USDT.P",
        position_id="pos-abc-123",
        tp_price=65928.22,
        tp_qty=0.0004,
    )
    assert result == "tpsl-venue-001"
    _args, kwargs = broker._request.await_args
    assert _args[0] == "POST"
    assert _args[1] == "/api/v1/futures/tpsl/place_order"
    body = kwargs["body"]
    assert body["positionId"] == "pos-abc-123"
    assert body["tpStopType"] == "MARK_PRICE"
    assert body["tpOrderType"] == "LIMIT"
    # tpOrderPrice must equal tpPrice (maker-limit-at-price behaviour)
    assert body["tpOrderPrice"] == body["tpPrice"]
    # tpQty and tpPrice present and non-empty
    assert body["tpQty"]
    assert body["tpPrice"]


def test_extract_tpsl_order_id_tolerates_both_shapes():
    """The parse helper must extract orderId from BOTH the documented dict shape
    and the LIVE list shape (report c8a426d), plus scalar/edge forms — and return
    "" only when genuinely absent."""
    # live shape: /tpsl/place_order returned a LIST → the bug the fix repairs
    assert _extract_tpsl_order_id([{"orderId": "L1"}]) == "L1"
    assert _extract_tpsl_order_id([{"orderId": "L1"}, {"orderId": "L2"}]) == "L1"
    # documented shape: a single dict
    assert _extract_tpsl_order_id({"orderId": "D1"}) == "D1"
    # alternate id key + bare-scalar forms
    assert _extract_tpsl_order_id({"id": "D2"}) == "D2"
    assert _extract_tpsl_order_id(["S1"]) == "S1"
    assert _extract_tpsl_order_id("S2") == "S2"
    assert _extract_tpsl_order_id(123) == "123"
    # genuinely absent → "" (caller decides what to do)
    assert _extract_tpsl_order_id({}) == ""
    assert _extract_tpsl_order_id([]) == ""
    assert _extract_tpsl_order_id(None) == ""
    assert _extract_tpsl_order_id([{"noId": "x"}]) == ""


@pytest.mark.asyncio
async def test_place_tpsl_order_parses_list_response():
    """THE FIX: the live /tpsl/place_order returns a LIST [{"orderId": ...}]
    (report c8a426d, trade cb6b4d4a). The id must be extracted from the list form
    instead of crashing with 'list' object has no attribute 'get'."""
    broker = _make_broker(request_returns=[{"orderId": "tpsl-list-001"}])
    result = await broker.place_tpsl_order(
        symbol="BTC/USDT.P", position_id="pos-123",
        tp_price=65928.22, tp_qty=0.0004,
    )
    assert result == "tpsl-list-001"


@pytest.mark.asyncio
async def test_place_tpsl_order_parses_dict_response():
    """Defensive both-shapes: the documented dict {"orderId": ...} must also parse."""
    broker = _make_broker(request_returns={"orderId": "tpsl-dict-001"})
    result = await broker.place_tpsl_order(
        symbol="BTC/USDT.P", position_id="pos-123",
        tp_price=65928.22, tp_qty=0.0004,
    )
    assert result == "tpsl-dict-001"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_response", [{}, [], {"noId": "x"}, [{"noId": "x"}]])
async def test_place_tpsl_order_raises_untracked_when_post_succeeds_without_id(empty_response):
    """HARDENING: POST accepted (code 0) but no orderId → the leg may be RESTING
    UNTRACKED. Must RAISE BitunixUntrackedTpslOrder carrying the reconcile context
    — NOT silently return "" (that silent-swallow was the Section-B risk)."""
    broker = _make_broker(request_returns=empty_response)
    with pytest.raises(BitunixUntrackedTpslOrder) as exc:
        await broker.place_tpsl_order(
            symbol="BTC/USDT.P", position_id="pos-123",
            tp_price=65928.22, tp_qty=0.0004,
        )
    # the exception must carry enough to find + reconcile the stray order
    assert exc.value.position_id == "pos-123"
    assert exc.value.raw_response == empty_response


@pytest.mark.asyncio
async def test_place_tpsl_order_raises_on_api_error():
    """Non-idempotent BitunixAPIError must propagate to the caller."""
    broker = _make_broker(
        request_raises=BitunixAPIError(30001, "some error", path="/api/v1/futures/tpsl/place_order"),
    )
    with pytest.raises(BitunixAPIError):
        await broker.place_tpsl_order(
            symbol="BTC/USDT.P",
            position_id="pos-123",
            tp_price=65928.22,
            tp_qty=0.0004,
        )


@pytest.mark.asyncio
async def test_place_tpsl_order_idempotent_duplicate_ok():
    """Code 30042 (CLIENT_ID_DUPLICATE) must be swallowed — not raised."""
    broker = _make_broker()
    broker._request = AsyncMock(
        side_effect=BitunixAPIError(30042, "clientId already used",
                                   path="/api/v1/futures/tpsl/place_order")
    )
    # Must not raise; returns empty string (no orderId from the idempotent path).
    result = await broker.place_tpsl_order(
        symbol="BTC/USDT.P",
        position_id="pos-123",
        tp_price=65928.22,
        tp_qty=0.0004,
    )
    assert result == ""


@pytest.mark.asyncio
async def test_place_tpsl_order_raises_in_stub_mode():
    """Stub mode (no creds) must raise NotImplementedError, not silently no-op."""
    broker = BitunixBroker(api_key="", api_secret="")
    with pytest.raises(NotImplementedError):
        await broker.place_tpsl_order(
            symbol="BTC/USDT.P",
            position_id="pos-123",
            tp_price=65928.22,
            tp_qty=0.0004,
        )


# ════════════════════════════════════════════════════════════════════════════════
# 2b. place_position_tpsl — auto-reducing whole-position SL (no qty)
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_place_position_tpsl_body_shape():
    """place_position_tpsl must POST to /tpsl/position/place_order with slPrice +
    slStopType + slOrderType and NO slQty (position-level, auto-reducing)."""
    broker = _make_broker(request_returns={"orderId": "pos-sl-venue-001"})
    result = await broker.place_position_tpsl(
        symbol="BTC/USDT.P",
        position_id="pos-abc-123",
        sl_price=66273.0,
    )
    assert result == "pos-sl-venue-001"
    _args, kwargs = broker._request.await_args
    assert _args[0] == "POST"
    assert _args[1] == "/api/v1/futures/tpsl/position/place_order"
    body = kwargs["body"]
    assert body["positionId"] == "pos-abc-123"
    assert body["slPrice"]
    assert body["slStopType"] == "MARK_PRICE"
    assert body["slOrderType"] == "MARKET"
    # HARD RULE: NO qty key (position-level, auto-reducing)
    assert "slQty" not in body
    assert "qty" not in body


@pytest.mark.asyncio
async def test_place_position_tpsl_returns_empty_string_when_no_order_id():
    """No orderId in the response → empty string (not a crash)."""
    broker = _make_broker(request_returns={})
    result = await broker.place_position_tpsl(
        symbol="BTC/USDT.P", position_id="pos-123", sl_price=66273.0,
    )
    assert result == ""


@pytest.mark.asyncio
async def test_place_position_tpsl_raises_on_api_error():
    """Non-idempotent BitunixAPIError must propagate."""
    broker = _make_broker(
        request_raises=BitunixAPIError(
            30001, "some error", path="/api/v1/futures/tpsl/position/place_order"),
    )
    with pytest.raises(BitunixAPIError):
        await broker.place_position_tpsl(
            symbol="BTC/USDT.P", position_id="pos-123", sl_price=66273.0,
        )


@pytest.mark.asyncio
async def test_place_position_tpsl_idempotent_duplicate_ok():
    """Idempotent duplicate code (30042) must be swallowed, not raised."""
    broker = _make_broker()
    broker._request = AsyncMock(
        side_effect=BitunixAPIError(30042, "already used",
                                   path="/api/v1/futures/tpsl/position/place_order")
    )
    result = await broker.place_position_tpsl(
        symbol="BTC/USDT.P", position_id="pos-123", sl_price=66273.0,
    )
    assert result == ""


@pytest.mark.asyncio
async def test_place_position_tpsl_raises_in_stub_mode():
    """Stub mode (no creds) must raise NotImplementedError."""
    broker = BitunixBroker(api_key="", api_secret="")
    with pytest.raises(NotImplementedError):
        await broker.place_position_tpsl(
            symbol="BTC/USDT.P", position_id="pos-123", sl_price=66273.0,
        )


# ════════════════════════════════════════════════════════════════════════════════
# 3. _place_bracket_exits wired to place_tpsl_order
# ════════════════════════════════════════════════════════════════════════════════

class FakeTpslBroker:
    """Minimal broker mock supporting the new tpsl path."""
    def __init__(self, position_id="pos-xyz-789", fail_legs=(), sl_should_fail=False,
                 untracked_legs=()):
        self._position_id = position_id
        self.placed: list[dict] = []
        self.position_sl_placed: list[dict] = []
        self.fail_legs = set(fail_legs)
        self.untracked_legs = set(untracked_legs)
        self.sl_should_fail = sl_should_fail
        self._positions = []

    def _set_positions(self, positions):
        self._positions = positions

    async def get_pending_positions(self):
        return self._positions

    async def place_tpsl_order(self, *, symbol, position_id, tp_price, tp_qty,
                                tp_stop_type="MARK_PRICE", tp_order_type="LIMIT"):
        # Determine leg label from price matching the plan
        leg_label = None
        for leg_name, price in [("tp1", 65928.22), ("tp2", 65801.10), ("tp3", 65482.04)]:
            if abs(tp_price - price) < 0.01:
                leg_label = leg_name
                break
        if leg_label in self.fail_legs:
            raise RuntimeError(f"tpsl fail for {leg_label}")
        if leg_label in self.untracked_legs:
            # POST reached the venue but the id was uncaptured (parse miss).
            raise BitunixUntrackedTpslOrder(
                position_id=position_id, symbol=symbol,
                tp_price=tp_price, tp_qty=tp_qty, raw_response=[{"noId": "x"}],
            )
        self.placed.append({
            "symbol": symbol,
            "position_id": position_id,
            "tp_price": tp_price,
            "tp_qty": tp_qty,
            "tp_stop_type": tp_stop_type,
            "tp_order_type": tp_order_type,
            "leg": leg_label,
        })
        return f"venue-tpsl-{leg_label}"

    async def place_position_tpsl(self, *, symbol, position_id, sl_price,
                                  sl_stop_type="MARK_PRICE", sl_order_type="MARKET"):
        if self.sl_should_fail:
            raise RuntimeError("position sl place fail")
        self.position_sl_placed.append({
            "symbol": symbol,
            "position_id": position_id,
            "sl_price": sl_price,
            "sl_stop_type": sl_stop_type,
            "sl_order_type": sl_order_type,
        })
        return "venue-position-sl-001"


class FakeDataExec:
    def __init__(self, broker):
        self.brokers = {"bitunix_futures": broker}


def _entry_order(qty: float, side: str = "sell", oid: str = "ord-tpsl-1") -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side=side, qty=qty,
        order_type="market", id=oid,
        extra={"tp_plan": SHORT_TP_PLAN, "stop_price": 66273.0,
               "tp1_price": 65928.22, "entry_reference_price": 66047.0,
               "execution_mode": "live"},
    )


def _short_position(qty_abs: float, position_id: str = "pos-xyz-789") -> Position:
    return Position(
        account="bitunix-futures",
        symbol="BTCUSDT",
        qty=-qty_abs,
        avg_price=66047.0,
        opened_ts="2026-06-18T12:00:00",
        extra={
            "side": "SHORT",
            "positionId": position_id,
            "leverage": "10",
            "marginMode": "ISOLATED",
            "unrealizedPNL": "0",
            "liqPrice": "70000",
        },
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
async def test_bracket_places_three_legs_via_tpsl(tmp_path):
    """3-leg bracket uses place_tpsl_order, positionId threaded, audit shape preserved."""
    broker = FakeTpslBroker(position_id="pos-xyz-789")
    broker._set_positions([_short_position(0.0016, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    assert [p["leg"] for p in broker.placed] == ["tp1", "tp2", "tp3"]
    # positionId must be threaded to each leg
    assert all(p["position_id"] == "pos-xyz-789" for p in broker.placed)
    # broker receives correct tpsl params (not reduce-only limit params)
    assert all(p["tp_stop_type"] == "MARK_PRICE" for p in broker.placed)
    assert all(p["tp_order_type"] == "LIMIT" for p in broker.placed)

    # The managed auto-reducing Position SL is placed at the structural stop.
    assert len(broker.position_sl_placed) == 1
    sl = broker.position_sl_placed[0]
    assert sl["position_id"] == "pos-xyz-789"
    assert sl["sl_price"] == pytest.approx(66273.0)  # the B1 structural stop level
    assert sl["sl_stop_type"] == "MARK_PRICE"
    assert sl["sl_order_type"] == "MARKET"

    extra = _read_extra(url, order.id)
    assert set(extra["bracket_tp_order_ids"]) == {"tp1", "tp2", "tp3"}
    assert extra["bracket_entry_qty"] == pytest.approx(0.0016)
    assert extra["current_sl"] == pytest.approx(66273.0)
    assert extra["bracket_position_id"] == "pos-xyz-789"
    assert extra["bracket_position_sl_order_id"] == "venue-position-sl-001"

    audit = _last_kind(url, "bracket_placed")
    assert audit["legs_placed"] == 3
    assert audit["legs_planned"] == 3
    assert "tp1" in audit["tp_order_ids"]
    assert audit["position_sl_order_id"] == "venue-position-sl-001"


@pytest.mark.asyncio
async def test_bracket_degrades_to_one_leg_via_tpsl(tmp_path):
    """1-leg degrade still uses place_tpsl_order and emits correct audit."""
    broker = FakeTpslBroker(position_id="pos-xyz-789")
    broker._set_positions([_short_position(0.0004, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0004)  # < 2*min → 1-leg
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    assert [p["leg"] for p in broker.placed] == ["tp1"]
    assert broker.placed[0]["tp_qty"] == pytest.approx(0.0004)
    audit = _last_kind(url, "bracket_placed")
    assert "degrade" in (audit["degrade_note"] or "").lower()


@pytest.mark.asyncio
async def test_bracket_degrades_to_two_legs_via_tpsl(tmp_path):
    """2-leg degrade (tp1+tp3) still uses place_tpsl_order."""
    broker = FakeTpslBroker(position_id="pos-xyz-789")
    broker._set_positions([_short_position(0.0008, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0008)  # >= 2*min but < 4*min → 2-leg
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    assert len(broker.placed) == 2
    legs = [p["leg"] for p in broker.placed]
    assert "tp1" in legs
    assert "tp3" in legs
    audit = _last_kind(url, "bracket_placed")
    assert audit["legs_placed"] == 2


@pytest.mark.asyncio
async def test_bracket_skips_when_position_id_unresolved(tmp_path):
    """If positionId cannot be found, NO TP legs are placed (B1 guards), audit emitted."""
    broker = FakeTpslBroker(position_id="pos-xyz-789")
    # Return empty positions — positionId unresolvable
    broker._set_positions([])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    # No TP legs placed
    assert broker.placed == []
    # bracket_tp_skipped audit emitted with reason
    audit = _last_kind(url, "bracket_tp_skipped")
    assert audit is not None
    assert "positionId" in audit["reason"] or "unresolved" in audit["reason"]


@pytest.mark.asyncio
async def test_bracket_leg_failure_failsoft_tpsl(tmp_path):
    """A single leg failure must not prevent other legs from being placed."""
    broker = FakeTpslBroker(position_id="pos-xyz-789", fail_legs={"tp2"})
    broker._set_positions([_short_position(0.0016, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)  # must NOT raise

    placed_legs = [p["leg"] for p in broker.placed]
    assert "tp2" not in placed_legs  # failed
    assert "tp1" in placed_legs and "tp3" in placed_legs  # rest placed
    assert _last_kind(url, "bracket_tp_leg_failed")["leg"] == "tp2"
    extra = _read_extra(url, order.id)
    assert "tp2" not in extra["bracket_tp_order_ids"]
    assert set(extra["bracket_tp_order_ids"]) == {"tp1", "tp3"}


@pytest.mark.asyncio
async def test_bracket_leg_untracked_flagged_not_swallowed(tmp_path):
    """HARDENING: a leg whose POST reached the venue but whose id was uncaptured
    must be FLAGGED (bracket_tp_leg_untracked audit, with reconcile context), NOT
    silently recorded as an empty id or dropped — and the other legs + the
    Position SL still place (fail-soft, B1 + SL guard)."""
    broker = FakeTpslBroker(position_id="pos-xyz-789", untracked_legs={"tp2"})
    broker._set_positions([_short_position(0.0016, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)  # must NOT raise

    # tp2 flagged for reconciliation with enough to find the stray order
    untracked = _last_kind(url, "bracket_tp_leg_untracked")
    assert untracked is not None
    assert untracked["leg"] == "tp2"
    assert untracked["position_id"] == "pos-xyz-789"
    assert untracked["qty"] == pytest.approx(0.0008)  # tp2 = 0.50 of 0.0016

    # tp2 is NOT recorded as a tracked leg (no empty-string swallow)
    extra = _read_extra(url, order.id)
    assert "tp2" not in extra["bracket_tp_order_ids"]
    assert set(extra["bracket_tp_order_ids"]) == {"tp1", "tp3"}

    audit = _last_kind(url, "bracket_placed")
    assert audit["legs_placed"] == 2          # only the 2 truly-tracked legs
    assert audit["legs_planned"] == 3
    assert "tp2" not in audit["tp_order_ids"]
    # fail-soft: the managed Position SL still placed
    assert audit["position_sl_order_id"] == "venue-position-sl-001"


@pytest.mark.asyncio
async def test_bracket_places_position_sl_failsoft_on_failure(tmp_path):
    """If the managed Position SL placement fails, the TP legs still rest and a
    bracket_position_sl_failed audit is emitted (B1 entry stop still guards)."""
    broker = FakeTpslBroker(position_id="pos-xyz-789", sl_should_fail=True)
    broker._set_positions([_short_position(0.0016, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)  # must NOT raise

    # TP legs all placed despite the SL failure
    assert [p["leg"] for p in broker.placed] == ["tp1", "tp2", "tp3"]
    assert broker.position_sl_placed == []
    # failure audit emitted, SL order id recorded empty
    sl_fail = _last_kind(url, "bracket_position_sl_failed")
    assert sl_fail is not None
    assert sl_fail["sl_price"] == pytest.approx(66273.0)
    extra = _read_extra(url, order.id)
    assert extra["bracket_position_sl_order_id"] == ""
    # bracket_placed still records the (empty) SL id
    assert _last_kind(url, "bracket_placed")["position_sl_order_id"] == ""


@pytest.mark.asyncio
async def test_bracket_skips_position_sl_when_no_structural_stop(tmp_path):
    """With no structural stop_price, the managed Position SL is NOT placed (the B1
    entry stop still guards); the TP legs are unaffected."""
    broker = FakeTpslBroker(position_id="pos-xyz-789")
    broker._set_positions([_short_position(0.0016, "pos-xyz-789")])
    obs, url = _observer(tmp_path, broker)
    order = _entry_order(0.0016)
    order.extra = dict(order.extra)
    order.extra.pop("stop_price", None)  # no structural stop
    rec = await _persist_entry(url, order)

    await obs._place_bracket_exits(order=order, record=rec)

    # No managed Position SL placed; TP legs still placed
    assert broker.position_sl_placed == []
    assert [p["leg"] for p in broker.placed] == ["tp1", "tp2", "tp3"]
    extra = _read_extra(url, order.id)
    assert extra["bracket_position_sl_order_id"] == ""


# ════════════════════════════════════════════════════════════════════════════════
# 4. modify_position_sl — correct path + positionId mandatory
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_modify_position_sl_uses_correct_path():
    """modify_position_sl must POST to /api/v1/futures/tpsl/position/modify_order."""
    broker = _make_broker(request_returns={})
    result = await broker.modify_position_sl(
        "BTC/USDT.P", 66047.0, position_id="pos-abc-123",
    )
    assert result is True
    _args, kwargs = broker._request.await_args
    assert _args[0] == "POST"
    assert _args[1] == "/api/v1/futures/tpsl/position/modify_order"
    body = kwargs["body"]
    assert body["positionId"] == "pos-abc-123"
    assert body["slStopType"] == "MARK_PRICE"
    assert body["slOrderType"] == "MARKET"


@pytest.mark.asyncio
async def test_modify_position_sl_returns_false_when_no_position_id():
    """If positionId is absent (None or empty string), must return False without
    calling the venue (never fire without positionId)."""
    broker = _make_broker(request_returns={})
    result_none = await broker.modify_position_sl("BTC/USDT.P", 66047.0, position_id=None)
    result_empty = await broker.modify_position_sl("BTC/USDT.P", 66047.0, position_id="")
    assert result_none is False
    assert result_empty is False
    # No _request call should have been made
    broker._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_modify_position_sl_failsoft_on_error():
    """Any _request exception must return False, not propagate."""
    broker = _make_broker(request_raises=RuntimeError("network error"))
    result = await broker.modify_position_sl(
        "BTC/USDT.P", 66047.0, position_id="pos-abc-123",
    )
    assert result is False


# ── move_bracket_sls threads positionId from broker Position.extra ────────────

class FakePosBrokerWithId:
    def __init__(self, positions, modify_ok=True):
        self._positions = positions
        self.modify_calls: list[tuple] = []
        self._ok = modify_ok

    async def get_pending_positions(self):
        return self._positions

    async def modify_position_sl(self, symbol, new_sl, **kw):
        position_id = kw.get("position_id")
        self.modify_calls.append((symbol, new_sl, position_id))
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


@pytest.mark.asyncio
async def test_move_bracket_sls_threads_position_id(tmp_path):
    """move_bracket_sls must pass positionId from broker position to modify_position_sl."""
    url = f"sqlite:///{tmp_path / 'slm_pid.db'}"
    init_db(url)
    _insert_bracket_row(url, oid="o-pid-1", side="sell", entry_qty=0.0016,
                        current_sl=66273.0, entry=66047.0, tp1=65928.22)
    pos = Position(
        account="bitunix-futures", symbol="BTCUSDT", qty=-0.0012,
        avg_price=66047.0, opened_ts="2026-06-18T12:00:00",
        extra={"side": "SHORT", "positionId": "pos-move-abc"},
    )
    broker = FakePosBrokerWithId(positions=[pos])

    await move_bracket_sls(broker, url)

    assert len(broker.modify_calls) == 1
    symbol, new_sl, position_id = broker.modify_calls[0]
    assert symbol == "BTC/USDT.P"
    assert new_sl == pytest.approx(66047.0)  # breakeven on 25% fill
    assert position_id == "pos-move-abc"


@pytest.mark.asyncio
async def test_move_bracket_sls_passes_none_position_id_when_absent(tmp_path):
    """When the broker Position.extra has no positionId, move_bracket_sls passes
    None — modify_position_sl will fail-soft internally (returns False without
    calling the venue). The reconciler should still emit the audit."""
    url = f"sqlite:///{tmp_path / 'slm_nopid.db'}"
    init_db(url)
    _insert_bracket_row(url, oid="o-nopid-1", side="sell", entry_qty=0.0016,
                        current_sl=66273.0, entry=66047.0, tp1=65928.22)
    pos = Position(
        account="bitunix-futures", symbol="BTCUSDT", qty=-0.0012,
        avg_price=66047.0, opened_ts="2026-06-18T12:00:00",
        extra={"side": "SHORT"},  # no positionId
    )
    broker = FakePosBrokerWithId(positions=[pos], modify_ok=False)

    await move_bracket_sls(broker, url)  # must NOT raise

    assert len(broker.modify_calls) == 1
    _, _, position_id = broker.modify_calls[0]
    assert position_id is None


# ════════════════════════════════════════════════════════════════════════════════
# 5. decide_sl_move integration unchanged (regression check)
# ════════════════════════════════════════════════════════════════════════════════

def test_decide_sl_move_tp1_fill_to_breakeven():
    from trading_corp.agents.divisions.bitunix_bracket import decide_sl_move
    new_sl, why = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66273.0,
        tp1_price=65928.22, entry_qty=0.0016, current_qty=0.0012,  # 25% closed
    )
    assert new_sl == pytest.approx(66047.0)
    assert "breakeven" in why.lower()


def test_decide_sl_move_tp1_tp2_fill_to_tp1():
    from trading_corp.agents.divisions.bitunix_bracket import decide_sl_move
    new_sl, why = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66047.0,
        tp1_price=65928.22, entry_qty=0.0016, current_qty=0.0004,  # 75% closed
    )
    assert new_sl == pytest.approx(65928.22)
    assert "tp1" in why.lower()


def test_decide_sl_move_no_move_when_full_position():
    from trading_corp.agents.divisions.bitunix_bracket import decide_sl_move
    new_sl, why = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66273.0,
        tp1_price=65928.22, entry_qty=0.0016, current_qty=0.0016,  # no fill
    )
    assert new_sl is None


def test_decide_sl_move_tighten_only_no_loosen_long():
    """A long's SL must only move UP; if target <= current it is suppressed."""
    from trading_corp.agents.divisions.bitunix_bracket import decide_sl_move
    new_sl, why = decide_sl_move(
        side="buy", entry_price=66047.0, current_sl=66200.0,  # already above BE
        tp1_price=65928.22, entry_qty=0.0016, current_qty=0.0012,
    )
    assert new_sl is None  # breakeven 66047 < current_sl 66200 → no loosen
