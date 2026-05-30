"""Tests for BitunixBroker Phase-4 write path (mocked REST, no network).

Covers the broker-write long pole built 2026-05-29 (Stage-1 #1, #2, #4): the
sign-what-you-send POST seam, the {code,msg,data} envelope + error taxonomy,
one-way position-mode guard, deterministic clientId idempotency (30042), fill
observation (poll order-detail + VWAP from trade-history, incl. partial fills),
and the cancel + kill-switch primitives.

Mocks-alone don't catch endpoint/payload drift (memory
`feedback_mocks_dont_catch_sdk_shape`); endpoint strings here were grounded
against the official BitUnix futures API docs (2026-05-29). Real wire
verification is the future live-smoke step — see the broker module's
VERIFY-ON-LIVE note for the `tradeSide`/`effect` open-payload caveat.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from trading_corp.brokers import bitunix as bx
from trading_corp.brokers.bitunix import (
    BitunixAPIError,
    BitunixBroker,
    BitunixPositionModeMismatch,
    classify_error,
)
from trading_corp.brokers.bitunix_symbols import (
    UnknownSymbolError,
    to_internal_format,
    to_wire_format,
)
from trading_corp.persistence.models import ProposedOrder

# Endpoint paths (must match the broker verbatim).
P_PENDING_POS = "/api/v1/futures/position/get_pending_positions"
P_POS_MODE = "/api/v1/futures/account/change_position_mode"
P_LEVERAGE = "/api/v1/futures/account/change_leverage"
P_PLACE = "/api/v1/futures/trade/place_order"
P_ORDER_DETAIL = "/api/v1/futures/trade/get_order_detail"
P_HISTORY = "/api/v1/futures/trade/get_history_trades"
P_CANCEL = "/api/v1/futures/trade/cancel_orders"
P_CANCEL_ALL = "/api/v1/futures/trade/cancel_all_orders"
P_FLASH_CLOSE = "/api/v1/futures/trade/flash_close_position"
P_CLOSE_ALL = "/api/v1/futures/trade/close_all_position"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class RecordingClient:
    """Stand-in for httpx.AsyncClient. Records every call (path, content,
    headers) and returns per-path queued envelopes (last one repeats, so
    poll loops keep getting the final state)."""

    def __init__(self):
        self.calls: list[dict] = []
        self._responses: dict[str, list[dict]] = {}

    def queue(self, path: str, *payloads: dict):
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

    # test helpers --------------------------------------------------------
    def posts_to(self, path):
        return [c for c in self.calls if c["method"] == "POST" and c["path"] == path]

    def gets_to(self, path):
        return [c for c in self.calls if c["method"] == "GET" and c["path"] == path]

    def body_of(self, path):
        """JSON-decode the body of the (last) POST to `path`."""
        posts = self.posts_to(path)
        assert posts, f"no POST recorded to {path}"
        return json.loads(posts[-1]["content"])


API_KEY = "key123"
API_SECRET = "secret456"


def _make_broker() -> tuple[BitunixBroker, RecordingClient]:
    broker = BitunixBroker(api_key=API_KEY, api_secret=API_SECRET)
    client = RecordingClient()
    broker._client = client  # type: ignore[assignment]
    broker._fill_poll_interval_s = 0.0  # no real sleeps in tests
    return broker, client


def _entry_order(qty: float = 0.001, side: str = "buy", leverage: int = 8) -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures",
        symbol="BTC/USDT.P",
        side=side,
        qty=qty,
        order_type="market",
        extra={"leverage": leverage},
    )


def _exit_order(qty: float = 0.001, side: str = "sell") -> ProposedOrder:
    return ProposedOrder(
        strategy="bitunix_futures",
        symbol="BTC/USDT.P",
        side=side,
        qty=qty,
        order_type="market",
        extra={"reduce_only": True},
    )


def _queue_flat_entry_fill(client: RecordingClient, *, status="FILLED",
                           trade_qty="0.001", price="65000", fee="0.02"):
    """Queue the happy-path responses for a flat entry that fills."""
    client.queue(P_PENDING_POS, {"code": 0, "data": []})  # flat
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": [{"leverage": 8}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OID1"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID1", "status": status,
                                      "tradeQty": trade_qty}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": trade_qty, "price": price, "fee": fee}]}})


# ---------------------------------------------------------------------------
# Symbol helper (strict map, fail-closed)
# ---------------------------------------------------------------------------

def test_symbol_to_wire():
    assert to_wire_format("BTC/USDT.P") == "BTCUSDT"


def test_symbol_to_internal():
    assert to_internal_format("BTCUSDT") == "BTC/USDT.P"


def test_symbol_roundtrip():
    assert to_internal_format(to_wire_format("BTC/USDT.P")) == "BTC/USDT.P"


def test_symbol_unknown_internal_raises():
    with pytest.raises(UnknownSymbolError, match="SOL/USDT.P"):
        to_wire_format("SOL/USDT.P")


def test_symbol_unknown_wire_raises():
    with pytest.raises(UnknownSymbolError):
        to_internal_format("ETHUSDT")


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

def test_classify_known_codes():
    assert classify_error(10007)[0] == "SIGN_ERROR"
    assert classify_error(30042)[0] == "CLIENT_ID_DUPLICATE"
    assert classify_error(20006)[0] == "LEVERAGE_LOCKED"


def test_classify_unknown_code():
    assert classify_error(99999) == ("UNKNOWN", "unrecognized BitUnix error code")


def test_api_error_retryable_flag():
    assert BitunixAPIError(10005, "rate").retryable is True
    assert BitunixAPIError(20003, "bal").retryable is False


@pytest.mark.asyncio
async def test_request_raises_on_nonzero_code():
    broker, client = _make_broker()
    client.queue(P_CLOSE_ALL, {"code": 20003, "msg": "no balance"})
    with pytest.raises(BitunixAPIError) as ei:
        await broker.close_all_position("BTC/USDT.P")
    assert ei.value.code == 20003
    assert ei.value.error_name == "INSUFFICIENT_BALANCE"


# ---------------------------------------------------------------------------
# sign-what-you-send
# ---------------------------------------------------------------------------

def _expected_sign(content: str, nonce: str, ts: str) -> str:
    digest = hashlib.sha256(
        (nonce + ts + API_KEY + "" + content).encode("utf-8")
    ).hexdigest()
    return hashlib.sha256((digest + API_SECRET).encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_sign_what_you_send_post_body():
    """The exact compact body string is what gets signed AND sent."""
    broker, client = _make_broker()
    _queue_flat_entry_fill(client)
    await broker.place_order(_entry_order())

    post = client.posts_to(P_PLACE)[-1]
    content = post["content"]
    headers = post["headers"]
    # Compact: no whitespace in the serialized body.
    assert " " not in content
    # Sent as raw content (RecordingClient.post only accepts `content`).
    assert content is not None
    # Signature was computed over EXACTLY the bytes we sent.
    assert headers["sign"] == _expected_sign(
        content, headers["nonce"], headers["timestamp"]
    )
    assert headers["api-key"] == API_KEY


# ---------------------------------------------------------------------------
# place_order — entry (flat, one-way)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_entry_payload_and_fill():
    broker, client = _make_broker()
    _queue_flat_entry_fill(client)
    order = _entry_order(qty=0.001, side="buy", leverage=8)

    fill = await broker.place_order(order)

    body = client.body_of(P_PLACE)
    assert body == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "orderType": "MARKET",
        "qty": "0.001",
        "tradeSide": "OPEN",
        "reduceOnly": False,
        "effect": "GTC",
        "clientId": f"tc-{order.id}",
    }
    # Fill parsed from history VWAP.
    assert fill.qty == pytest.approx(0.001)
    assert fill.price == pytest.approx(65000.0)
    assert fill.side == "buy"
    assert fill.venue == "bitunix_futures"
    assert fill.order_id == order.id


@pytest.mark.asyncio
async def test_place_entry_sets_mode_and_leverage_when_flat():
    broker, client = _make_broker()
    _queue_flat_entry_fill(client)
    await broker.place_order(_entry_order(leverage=8))

    # Flat → mode set+verified; leverage set.
    assert len(client.posts_to(P_POS_MODE)) == 1
    lev_body = client.body_of(P_LEVERAGE)
    assert lev_body == {"symbol": "BTCUSDT", "marginCoin": "USDT", "leverage": 8}


@pytest.mark.asyncio
async def test_clientid_is_deterministic():
    broker, _ = _make_broker()
    order = _entry_order()
    assert broker._client_id(order) == f"tc-{order.id}"


# ---------------------------------------------------------------------------
# place_order — exit (reduce-only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_exit_reduce_only_payload():
    broker, client = _make_broker()
    # Position open in ONE_WAY → mode read from position, no change_position_mode.
    client.queue(P_PENDING_POS,
                 {"code": 0, "data": [{"positionMode": "ONE_WAY", "side": "LONG"}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OID2"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID2", "status": "FILLED",
                                      "tradeQty": "0.001"}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": "0.001", "price": "64000", "fee": "0.02"}]}})

    order = _exit_order(qty=0.001, side="sell")
    fill = await broker.place_order(order)

    body = client.body_of(P_PLACE)
    assert body == {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "orderType": "MARKET",
        "qty": "0.001",
        "reduceOnly": True,
        "clientId": f"tc-{order.id}",
    }
    assert "tradeSide" not in body and "effect" not in body
    # Mode came from the open position → no change_position_mode write.
    assert client.posts_to(P_POS_MODE) == []
    assert fill.price == pytest.approx(64000.0)


# ---------------------------------------------------------------------------
# position-mode guard (fail closed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mismatch_open_position_hedge_refuses():
    broker, client = _make_broker()
    client.queue(P_PENDING_POS,
                 {"code": 0, "data": [{"positionMode": "HEDGE", "side": "LONG"}]})
    with pytest.raises(BitunixPositionModeMismatch) as ei:
        await broker.place_order(_entry_order())
    assert ei.value.current == "HEDGE"
    assert broker._halt_new_orders is True
    # No order was sent.
    assert client.posts_to(P_PLACE) == []


@pytest.mark.asyncio
async def test_mismatch_flat_set_returns_hedge_refuses():
    broker, client = _make_broker()
    client.queue(P_PENDING_POS, {"code": 0, "data": []})  # flat
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "HEDGE"}})
    with pytest.raises(BitunixPositionModeMismatch):
        await broker.place_order(_entry_order())
    assert client.posts_to(P_PLACE) == []


@pytest.mark.asyncio
async def test_halt_latch_blocks_subsequent_orders():
    broker, client = _make_broker()
    client.queue(P_PENDING_POS,
                 {"code": 0, "data": [{"positionMode": "HEDGE"}]})
    with pytest.raises(BitunixPositionModeMismatch):
        await broker.place_order(_entry_order())
    # Halted → next order refused with a halt error (not a mode probe).
    with pytest.raises(RuntimeError, match="halted"):
        await broker.place_order(_entry_order())
    broker.resume()
    assert broker._halt_new_orders is False


# ---------------------------------------------------------------------------
# clientId idempotency (30042)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_clientid_treated_as_success():
    broker, client = _make_broker()
    client.queue(P_PENDING_POS, {"code": 0, "data": []})
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": []})
    client.queue(P_PLACE, {"code": 30042, "msg": "duplicate clientId"})
    # 30042 → observe fill by clientId (orderId unknown), detail resolves it.
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID9", "status": "FILLED",
                                      "tradeQty": "0.001"}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": "0.001", "price": "65010", "fee": "0.02"}]}})

    order = _entry_order()
    fill = await broker.place_order(order)  # must NOT raise
    assert fill.price == pytest.approx(65010.0)
    # Fill was observed by clientId (orderId was unknown after the dup).
    detail_gets = client.gets_to(P_ORDER_DETAIL)
    assert any(g["params"].get("clientId") == f"tc-{order.id}" for g in detail_gets)


@pytest.mark.asyncio
async def test_nonidempotent_error_propagates():
    broker, client = _make_broker()
    client.queue(P_PENDING_POS, {"code": 0, "data": []})
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": []})
    client.queue(P_PLACE, {"code": 30001, "msg": "would liquidate"})
    with pytest.raises(BitunixAPIError) as ei:
        await broker.place_order(_entry_order())
    assert ei.value.error_name == "WOULD_LIQUIDATE"


# ---------------------------------------------------------------------------
# partial fill
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_fill_status_and_qty():
    broker, client = _make_broker()
    broker._fill_max_polls = 2  # never reaches terminal
    client.queue(P_PENDING_POS, {"code": 0, "data": []})
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": []})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OIDP"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OIDP", "status": "PART_FILLED",
                                      "tradeQty": "0.0006"}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": "0.0006", "price": "65000", "fee": "0.01"}]}})

    fill = await broker.place_order(_entry_order(qty=0.001))
    assert fill.qty == pytest.approx(0.0006)
    assert fill.venue == "bitunix_futures:part_filled"


# ---------------------------------------------------------------------------
# cancel + kill-switch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_order_success():
    broker, client = _make_broker()
    client.queue(P_ORDER_DETAIL, {"code": 0, "data": {"symbol": "BTCUSDT"}})
    client.queue(P_CANCEL, {"code": 0, "data": {"successList": [{"orderId": "OID1"}]}})
    assert await broker.cancel_order("OID1") is True
    body = client.body_of(P_CANCEL)
    assert body == {"orderList": [{"orderId": "OID1"}], "symbol": "BTCUSDT"}


@pytest.mark.asyncio
async def test_cancel_order_not_in_successlist_returns_false():
    broker, client = _make_broker()
    client.queue(P_ORDER_DETAIL, {"code": 0, "data": {"symbol": "BTCUSDT"}})
    client.queue(P_CANCEL, {"code": 0, "data": {"successList": [], "failureList": [
        {"orderId": "OID1", "errorCode": "30000"}]}})
    assert await broker.cancel_order("OID1") is False


@pytest.mark.asyncio
async def test_cancel_order_swallows_api_error():
    broker, client = _make_broker()
    client.queue(P_ORDER_DETAIL, {"code": 10007, "msg": "sign"})
    assert await broker.cancel_order("OID1") is False


@pytest.mark.asyncio
async def test_cancel_all_orders_body_symbol():
    broker, client = _make_broker()
    client.queue(P_CANCEL_ALL, {"code": 0, "data": {"successList": []}})
    await broker.cancel_all_orders("BTC/USDT.P")
    assert client.body_of(P_CANCEL_ALL) == {"symbol": "BTCUSDT"}


@pytest.mark.asyncio
async def test_cancel_all_orders_account_wide():
    broker, client = _make_broker()
    client.queue(P_CANCEL_ALL, {"code": 0, "data": {"successList": []}})
    await broker.cancel_all_orders()
    assert client.body_of(P_CANCEL_ALL) == {}


@pytest.mark.asyncio
async def test_flash_close_position_body():
    broker, client = _make_broker()
    client.queue(P_FLASH_CLOSE, {"code": 0, "data": {"positionId": "PID1"}})
    await broker.flash_close_position("PID1")
    assert client.body_of(P_FLASH_CLOSE) == {"positionId": "PID1"}


@pytest.mark.asyncio
async def test_close_all_position_body():
    broker, client = _make_broker()
    client.queue(P_CLOSE_ALL, {"code": 0, "data": ""})
    await broker.close_all_position("BTC/USDT.P")
    assert client.body_of(P_CLOSE_ALL) == {"symbol": "BTCUSDT"}


@pytest.mark.asyncio
async def test_flatten_halts_and_flattens():
    broker, client = _make_broker()
    client.queue(P_CANCEL_ALL, {"code": 0, "data": {"successList": []}})
    client.queue(P_CLOSE_ALL, {"code": 0, "data": ""})
    res = await broker.flatten("BTC/USDT.P")
    assert res["halted"] is True
    assert broker._halt_new_orders is True
    assert client.posts_to(P_CANCEL_ALL) and client.posts_to(P_CLOSE_ALL)
    # Halt latch blocks new orders after the kill switch.
    with pytest.raises(RuntimeError, match="halted"):
        await broker.place_order(_entry_order())


# ---------------------------------------------------------------------------
# leverage cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_leverage_cached_after_first_set():
    broker, client = _make_broker()
    client.queue(P_LEVERAGE, {"code": 0, "data": [{"leverage": 8}]})
    await broker._ensure_leverage("BTCUSDT", 8)
    await broker._ensure_leverage("BTCUSDT", 8)
    assert len(client.posts_to(P_LEVERAGE)) == 1


@pytest.mark.asyncio
async def test_leverage_20006_does_not_raise():
    broker, client = _make_broker()
    client.queue(P_LEVERAGE, {"code": 20006, "msg": "open orders"})
    await broker._ensure_leverage("BTCUSDT", 8)  # must not raise
    assert "BTCUSDT" not in broker._leverage_cache


# ---------------------------------------------------------------------------
# stub mode (no creds) — fails closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stub_place_order_raises():
    broker = BitunixBroker()  # no creds → stub
    with pytest.raises(NotImplementedError, match="STUB"):
        await broker.place_order(_entry_order())


@pytest.mark.asyncio
async def test_stub_cancel_order_returns_false():
    broker = BitunixBroker()
    assert await broker.cancel_order("X") is False
