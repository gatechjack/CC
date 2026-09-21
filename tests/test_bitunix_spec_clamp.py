"""Venue instrument-spec clamping for the BitUnix broker (2026-09-21).

Root-cause fix for the recurring 10002 'Parameter error' that rejected EVERY
bitunix entry (both divisions) from ~2026-08-27: the venue tightened structural
validation and began rejecting wire bodies whose qty exceeded the pair
`basePrecision` or whose price (slPrice / tpPrice / limit) exceeded
`quotePrecision`. `_amount_str` emitted up to 8 dp and never quantized to the
pair step/tick. These tests pin: qty floored to the lot step (>= min), prices
rounded to the tick, `effect` (a LIMIT-only field) dropped from MARKET orders,
fail-open to raw formatting for unknown symbols, and the exact rejected order
now producing a spec-valid body.
"""
from __future__ import annotations

import json

import pytest

from trading_corp.brokers import bitunix as bx
from trading_corp.brokers.bitunix import (
    BitunixBroker,
    _INSTRUMENT_SPEC_FALLBACK,
    _quantize_price,
    _quantize_qty,
    classify_error,
)
from trading_corp.persistence.models import ProposedOrder

P_PLACE = "/api/v1/futures/trade/place_order"
P_PAIRS = "/api/v1/futures/market/trading_pairs"
P_PENDING_POS = "/api/v1/futures/position/get_pending_positions"
P_POS_MODE = "/api/v1/futures/account/change_position_mode"
P_LEVERAGE = "/api/v1/futures/account/change_leverage"
P_ORDER_DETAIL = "/api/v1/futures/trade/get_order_detail"
P_HISTORY = "/api/v1/futures/trade/get_history_trades"
P_TPSL = "/api/v1/futures/tpsl/place_order"
P_POS_TPSL = "/api/v1/futures/tpsl/position/place_order"
P_MODIFY_SL = "/api/v1/futures/tpsl/position/modify_order"


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

    def body_of(self, path):
        posts = [c for c in self.calls if c["method"] == "POST" and c["path"] == path]
        assert posts, f"no POST recorded to {path}"
        return json.loads(posts[-1]["content"])


BTC_PAIRS_RESP = {
    "code": 0, "msg": "Success",
    "data": [
        {"symbol": "BTCUSDT", "basePrecision": 4, "quotePrecision": 1,
         "minTradeVolume": "0.0001"},
        {"symbol": "SOLUSDT", "basePrecision": 2, "quotePrecision": 2,
         "minTradeVolume": "0.1"},
    ],
}


def _make_broker():
    b = BitunixBroker(api_key="k", api_secret="s")
    client = RecordingClient()
    b._client = client  # type: ignore[assignment]
    b._fill_poll_interval_s = 0.0
    return b, client


def _entry(qty, side="sell", stop=None, otype="market", limit=None):
    extra = {"leverage": 25}
    if stop is not None:
        extra["stop_price"] = stop
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side=side, qty=qty,
        order_type=otype, limit_price=limit, extra=extra,
    )


def _queue_flat_fill(client, trade_qty="0.0002", price="80000", fee="0.02"):
    client.queue(P_PAIRS, BTC_PAIRS_RESP)
    client.queue(P_PENDING_POS, {"code": 0, "data": []})
    client.queue(P_POS_MODE, {"code": 0, "data": {"positionMode": "ONE_WAY"}})
    client.queue(P_LEVERAGE, {"code": 0, "data": [{"leverage": 25}]})
    client.queue(P_PLACE, {"code": 0, "data": {"orderId": "OID1"}})
    client.queue(P_ORDER_DETAIL,
                 {"code": 0, "data": {"orderId": "OID1", "status": "FILLED",
                                      "tradeQty": trade_qty}})
    client.queue(P_HISTORY,
                 {"code": 0, "data": {"tradeList": [
                     {"qty": trade_qty, "price": price, "fee": fee}]}})


# ── pure quantizers ────────────────────────────────────────────────────────

def test_quantize_qty_floors_to_step():
    # BTC base precision 4 -> step 0.0001. 0.00029086 floors to 0.0002.
    assert _quantize_qty(0.00029086347613353585, 4, 0.0001) == pytest.approx(0.0002)
    assert _quantize_qty(0.00014444, 4, 0.0001) == pytest.approx(0.0001)


