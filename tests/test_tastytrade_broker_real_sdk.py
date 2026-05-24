"""Real-SDK shape tests for TastytradeBroker — no network.

These tests instantiate REAL `tastytrade` SDK objects (NewOrder, Leg,
OrderAction, etc.) to assert constructor signatures haven't drifted in
SDK upgrades. They never call the network — Session and Account are
mocked. Per memory `feedback_mocks_dont_catch_sdk_shape`: mocks alone
caught 0/5 SDK-shape bugs in tastytrade_provider.py during initial
deploy; the shape tests close that gap for the broker side.

Run in CI alongside unit tests — no `@pytest.mark.real_sdk` skip; they
are fast and require no credentials.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def test_new_order_accepts_legs_price_time_in_force_and_order_type():
    """NewOrder ctor signature includes the fields TastytradeBroker passes."""
    from tastytrade.order import (
        NewOrder, Leg, OrderAction, OrderType, OrderTimeInForce, InstrumentType,
    )
    leg = Leg(
        instrument_type=InstrumentType.EQUITY_OPTION,
        symbol="SPY   260620C00510000",
        action=OrderAction.SELL_TO_OPEN,
        quantity=Decimal(1),
    )
    new_order = NewOrder(
        time_in_force=OrderTimeInForce.DAY,
        order_type=OrderType.LIMIT,
        legs=[leg],
        price=Decimal("0.80"),
    )
    assert new_order.legs == [leg]
    assert new_order.price == Decimal("0.80")
    assert new_order.order_type == OrderType.LIMIT
    assert new_order.time_in_force == OrderTimeInForce.DAY


def test_new_order_accepts_negative_price_for_debit():
    """Price sign carries credit (positive) / debit (negative)."""
    from tastytrade.order import (
        NewOrder, Leg, OrderAction, OrderType, OrderTimeInForce, InstrumentType,
    )
    leg = Leg(
        instrument_type=InstrumentType.EQUITY_OPTION,
        symbol="SPY   260620C00510000",
        action=OrderAction.BUY_TO_CLOSE,
        quantity=Decimal(1),
    )
    new_order = NewOrder(
        time_in_force=OrderTimeInForce.DAY,
        order_type=OrderType.LIMIT,
        legs=[leg],
        price=Decimal("-0.40"),
    )
    assert new_order.price == Decimal("-0.40")


def test_order_action_enum_has_all_four_open_close_combinations():
    """TastytradeBroker._order_action maps to these four exact strings."""
    from tastytrade.order import OrderAction
    assert OrderAction("Buy to Open") == OrderAction.BUY_TO_OPEN
    assert OrderAction("Buy to Close") == OrderAction.BUY_TO_CLOSE
    assert OrderAction("Sell to Open") == OrderAction.SELL_TO_OPEN
    assert OrderAction("Sell to Close") == OrderAction.SELL_TO_CLOSE


def test_instrument_type_equity_option_exists():
    """EQUITY_OPTION is the value passed to Leg.instrument_type for options."""
    from tastytrade.order import InstrumentType
    assert InstrumentType.EQUITY_OPTION.value == "Equity Option"


def test_order_status_terminal_set_unchanged():
    """_TERMINAL_STATUSES strings must match the OrderStatus enum values."""
    from tastytrade.order import OrderStatus
    from trading_corp.brokers.tastytrade import _TERMINAL_STATUSES
    enum_values = {s.value for s in OrderStatus}
    for terminal in _TERMINAL_STATUSES:
        assert terminal in enum_values, (
            f"_TERMINAL_STATUSES has {terminal!r}; "
            f"not found in OrderStatus enum (SDK signature drift?)"
        )


def test_new_order_4_leg_iron_condor_builds():
    """End-to-end IC shape: 4 Legs + signed price → NewOrder."""
    from tastytrade.order import (
        NewOrder, Leg, OrderAction, OrderType, OrderTimeInForce, InstrumentType,
    )
    legs = [
        Leg(instrument_type=InstrumentType.EQUITY_OPTION,
            symbol="SPY   260620C00510000",
            action=OrderAction.SELL_TO_OPEN,
            quantity=Decimal(1)),
        Leg(instrument_type=InstrumentType.EQUITY_OPTION,
            symbol="SPY   260620C00513000",
            action=OrderAction.BUY_TO_OPEN,
            quantity=Decimal(1)),
        Leg(instrument_type=InstrumentType.EQUITY_OPTION,
            symbol="SPY   260620P00487000",
            action=OrderAction.BUY_TO_OPEN,
            quantity=Decimal(1)),
        Leg(instrument_type=InstrumentType.EQUITY_OPTION,
            symbol="SPY   260620P00490000",
            action=OrderAction.SELL_TO_OPEN,
            quantity=Decimal(1)),
    ]
    order = NewOrder(
        time_in_force=OrderTimeInForce.DAY,
        order_type=OrderType.LIMIT,
        legs=legs,
        price=Decimal("1.50"),
    )
    assert len(order.legs) == 4
    actions = [leg.action for leg in order.legs]
    assert actions == [
        OrderAction.SELL_TO_OPEN, OrderAction.BUY_TO_OPEN,
        OrderAction.BUY_TO_OPEN, OrderAction.SELL_TO_OPEN,
    ]


def test_broker_build_leg_produces_real_sdk_leg_object():
    """TastytradeBroker._build_leg_from_order returns a real SDK Leg with
    the OCC symbol it built — no mocks involved on the SDK side."""
    from tastytrade.order import Leg, OrderAction, InstrumentType
    from trading_corp.brokers.tastytrade import TastytradeBroker
    from trading_corp.persistence.models import ProposedOrder

    broker = TastytradeBroker(
        provider_secret="ps", refresh_token="rt",
    )
    order = ProposedOrder(
        strategy="tasty_options_iron_condor",
        symbol="SPY",
        side="sell",
        qty=1,
        order_type="limit",
        limit_price=0.80,
        extra={
            "expiration": "2026-06-20",
            "strike": 510.0,
            "option_type": "call",
            "position_effect": "open",
            "underlying": "SPY",
        },
    )
    leg = broker._build_leg_from_order(order)
    assert isinstance(leg, Leg)
    assert leg.instrument_type == InstrumentType.EQUITY_OPTION
    assert leg.action == OrderAction.SELL_TO_OPEN
    assert leg.symbol == "SPY   260620C00510000"
    assert leg.quantity == Decimal(1)


def test_account_has_expected_method_signatures():
    """Account methods TastytradeBroker calls — present in the SDK."""
    from tastytrade.account import Account
    assert hasattr(Account, "get")             # connect()
    assert hasattr(Account, "get_balances")    # snapshot()
    assert hasattr(Account, "get_positions")   # snapshot()
    assert hasattr(Account, "place_order")     # place_order / place_multi_leg
    assert hasattr(Account, "delete_order")    # cancel_order
    assert hasattr(Account, "get_order")       # _poll_to_terminal


def test_session_init_accepts_is_test_kwarg():
    """Session(is_test=True) routes to TT cert env (Phase-0 sandbox)."""
    import inspect
    from tastytrade import Session
    sig = inspect.signature(Session.__init__)
    assert "is_test" in sig.parameters
    assert sig.parameters["is_test"].default is False
