"""Tests for RobinhoodBroker.place_multi_leg and .get_option_greeks.

`robin_stocks` is real and installed, but we never log in: we monkey-patch
the two functions the broker calls (`rs.orders.order_option_spread` and
`rs.options.get_option_market_data_by_id`) so the suite stays offline.

Combo cohesion + `is_multi_leg`-rejection are validated as documented in
planning/broker_multi_leg_interface_design.md.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import robin_stocks.robinhood as rs  # type: ignore

from trading_corp.brokers.base import validate_combo_cohesion
from trading_corp.brokers.robinhood import (
    RobinhoodBroker,
    RobinhoodComboPending,
    RobinhoodOrderError,
)
from trading_corp.persistence.models import ProposedOrder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_broker() -> RobinhoodBroker:
    """Construct a RobinhoodBroker without actually logging in.

    Bypasses connect() (which would call rs.login) by directly setting
    the connection-state private attrs.
    """
    b = RobinhoodBroker(
        username="x",
        password="y",
        account_filter="joint",
    )
    b._connected = True              # type: ignore[attr-defined]
    b._account_number = "ACCT-123"   # type: ignore[attr-defined]
    return b


def _make_ic_leg(
    *,
    combo_id: str = "combo-1",
    role: str,
    side: str,
    strike: float,
    option_type: str,
    effect: str = "open",
    qty: int = 1,
    net_limit: float = 1.20,
    direction: str = "credit",
    underlying: str = "SPY",
    expiration: str = "2026-06-19",
    limit_price: float = 0.50,
) -> ProposedOrder:
    return ProposedOrder(
        strategy="robinhood_joint_iron_condor",
        symbol=underlying,
        side=side,    # type: ignore[arg-type]
        qty=float(qty),
        order_type="limit",
        limit_price=limit_price,
        extra={
            "is_option": True,
            "is_multi_leg": True,
            "combo_id": combo_id,
            "combo_role": role,
            "combo_direction": direction,
            "net_limit_price": net_limit,
            "underlying": underlying,
            "expiration": expiration,
            "strike": strike,
            "option_type": option_type,
            "position_effect": effect,
            "ratio_quantity": 1,
        },
    )


def _standard_ic_legs(combo_id: str = "combo-1") -> list[ProposedOrder]:
    """4 legs of an SPY iron condor: short call, long call, short put, long put."""
    return [
        _make_ic_leg(combo_id=combo_id, role="short_put",
                     side="sell", option_type="put",  strike=430.0),
        _make_ic_leg(combo_id=combo_id, role="long_put",
                     side="buy",  option_type="put",  strike=427.0),
        _make_ic_leg(combo_id=combo_id, role="short_call",
                     side="sell", option_type="call", strike=470.0),
        _make_ic_leg(combo_id=combo_id, role="long_call",
                     side="buy",  option_type="call", strike=473.0),
    ]


# ---------------------------------------------------------------------------
# place_multi_leg — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_multi_leg_submits_one_combo_with_correct_shape():
    b = _make_broker()
    legs = _standard_ic_legs()

    captured: dict = {}

    def fake_spread(direction, price, symbol, quantity, spread,
                    account_number=None, timeInForce="gfd", **kwargs):
        captured.update(
            direction=direction, price=price, symbol=symbol,
            quantity=quantity, spread=spread,
            account_number=account_number, timeInForce=timeInForce,
        )
        return {
            "id": "RH-ORD-1",
            "state": "filled",
            "legs": [
                {"price": "0.55"},
                {"price": "0.20"},
                {"price": "0.60"},
                {"price": "0.15"},
            ],
        }

    with patch.object(rs.orders, "order_option_spread", new=fake_spread):
        fills = await b.place_multi_leg(legs)

    # robin_stocks call shape
    assert captured["direction"] == "credit"
    assert captured["price"] == 1.20
    assert captured["symbol"] == "SPY"
    assert captured["quantity"] == 1
    assert captured["account_number"] == "ACCT-123"
    assert captured["timeInForce"] == "gfd"
    assert len(captured["spread"]) == 4

    # Spot-check spread shape on one leg.
    sp0 = captured["spread"][0]
    assert sp0["expirationDate"] == "2026-06-19"
    assert sp0["strike"] == 430.0
    assert sp0["optionType"] == "put"
    assert sp0["effect"] == "open"
    assert sp0["action"] == "sell"
    assert sp0["ratio_quantity"] == 1

    # Per-leg fills with correct prices and shared metadata.
    assert len(fills) == 4
    assert [f.price for f in fills] == [0.55, 0.20, 0.60, 0.15]
    assert [f.side for f in fills] == ["sell", "buy", "sell", "buy"]
    assert all(f.venue == "robinhood" for f in fills)
    assert all(f.qty == 1.0 for f in fills)


# ---------------------------------------------------------------------------
# place_multi_leg — per-leg fill attribution by OPTION IDENTITY (2026-07-24)
# Robinhood returns spread legs in ITS OWN order; pairing by list index lands
# one leg's fill on another and sign-flips the combo net. Match by identity.
# ---------------------------------------------------------------------------


def _roll_short_legs(combo_id: str = "roll-1") -> list[ProposedOrder]:
    """2-leg PMCC roll_short: buy-to-close 74C (limit 0.03) + sell-to-open 75C
    (limit 1.25), net credit 1.14. Order list is [buy_close, sell_open]."""
    def _leg(side, effect, strike, exp, limit):
        return ProposedOrder(
            strategy="robinhood_pmcc",
            symbol="RKLB",
            side=side,  # type: ignore[arg-type]
            qty=1.0,
            order_type="limit",
            limit_price=limit,
            extra={
                "is_option": True, "is_multi_leg": True,
                "combo_id": combo_id, "combo_direction": "credit",
                "net_limit_price": 1.14, "underlying": "RKLB",
                "expiration": exp, "strike": strike, "option_type": "call",
                "position_effect": effect, "ratio_quantity": 1,
            },
        )
    return [
        _leg("buy", "close", 74.0, "2026-07-24", 0.03),
        _leg("sell", "open", 75.0, "2026-07-31", 1.25),
    ]


def _rh_filled(legs_in_response):
    return {"id": "RH-ORD", "state": "filled", "legs": legs_in_response}


@pytest.mark.asyncio
async def test_place_multi_leg_attributes_fills_by_identity_when_rh_reorders():
    """RH returns legs REVERSED (sell-leg data first) with identity fields.
    The buy leg must get 0.03 and the sell leg 1.20 — NOT the positional swap
    that would put 1.20 on the buy leg (the 2026-07-24 phantom-debit bug)."""
    b = _make_broker()
    legs = _roll_short_legs()
    reversed_resp = _rh_filled([
        {"option_type": "call", "expiration_date": "2026-07-31",
         "strike_price": "75.0000", "price": "1.20"},   # sell-leg fill, first
        {"option_type": "call", "expiration_date": "2026-07-24",
         "strike_price": "74.0000", "price": "0.03"},   # buy-leg fill, second
    ])
    with patch.object(rs.orders, "order_option_spread", return_value=reversed_resp):
        fills = await b.place_multi_leg(legs)

    by_side = {f.side: f.price for f in fills}
    assert by_side["buy"] == 0.03      # buy-to-close 74C, NOT 1.20
    assert by_side["sell"] == 1.20     # sell-to-open 75C, NOT 0.03
    # fills stay parallel to `orders`; net = sell - buy = +1.17 (a credit).
    assert fills[0].side == "buy" and fills[0].price == 0.03
    assert fills[1].side == "sell" and fills[1].price == 1.20
    assert fills[1].price - fills[0].price == pytest.approx(1.17)


@pytest.mark.asyncio
async def test_place_multi_leg_identity_match_is_order_independent():
    """Same attribution whether RH echoes legs in submitted or reversed order."""
    b = _make_broker()
    sell_leg = {"option_type": "call", "expiration_date": "2026-07-31",
                "strike_price": "75.0000", "price": "1.20"}
    buy_leg = {"option_type": "call", "expiration_date": "2026-07-24",
               "strike_price": "74.0000", "price": "0.03"}
    for resp_legs in ([buy_leg, sell_leg], [sell_leg, buy_leg]):
        with patch.object(rs.orders, "order_option_spread",
                          return_value=_rh_filled(resp_legs)):
            fills = await b.place_multi_leg(_roll_short_legs())
        by_side = {f.side: f.price for f in fills}
        assert by_side == {"buy": 0.03, "sell": 1.20}


@pytest.mark.asyncio
async def test_place_multi_leg_falls_back_to_positional_without_identity_fields():
    """When RH omits identity fields (strike/expiration/type), attribution
    falls back to positional index (legacy). Response legs [sell_price,
    buy_price] then map positionally to orders [buy, sell]."""
    b = _make_broker()
    resp = _rh_filled([{"price": "1.20"}, {"price": "0.03"}])  # no identity keys
    with patch.object(rs.orders, "order_option_spread", return_value=resp):
        fills = await b.place_multi_leg(_roll_short_legs())
    # Positional: orders[0]=buy gets legs[0]=1.20, orders[1]=sell gets legs[1]=0.03.
    assert fills[0].price == 1.20
    assert fills[1].price == 0.03


@pytest.mark.asyncio
async def test_place_multi_leg_returns_empty_for_empty_input():
    b = _make_broker()
    # Should not even call robin_stocks.
    with patch.object(rs.orders, "order_option_spread") as m:
        out = await b.place_multi_leg([])
    assert out == []
    m.assert_not_called()


# ---------------------------------------------------------------------------
# place_multi_leg — partial / malformed response handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_multi_leg_synthesizes_fill_prices_when_legs_missing():
    """If RH doesn't echo legs[], FillEvents use limit_price."""
    b = _make_broker()
    legs = _standard_ic_legs()

    with patch.object(rs.orders, "order_option_spread",
                      return_value={"id": "RH-ORD", "state": "filled"}):
        fills = await b.place_multi_leg(legs)

    assert len(fills) == 4
    # limit_price = 0.50 on every leg fixture.
    assert all(f.price == 0.50 for f in fills)