def test_quantize_qty_never_rounds_up():
    # 0.00019999 must floor to 0.0001, never 0.0002 (never exceed intent).
    assert _quantize_qty(0.00019999, 4, 0.0001) == pytest.approx(0.0001)


def test_quantize_qty_raises_to_min():
    # SOL step 0.01, min 0.1: 0.09 floors to 0.09 then raised to the 0.1 min.
    assert _quantize_qty(0.09, 2, 0.1) == pytest.approx(0.1)


def test_quantize_qty_zero_and_invalid():
    assert _quantize_qty(0.0, 4, 0.0001) == 0.0
    assert _quantize_qty(-5.0, 4, 0.0001) == 0.0
    assert _quantize_qty("bad", 4, 0.0001) == 0.0


def test_quantize_price_rounds_to_tick():
    assert _quantize_price(81223.50744962254, 1) == pytest.approx(81223.5)
    assert _quantize_price(1.4227645, 4) == pytest.approx(1.4228)
    assert _quantize_price(104.69478, 2) == pytest.approx(104.69)


# ── formatting helpers (cache / fallback / fail-open) ───────────────────────

def test_fmt_uses_fallback_map_when_cache_cold():
    b, _ = _make_broker()
    assert not b._instrument_spec  # cold
    # BTCUSDT is in the hardcoded fallback -> clamps without a live fetch.
    assert b._fmt_qty("BTCUSDT", 0.00029086) == "0.0002"
    assert b._fmt_price("BTCUSDT", 81223.50744962) == "81223.5"


def test_fmt_unknown_symbol_fails_open_to_raw():
    b, _ = _make_broker()
    # Symbol absent from BOTH the live cache and the fallback map -> raw
    # _amount_str (today's behaviour, never blocks trading on a spec gap).
    raw = bx._amount_str(0.00029086347613353585)
    assert b._fmt_qty("DOGEUSDT", 0.00029086347613353585) == raw


def test_fmt_prefers_live_cache_over_fallback():
    b, _ = _make_broker()
    # A live spec with different precision must win over the fallback map.
    b._instrument_spec = {"BTCUSDT": (2, 0, 0.01)}
    assert b._fmt_qty("BTCUSDT", 0.12345) == "0.12"     # base 2
    assert b._fmt_price("BTCUSDT", 81223.57, ) == "81224"  # quote 0


@pytest.mark.asyncio
async def test_ensure_specs_populates_cache():
    b, client = _make_broker()
    client.queue(P_PAIRS, BTC_PAIRS_RESP)
    await b._ensure_instrument_specs()
    assert b._instrument_spec["BTCUSDT"] == (4, 1, 0.0001)
    assert b._instrument_spec["SOLUSDT"] == (2, 2, 0.1)


@pytest.mark.asyncio
async def test_ensure_specs_failsoft_keeps_fallback():
    b, client = _make_broker()
    client.queue(P_PAIRS, {"code": 10001, "msg": "boom"})
    await b._ensure_instrument_specs()  # must not raise
    assert not b._instrument_spec  # cache stays cold
    # fallback still clamps
    assert b._fmt_price("BTCUSDT", 81223.507, ) == "81223.5"


# ── _build_order_body: the clamp on the entry body ──────────────────────────

def test_entry_body_clamps_qty_and_slprice():
    b, _ = _make_broker()
    order = _entry(qty=0.00029086347613353585, side="sell", stop=81223.50744962254)
    body = b._build_order_body(order, "BTCUSDT", reduce_only=False)
    assert body["qty"] == "0.0002"          # floored to basePrecision 4
    assert body["slPrice"] == "81223.5"     # rounded to quotePrecision 1
    assert body["slStopType"] == "MARK_PRICE"
    assert body["slOrderType"] == "MARKET"


def test_entry_market_drops_effect_keeps_tradeside():
    b, _ = _make_broker()
    body = b._build_order_body(_entry(qty=0.0002), "BTCUSDT", reduce_only=False)
    assert "effect" not in body               # LIMIT-only -> dropped on MARKET
    assert body["tradeSide"] == "OPEN"        # tradeSide is REQUIRED (kept)
    assert body["reduceOnly"] is False


