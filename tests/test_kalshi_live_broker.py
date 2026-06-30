"""Unit tests for KalshiLiveBroker (K5·1b — V2 event-order endpoint). Fundless.

Covers the grounded YES-centric bid/ask mapping (all 4 outcome×side cases — the
load-bearing, money-if-wrong part), the V2 request body shape, V2 response->FillEvent
parsing (full / partial / zero fill), error paths, cancel, connect preflight, and the
external-api host default. The pure helpers import without the SDK; place/cancel use a
fake signed client (the real exchange is never hit)."""
from __future__ import annotations

import pytest

from trading_corp.brokers.base import Broker, ReadOnlyBroker
import trading_corp.brokers.kalshi_live as kl
from trading_corp.brokers.kalshi_live import (
    KalshiLiveBroker,
    KalshiNoFill,
    OrderPlacementError,
    build_v2_event_order,
    client_order_id,
    fill_event_from_v2_response,
    round_to_cent,
    usd_to_contracts,
    v2_side_and_price,
)
from trading_corp.persistence.models import ProposedOrder


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeBalance:
    def __init__(self, cents):
        self.balance = cents
        self.portfolio_value = 0


class FakePortfolio:
    def __init__(self, balance_cents=50000):
        self._b = balance_cents

    async def get_balance(self):
        return FakeBalance(self._b)


class FakeClient:
    def __init__(self, *, post_resp=None, post_exc=None, delete_exc=None, balance_cents=50000):
        self._post_resp = post_resp if post_resp is not None else {}
        self._post_exc = post_exc
        self._delete_exc = delete_exc
        self.portfolio = FakePortfolio(balance_cents)
        self.posts = []
        self.deletes = []

    async def post(self, endpoint, data):
        self.posts.append((endpoint, data))
        if self._post_exc:
            raise self._post_exc
        return self._post_resp

    async def delete(self, endpoint, body=None):
        self.deletes.append(endpoint)
        if self._delete_exc:
            raise self._delete_exc
        return {}


def _connected(client, *, order_type="ioc", slip=2):
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem", order_type=order_type, max_slippage_cents=slip)
    lb._read._client = client
    lb._read._stub = False
    lb._connected = True
    return lb


def _order(*, side="buy", outcome="yes", ticker="KXBTC-T1", qty=2.0, limit_price=0.50, oid=None):
    o = ProposedOrder(
        strategy="kalshi_copy_trader", symbol=f"{ticker}:{outcome}", side=side, qty=qty,
        order_type="market", limit_price=limit_price,
        extra={"is_entry": side == "buy", "outcome": outcome, "ticker": ticker,
               "whale_handle": "alice", "division": "kalshi_copy_trading"},
    )
    if oid is not None:
        o.id = oid
    return o


def _resp(fill="4.00", price="0.4900", fee="0.0100", oid="O1", remaining="0.00"):
    r = {"order_id": oid, "fill_count": fill, "remaining_count": remaining, "ts_ms": 1}
    if price is not None:
        r["average_fill_price"] = price
    if fee is not None:
        r["average_fee_paid"] = fee
    return r


# ── pure: sizing/price ───────────────────────────────────────────────────────


def test_round_to_cent_clamps_band():
    assert round_to_cent(0.523) == 0.52
    assert round_to_cent(0.005) == 0.01
    assert round_to_cent(1.2) == 0.99


def test_usd_to_contracts_floor_min1():
    assert usd_to_contracts(2.0, 0.50) == 4
    assert usd_to_contracts(0.40, 0.50) == 1
    assert usd_to_contracts(10.0, 0.33) == 30


def test_client_order_id_deterministic():
    assert client_order_id("d", "a", "T", "yes", "s1") == client_order_id("d", "a", "T", "yes", "s1")
    assert client_order_id("d", "a", "T", "yes", "s1") != client_order_id("d", "a", "T2", "yes", "s1")


# ── pure: the load-bearing V2 side/price mapping (all 4 cases) ────────────────


def test_v2_mapping_buy_yes():
    assert v2_side_and_price(outcome="yes", is_buy=True, base_price=0.50, max_slippage_cents=2) == ("bid", 0.52)


def test_v2_mapping_sell_yes_exit():
    assert v2_side_and_price(outcome="yes", is_buy=False, base_price=0.50, max_slippage_cents=2) == ("ask", 0.48)


def test_v2_mapping_buy_no_is_ask_at_complement():
    # buy NO @0.40 == sell YES @ (1-0.40) - slip = 0.58, side=ask
    assert v2_side_and_price(outcome="no", is_buy=True, base_price=0.40, max_slippage_cents=2) == ("ask", 0.58)


def test_v2_mapping_sell_no_is_bid_at_complement():
    # sell NO @0.40 == buy YES @ (1-0.40) + slip = 0.62, side=bid
    assert v2_side_and_price(outcome="no", is_buy=False, base_price=0.40, max_slippage_cents=2) == ("bid", 0.62)


