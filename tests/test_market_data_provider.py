"""Tests for MarketDataProvider ABC and _is_degenerate_iv helper."""
from __future__ import annotations

import math
from datetime import date

import pytest

from trading_corp.data.market_data_provider import (
    MarketDataProvider,
    OptionContract,
    _is_degenerate_iv,
)


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


def test_market_data_provider_is_instantiable():
    """ABC must be instantiable with no overrides (no @abstractmethod)."""
    provider = MarketDataProvider()
    assert provider is not None


@pytest.mark.asyncio
async def test_get_option_chain_raises_not_implemented():
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_option_chain("SPY", date.today())


@pytest.mark.asyncio
async def test_get_greeks_raises_not_implemented():
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_greeks("SPY231215C00450000")


@pytest.mark.asyncio
async def test_get_atm_iv_raises_not_implemented():
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_atm_iv("SPY", 45)


@pytest.mark.asyncio
async def test_get_iv_rank_raises_not_implemented():
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_iv_rank("SPY")


@pytest.mark.asyncio
async def test_get_underlying_price_raises_not_implemented():
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_underlying_price("SPY")


# ---------------------------------------------------------------------------
# _is_degenerate_iv — parametrised coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iv,expected", [
    # Degenerate cases — all return True
    (None,             True),
    (float("nan"),     True),
    (float("inf"),     True),
    (float("-inf"),    True),
    (0.0,              True),
    (-0.1,             True),
    (0.005,            True),   # below 0.01 floor — catches 1e-5 degenerate
    (1e-5,             True),   # the original yfinance bug value
    (0.009,            True),   # just below floor
    # Plausible IV values — all return False
    (0.01,             False),  # exactly at floor — boundary is inclusive (iv < 0.01 → True)
    (0.10,             False),
    (0.20,             False),
    (0.50,             False),
    (0.99,             False),
])
def test_is_degenerate_iv(iv, expected):
    assert _is_degenerate_iv(iv) is expected, (
        f"_is_degenerate_iv({iv!r}) expected {expected}, got {_is_degenerate_iv(iv)}"
    )


# ---------------------------------------------------------------------------
# OptionContract dataclass
# ---------------------------------------------------------------------------


def test_option_contract_frozen():
    """OptionContract must be frozen (immutable)."""
    contract = OptionContract(
        option_id="TEST",
        expiration_date="2026-06-20",
        strike=450.0,
        option_type="call",
        delta=0.16,
        gamma=0.01,
        theta=-0.05,
        vega=0.20,
        iv=0.22,
        mark=1.10,
        bid=1.08,
        ask=1.12,
        bid_size=10.0,
        ask_size=10.0,
        open_interest=5000.0,
        volume=200.0,
        dte=45.0,
    )
    with pytest.raises((AttributeError, TypeError)):
        contract.iv = 0.99  # type: ignore[misc]


def test_option_contract_none_fields():
    """All optional numeric fields accept None."""
    contract = OptionContract(
        option_id="TEST2",
        expiration_date="2026-06-20",
        strike=None,
        option_type="put",
        delta=None,
        gamma=None,
        theta=None,
        vega=None,
        iv=None,
        mark=None,
        bid=None,
        ask=None,
        bid_size=None,
        ask_size=None,
        open_interest=None,
        volume=None,
        dte=None,
    )
    assert contract.iv is None
    assert contract.delta is None
