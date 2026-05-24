"""Tests for TastytradeBroker — mocked SDK, no network calls.

Mocks-alone is NOT sufficient per memory `feedback_mocks_dont_catch_sdk_shape`
(5 prior SDK-shape bugs slipped past mock-only coverage in tastytrade_provider.py).
The companion file `tests/test_tastytrade_broker_real_sdk.py` exercises real
SDK constructors with no network to catch signature drift; sandbox smoke
script `scripts/tasty_sandbox_smoke.py` exercises live calls before deploy.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_corp.brokers.tastytrade import (
    TastytradeBroker, _occ_symbol, _order_action,
)
from trading_corp.persistence.models import ProposedOrder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_broker(
    ps: str = "secret123",
    rt: str = "refresh456",
    account_filter: str | None = None,
    is_test: bool = False,
    data_provider=None,
) -> TastytradeBroker:
    return TastytradeBroker(
        provider_secret=ps,
        refresh_token=rt,
        account_filter=account_filter,
        is_test=is_test,
        data_provider=data_provider,
    )


def _leg_order(
    *,
    symbol: str = "SPY",
    side: str = "sell",
    qty: int = 1,
    limit_price: float = 0.50,
    expiration: str = "2026-06-20",
    strike: float = 500.0,
    option_type: str = "call",
    position_effect: str = "open",
    combo_id: str = "C1",
    combo_direction: str = "credit",
    net_limit_price: float = 1.50,
    underlying: str = "SPY",
    ratio_quantity: int = 1,
) -> ProposedOrder:
    return ProposedOrder(
        strategy="tasty_options_iron_condor",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type="limit",
        limit_price=limit_price,
        extra={
            "expiration": expiration,
            "strike": strike,
            "option_type": option_type,
            "position_effect": position_effect,
            "combo_id": combo_id,
            "combo_direction": combo_direction,
            "net_limit_price": net_limit_price,
            "underlying": underlying,
            "ratio_quantity": ratio_quantity,
        },
    )


def _ic_combo() -> list[ProposedOrder]:
    """Standard 4-leg IC: short call + long call + long put + short put.

    All same combo_id / direction / net_limit / qty / underlying.
    """
    return [
        _leg_order(side="sell", strike=510.0, option_type="call",
                   limit_price=0.80),
        _leg_order(side="buy",  strike=513.0, option_type="call",
                   limit_price=0.30),
        _leg_order(side="buy",  strike=487.0, option_type="put",
                   limit_price=0.20),
        _leg_order(side="sell", strike=490.0, option_type="put",
                   limit_price=0.60),
    ]


# ---------------------------------------------------------------------------
# Auth / construction
# ---------------------------------------------------------------------------

def test_auth_missing_provider_secret_raises(monkeypatch):
    monkeypatch.delenv("TASTYTRADE_PROVIDER_SECRET", raising=False)
    with pytest.raises(ValueError, match="TASTYTRADE_PROVIDER_SECRET"):
        TastytradeBroker(provider_secret=None, refresh_token="rt")


def test_auth_missing_refresh_token_raises(monkeypatch):
    monkeypatch.delenv("TASTYTRADE_REFRESH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TASTYTRADE_REFRESH_TOKEN"):
        TastytradeBroker(provider_secret="ps", refresh_token=None)


def test_auth_from_env_vars(monkeypatch):
    monkeypatch.setenv("TASTYTRADE_PROVIDER_SECRET", "env_ps")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "env_rt")
    broker = TastytradeBroker()
    assert broker._provider_secret == "env_ps"
    assert broker._refresh_token == "env_rt"


# ---------------------------------------------------------------------------
# OCC symbol builder
# ---------------------------------------------------------------------------

def test_occ_symbol_spy_call():
    # SPY 2024-09-20 $500 call → 21-char OCC.
    assert _occ_symbol("SPY", "2024-09-20", "call", 500.0) == "SPY   240920C00500000"


def test_occ_symbol_iwm_put_fractional_strike():
    # IWM 2026-06-20 $187.50 put — strike × 1000 = 187500, 8-digit padded.
    assert _occ_symbol("IWM", "2026-06-20", "put", 187.5) == "IWM   260620P00187500"


def test_occ_symbol_accepts_date_object():
    from datetime import date
    assert _occ_symbol("SPY", date(2024, 9, 20), "C", 500.0) == "SPY   240920C00500000"


def test_occ_symbol_long_root():
    # Long-root tickers consume more of the 6-char field (e.g. BRK.B).
    # Root field is right-padded with spaces to 6 chars regardless.
    assert _occ_symbol("BRK.B", "2024-09-20", "call", 400.0) == "BRK.B 240920C00400000"


# ---------------------------------------------------------------------------
# Order-action mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("side,effect,expected", [
    ("buy",  "open",  "Buy to Open"),
    ("buy",  "close", "Buy to Close"),
    ("sell", "open",  "Sell to Open"),
    ("sell", "close", "Sell to Close"),
])
def test_order_action_mapping(side, effect, expected):
    assert _order_action(side, effect) == expected


def test_order_action_rejects_unknown():
    with pytest.raises(ValueError, match="unrecognized"):
        _order_action("hold", "open")


# ---------------------------------------------------------------------------
# place_multi_leg — credit / debit / build / submit / map fills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_multi_leg_builds_credit_combo_with_positive_price():
    """A 'credit' combo passes positive Decimal price to NewOrder."""
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()

    fake_response = MagicMock()
    fake_response.order = MagicMock()
    fake_response.order.id = 42
    broker._account.place_order = MagicMock(return_value=fake_response)

    fake_terminal = MagicMock()
    fake_terminal.status = MagicMock()
    fake_terminal.status.value = "Filled"
    fake_terminal.legs = []
    broker._account.get_order = MagicMock(return_value=fake_terminal)

    orders = _ic_combo()  # combo_direction="credit", net_limit=1.50
    fills = await broker.place_multi_leg(orders)

    # NewOrder submitted with positive price.
    call_args = broker._account.place_order.call_args
    submitted_order = call_args.args[1]  # (session, order, dry_run)
    assert submitted_order.price == Decimal("1.50")
    assert len(submitted_order.legs) == 4

    # 4 FillEvents, one per input order, all venue=tastytrade.
    assert len(fills) == 4
    assert all(f.venue == "tastytrade" for f in fills)
    assert all(f.order_id == "42" for f in fills)


@pytest.mark.asyncio
async def test_place_multi_leg_builds_debit_combo_with_negative_price():
    """A 'debit' combo passes negative Decimal price (we pay)."""
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()

    fake_response = MagicMock()
    fake_response.order = MagicMock()
    fake_response.order.id = 99
    broker._account.place_order = MagicMock(return_value=fake_response)

    fake_terminal = MagicMock()
    fake_terminal.status = MagicMock()
    fake_terminal.status.value = "Filled"
    fake_terminal.legs = []
    broker._account.get_order = MagicMock(return_value=fake_terminal)

    # IC close — opposite of opening, so direction="debit"
    orders = [
        _leg_order(side="buy",  strike=510.0, option_type="call",
                   position_effect="close", combo_direction="debit"),
        _leg_order(side="sell", strike=513.0, option_type="call",
                   position_effect="close", combo_direction="debit"),
        _leg_order(side="sell", strike=487.0, option_type="put",
                   position_effect="close", combo_direction="debit"),
        _leg_order(side="buy",  strike=490.0, option_type="put",
                   position_effect="close", combo_direction="debit"),
    ]
    await broker.place_multi_leg(orders)

    submitted_order = broker._account.place_order.call_args.args[1]
    assert submitted_order.price == Decimal("-1.50")


@pytest.mark.asyncio
async def test_place_multi_leg_propagates_cohesion_failure():
    """validate_combo_cohesion failure surfaces as ValueError before any SDK call."""
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()
    broker._account.place_order = MagicMock()

    orders = _ic_combo()
    orders[2].extra["combo_id"] = "DIFFERENT"  # break cohesion

    with pytest.raises(ValueError, match="mixed combo_ids"):
        await broker.place_multi_leg(orders)
    broker._account.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_multi_leg_raises_when_terminal_not_filled():
    """Rejected / cancelled terminal status → RuntimeError (no fills recorded)."""
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()

    fake_response = MagicMock()
    fake_response.order = MagicMock()
    fake_response.order.id = 7
    broker._account.place_order = MagicMock(return_value=fake_response)

    fake_terminal = MagicMock()
    fake_terminal.status = MagicMock()
    fake_terminal.status.value = "Rejected"
    fake_terminal.legs = []
    broker._account.get_order = MagicMock(return_value=fake_terminal)

    with pytest.raises(RuntimeError, match="Rejected"):
        await broker.place_multi_leg(_ic_combo())


@pytest.mark.asyncio
async def test_place_multi_leg_empty_orders_returns_empty_list():
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()
    assert await broker.place_multi_leg([]) == []


@pytest.mark.asyncio
async def test_place_multi_leg_requires_extra_leg_keys():
    """Missing per-leg extra key → ValueError naming the missing field."""
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()

    orders = _ic_combo()
    del orders[0].extra["strike"]

    with pytest.raises(ValueError, match="strike"):
        await broker.place_multi_leg(orders)


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_order_coerces_string_to_int():
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()
    broker._account.delete_order = MagicMock(return_value=None)

    assert (await broker.cancel_order("123")) is True
    broker._account.delete_order.assert_called_once()
    # Second positional arg should be int(order_id).
    call_args = broker._account.delete_order.call_args
    assert call_args.args[1] == 123


@pytest.mark.asyncio
async def test_cancel_order_returns_false_on_non_int_order_id():
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()
    broker._account.delete_order = MagicMock()

    assert (await broker.cancel_order("not-an-int")) is False
    broker._account.delete_order.assert_not_called()


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_parses_balances_and_positions():
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()
    broker._account_number = "TT-12345"

    fake_balances = MagicMock()
    fake_balances.net_liquidating_value = 50000.0
    fake_balances.derivative_buying_power = 100000.0
    fake_balances.equity_buying_power = 100000.0
    fake_balances.cash_balance = 25000.0
    broker._account.get_balances = MagicMock(return_value=fake_balances)

    pos_long = MagicMock()
    pos_long.quantity = 10
    pos_long.quantity_direction = "Long"
    pos_long.symbol = "SPY"
    pos_long.average_open_price = 500.0
    pos_long.created_at = "2026-05-24T12:00:00Z"
    pos_long.instrument_type = "Equity"
    pos_long.underlying_symbol = "SPY"
    pos_long.multiplier = 1

    pos_short = MagicMock()
    pos_short.quantity = 1
    pos_short.quantity_direction = "Short"
    pos_short.symbol = "SPY   260620C00510000"
    pos_short.average_open_price = 0.80
    pos_short.created_at = "2026-05-24T13:00:00Z"
    pos_short.instrument_type = "Equity Option"
    pos_short.underlying_symbol = "SPY"
    pos_short.multiplier = 100

    broker._account.get_positions = MagicMock(return_value=[pos_long, pos_short])

    snap = await broker.snapshot()
    assert snap.account == "TT-12345"
    assert snap.equity == 50000.0
    assert snap.buying_power == 100000.0
    assert snap.cash == 25000.0
    assert len(snap.positions) == 2
    # Short position carries negative qty.
    spy_opt = next(p for p in snap.positions if "C00510000" in p.symbol)
    assert spy_opt.qty == -1.0
    # Long position carries positive qty.
    spy_long = next(p for p in snap.positions if p.symbol == "SPY")
    assert spy_long.qty == 10.0


@pytest.mark.asyncio
async def test_snapshot_skips_zero_qty_positions():
    broker = _make_broker()
    broker._connected = True
    broker._account = MagicMock()
    broker._account_number = "TT"

    fake_balances = MagicMock()
    fake_balances.net_liquidating_value = 0
    fake_balances.derivative_buying_power = 0
    fake_balances.equity_buying_power = 0
    fake_balances.cash_balance = 0
    broker._account.get_balances = MagicMock(return_value=fake_balances)

    zero_pos = MagicMock()
    zero_pos.quantity = 0
    zero_pos.quantity_direction = "Long"
    broker._account.get_positions = MagicMock(return_value=[zero_pos])

    snap = await broker.snapshot()
    assert snap.positions == []


# ---------------------------------------------------------------------------
# get_option_greeks delegation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_option_greeks_delegates_to_data_provider():
    fake_provider = MagicMock()
    fake_provider.get_greeks = AsyncMock(return_value={"delta": 0.16, "iv": 0.22})
    broker = _make_broker(data_provider=fake_provider)

    result = await broker.get_option_greeks("SPY   260620C00510000")
    assert result == {"delta": 0.16, "iv": 0.22}
    fake_provider.get_greeks.assert_awaited_once_with("SPY   260620C00510000")


@pytest.mark.asyncio
async def test_get_option_greeks_without_provider_raises_not_implemented():
    broker = _make_broker(data_provider=None)
    with pytest.raises(NotImplementedError, match="data_provider"):
        await broker.get_option_greeks("anything")


# ---------------------------------------------------------------------------
# Connect — account_filter resolution
# ---------------------------------------------------------------------------

def test_resolve_account_empty_filter_returns_first():
    broker = _make_broker(account_filter=None)
    a1 = MagicMock()
    a1.account_number = "A1"
    a2 = MagicMock()
    a2.account_number = "A2"
    assert broker._resolve_account([a1, a2]) is a1


def test_resolve_account_substring_match():
    broker = _make_broker(account_filter="A2")
    a1 = MagicMock()
    a1.account_number = "TT-A1"
    a1.nickname = "Main"
    a2 = MagicMock()
    a2.account_number = "TT-A2"
    a2.nickname = "Joint"
    assert broker._resolve_account([a1, a2]) is a2


def test_resolve_account_nickname_match():
    broker = _make_broker(account_filter="joint")
    a1 = MagicMock()
    a1.account_number = "TT-A1"
    a1.nickname = "Main"
    a2 = MagicMock()
    a2.account_number = "TT-A2"
    a2.nickname = "Joint"
    assert broker._resolve_account([a1, a2]) is a2


def test_resolve_account_no_match_raises():
    broker = _make_broker(account_filter="nope")
    a1 = MagicMock()
    a1.account_number = "TT-A1"
    a1.nickname = "Main"
    with pytest.raises(RuntimeError, match="matched none"):
        broker._resolve_account([a1])


# ---------------------------------------------------------------------------
# Not-connected guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_without_connect_raises():
    broker = _make_broker()
    with pytest.raises(RuntimeError, match="not connected"):
        await broker.snapshot()


@pytest.mark.asyncio
async def test_place_multi_leg_without_connect_raises():
    broker = _make_broker()
    with pytest.raises(RuntimeError, match="not connected"):
        await broker.place_multi_leg(_ic_combo())
