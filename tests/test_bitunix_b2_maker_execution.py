"""B2 — maker (POST_ONLY) entry execution (2026-06-15).

The entry is placed as a guaranteed-maker POST_ONLY limit (BitUnix rejects it if
it would cross — never accidental taker); on non-fill within a short rest timeout
OR a post-only rejection it CROSSES TO TAKER market (the signal is never silently
dropped). Behind a config flag, DEFAULT OFF (behavior-preserving). The B1
catastrophic stop is unaffected (stays MARK_PRICE + MARKET taker). #1's
signed-fetch auto-book reads the real per-fill fee, so a maker fill books at the
maker rate.

Covers: maker/taker clone construction; the maker order body (LIMIT + POST_ONLY
at offset) WITH the B1 stop intact; a maker fill (single place, maker fee);
non-fill → taker fallback; post-only rejection → taker fallback; cancel-FAILED →
NO fallback (double-fill guard); abandon mode → explicit raise; flag OFF → exact
current taker behavior; FeeConfig parsing default-OFF.

Mocked + fundless — no live API call.
"""
from __future__ import annotations

import json

import pytest

from trading_corp.brokers.bitunix import BitunixAPIError, BitunixBroker
from trading_corp.brokers.bitunix_exceptions import (
    BitunixMakerEntryUnfilled,
    BitunixStuckOrderCancelFailed,
    BitunixStuckOrderCancelled,
)
from trading_corp.agents.strategies.trade_plan import FeeConfig
from trading_corp.persistence.models import FillEvent, ProposedOrder

P_PENDING_POS = "/api/v1/futures/position/get_pending_positions"
P_POS_MODE = "/api/v1/futures/account/change_position_mode"
P_LEVERAGE = "/api/v1/futures/account/change_leverage"
P_PLACE = "/api/v1/futures/trade/place_order"
P_ORDER_DETAIL = "/api/v1/futures/trade/get_order_detail"
P_HISTORY = "/api/v1/futures/trade/get_history_trades"

API_KEY, API_SECRET = "k", "s"


# ── harness ───────────────────────────────────────────────────────────────


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class RecordingClient:
    def __init__(self):
        self.calls = []
        self._responses = {}

    def queue(self, path, *payloads):
        self._responses.setdefault(path, []).extend(payloads)

    def _resp(self, path):
        q = self._responses.get(path)
        if not q:
            return FakeResp({"code": 0, "msg": "ok", "data": {}})
        return FakeResp(q.pop(0) if len(q) > 1 else q[0])

    async def get(self, path, params=None, headers=None):
        self.calls.append({"method": "GET", "path": path, "content": None})
        return self._resp(path)

    async def post(self, path, content=None, headers=None):
        self.calls.append({"method": "POST", "path": path, "content": content})
        return self._resp(path)

    def posts_to(self, path):
        return [c for c in self.calls if c["method"] == "POST" and c["path"] == path]

    def body_of(self, path):
        posts = self.posts_to(path)
        assert posts, f"no POST to {path}"
        return json.loads(posts[-1]["content"])


def _broker():
    b = BitunixBroker(api_key=API_KEY, api_secret=API_SECRET)
    b._client = RecordingClient()
    b._fill_poll_interval_s = 0.0   # no real sleeps
    return b, b._client


def _maker_order(side="buy", *, ref=65000.0, offset=0.0005, mode="cross_to_taker",
                 stop=64000.0, qty=0.001):
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side=side, qty=qty,
        order_type="market",
        extra={
            "maker_entry": True, "maker_rest_timeout_s": 2.0,
            "maker_offset_pct": offset, "maker_fallback_mode": mode,
            "entry_reference_price": ref, "stop_price": stop, "leverage": 8,
        },
    )


def _queue_flat_fill(client, *, price="64967.5", fee="0.009", status="FILLED"):
    client.queue(P_PENDING_POS, {"code": 0, "data": []})
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": [{"leverage": 8}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OID1"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID1", "status": status,
                                      "tradeQty": "0.001"}})
    client.queue(P_HISTORY, {"code": 0, "data": {"tradeList": [
        {"qty": "0.001", "price": price, "fee": fee}]}})