def test_entry_limit_keeps_effect_and_clamps_price():
    b, _ = _make_broker()
    order = _entry(qty=0.0002, otype="limit", limit=81223.57744)
    body = b._build_order_body(order, "BTCUSDT", reduce_only=False)
    assert body["effect"] == "GTC"            # LIMIT keeps TIF
    assert body["price"] == "81223.6"         # clamped to tick


def test_exit_market_clamps_qty_no_effect_no_tradeside():
    b, _ = _make_broker()
    order = ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side="buy",
        qty=0.00025999, order_type="market", extra={"reduce_only": True},
    )
    body = b._build_order_body(order, "BTCUSDT", reduce_only=True)
    assert body["qty"] == "0.0002"            # floored
    assert body["reduceOnly"] is True
    assert "tradeSide" not in body and "effect" not in body


# ── end-to-end: the exact rejected order now sends a spec-valid body ─────────

@pytest.mark.asyncio
async def test_regression_exact_reject_now_spec_valid():
    """The precise order that produced the live 10002 (order 2c5ab1a6):
    sell qty 0.00029086347613353585, slPrice 81223.50744962254 -> the wire
    body now carries qty '0.0002' (<=4dp) and slPrice '81223.5' (<=1dp)."""
    b, client = _make_broker()
    _queue_flat_fill(client)
    order = _entry(qty=0.00029086347613353585, side="sell", stop=81223.50744962254)
    await b.place_order(order)
    body = client.body_of(P_PLACE)
    assert body["qty"] == "0.0002"
    assert body["slPrice"] == "81223.5"
    assert "effect" not in body
    # every price/qty field is within the BTCUSDT precision (<=4 / <=1 dp)
    assert len(body["qty"].split(".")[1]) <= 4
    assert len(body["slPrice"].split(".")[1]) <= 1


# ── TP/SL placement + SL-move also clamp ────────────────────────────────────

@pytest.mark.asyncio
async def test_place_tpsl_clamps_price_and_qty():
    b, client = _make_broker()
    client.queue(P_PAIRS, BTC_PAIRS_RESP)
    client.queue(P_TPSL, {"code": 0, "data": [{"orderId": "TP1"}]})
    await b.place_tpsl_order(symbol="BTC/USDT.P", position_id="PID",
                             tp_price=80315.93137594365, tp_qty=0.00019999)
    body = client.body_of(P_TPSL)
    assert body["tpPrice"] == "80315.9"       # quote 1
    assert body["tpOrderPrice"] == "80315.9"
    assert body["tpQty"] == "0.0001"          # base 4 floor


@pytest.mark.asyncio
async def test_modify_position_sl_clamps_price():
    b, client = _make_broker()
    client.queue(P_PAIRS, BTC_PAIRS_RESP)
    client.queue(P_MODIFY_SL, {"code": 0, "data": {}})
    ok = await b.modify_position_sl("BTC/USDT.P", 81856.5612, position_id="PID")
    assert ok is True
    assert client.body_of(P_MODIFY_SL)["slPrice"] == "81856.6"


@pytest.mark.asyncio
async def test_place_position_tpsl_clamps_price():
    b, client = _make_broker()
    client.queue(P_PAIRS, BTC_PAIRS_RESP)
    client.queue(P_POS_TPSL, {"code": 0, "data": {"orderId": "SL1"}})
    await b.place_position_tpsl(symbol="BTC/USDT.P", position_id="PID",
                                sl_price=81347.67055000001)
    assert client.body_of(P_POS_TPSL)["slPrice"] == "81347.7"


# ── error-code taxonomy (observability: 10002 was 'UNKNOWN' pre-fix) ────────

def test_error_codes_now_classified():
    assert classify_error(10002)[0] == "PARAMETER_ERROR"
    assert classify_error(30031)[0] == "SL_WRONG_SIDE_MARK"
    assert classify_error(30005)[0] == "TRIGGER_TOO_CLOSE"
    assert classify_error(30022)[0] == "SL_WRONG_SIDE_MARK"


def test_btc_fallback_matches_venue_spec():
    # Guard the hardcoded fallback against the documented BTCUSDT spec.
    assert _INSTRUMENT_SPEC_FALLBACK["BTCUSDT"] == (4, 1, 0.0001)