@pytest.mark.asyncio
async def test_place_multi_leg_handles_partial_legs_array():
    """RH returns fewer legs than we sent — remaining fills use limit_price."""
    b = _make_broker()
    legs = _standard_ic_legs()

    with patch.object(rs.orders, "order_option_spread",
                      return_value={"id": "RH-ORD", "state": "filled",
                                    "legs": [{"price": "0.55"}, {"price": "0.20"}]}):
        fills = await b.place_multi_leg(legs)

    assert len(fills) == 4
    assert fills[0].price == 0.55
    assert fills[1].price == 0.20
    assert fills[2].price == 0.50    # fallback to limit_price
    assert fills[3].price == 0.50


@pytest.mark.asyncio
async def test_place_multi_leg_handles_executions_shape():
    """Alt RH response shape: legs[].executions[0].price."""
    b = _make_broker()
    legs = _standard_ic_legs()
    rh_response = {
        "id": "RH-ORD",
        "state": "filled",
        "legs": [
            {"executions": [{"price": "0.57"}]},
            {"executions": [{"price": "0.22"}]},
            {"executions": [{"price": "0.58"}]},
            {"executions": [{"price": "0.18"}]},
        ],
    }
    with patch.object(rs.orders, "order_option_spread", return_value=rh_response):
        fills = await b.place_multi_leg(legs)
    assert [f.price for f in fills] == [0.57, 0.22, 0.58, 0.18]