def _fill(price=65010.0, fee=0.04):
    return FillEvent(order_id="x", symbol="BTC/USDT.P", side="buy", qty=0.001,
                     price=price, ts="2026-06-15T00:00:00+00:00",
                     venue="bitunix_futures", fee=fee)


# ── clone construction ──────────────────────────────────────────────────


def test_maker_clone_buy_is_post_only_limit_below_ref():
    b, _ = _broker()
    mk = b._maker_clone(_maker_order(side="buy", ref=65000.0, offset=0.0005))
    assert mk.order_type == "limit"
    assert mk.limit_price == pytest.approx(65000.0 * (1 - 0.0005))   # passive: below ref
    assert mk.extra["tif"] == "POST_ONLY"
    assert mk.extra["client_id_suffix"] == "-mk"
    assert "maker_entry" not in mk.extra                              # stripped (no recursion)


def test_maker_clone_sell_is_above_ref():
    b, _ = _broker()
    mk = b._maker_clone(_maker_order(side="sell", ref=65000.0, offset=0.0005))
    assert mk.limit_price == pytest.approx(65000.0 * (1 + 0.0005))   # passive: above ref


def test_maker_clone_no_reference_returns_none():
    b, _ = _broker()
    o = _maker_order()
    o.extra.pop("entry_reference_price")
    o.limit_price = None
    assert b._maker_clone(o) is None                                 # → caller falls back to taker


def test_taker_clone_is_market_with_distinct_suffix():
    b, _ = _broker()
    tk = b._taker_clone(_maker_order())
    assert tk.order_type == "market"
    assert tk.limit_price is None
    assert tk.extra["client_id_suffix"] == "-tk"                     # distinct from maker's -mk
    assert "maker_entry" not in tk.extra and "tif" not in tk.extra


# ── the maker order body: LIMIT + POST_ONLY, B1 stop intact ──────────────


def test_maker_body_is_limit_post_only_with_b1_stop_unchanged():
    b, _ = _broker()
    mk = b._maker_clone(_maker_order(side="sell", ref=65000.0, stop=66000.0))
    body = b._build_order_body(mk, "BTCUSDT", reduce_only=False)
    assert body["orderType"] == "LIMIT"
    assert body["effect"] == "POST_ONLY"            # the confirmed guaranteed-maker TIF
    assert "price" in body
    # B1 catastrophic stop attached to the entry, UNCHANGED by B2:
    assert body["slStopType"] == "MARK_PRICE"
    assert body["slOrderType"] == "MARKET"
    assert body["clientId"].endswith("-mk")


# ── real path: a maker entry that fills (single place, no fallback) ──────


@pytest.mark.asyncio
async def test_maker_fills_single_place_at_maker_fee():
    b, client = _broker()
    _queue_flat_fill(client, price="64967.5", fee="0.009")
    fill = await b.place_order(_maker_order(side="buy"))
    body = client.body_of(P_PLACE)
    assert body["orderType"] == "LIMIT" and body["effect"] == "POST_ONLY"
    assert len(client.posts_to(P_PLACE)) == 1                        # filled → NO taker fallback
    assert fill.price == pytest.approx(64967.5)
    assert fill.fee == pytest.approx(0.009)                          # maker fee booked (via #1's fee read)
    assert body["clientId"].endswith("-mk")


# ── fallback orchestration (isolated: fake place_order) ──────────────────


def _arm_fake_place(broker, behaviors):
    """Replace broker.place_order with a fake that runs `behaviors[i]` for the
    i-th call (an exception to raise, or a FillEvent to return). Records the
    (order, fill_timeout_s) of each call."""
    calls = []

    async def fake_place_order(order, *, fill_timeout_s=None):
        calls.append((order, fill_timeout_s))
        b = behaviors[len(calls) - 1]
        if isinstance(b, Exception):
            raise b
        return b

    broker.place_order = fake_place_order
    return calls


