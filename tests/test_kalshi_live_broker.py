"""Unit tests for KalshiLiveBroker (K5·1) — fundless, no live Kalshi calls.

Covers the pure mapping/sizing helpers and the broker's place/confirm/cancel
behavior with a fake pykalshi portfolio (full / partial / zero fill, favorable
price improvement, get_fills fallback, exchange reject, reduce_only on exits,
idempotency key, connect preflight). pykalshi 1.0.6 is installed locally so the
real Action/Side/TimeInForce enums are exercised; only the network surface
(portfolio) is faked.
"""
from __future__ import annotations

import pytest

from trading_corp.brokers.base import Broker, ReadOnlyBroker
from trading_corp.brokers.kalshi_live import (
    KalshiLiveBroker,
    KalshiNoFill,
    OrderPlacementError,
    build_kalshi_order_params,
    ceiling_price,
    client_order_id,
    compute_fill_economics,
    round_to_cent,
    usd_to_contracts,
)
from trading_corp.persistence.models import ProposedOrder


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeBalance:
    def __init__(self, cents):
        self.balance = cents
        self.portfolio_value = 0
        self.updated_ts = 0


class FakeFill:
    def __init__(self, count_fp, *, yes=None, no=None, fee=0.0, is_taker=True):
        self.count_fp = count_fp
        self.yes_price_dollars = yes
        self.no_price_dollars = no
        self.fee_cost_dollars = fee
        self.is_taker = is_taker


class FakeOrder:
    def __init__(self, order_id="ORD1", fill_count_fp="0", status=None):
        self.order_id = order_id
        self.fill_count_fp = fill_count_fp
        self.status = status

    async def wait_until_terminal(self, timeout=30.0, poll_interval=0.5):
        return self

    async def refresh(self):
        return self


class FakePortfolio:
    def __init__(self, *, order=None, fills=None, place_exc=None,
                 balance_cents=50000, cancel_exc=None, fills_exc=None):
        self._order = order
        self._fills = fills or []
        self._place_exc = place_exc
        self._balance_cents = balance_cents
        self._cancel_exc = cancel_exc
        self._fills_exc = fills_exc
        self.place_calls = []
        self.cancel_calls = []

    async def get_balance(self):
        return FakeBalance(self._balance_cents)

    async def place_order(self, ticker, action, side, count_fp, **kwargs):
        self.place_calls.append(
            {"ticker": ticker, "action": action, "side": side, "count_fp": count_fp, **kwargs}
        )
        if self._place_exc:
            raise self._place_exc
        return self._order

    async def get_fills(self, *, order_id=None, fetch_all=False):
        if self._fills_exc:
            raise self._fills_exc
        return self._fills

    async def cancel_order(self, order_id, **kwargs):
        self.cancel_calls.append(order_id)
        if self._cancel_exc:
            raise self._cancel_exc
        return None


class FakeClient:
    def __init__(self, portfolio):
        self.portfolio = portfolio


def _connected_broker(portfolio, *, order_type="ioc", slip=2):
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem",
                          order_type=order_type, max_slippage_cents=slip)
    lb._read._client = FakeClient(portfolio)
    lb._read._stub = False
    lb._connected = True
    return lb


def _order(*, side="buy", outcome="yes", ticker="KXBTC-T1", qty=2.0,
           limit_price=0.50, oid=None, **extra_over):
    extra = {
        "is_entry": side == "buy", "outcome": outcome, "ticker": ticker,
        "whale_handle": "alice", "division": "kalshi_copy_trading",
    }
    extra.update(extra_over)
    o = ProposedOrder(
        strategy="kalshi_copy_trader", symbol=f"{ticker}:{outcome}", side=side,
        qty=qty, order_type="market", limit_price=limit_price, extra=extra,
    )
    if oid is not None:
        o.id = oid
    return o


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_round_to_cent_clamps_band():
    assert round_to_cent(0.523) == 0.52
    assert round_to_cent(0.005) == 0.01   # rounds to 0c then clamps up to 1c
    assert round_to_cent(1.2) == 0.99     # clamp down to 99c
    assert round_to_cent(0.0) == 0.01


def test_ceiling_price_directional():
    assert ceiling_price(0.50, is_buy=True, max_slippage_cents=2) == 0.52   # buy pays up to
    assert ceiling_price(0.50, is_buy=False, max_slippage_cents=2) == 0.48  # sell accepts down to
    assert ceiling_price(0.99, is_buy=True, max_slippage_cents=2) == 0.99   # clamp
    assert ceiling_price(0.02, is_buy=False, max_slippage_cents=2) == 0.01  # clamp