@pytest.mark.asyncio
async def test_place_multi_leg_handles_malformed_price_strings():
    """Non-numeric or None per-leg prices fall back to limit_price."""
    b = _make_broker()
    legs = _standard_ic_legs()
    rh_response = {
        "id": "RH-ORD",
        "state": "filled",
        "legs": [
            {"price": "0.55"},
            {"price": None},
            {"price": "garbage"},
            {},                      # leg dict with no price + no executions
        ],
    }
    with patch.object(rs.orders, "order_option_spread", return_value=rh_response):
        fills = await b.place_multi_leg(legs)
    assert fills[0].price == 0.55
    assert fills[1].price == 0.50      # fallback
    assert fills[2].price == 0.50      # fallback
    assert fills[3].price == 0.50      # fallback


@pytest.mark.asyncio
async def test_place_multi_leg_handles_none_response():
    """RH returns None (no id) → FAIL CLOSED: raise, book nothing. Never
    synthesize a phantom fill for an order whose placement is unconfirmed —
    this IS the ambiguous 'did it place?' state on the hot combo path."""
    b = _make_broker()
    legs = _standard_ic_legs()
    # Session ALIVE (auth ok) → a None response is a genuine reject → RobinhoodOrderError.
    # (A None response while the session is DEAD takes the 401 reconcile path — see
    # test_robinhood_order_reconcile.py.)
    with patch.object(rs.orders, "order_option_spread", return_value=None), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=False)):
        with pytest.raises(RobinhoodOrderError):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_resting_confirmed_times_out_pending():
    """Submit acknowledged (`confirmed`) but never fills → RobinhoodComboPending;
    books NOTHING (no fills). This is today's failure mode, now gated."""
    b = _make_broker()
    b._COMBO_FILL_TIMEOUT_S = 0.05
    b._COMBO_FILL_POLL_S = 0.01
    legs = _standard_ic_legs()
    with patch.object(rs.orders, "order_option_spread",
                      return_value={"id": "RH-ORD", "state": "confirmed"}), \
         patch.object(rs.orders, "get_option_order_info",
                      return_value={"id": "RH-ORD", "state": "confirmed"}):
        with pytest.raises(RobinhoodComboPending):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_polls_confirmed_to_filled_then_books():
    """Submit `confirmed`, poll returns `filled` → book from the TERMINAL order."""
    b = _make_broker()
    b._COMBO_FILL_TIMEOUT_S = 1.0
    b._COMBO_FILL_POLL_S = 0.01
    legs = _standard_ic_legs()
    filled = {"id": "RH-ORD", "state": "filled",
              "legs": [{"price": "0.55"}, {"price": "0.20"},
                       {"price": "0.60"}, {"price": "0.15"}]}
    with patch.object(rs.orders, "order_option_spread",
                      return_value={"id": "RH-ORD", "state": "confirmed"}), \
         patch.object(rs.orders, "get_option_order_info", return_value=filled):
        fills = await b.place_multi_leg(legs)
    assert [f.price for f in fills] == [0.55, 0.20, 0.60, 0.15]
    assert all(f.broker_order_id == "RH-ORD" for f in fills)