def test_v2_mapping_clamps():
    assert v2_side_and_price(outcome="yes", is_buy=True, base_price=0.99, max_slippage_cents=2) == ("bid", 0.99)
    assert v2_side_and_price(outcome="yes", is_buy=False, base_price=0.02, max_slippage_cents=2) == ("ask", 0.01)


def test_v2_mapping_rejects_bad_price():
    with pytest.raises(ValueError):
        v2_side_and_price(outcome="yes", is_buy=True, base_price=0.0, max_slippage_cents=2)
    with pytest.raises(ValueError):
        v2_side_and_price(outcome="maybe", is_buy=True, base_price=0.5, max_slippage_cents=2)


# ── pure: V2 body shape ──────────────────────────────────────────────────────


def test_build_v2_body_buy_yes():
    body, count, price = build_v2_event_order(
        ticker="kxbtc-t1", outcome="yes", is_buy=True, base_price=0.50, copy_usd=2.0,
        max_slippage_cents=2, tif="immediate_or_cancel", client_order_id="cid",
    )
    assert body == {
        "ticker": "KXBTC-T1", "client_order_id": "cid", "side": "bid", "count": "4",
        "price": "0.5200", "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross", "post_only": False,
    }
    assert count == 4 and price == 0.52
    assert "reduce_only" not in body


def test_build_v2_body_exit_sets_reduce_only():
    body, count, price = build_v2_event_order(
        ticker="KXBTC-T1", outcome="no", is_buy=False, base_price=0.40, copy_usd=2.0,
        max_slippage_cents=2, tif="immediate_or_cancel", client_order_id="cid",
    )
    assert body["side"] == "bid" and body["price"] == "0.6200" and body["reduce_only"] is True
    assert body["count"] == "5"


# ── pure: V2 response -> FillEvent ───────────────────────────────────────────


def test_fill_event_full():
    fe = fill_event_from_v2_response(_resp(fill="4.00", price="0.4900", fee="0.0100"),
                                     symbol="S", side="buy", fallback_price=0.52, fallback_order_id="c")
    assert fe.qty == 4.0 and fe.price == pytest.approx(0.49) and fe.venue == "kalshi"
    assert fe.fee == pytest.approx(0.04) and fe.role == "taker" and fe.order_id == "O1"


def test_fill_event_zero_raises_nofill():
    with pytest.raises(KalshiNoFill):
        fill_event_from_v2_response(_resp(fill="0.00"), symbol="S", side="buy",
                                    fallback_price=0.52, fallback_order_id="c")


def test_fill_event_missing_avg_uses_fallback():
    fe = fill_event_from_v2_response(_resp(fill="2.00", price=None, fee=None),
                                     symbol="S", side="buy", fallback_price=0.52, fallback_order_id="c")
    assert fe.price == 0.52 and fe.fee == 0.0 and fe.qty == 2.0


def test_nofill_is_orderplacementerror_subclass():
    assert issubclass(KalshiNoFill, OrderPlacementError)


# ── place_order (fake signed client) ─────────────────────────────────────────


async def test_place_full_fill_posts_v2_and_builds_fillevent():
    c = FakeClient(post_resp=_resp(fill="4.00", price="0.4900", fee="0.0100"))
    lb = _connected(c)
    fe = await lb.place_order(_order())  # buy yes, limit 0.50 -> bid 0.52, count 4
    assert fe.qty == 4.0 and fe.price == pytest.approx(0.49) and fe.fee == pytest.approx(0.04)
    endpoint, body = c.posts[0]
    assert endpoint == kl._V2_ORDERS_PATH
    assert body["side"] == "bid" and body["price"] == "0.5200" and body["count"] == "4"
    assert body["time_in_force"] == "immediate_or_cancel"
    assert body["self_trade_prevention_type"] == "taker_at_cross"
    assert body["client_order_id"] and "reduce_only" not in body


async def test_place_partial_fill_records_actual():
    c = FakeClient(post_resp=_resp(fill="2.00"))
    lb = _connected(c)
    fe = await lb.place_order(_order())
    assert fe.qty == 2.0


async def test_place_zero_fill_raises_nofill():
    c = FakeClient(post_resp=_resp(fill="0.00", price=None, fee=None))
    lb = _connected(c)
    with pytest.raises(KalshiNoFill):
        await lb.place_order(_order())


async def test_place_favorable_uses_response_avg():
    c = FakeClient(post_resp=_resp(fill="2.00", price="0.7000"))
    lb = _connected(c)
    fe = await lb.place_order(_order(limit_price=0.75))  # bid 0.77 ceiling, fills 0.70
    assert fe.price == pytest.approx(0.70)