def test_usd_to_contracts_floor_min1():
    assert usd_to_contracts(2.0, 0.50) == 4
    assert usd_to_contracts(0.40, 0.50) == 1   # floor 0 -> min 1
    assert usd_to_contracts(10.0, 0.33) == 30


def test_build_params_yes_entry():
    p = build_kalshi_order_params(
        ticker="kxbtc-t1", outcome="yes", is_buy=True, base_price=0.50,
        copy_usd=2.0, max_slippage_cents=2,
    )
    assert p["ticker"] == "KXBTC-T1"
    assert p["count_fp"] == "4"
    assert p["price_field"] == "yes_price_dollars"
    assert p["price_dollars"] == "0.52"
    assert p["price_float"] == 0.52


def test_build_params_no_exit():
    p = build_kalshi_order_params(
        ticker="KXBTC-T1", outcome="no", is_buy=False, base_price=0.40,
        copy_usd=2.0, max_slippage_cents=2,
    )
    assert p["price_field"] == "no_price_dollars"
    assert p["price_dollars"] == "0.38"
    assert p["count_fp"] == "5"   # floor(2 / 0.40)


def test_client_order_id_deterministic():
    a = client_order_id("div", "alice", "KXBTC-T1", "yes", "sig1")
    b = client_order_id("div", "alice", "KXBTC-T1", "yes", "sig1")
    c = client_order_id("div", "alice", "KXBTC-T2", "yes", "sig1")
    assert a == b
    assert a != c


def test_compute_fill_economics_vwap_and_fee():
    fills = [FakeFill("2.00", yes=0.45, fee=0.01), FakeFill("3.00", yes=0.50, fee=0.02)]
    avg, fee, role = compute_fill_economics(fills, outcome="yes", fallback_price=0.99)
    assert avg == pytest.approx(0.48)   # (2*.45 + 3*.50)/5
    assert fee == pytest.approx(0.03)
    assert role == "taker"


def test_compute_fill_economics_empty_falls_back():
    avg, fee, role = compute_fill_economics([], outcome="yes", fallback_price=0.52)
    assert avg == 0.52 and fee == 0.0 and role == "taker"


def test_compute_fill_economics_mixed_role():
    fills = [FakeFill("1.00", yes=0.5, is_taker=True), FakeFill("1.00", yes=0.5, is_taker=False)]
    _, _, role = compute_fill_economics(fills, outcome="yes", fallback_price=0.5)
    assert role == "mixed"


# ── place_order ──────────────────────────────────────────────────────────────


async def test_place_full_fill_builds_fillevent():
    pf = FakePortfolio(order=FakeOrder(order_id="O1", fill_count_fp="4"),
                       fills=[FakeFill("4.00", yes=0.49, fee=0.03)])
    lb = _connected_broker(pf)
    fill = await lb.place_order(_order())
    assert fill.venue == "kalshi"
    assert fill.qty == 4.0
    assert fill.price == pytest.approx(0.49)   # price improvement below 0.52 ceiling
    assert fill.fee == pytest.approx(0.03)
    assert fill.role == "taker"
    assert fill.side == "buy"
    assert fill.order_id == "O1"
    call = pf.place_calls[0]
    assert call["yes_price_dollars"] == "0.52"
    assert call["count_fp"] == "4"
    assert call["client_order_id"]
    assert "reduce_only" not in call            # entries never reduce_only
    assert str(call["time_in_force"].value) == "immediate_or_cancel"


async def test_place_partial_fill_records_actual_qty():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="2"),
                       fills=[FakeFill("2.00", yes=0.50)])
    lb = _connected_broker(pf)
    fill = await lb.place_order(_order())   # intended 4
    assert fill.qty == 2.0


async def test_place_zero_fill_raises_kalshi_nofill():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="0"))
    lb = _connected_broker(pf)
    with pytest.raises(KalshiNoFill):
        await lb.place_order(_order())


async def test_kalshi_nofill_is_orderplacementerror_subclass():
    assert issubclass(KalshiNoFill, OrderPlacementError)


async def test_place_favorable_move_fills_below_ceiling():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="2"),
                       fills=[FakeFill("2.00", yes=0.70)])
    lb = _connected_broker(pf)
    fill = await lb.place_order(_order(limit_price=0.75))   # ceiling 0.77
    assert fill.price == pytest.approx(0.70)