@pytest.mark.asyncio
async def test_place_multi_leg_rejected_state_raises_books_nothing():
    """Terminal `rejected` → RobinhoodOrderError; book NOTHING."""
    b = _make_broker()
    legs = _standard_ic_legs()
    with patch.object(rs.orders, "order_option_spread",
                      return_value={"id": "RH-ORD", "state": "rejected"}):
        with pytest.raises(RobinhoodOrderError, match="rejected"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_partial_fill_raises_books_nothing():
    """Terminal `partially_filled` on an atomic spread → anomaly; raise, no book."""
    b = _make_broker()
    legs = _standard_ic_legs()
    with patch.object(rs.orders, "order_option_spread",
                      return_value={"id": "RH-ORD", "state": "partially_filled"}):
        with pytest.raises(RobinhoodOrderError, match="PARTIALLY_FILLED"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_propagates_network_failure():
    """Exception from rs.orders.order_option_spread bubbles up."""
    b = _make_broker()
    legs = _standard_ic_legs()

    def boom(*a, **kw):
        raise ConnectionError("robinhood unreachable")

    with patch.object(rs.orders, "order_option_spread", new=boom):
        with pytest.raises(ConnectionError, match="robinhood unreachable"):
            await b.place_multi_leg(legs)


# ---------------------------------------------------------------------------
# place_multi_leg — deterministic-ref_id payload parity (library-drift guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_spread_ref_id_payload_parity_with_library():
    """_submit_spread_with_ref_id forks order_option_spread's payload build — this
    guards against robin_stocks drift: the two POSTed payloads must be key-for-key
    identical except ref_id. Pins the version so a bump that changes the payload
    fails HERE, not in production."""
    import importlib.metadata as _md

    import robin_stocks.robinhood.helper as _H
    import robin_stocks.robinhood.orders as O
    assert _md.version("robin_stocks") == "3.4.0"

    b = _make_broker()
    legs = _standard_ic_legs()
    combo = validate_combo_cohesion(legs)
    spread = [
        {"expirationDate": (o.extra or {})["expiration"],
         "strike": float((o.extra or {})["strike"]),
         "optionType": (o.extra or {})["option_type"],
         "effect": (o.extra or {})["position_effect"],
         "action": o.side,
         "ratio_quantity": int((o.extra or {}).get("ratio_quantity", 1))}
        for o in legs
    ]

    captured: list[dict] = []

    def fake_post(url, payload, json=True, jsonify_data=True):
        captured.append(dict(payload))
        return {"id": "X", "state": "filled"}

    with patch.object(_H, "LOGGED_IN", True), \
         patch.object(O, "request_post", new=fake_post), \
         patch.object(O, "id_for_option", new=lambda *a, **k: "OPT"), \
         patch.object(O, "option_instruments_url", new=lambda i: f"iurl/{i}"), \
         patch.object(O, "load_account_profile", new=lambda **k: "acct/url"), \
         patch.object(O, "option_orders_url", new=lambda **k: "orders/url"):
        O.order_option_spread("credit", combo.net_limit, combo.underlying,
                              combo.quantity, spread, account_number="ACCT-123",
                              timeInForce="gfd")
        b._submit_spread_with_ref_id(spread, combo, "ACCT-123", "REF-123")

    lib_payload, our_payload = captured[0], captured[1]
    assert set(lib_payload) == set(our_payload), "payload key drift vs library"
    for k in lib_payload:
        if k == "ref_id":
            continue
        assert lib_payload[k] == our_payload[k], f"payload drift at {k!r}"
    assert our_payload["ref_id"] == "REF-123"          # ours: deterministic
    assert lib_payload["ref_id"] != "REF-123"          # library: its own uuid4


# ---------------------------------------------------------------------------
# place_multi_leg — combo cohesion validation (programming-error guards)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_mixed_combo_ids():
    b = _make_broker()
    legs = _standard_ic_legs()
    legs[2].extra["combo_id"] = "different-combo"
    with patch.object(rs.orders, "order_option_spread") as m:
        with pytest.raises(ValueError, match="mixed combo_ids"):
            await b.place_multi_leg(legs)
    m.assert_not_called()


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_mixed_directions():
    b = _make_broker()
    legs = _standard_ic_legs()
    legs[1].extra["combo_direction"] = "debit"
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="mixed combo_direction"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_mismatched_net_limit():
    b = _make_broker()
    legs = _standard_ic_legs()
    legs[3].extra["net_limit_price"] = 2.50
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="mismatched net_limit_price"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_mismatched_qty():
    b = _make_broker()
    legs = _standard_ic_legs()
    legs[2].qty = 2.0
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="mismatched qty"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_mixed_underlying():
    b = _make_broker()
    legs = _standard_ic_legs()
    legs[3].extra["underlying"] = "QQQ"
    legs[3].symbol = "QQQ"
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="mixed underlying"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_invalid_direction():
    b = _make_broker()
    legs = _standard_ic_legs()
    for o in legs:
        o.extra["combo_direction"] = "sideways"
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="combo_direction must be"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_nonpositive_net_limit():
    b = _make_broker()
    legs = _standard_ic_legs()
    for o in legs:
        o.extra["net_limit_price"] = 0.0
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="net_limit_price must be positive"):
            await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_place_multi_leg_rejects_leg_missing_required_extra():
    b = _make_broker()
    legs = _standard_ic_legs()
    del legs[1].extra["strike"]
    with patch.object(rs.orders, "order_option_spread"):
        with pytest.raises(ValueError, match="missing required extra key 'strike'"):
            await b.place_multi_leg(legs)


# ---------------------------------------------------------------------------
# _place_option_order rejection of multi-leg orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_rejects_multi_leg_leg_submitted_alone():
    """A leg with is_multi_leg=True must never go through the single-leg path."""
    b = _make_broker()
    leg = _make_ic_leg(role="short_put", side="sell",
                       option_type="put", strike=430.0)
    # Make sure the safety net is the only thing that fires — no network.
    with patch.object(rs.orders, "order_sell_option_limit") as m:
        with pytest.raises(ValueError, match="is a multi-leg combo leg"):
            await b.place_order(leg)
    m.assert_not_called()


# ---------------------------------------------------------------------------
# get_option_greeks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_option_greeks_happy_path_dict_response():
    b = _make_broker()
    md = {
        "delta": "0.16",
        "gamma": "0.008",
        "theta": "-0.05",
        "vega":  "0.12",
        "implied_volatility": "0.23",
        "adjusted_mark_price": "1.47",
        "bid_price": "1.45",
        "ask_price": "1.49",
    }
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=md):
        out = await b.get_option_greeks("opt-id-1")
    assert out == {
        "delta": 0.16,
        "gamma": 0.008,
        "theta": -0.05,
        "vega":  0.12,
        "iv":    0.23,
        "mark_price": 1.47,
        # bid/ask added 2026-07-23 for combo re-pricing (additive).
        "bid": 1.45,
        "ask": 1.49,
    }


