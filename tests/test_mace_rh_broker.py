"""Phase-3 tests for mace/rh_broker.py — the neutral port over RobinhoodBroker.

Mocks the RobinhoodBroker (byte-untouched real-money adapter) and asserts the
translation each way: opening vs closing leg construction (incl. the fixed call
side), credit/debit direction, the combo-fill-timeout leg-0 extra, pending ->
non-terminal, reject -> propagate (fake-fill guard), resting GTC PT, order-status
mapping, and the settled-cash snapshot basis.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

import robin_stocks.robinhood as rs  # type: ignore

from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.robinhood import RobinhoodComboPending, RobinhoodOrderError
from trading_corp.mace import broker_port as bp
from trading_corp.mace.domain import CondorSpec
from trading_corp.mace.rh_broker import RobinhoodOptionsBroker
from trading_corp.persistence.models import FillEvent

SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)


class MockRHBroker:
    """Minimal async stand-in exposing exactly what rh_broker calls."""

    def __init__(self):
        self.option_level = "option_level_3"
        self._account_number = "116637293063"
        self._account_type = "joint_tenancy_with_ros"
        self.last_orders = None
        self.last_ref = None
        self.resting_orders = None
        self.resting_tif = None
        self.pml_raises = None       # set to an Exception to raise from place_multi_leg
        self.status_ret = {}
        self.positions = []
        self.snap = AccountSnapshot(account="a", equity=10000.0, buying_power=8000.0,
                                    cash=5000.0, positions=[], settled_cash=4200.0)
        self.cancelled = []

    async def place_multi_leg(self, orders, *, ref_id=None):
        self.last_orders = orders
        self.last_ref = ref_id
        if self.pml_raises is not None:
            raise self.pml_raises
        return [FillEvent(order_id="RH1", symbol="SPY", side=orders[0].side, qty=1.0,
                          price=0.0, ts="2026-08-10T19:45:00+00:00", venue="robinhood",
                          broker_order_id="RH1", account="https://api/accounts/116637293063/")]

    async def place_multi_leg_resting(self, orders, *, ref_id, time_in_force="gtc"):
        self.resting_orders = orders
        self.resting_tif = time_in_force
        self.last_ref = ref_id
        return "PT-RH-1"

    async def get_option_order_status(self, order_id):
        return dict(self.status_ret, id=order_id)

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True

    async def get_option_positions_detail(self):
        return list(self.positions)

    async def snapshot(self):
        return self.snap

    async def quote(self, symbol):
        return 610.0

    async def get_expiration_dates(self, symbol):
        return ["2026-09-18"]

    async def get_calls_for_expiry(self, symbol, expiry):
        return [{"strike_price": 615.0, "bid": 1.08, "ask": 1.12, "delta": 0.20,
                 "option_id": "C615"},
                {"strike_price": 618.0, "bid": 0.48, "ask": 0.52, "delta": 0.12,
                 "option_id": "C618"}]

    async def get_puts_for_expiry(self, symbol, expiry):
        return [{"strike_price": 585.0, "bid": 0.98, "ask": 1.02, "delta": -0.20,
                 "option_id": "P585"},
                {"strike_price": 582.0, "bid": 0.38, "ask": 0.42, "delta": -0.12,
                 "option_id": "P582"}]


def _port(broker):
    return RobinhoodOptionsBroker(broker, now_et_fn=lambda: __import__("datetime").datetime(
        2026, 8, 10, 15, 45))


def _legs_by_key(orders):
    return {(o.extra["option_type"], o.extra["strike"]): o for o in orders}


@pytest.mark.asyncio
async def test_place_condor_credit_uses_opening_legs():
    b = MockRHBroker(); port = _port(b)
    res = await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                  time_in_force="gfd", fill_timeout_s=60)
    assert res.is_filled and res.order_id == "RH1"
    legs = _legs_by_key(b.last_orders)
    # short put/call SELL, long put/call BUY, effect open
    assert legs[("put", 585.0)].side == "sell" and legs[("put", 585.0)].extra["position_effect"] == "open"
    assert legs[("put", 582.0)].side == "buy"
    assert legs[("call", 615.0)].side == "sell"
    assert legs[("call", 618.0)].side == "buy"
    assert all(o.extra["combo_direction"] == "credit" for o in b.last_orders)
    # combo-fill-timeout ONLY on leg 0 (the additive robinhood.py read site)
    assert b.last_orders[0].extra["combo_fill_timeout_s"] == 60.0
    assert "combo_fill_timeout_s" not in b.last_orders[1].extra
    assert b.last_ref == "mace-x-a1"


@pytest.mark.asyncio
async def test_place_condor_debit_uses_closing_legs_fixed_call_side():
    b = MockRHBroker(); port = _port(b)
    await port.place_condor(SPEC, 1, 2.04, "mace-x-x1", direction=bp.DIR_DEBIT,
                            time_in_force="gfd", fill_timeout_s=20)
    legs = _legs_by_key(b.last_orders)
    # flatten: buy back shorts, sell longs — call side must be buy/sell (the fix)
    assert legs[("put", 585.0)].side == "buy"
    assert legs[("put", 582.0)].side == "sell"
    assert legs[("call", 615.0)].side == "buy"     # buy back short call (was the bug)
    assert legs[("call", 618.0)].side == "sell"    # sell long call
    assert all(o.extra["position_effect"] == "close" for o in b.last_orders)
    assert all(o.extra["combo_direction"] == "debit" for o in b.last_orders)


@pytest.mark.asyncio
async def test_place_condor_pending_is_non_terminal():
    b = MockRHBroker(); port = _port(b)
    b.pml_raises = RobinhoodComboPending("still queued", order_id="RHPEND")
    res = await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                  time_in_force="gfd", fill_timeout_s=60)
    assert res.order_id == "RHPEND" and not res.is_terminal and not res.is_filled


@pytest.mark.asyncio
async def test_place_condor_reject_translates_to_mace_order_rejected():
    # 2026-08-20 P1.2: a HARD RobinhoodOrderError (no id -> nothing placed) is
    # translated to the NEUTRAL MaceOrderRejected so execution can clean the
    # anchor without importing trading_corp.brokers.*. Still never books.
    b = MockRHBroker(); port = _port(b)
    b.pml_raises = RobinhoodOrderError("compliance reject — no id")
    with pytest.raises(bp.MaceOrderRejected):
        await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                time_in_force="gfd", fill_timeout_s=60)


@pytest.mark.asyncio
async def test_place_condor_ambiguous_error_propagates_raw():
    # A NON-RobinhoodOrderError (network/timeout) is NOT a definitive reject —
    # it must propagate raw so execution's fake-fill guard RETAINS the anchor.
    b = MockRHBroker(); port = _port(b)
    b.pml_raises = TimeoutError("socket read timeout")
    with pytest.raises(TimeoutError):
        await port.place_condor(SPEC, 1, 1.18, "mace-x-a1", direction=bp.DIR_CREDIT,
                                time_in_force="gfd", fill_timeout_s=60)


@pytest.mark.asyncio
async def test_place_resting_close_uses_closing_legs_gtc():
    b = MockRHBroker(); port = _port(b)
    pt_id = await port.place_resting_close(SPEC, 1, 0.59, "mace-x-pt")
    assert pt_id == "PT-RH-1" and b.resting_tif == "gtc"
    legs = _legs_by_key(b.resting_orders)
    assert legs[("call", 615.0)].side == "buy" and legs[("call", 618.0)].side == "sell"
    assert all(o.extra["position_effect"] == "close" for o in b.resting_orders)
    # a resting order sets NO combo-fill-timeout (it rests, never polls)
    assert "combo_fill_timeout_s" not in b.resting_orders[0].extra


@pytest.mark.asyncio
async def test_order_status_maps_state_and_quantities():
    b = MockRHBroker(); port = _port(b)
    b.status_ret = {"state": "Filled", "processed_quantity": "1", "pending_quantity": "0",
                    "time_in_force": "gtc"}
    res = await port.order_status("RH1")
    assert res.state == "filled" and res.is_filled and res.processed_quantity == 1.0
    assert res.time_in_force == "gtc" and res.order_id == "RH1"


@pytest.mark.asyncio
async def test_snapshot_uses_settled_cash_basis():
    b = MockRHBroker(); port = _port(b)
    snap = await port.snapshot()
    assert snap.equity == 4200.0 and snap.cash == 5000.0   # equity = settled cash


@pytest.mark.asyncio
async def test_account_assertions_parses_option_level():
    b = MockRHBroker(); port = _port(b)
    info = await port.account_assertions()
    assert info.account_number == "116637293063" and info.option_level == 3 and info.margin


@pytest.mark.asyncio
async def test_open_positions_map_to_neutral(monkeypatch):
    b = MockRHBroker(); port = _port(b)
    b.positions = [{"chain_symbol": "spy", "option_type": "put", "strike_price": 585.0,
                    "expiration_date": "2026-09-18", "quantity": -1.0, "option_id": "P585"}]
    out = await port.open_positions()
    assert out[0].symbol == "SPY" and out[0].option_id == "P585" and out[0].quantity == -1.0


@pytest.mark.asyncio
async def test_open_orders_map_ref_id(monkeypatch):
    b = MockRHBroker(); port = _port(b)
    monkeypatch.setattr(rs.orders, "get_all_open_option_orders",
                        lambda acct=None: [{"id": "O1", "state": "queued",
                                            "time_in_force": "gtc",
                                            "ref_id": "mace-SPY-x-pt"}], raising=False)
    out = await port.open_orders()
    assert out[0].order_id == "O1" and out[0].ref_id == "mace-SPY-x-pt"


@pytest.mark.asyncio
async def test_leg_quote_and_chain_bind_strikes():
    b = MockRHBroker(); port = _port(b)
    q = await port.leg_quote("SPY", date(2026, 9, 18), "put", 585.0)
    assert q is not None and q.bid == 0.98 and q.ask == 1.02 and q.delta == -0.20
    chain = await port.chain("SPY")
    assert chain.spot == 610.0 and date(2026, 9, 18) in chain.expiries
    assert chain.listed(date(2026, 9, 18), "call", 615.0)


# ── resilient cancel (Checkpoint-0 cancel-path fix — 2026-08-10 capture) ──
# Reality model from the operator's devtools capture: a POST carrying the
# {"account_number": ...} body cancels (the fix, rung 0); a BODYLESS POST to the
# order's cancel_url 404s and does NOT cancel (the pre-fix path) unless a test opts
# a legacy fallback in; the constructed no-body endpoint cancels only if opted in.

CONSTRUCTED = "https://api.robinhood.com/options/orders/OID/cancel/"


class _OrderSim:
    """Models one RH option order + the endpoints rh_broker.cancel drives."""

    def __init__(self, *, state="confirmed", cancel_url=None, cancel_url_after=0,
                 body_cancels=True, cancel_url_cancels=False, constructed_cancels=False):
        self.state = state
        self._cancel_url = cancel_url
        self.cancel_url_after = cancel_url_after   # reads before cancel_url appears
        self.body_cancels = body_cancels           # the {"account_number"} body-POST works
        self.cancel_url_cancels = cancel_url_cancels  # legacy bodyless cancel_url works
        self.constructed_cancels = constructed_cancels
        self.reads = 0
        self.posts = []                            # [{url, payload, json}, ...]
        self.constructed_calls = 0

    def get_info(self, oid):
        self.reads += 1
        cu = self._cancel_url if self.reads > self.cancel_url_after else None
        return {"id": oid, "state": self.state, "cancel_url": cu}

    def request_post(self, url, payload=None, *a, **k):
        self.posts.append({"url": url, "payload": payload, "json": k.get("json")})
        has_body = isinstance(payload, dict) and "account_number" in payload
        if has_body and self.body_cancels:
            self.state = "cancelled"               # the FIX: account-context POST cancels
        elif (not has_body) and self.cancel_url_cancels:
            self.state = "cancelled"
        return {"id": "x", "state": self.state}    # a "200" — books NOTHING on its own

    def cancel_option_order(self, oid):
        self.constructed_calls += 1
        if self.constructed_cancels:
            self.state = "cancelled"
        return None

    @property
    def post_urls(self):
        return [p["url"] for p in self.posts]

    @property
    def body_posts(self):
        return [p for p in self.posts
                if isinstance(p["payload"], dict) and "account_number" in p["payload"]]


def _install(monkeypatch, sim):
    monkeypatch.setattr("robin_stocks.robinhood.orders.get_option_order_info",
                        sim.get_info, raising=False)
    monkeypatch.setattr("robin_stocks.robinhood.orders.cancel_option_order",
                        sim.cancel_option_order, raising=False)
    monkeypatch.setattr("robin_stocks.robinhood.helper.request_post",
                        sim.request_post, raising=False)


def _cancel_port(broker=None):
    return RobinhoodOptionsBroker(broker or MockRHBroker(), cancel_url_polls=3,
                                  cancel_url_poll_s=0.0, cancel_confirm_polls=3,
                                  cancel_confirm_poll_s=0.0)


@pytest.mark.asyncio
async def test_cancel_primary_posts_constructed_url_with_account_body(monkeypatch):
    """PRIMARY rung: the constructed cancel URL WITH the {"account_number"} json body
    (the captured web-app request). Cancels on the first rung; legacy rungs untouched."""
    sim = _OrderSim(body_cancels=True)
    _install(monkeypatch, sim)
    await _cancel_port().cancel("OID")
    assert len(sim.posts) == 1
    p = sim.posts[0]
    assert p["url"] == CONSTRUCTED                 # SAME url robin_stocks builds
    assert p["payload"] == {"account_number": "116637293063"}  # the required body
    assert p["json"] is True                       # Content-Type: application/json
    assert sim.constructed_calls == 0              # never reached the legacy fallbacks


@pytest.mark.asyncio
async def test_cancel_last_rung_records_primary(monkeypatch):
    sim = _OrderSim(body_cancels=True)
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert port._last_cancel_rung == "constructed_json_body"


@pytest.mark.asyncio
async def test_cancel_body_uses_bound_account_never_hardcoded(monkeypatch):
    """account_number is sourced from the broker's BOUND account — change the bound
    account and the body follows it (nothing hardcoded)."""
    broker = MockRHBroker()
    broker._account_number = "999888777"
    sim = _OrderSim(body_cancels=True)
    _install(monkeypatch, sim)
    await _cancel_port(broker).cancel("OID")
    assert sim.posts[0]["payload"] == {"account_number": "999888777"}


@pytest.mark.asyncio
async def test_cancel_fake_guard_absolute_even_on_primary_200(monkeypatch):
    """The body-POST returns a 200-shaped dict but the state read-back NEVER goes
    terminal -> the cancel is NOT believed; it falls through and RAISES. A 200 on the
    POST books nothing; only a terminal read-back confirms."""
    sim = _OrderSim(body_cancels=False, cancel_url=None, constructed_cancels=False)
    _install(monkeypatch, sim)
    port = _cancel_port()
    with pytest.raises(RuntimeError, match="NO terminal state"):
        await port.cancel("OID")
    assert sim.body_posts                          # the primary WAS issued
    assert port._last_cancel_rung != "constructed_json_body"   # but never confirmed


@pytest.mark.asyncio
async def test_cancel_falls_to_cancel_url_when_primary_fails(monkeypatch):
    """Fallback chain intact: primary tried first, then the legacy cancel_url rung."""
    sim = _OrderSim(body_cancels=False, cancel_url="https://api/cu/", cancel_url_cancels=True)
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.posts[0]["url"] == CONSTRUCTED       # primary first
    assert "https://api/cu/" in sim.post_urls       # then cancel_url
    assert port._last_cancel_rung == "cancel_url"


@pytest.mark.asyncio
async def test_cancel_falls_back_to_constructed_endpoint(monkeypatch):
    sim = _OrderSim(body_cancels=False, cancel_url=None, constructed_cancels=True)
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.constructed_calls == 1 and port._last_cancel_rung == "constructed"


@pytest.mark.asyncio
async def test_cancel_raises_loudly_when_all_paths_fail(monkeypatch):
    sim = _OrderSim(body_cancels=False, cancel_url=None, constructed_cancels=False)
    _install(monkeypatch, sim)
    port = _cancel_port()
    with pytest.raises(RuntimeError, match="NO terminal state"):
        await port.cancel("OID")


@pytest.mark.asyncio
async def test_cancel_noop_when_already_terminal(monkeypatch):
    sim = _OrderSim(state="cancelled")
    _install(monkeypatch, sim)
    port = _cancel_port()
    await port.cancel("OID")
    assert sim.posts == [] and sim.constructed_calls == 0
    assert port._last_cancel_rung == "already_terminal"


# ── strike_band_pct config surface + high-IV band widen (2026-08-18) ──────
# chain() clips to +/- strike_band_pct of spot. The hardcoded 0.15 dropped
# high-IV names' wings (GDX/XLE) -> no_wing / no_delta_strike; it is now config-
# driven (mace.yaml entry.strike_band_pct=0.25, passed by main.py). Pins:
# (a) the config value overrides the 0.15 default; (b) widening un-clips the wings
# so a high-IV condor builds; (c) genuinely-far strikes still clip; (e) SPY
# (already well inside any band) is unchanged.
from datetime import datetime as _dt
from pathlib import Path as _Path

from trading_corp.mace import strategy as _st
from trading_corp.mace.config import load_mace_config as _load_cfg

_ROOT = _Path(__file__).resolve().parents[1]
_CFG = _load_cfg(_ROOT / "config" / "mace.yaml",
                 exdiv_calendar_path=_ROOT / "config" / "ex_dividend_calendar.yaml")
_EXP = date(2026, 9, 18)          # 39 DTE from the mock's 2026-08-10 clock (in [30,45])


class _HighIvMock:
    """GDX-like chain (spot 88.95): the 20-delta short call 102 is ~14.7% OTM; its
    wings 103/104 are ~15.8/16.9% OTM (past +/-15%, inside +/-25%); 120 is ~35% OTM
    (past both). Put side symmetric (short 80, wings 78/79; 60 is ~33% OTM)."""
    async def quote(self, symbol): return 88.95
    async def get_expiration_dates(self, symbol): return ["2026-09-18"]
    async def get_calls_for_expiry(self, symbol, expiry):
        return [{"strike_price": 102.0, "bid": 1.50, "ask": 1.60, "delta": 0.20, "option_id": "C102"},
                {"strike_price": 103.0, "bid": 1.35, "ask": 1.45, "delta": 0.17, "option_id": "C103"},
                {"strike_price": 104.0, "bid": 1.20, "ask": 1.30, "delta": 0.15, "option_id": "C104"},
                {"strike_price": 120.0, "bid": 0.05, "ask": 0.15, "delta": 0.03, "option_id": "C120"}]
    async def get_puts_for_expiry(self, symbol, expiry):
        return [{"strike_price": 80.0, "bid": 1.65, "ask": 1.75, "delta": -0.20, "option_id": "P80"},
                {"strike_price": 79.0, "bid": 1.43, "ask": 1.53, "delta": -0.17, "option_id": "P79"},
                {"strike_price": 78.0, "bid": 1.19, "ask": 1.29, "delta": -0.15, "option_id": "P78"},
                {"strike_price": 60.0, "bid": 0.05, "ask": 0.15, "delta": -0.02, "option_id": "P60"}]


def _band_port(broker, band):
    return RobinhoodOptionsBroker(broker, strike_band_pct=band,
                                  now_et_fn=lambda: _dt(2026, 8, 10, 15, 45))


@pytest.mark.asyncio
async def test_strike_band_config_overrides_default():
    # (a) call 104 (~16.9% OTM) is excluded at the 0.15 default, INCLUDED at 0.25.
    m = _HighIvMock()
    ch15 = await _band_port(m, 0.15).chain("GDX")
    ch25 = await _band_port(m, 0.25).chain("GDX")
    assert not ch15.listed(_EXP, "call", 104.0)
    assert ch25.listed(_EXP, "call", 104.0)


@pytest.mark.asyncio
async def test_band_widen_unclips_wings_and_builds():
    # (b) at 0.15 the 103/104 call wings are clipped -> build_condor no_wing; at
    # 0.25 they're present -> a width-2 GDX condor builds (credit 0.76 >= 0.60 floor).
    m = _HighIvMock(); gdx = _CFG.symbols["GDX"]
    ch15 = await _band_port(m, 0.15).chain("GDX")
    ch25 = await _band_port(m, 0.25).chain("GDX")
    b15 = _st.build_condor("GDX", gdx, ch15, _CFG, date(2026, 8, 10))
    b25 = _st.build_condor("GDX", gdx, ch25, _CFG, date(2026, 8, 10))
    assert b15.skip_reason == _st.SKIP_NO_WING
    assert b25.skip_reason is None and b25.width == 2.0
    assert b25.spec.short_call == 102.0 and b25.spec.long_call == 104.0
    assert abs(b25.credit_mid - 0.76) < 1e-9


@pytest.mark.asyncio
async def test_band_still_clips_far_strikes():
    # (c) 120 call (~35% OTM) and 60 put stay excluded even at 0.25 (hi = 111.2).
    ch25 = await _band_port(_HighIvMock(), 0.25).chain("GDX")
    assert not ch25.listed(_EXP, "call", 120.0)
    assert not ch25.listed(_EXP, "put", 60.0)


@pytest.mark.asyncio
async def test_band_does_not_affect_spy():
    # (e) SPY strikes (585-618 vs spot 610, all <5% OTM) are inside any band ->
    # 0.15 and 0.25 produce the identical ChainView (behavior unchanged).
    m = MockRHBroker()
    ch15 = await _band_port(m, 0.15).chain("SPY")
    ch25 = await _band_port(m, 0.25).chain("SPY")
    assert set(ch15.quotes.keys()) == set(ch25.quotes.keys())
    assert ch15.listed(_EXP, "call", 615.0) and ch15.listed(_EXP, "put", 585.0)