async def test_place_get_fills_error_falls_back_to_limit():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="4"),
                       fills_exc=RuntimeError("boom"))
    lb = _connected_broker(pf)
    fill = await lb.place_order(_order())
    assert fill.price == pytest.approx(0.52)   # ceiling fallback
    assert fill.fee == 0.0


async def test_place_exchange_reject_raises_orderplacementerror():
    from pykalshi.exceptions import KalshiError
    pf = FakePortfolio(place_exc=KalshiError("rejected"))
    lb = _connected_broker(pf)
    with pytest.raises(OrderPlacementError) as ei:
        await lb.place_order(_order())
    assert not isinstance(ei.value, KalshiNoFill)


async def test_exit_sets_reduce_only():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="4"),
                       fills=[FakeFill("4.00", no=0.40)])
    lb = _connected_broker(pf)
    await lb.place_order(_order(side="sell", outcome="no", limit_price=0.42))
    assert pf.place_calls[0]["reduce_only"] is True
    assert pf.place_calls[0]["no_price_dollars"] == "0.40"   # floor = 0.42 - 0.02


async def test_unresolvable_outcome_raises():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="1"))
    lb = _connected_broker(pf)
    bad = ProposedOrder(strategy="kalshi_copy_trader", symbol="NOCOLON",
                        side="buy", qty=1.0, limit_price=0.5, extra={})
    with pytest.raises(OrderPlacementError):
        await lb.place_order(bad)


async def test_base_price_from_quote_when_limit_none():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="3"),
                       fills=[FakeFill("3.00", yes=0.60)])
    lb = _connected_broker(pf)

    async def _quote(symbol):
        return 0.60

    lb._read.quote = _quote
    fill = await lb.place_order(_order(limit_price=None))
    assert fill.qty == 3.0
    assert pf.place_calls[0]["yes_price_dollars"] == "0.62"   # 0.60 + 0.02
    assert pf.place_calls[0]["count_fp"] == "3"               # floor(2/0.60)


async def test_idempotency_same_coid_for_same_signal():
    pf = FakePortfolio(order=FakeOrder(fill_count_fp="4"), fills=[FakeFill("4.00", yes=0.5)])
    lb = _connected_broker(pf)
    await lb.place_order(_order(oid="sig-X"))
    await lb.place_order(_order(oid="sig-X"))
    assert pf.place_calls[0]["client_order_id"] == pf.place_calls[1]["client_order_id"]


# ── cancel_order ─────────────────────────────────────────────────────────────


async def test_cancel_success_true():
    pf = FakePortfolio()
    lb = _connected_broker(pf)
    assert await lb.cancel_order("ORD1") is True
    assert pf.cancel_calls == ["ORD1"]


async def test_cancel_error_returns_false():
    pf = FakePortfolio(cancel_exc=RuntimeError("nope"))
    lb = _connected_broker(pf)
    assert await lb.cancel_order("ORD1") is False


async def test_cancel_empty_id_false_no_call():
    pf = FakePortfolio()
    lb = _connected_broker(pf)
    assert await lb.cancel_order("") is False
    assert pf.cancel_calls == []


# ── connect / preflight / ctor ───────────────────────────────────────────────


async def test_connect_stub_raises():
    lb = KalshiLiveBroker()   # no creds -> stub read adapter
    with pytest.raises(RuntimeError):
        await lb.connect()


async def test_connect_unfunded_raises():
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem")

    async def _noop():
        return None

    lb._read.connect = _noop
    lb._read._stub = False
    lb._read._client = FakeClient(FakePortfolio(balance_cents=0))
    with pytest.raises(RuntimeError):
        await lb.connect()
    assert lb._connected is False


async def test_connect_funded_ok():
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem")

    async def _noop():
        return None

    lb._read.connect = _noop
    lb._read._stub = False
    lb._read._client = FakeClient(FakePortfolio(balance_cents=50000))
    await lb.connect()
    assert lb._connected is True


def test_invalid_order_type_raises():
    with pytest.raises(ValueError):
        KalshiLiveBroker(api_key_id="k", private_key_pem="pem", order_type="xyz")


def test_is_placement_legal_broker():
    lb = KalshiLiveBroker(api_key_id="k", private_key_pem="pem")
    assert isinstance(lb, Broker)
    assert isinstance(lb, ReadOnlyBroker)
    assert lb.paper is False
    assert hasattr(lb, "place_order") and hasattr(lb, "cancel_order")