@pytest.mark.asyncio
async def test_get_option_greeks_handles_list_response():
    """robin_stocks sometimes wraps responses in a single-element list."""
    b = _make_broker()
    md = [{"delta": "0.30", "mark_price": "2.10"}]
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=md):
        out = await b.get_option_greeks("opt-id-2")
    assert out["delta"] == 0.30
    assert out["mark_price"] == 2.10
    # Unsupplied fields → None.
    assert out["gamma"] is None
    assert out["theta"] is None
    assert out["vega"] is None
    assert out["iv"] is None


@pytest.mark.asyncio
async def test_get_option_greeks_prefers_adjusted_mark_over_mark_price():
    b = _make_broker()
    md = {"adjusted_mark_price": "1.50", "mark_price": "1.45"}
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=md):
        out = await b.get_option_greeks("opt-id-3")
    assert out["mark_price"] == 1.50


@pytest.mark.asyncio
async def test_get_option_greeks_falls_back_to_mark_price_when_adjusted_missing():
    b = _make_broker()
    md = {"mark_price": "1.45"}
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=md):
        out = await b.get_option_greeks("opt-id-4")
    assert out["mark_price"] == 1.45


@pytest.mark.asyncio
async def test_get_option_greeks_handles_malformed_values():
    b = _make_broker()
    md = {"delta": "garbage", "gamma": None, "theta": "0.05"}
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=md):
        out = await b.get_option_greeks("opt-id-5")
    assert out["delta"] is None    # unparseable → None
    assert out["gamma"] is None    # None → None
    assert out["theta"] == 0.05
    assert out["mark_price"] is None  # missing → None


