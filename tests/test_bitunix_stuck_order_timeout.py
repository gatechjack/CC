"""Tests for the stuck-order timeout → cancel layer (gate (a) sub-item 3, 2026-05-30).

Exercises `BitunixBroker._observe_fill` + `_handle_stuck_order` against a
recording-mock httpx client. The matrix covers the four prompt-required
cases (happy path / PENDING-exhausted / PART_FILLED-exhausted / cancel-fails)
plus three defense-in-depth checks.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.brokers.bitunix_exceptions import (
    BitunixStuckOrderCancelFailed,
    BitunixStuckOrderCancelled,
)
from trading_corp.persistence.models import ProposedOrder


# Endpoint constants (mirror tests/test_bitunix_broker_write.py).
P_PENDING_POS = "/api/v1/futures/position/get_pending_positions"
P_POS_MODE = "/api/v1/futures/account/change_position_mode"
P_LEVERAGE = "/api/v1/futures/account/change_leverage"
P_PLACE = "/api/v1/futures/trade/place_order"
P_ORDER_DETAIL = "/api/v1/futures/trade/get_order_detail"
P_HISTORY = "/api/v1/futures/trade/get_history_trades"
P_CANCEL = "/api/v1/futures/trade/cancel_orders"


class FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200
    def json(self): return self._payload
    def raise_for_status(self): return None


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: dict[str, list[dict]] = {}

    def queue(self, path: str, *payloads: dict) -> None:
        self._responses.setdefault(path, []).extend(payloads)

    def _resp(self, path: str) -> FakeResp:
        q = self._responses.get(path)
        if not q:
            return FakeResp({"code": 0, "msg": "Success", "data": {}})
        payload = q.pop(0) if len(q) > 1 else q[0]
        return FakeResp(payload)

    async def get(self, path, params=None, headers=None):
        self.calls.append({"method": "GET", "path": path, "params": params,
                           "content": None, "headers": headers})
        return self._resp(path)

    async def post(self, path, content=None, headers=None):
        self.calls.append({"method": "POST", "path": path, "params": None,
                           "content": content, "headers": headers})
        return self._resp(path)

    def posts_to(self, path):
        return [c for c in self.calls if c["method"] == "POST" and c["path"] == path]

    def gets_to(self, path):
        return [c for c in self.calls if c["method"] == "GET" and c["path"] == path]


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []
    def log_event(self, *, actor, kind, payload):
        self.events.append({"actor": actor, "kind": kind, "payload": payload})
        return len(self.events)


class FakeSafetyNotifier:
    def __init__(self, return_value: bool = True) -> None:
        self.calls: list[dict] = []
        self.return_value = return_value
    async def push(self, text, *, audit_path="other", audit_context=None):
        self.calls.append({"text": text, "audit_path": audit_path,
                           "audit_context": audit_context or {}})
        return self.return_value


API_KEY = "key_stuck"
API_SECRET = "secret_stuck"


def _make_broker(*, with_logger=True, with_safety=True
                 ) -> tuple[BitunixBroker, RecordingClient,
                            FakeLogger | None, FakeSafetyNotifier | None]:
    logger = FakeLogger() if with_logger else None
    safety = FakeSafetyNotifier() if with_safety else None
    broker = BitunixBroker(
        api_key=API_KEY, api_secret=API_SECRET,
        logger=logger, safety_notifier=safety,
    )
    client = RecordingClient()
    broker._client = client  # type: ignore[assignment]
    broker._fill_poll_interval_s = 0.0  # no waits in tests
    broker._fill_max_polls = 3  # small budget so PENDING repeats are quick
    return broker, client, logger, safety


def _entry_order() -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P",
        side="buy", qty=0.001, order_type="market",
        extra={"leverage": 8},
    )


def _queue_flat_entry_until_status(client: RecordingClient,
                                   *, status: str,
                                   trade_qty: str = "0",
                                   final_status: str | None = None):
    """Queue responses so `place_order` reaches `_observe_fill` and polls
    return `status` repeatedly. `final_status` (if set) is what
    `get_order_detail` returns AFTER cancel (used for the trade-history
    fetch path)."""
    client.queue(P_PENDING_POS, {"code": 0, "data": []})  # flat
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": [{"leverage": 8}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OID1"}})
    # Order detail repeats with non-terminal status for every poll.
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID1",
                                      "status": status,
                                      "tradeQty": trade_qty}})


# ---------------------------------------------------------------------------
# Required test 1 — Happy path: order fills within polls; no cancel attempt.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_terminal_status_skips_stuck_handling():
    broker, client, logger, safety = _make_broker()
    client.queue(P_PENDING_POS, {"code": 0, "data": []})
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": [{"leverage": 8}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OID1"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID1", "status": "FILLED",
                                      "tradeQty": "0.001"}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": "0.001", "price": "65000", "fee": "0.02"}]}})
    # Set the broker fresh so the staleness gate (sub-item 2) doesn't block
    # place_order. Tests for the snapshot gate live in their own file.
    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    fill = await broker.place_order(_entry_order())
    assert fill.venue == "bitunix_futures"
    # No cancel POST, no stuck audit, no telegram.
    assert not client.posts_to(P_CANCEL)
    assert all(e["kind"] not in ("stuck_order_cancelled",
                                 "stuck_order_cancel_failed")
               for e in logger.events)
    assert safety.calls == []


# ---------------------------------------------------------------------------
# Required test 2 — PENDING through all polls → cancel + audit + telegram + raise.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_exhausted_cancel_succeeds_and_raises():
    broker, client, logger, safety = _make_broker()
    _queue_flat_entry_until_status(client, status="NEW", trade_qty="0")
    # cancel_order GETs the order detail again to learn the symbol, then POSTs.
    # The repeating detail (non-terminal NEW) is fine — symbol present.
    client.queue(P_CANCEL,
                 {"code": 0, "data": {"successList": [{"orderId": "OID1"}],
                                      "failureList": []}})

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    with pytest.raises(BitunixStuckOrderCancelled) as ei:
        await broker.place_order(_entry_order())
    assert ei.value.order_id == "OID1"
    assert ei.value.status == "NEW"

    # Cancel was attempted.
    assert len(client.posts_to(P_CANCEL)) == 1
    # Audit row written.
    audit_kinds = [e["kind"] for e in logger.events]
    assert "stuck_order_cancelled" in audit_kinds
    ev = [e for e in logger.events if e["kind"] == "stuck_order_cancelled"][0]
    assert ev["actor"] == "bitunix_broker"
    assert ev["payload"]["order_id"] == "OID1"
    assert ev["payload"]["status_at_exhaustion"] == "NEW"
    assert ev["payload"]["cancel_ok"] is True
    # Telegram pushed.
    assert len(safety.calls) == 1
    assert safety.calls[0]["audit_path"] == "safety_alert"
    assert "STUCK" in safety.calls[0]["text"]
    assert "No fills landed" in safety.calls[0]["text"]


# ---------------------------------------------------------------------------
# Required test 3 — PART_FILLED through polls → cancel + audit + telegram +
# return partial fill (no raise).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_part_filled_exhausted_cancel_succeeds_returns_partial():
    broker, client, logger, safety = _make_broker()
    _queue_flat_entry_until_status(client, status="PART_FILLED",
                                   trade_qty="0.0005")
    client.queue(P_CANCEL,
                 {"code": 0, "data": {"successList": [{"orderId": "OID1"}],
                                      "failureList": []}})
    # Trade history shows the partial fill that landed.
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": "0.0005", "price": "65000", "fee": "0.01"}]}})

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    fill = await broker.place_order(_entry_order())

    # Partial fill returned (qty < order.qty).
    assert fill.qty == pytest.approx(0.0005)
    assert fill.price == pytest.approx(65000.0)
    # Venue suffix encodes part-filled state.
    assert fill.venue == "bitunix_futures:part_filled"
    # Cancel was attempted + audited.
    assert len(client.posts_to(P_CANCEL)) == 1
    audit_kinds = [e["kind"] for e in logger.events]
    assert "stuck_order_cancelled" in audit_kinds
    ev = [e for e in logger.events if e["kind"] == "stuck_order_cancelled"][0]
    assert ev["payload"]["status_at_exhaustion"] == "PART_FILLED"
    # Telegram pushed; text reflects partial fill kept.
    assert len(safety.calls) == 1
    assert "Partial fill kept" in safety.calls[0]["text"]


# ---------------------------------------------------------------------------
# Required test 4 — Cancel itself fails → stuck_order_cancel_failed + raise.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_failure_audits_failed_and_raises_cancel_failed():
    broker, client, logger, safety = _make_broker()
    _queue_flat_entry_until_status(client, status="NEW", trade_qty="0")
    # cancel POST returns successList NOT containing OID1 → cancel_order returns False.
    client.queue(P_CANCEL,
                 {"code": 0, "data": {"successList": [],
                                      "failureList": [{"orderId": "OID1",
                                                       "errorMsg": "not found"}]}})

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    with pytest.raises(BitunixStuckOrderCancelFailed) as ei:
        await broker.place_order(_entry_order())
    assert ei.value.order_id == "OID1"
    assert ei.value.status == "NEW"

    # Audit row for cancel-FAILED kind.
    audit_kinds = [e["kind"] for e in logger.events]
    assert "stuck_order_cancel_failed" in audit_kinds
    assert "stuck_order_cancelled" not in audit_kinds  # not the success kind
    ev = [e for e in logger.events
          if e["kind"] == "stuck_order_cancel_failed"][0]
    assert ev["payload"]["cancel_ok"] is False
    # Escalated telegram pushed.
    assert len(safety.calls) == 1
    assert "CANCEL FAILED" in safety.calls[0]["text"]
    assert "Operator" in safety.calls[0]["text"]


# ---------------------------------------------------------------------------
# Defense-in-depth — cancel raises (network down) → cancel_failed + raise.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_raising_treated_as_failure():
    broker, client, logger, safety = _make_broker()
    _queue_flat_entry_until_status(client, status="NEW", trade_qty="0")

    # Force cancel_order to raise by replacing it.
    async def boom(order_id):
        raise RuntimeError("network down")
    broker.cancel_order = boom  # type: ignore[assignment]

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    with pytest.raises(BitunixStuckOrderCancelFailed):
        await broker.place_order(_entry_order())
    # Audit kind reflects failure.
    audit_kinds = [e["kind"] for e in logger.events]
    assert "stuck_order_cancel_failed" in audit_kinds


# ---------------------------------------------------------------------------
# Defense-in-depth — broker WITHOUT logger/safety still raises cleanly.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_logger_no_safety_still_raises():
    broker, client, _, _ = _make_broker(with_logger=False, with_safety=False)
    _queue_flat_entry_until_status(client, status="NEW", trade_qty="0")
    client.queue(P_CANCEL,
                 {"code": 0, "data": {"successList": [{"orderId": "OID1"}]}})

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    with pytest.raises(BitunixStuckOrderCancelled):
        await broker.place_order(_entry_order())
    # No crash from missing logger/safety — they were skipped silently.


# ---------------------------------------------------------------------------
# Defense-in-depth — INIT status (another non-terminal) also triggers cancel.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_status_triggers_cancel_and_raise():
    broker, client, logger, safety = _make_broker()
    _queue_flat_entry_until_status(client, status="INIT", trade_qty="0")
    client.queue(P_CANCEL,
                 {"code": 0, "data": {"successList": [{"orderId": "OID1"}]}})

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    with pytest.raises(BitunixStuckOrderCancelled) as ei:
        await broker.place_order(_entry_order())
    assert ei.value.status == "INIT"
    audit_kinds = [e["kind"] for e in logger.events]
    assert "stuck_order_cancelled" in audit_kinds


# ---------------------------------------------------------------------------
# Defense-in-depth — Logger raises during audit doesn't block the raise.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_failure_does_not_block_raise():
    broker, client, _, safety = _make_broker(with_logger=False, with_safety=True)
    _queue_flat_entry_until_status(client, status="NEW", trade_qty="0")
    client.queue(P_CANCEL,
                 {"code": 0, "data": {"successList": [{"orderId": "OID1"}]}})

    class RaisingLogger:
        def log_event(self, *, actor, kind, payload):
            raise RuntimeError("audit DB down")
    broker.logger = RaisingLogger()

    import time as _time
    broker._last_successful_snapshot_ts = _time.monotonic()

    with pytest.raises(BitunixStuckOrderCancelled):
        await broker.place_order(_entry_order())
    # Telegram still fired even with broken audit.
    assert len(safety.calls) == 1