@pytest.mark.asyncio
async def test_nonfill_crosses_to_taker():
    b, _ = _broker()
    taker_fill = _fill()
    calls = _arm_fake_place(b, [
        BitunixStuckOrderCancelled(order_id="OID-mk", status="NEW"),  # maker rests, unfilled
        taker_fill,                                                   # taker fallback fills
    ])
    out = await b._place_maker_entry(_maker_order(side="buy"))
    assert out is taker_fill                                          # signal NOT dropped
    assert len(calls) == 2
    assert calls[0][0].order_type == "limit"                         # 1st = maker
    assert calls[0][1] == pytest.approx(2.0)                         # maker placed with the rest timeout
    assert calls[1][0].order_type == "market"                        # 2nd = taker fallback
    assert calls[1][0].extra["client_id_suffix"] == "-tk"


@pytest.mark.asyncio
async def test_post_only_rejection_crosses_to_taker():
    b, _ = _broker()
    taker_fill = _fill()
    calls = _arm_fake_place(b, [
        BitunixAPIError(30001, "post-only would cross", path=P_PLACE),  # simulated would-cross reject
        taker_fill,
    ])
    out = await b._place_maker_entry(_maker_order())
    assert out is taker_fill
    assert len(calls) == 2 and calls[1][0].order_type == "market"


@pytest.mark.asyncio
async def test_cancel_failed_does_not_cross_to_taker():
    """If the maker cancel FAILS the resting order may still fill — crossing
    would risk a DOUBLE position, so it must raise (no fallback)."""
    b, _ = _broker()
    calls = _arm_fake_place(b, [
        BitunixStuckOrderCancelFailed(order_id="OID-mk", status="NEW"),
    ])
    with pytest.raises(BitunixStuckOrderCancelFailed):
        await b._place_maker_entry(_maker_order())
    assert len(calls) == 1                                           # NO taker fallback attempted


@pytest.mark.asyncio
async def test_abandon_mode_raises_explicit_not_silent():
    b, _ = _broker()
    calls = _arm_fake_place(b, [
        BitunixStuckOrderCancelled(order_id="OID-mk", status="NEW"),
    ])
    with pytest.raises(BitunixMakerEntryUnfilled):
        await b._place_maker_entry(_maker_order(mode="abandon"))
    assert len(calls) == 1                                           # abandoned, not crossed — explicit


# ── flag OFF → exact current taker behavior (behavior-preserving) ────────


@pytest.mark.asyncio
async def test_flag_off_is_plain_taker_market_entry():
    b, client = _broker()
    _queue_flat_fill(client, price="65000", fee="0.04")
    order = ProposedOrder(strategy="bitunix_futures", symbol="BTC/USDT.P",
                          side="buy", qty=0.001, order_type="market",
                          extra={"leverage": 8, "stop_price": 64000.0})  # NO maker_entry
    await b.place_order(order)
    body = client.body_of(P_PLACE)
    assert body["orderType"] == "MARKET"                            # taker market, unchanged
    assert body["effect"] == "GTC"                                  # NOT POST_ONLY
    assert "-mk" not in body["clientId"] and "-tk" not in body["clientId"]
    # B1 stop still attached + taker on this plain entry too:
    assert body["slStopType"] == "MARK_PRICE" and body["slOrderType"] == "MARKET"


# ── FeeConfig parsing: maker fields, default OFF ─────────────────────────


def test_feeconfig_maker_defaults_off():
    fc = FeeConfig.from_dict({})
    assert fc.maker_entry_enabled is False                          # DEFAULT OFF
    assert fc.maker_entry_fallback_mode == "cross_to_taker"


def test_feeconfig_parses_maker_block():
    fc = FeeConfig.from_dict({
        "maker_entry_enabled": True, "maker_entry_rest_timeout_s": 1.5,
        "maker_entry_offset_pct": 0.0003, "maker_entry_fallback_mode": "abandon",
    })
    assert fc.maker_entry_enabled is True
    assert fc.maker_entry_rest_timeout_s == pytest.approx(1.5)
    assert fc.maker_entry_offset_pct == pytest.approx(0.0003)
    assert fc.maker_entry_fallback_mode == "abandon"