@pytest.mark.asyncio
async def test_get_option_greeks_handles_empty_list():
    b = _make_broker()
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=[]):
        out = await b.get_option_greeks("opt-id-6")
    # All fields None — the calendar / strategy treats this as "undetermined".
    assert all(v is None for v in out.values())


@pytest.mark.asyncio
async def test_get_option_greeks_handles_none_response():
    b = _make_broker()
    with patch.object(rs.options, "get_option_market_data_by_id", return_value=None):
        out = await b.get_option_greeks("opt-id-7")
    assert all(v is None for v in out.values())


@pytest.mark.asyncio
async def test_get_option_greeks_propagates_network_failure():
    b = _make_broker()

    def boom(*a, **kw):
        raise TimeoutError("robinhood timed out")

    with patch.object(rs.options, "get_option_market_data_by_id", new=boom):
        with pytest.raises(TimeoutError, match="timed out"):
            await b.get_option_greeks("opt-id-8")


# ---------------------------------------------------------------------------
# Connection guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_multi_leg_requires_connection():
    b = RobinhoodBroker(username="x", password="y")
    # _connected is False by default — no connect() called.
    with pytest.raises(RuntimeError, match="not connected"):
        await b.place_multi_leg(_standard_ic_legs())


@pytest.mark.asyncio
async def test_get_option_greeks_requires_connection():
    b = RobinhoodBroker(username="x", password="y")
    with pytest.raises(RuntimeError, match="not connected"):
        await b.get_option_greeks("opt-id")


# ---------------------------------------------------------------------------
# Broker ABC defaults raise NotImplementedError on non-Robinhood brokers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abc_default_place_multi_leg_raises():
    """Other concrete brokers should NOT silently no-op — default raises."""
    from trading_corp.brokers.paper import PaperBroker
    p = PaperBroker(starting_equity=100_000.0)
    with pytest.raises(NotImplementedError, match="multi-leg combo"):
        await p.place_multi_leg([])


@pytest.mark.asyncio
async def test_abc_default_get_option_greeks_raises():
    from trading_corp.brokers.paper import PaperBroker
    p = PaperBroker(starting_equity=100_000.0)
    with pytest.raises(NotImplementedError, match="option Greeks"):
        await p.get_option_greeks("opt-id")