async def test_place_exit_sets_reduce_only_no_leg():
    c = FakeClient(post_resp=_resp(fill="5.00", price="0.6200"))
    lb = _connected(c)
    await lb.place_order(_order(side="sell", outcome="no", limit_price=0.40))
    body = c.posts[0][1]
    assert body["reduce_only"] is True and body["side"] == "bid" and body["price"] == "0.6200"


async def test_place_buy_no_is_ask():
    c = FakeClient(post_resp=_resp(fill="1.00"))
    lb = _connected(c)
    await lb.place_order(_order(outcome="no", limit_price=0.40, qty=0.50))
    body = c.posts[0][1]
    assert body["side"] == "ask" and body["price"] == "0.5800" and "reduce_only" not in body


async def test_place_exchange_reject_is_loud():
    from pykalshi.exceptions import KalshiError
    c = FakeClient(post_exc=KalshiError("bad request"))
    lb = _connected(c)
    with pytest.raises(OrderPlacementError) as ei:
        await lb.place_order(_order())
    assert not isinstance(ei.value, KalshiNoFill)


async def test_place_unresolvable_outcome_loud():
    c = FakeClient(post_resp=_resp())
    lb = _connected(c)
    bad = ProposedOrder(strategy="k", symbol="NOCOLON", side="buy", qty=1.0, limit_price=0.5, extra={})
    with pytest.raises(OrderPlacementError):
        await lb.place_order(bad)


async def test_place_base_price_from_quote():
    c = FakeClient(post_resp=_resp(fill="3.00"))
    lb = _connected(c)

    async def _q(symbol):
        return 0.60

    lb._read.quote = _q
    await lb.place_order(_order(limit_price=None))
    body = c.posts[0][1]
    assert body["price"] == "0.6200" and body["count"] == "3"  # bid 0.60+0.02, floor(2/0.60)


async def test_place_unpriceable_is_benign_nofill():
    c = FakeClient(post_resp=_resp())
    lb = _connected(c)

    async def _q(symbol):
        return 0.0

    lb._read.quote = _q
    with pytest.raises(KalshiNoFill):
        await lb.place_order(_order(limit_price=None))
    assert c.posts == []  # never posted


async def test_idempotency_same_coid():
    c = FakeClient(post_resp=_resp())
    lb = _connected(c)
    await lb.place_order(_order(oid="sig-X"))
    await lb.place_order(_order(oid="sig-X"))
    assert c.posts[0][1]["client_order_id"] == c.posts[1][1]["client_order_id"]


# ── cancel (V2 DELETE) ───────────────────────────────────────────────────────


async def test_cancel_success():
    c = FakeClient()
    lb = _connected(c)
    assert await lb.cancel_order("ORD1") is True
    assert c.deletes == [f"{kl._V2_ORDERS_PATH}/ORD1"]


async def test_cancel_error_false():
    c = FakeClient(delete_exc=RuntimeError("nope"))
    lb = _connected(c)
    assert await lb.cancel_order("ORD1") is False


async def test_cancel_empty_id_false():
    c = FakeClient()
    lb = _connected(c)
    assert await lb.cancel_order("") is False
    assert c.deletes == []


# ── connect / host / ctor ────────────────────────────────────────────────────


async def test_connect_stub_raises():
    lb = KalshiLiveBroker()
    with pytest.raises(RuntimeError):
        await lb.connect()


async def test_connect_unfunded_raises():
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem")

    async def _noop():
        return None

    lb._read.connect = _noop
    lb._read._stub = False
    lb._read._client = FakeClient(balance_cents=0)
    with pytest.raises(RuntimeError):
        await lb.connect()
    assert lb._connected is False


async def test_connect_funded_ok():
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem")

    async def _noop():
        return None

    lb._read.connect = _noop
    lb._read._stub = False
    lb._read._client = FakeClient(balance_cents=50000)
    await lb.connect()
    assert lb._connected is True


def test_host_defaults_external_api():
    assert KalshiLiveBroker(api_key_id="k", private_key_pem="pem", demo=False)._api_base == kl._PROD_API_BASE
    assert KalshiLiveBroker(api_key_id="k", private_key_pem="pem", demo=True)._api_base == kl._DEMO_API_BASE
    custom = KalshiLiveBroker(api_key_id="k", private_key_pem="pem", api_base="https://x/v2")
    assert custom._api_base == "https://x/v2"


def test_invalid_order_type_raises():
    with pytest.raises(ValueError):
        KalshiLiveBroker(api_key_id="k", private_key_pem="pem", order_type="xyz")


def test_is_placement_legal_broker():
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem")
    assert isinstance(lb, Broker) and isinstance(lb, ReadOnlyBroker)
    assert lb.paper is False and hasattr(lb, "place_order") and hasattr(lb, "cancel_order")
