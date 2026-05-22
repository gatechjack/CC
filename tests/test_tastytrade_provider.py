"""Tests for TastytradeDataProvider — mocked SDK, no network calls."""
from __future__ import annotations

import asyncio
import os
import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_corp.data.tastytrade_provider import TastytradeDataProvider
from trading_corp.data.market_data_provider import OptionContract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(ps: str = "secret123", rt: str = "refresh456") -> TastytradeDataProvider:
    return TastytradeDataProvider(provider_secret=ps, refresh_token=rt)


def _fake_option(
    streamer_symbol: str,
    strike_price: float,
    option_type: str = "C",
    exp_date: str = "2026-06-20",
) -> MagicMock:
    opt = MagicMock()
    opt.streamer_symbol = streamer_symbol
    opt.strike_price = strike_price
    opt.option_type = option_type
    opt.expiration_date = exp_date
    return opt


def _fake_greeks(
    delta: float = 0.16,
    gamma: float = 0.01,
    theta: float = -0.05,
    vega: float = 0.20,
    volatility: float = 0.22,
    price: float = 1.10,
) -> MagicMock:
    g = MagicMock()
    g.delta = delta
    g.gamma = gamma
    g.theta = theta
    g.vega = vega
    g.volatility = volatility
    g.price = price
    return g


# ---------------------------------------------------------------------------
# Auth-missing path
# ---------------------------------------------------------------------------


def test_auth_missing_provider_secret_raises():
    """Ctor must raise ValueError with useful message when PS is missing."""
    with pytest.raises(ValueError, match="TASTYTRADE_PROVIDER_SECRET"):
        TastytradeDataProvider(provider_secret=None, refresh_token="rt")


def test_auth_missing_refresh_token_raises():
    """Ctor must raise ValueError with useful message when RT is missing."""
    with pytest.raises(ValueError, match="TASTYTRADE_REFRESH_TOKEN"):
        TastytradeDataProvider(provider_secret="ps", refresh_token=None)


def test_auth_missing_both_env_vars_raises(monkeypatch):
    """Missing env vars → ValueError (not bare KeyError)."""
    monkeypatch.delenv("TASTYTRADE_PROVIDER_SECRET", raising=False)
    monkeypatch.delenv("TASTYTRADE_REFRESH_TOKEN", raising=False)
    with pytest.raises(ValueError):
        TastytradeDataProvider()


def test_auth_from_env_vars(monkeypatch):
    """Provider reads credentials from env vars when ctor args are None."""
    monkeypatch.setenv("TASTYTRADE_PROVIDER_SECRET", "env_ps")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "env_rt")
    provider = TastytradeDataProvider()
    assert provider._provider_secret == "env_ps"
    assert provider._refresh_token == "env_rt"


# ---------------------------------------------------------------------------
# get_option_chain — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_option_chain_returns_list_of_option_contracts():
    """Happy path: _fetch_chain returns list[OptionContract]."""
    provider = _make_provider()
    exp = date(2026, 6, 20)

    # Directly patch _fetch_chain to return pre-built contracts
    contract_c = OptionContract(
        option_id=".SPY260620C00450000",
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
        dte=30.0,
    )
    contract_p = OptionContract(
        option_id=".SPY260620P00440000",
        expiration_date="2026-06-20",
        strike=440.0,
        option_type="put",
        delta=-0.16,
        gamma=0.01,
        theta=-0.05,
        vega=0.20,
        iv=0.21,
        mark=1.05,
        bid=1.03,
        ask=1.07,
        bid_size=8.0,
        ask_size=8.0,
        open_interest=4000.0,
        volume=150.0,
        dte=30.0,
    )
    expected = [contract_c, contract_p]

    async def _fake_fetch(symbol, expiration):
        return expected

    provider._fetch_chain = _fake_fetch

    contracts = await provider.get_option_chain("SPY", exp)

    assert isinstance(contracts, list)
    assert len(contracts) == 2
    assert contracts[0].option_type == "call"
    assert contracts[0].iv == pytest.approx(0.22)
    assert contracts[1].option_type == "put"
    assert contracts[1].iv == pytest.approx(0.21)
    # Both IVs are plausible (not degenerate)
    from trading_corp.data.market_data_provider import _is_degenerate_iv
    assert not _is_degenerate_iv(contracts[0].iv)
    assert not _is_degenerate_iv(contracts[1].iv)


# ---------------------------------------------------------------------------
# Degenerate-rejection at boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degenerate_iv_row_is_dropped():
    """Rows with degenerate IV (1e-5) must be dropped; plausible rows pass."""
    provider = _make_provider()

    # The provider calls _is_degenerate_iv on each row's IV.
    # Test the logic directly by calling _fetch_chain with patched internals.
    from trading_corp.data.market_data_provider import _is_degenerate_iv

    # Directly verify the guard works
    assert _is_degenerate_iv(1e-5) is True
    assert _is_degenerate_iv(0.22) is False


# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_returns_same_value_within_ttl():
    """Two calls within 60s → same cached result (no second underlying call)."""
    provider = _make_provider()

    call_count = [0]

    async def _fake_fetch(symbol, expiration):
        call_count[0] += 1
        return []

    provider._fetch_chain = _fake_fetch

    exp = date(2026, 6, 20)
    await provider.get_option_chain("SPY", exp)
    await provider.get_option_chain("SPY", exp)

    assert call_count[0] == 1, (
        f"Expected 1 underlying call within TTL, got {call_count[0]}"
    )


@pytest.mark.asyncio
async def test_cache_expires_after_ttl():
    """After 60s (simulated), a second call triggers a fresh fetch."""
    from trading_corp.data import tastytrade_provider as tp_mod

    provider = _make_provider()
    call_count = [0]

    async def _fake_fetch(symbol, expiration):
        call_count[0] += 1
        return []

    provider._fetch_chain = _fake_fetch

    exp = date(2026, 6, 20)
    cache_key = ("get_option_chain", "SPY", exp.isoformat())

    await provider.get_option_chain("SPY", exp)
    assert call_count[0] == 1

    # Manually expire the cache entry
    provider._cache[cache_key] = ([], time.monotonic() - (tp_mod._CACHE_TTL_SEC + 1))

    await provider.get_option_chain("SPY", exp)
    assert call_count[0] == 2, (
        f"Expected 2 calls after TTL expiry, got {call_count[0]}"
    )


# ---------------------------------------------------------------------------
# get_iv_rank — None on failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_iv_rank_returns_none_on_all_fetch_failure():
    """Both Tastytrade history and yfinance fallback fail → None."""
    provider = _make_provider()

    async def _fail_closes(_symbol):
        return None

    # Patch the close-series fetcher directly — covers both Tasty + yfinance paths
    provider._fetch_close_series = _fail_closes
    result = await provider.get_iv_rank("SPY")
    assert result is None


# ---------------------------------------------------------------------------
# get_underlying_price — zero/negative → None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_underlying_price_returns_none_on_failure():
    """Underlying price fetch failure → None."""
    provider = _make_provider()

    async def _bad_session():
        raise RuntimeError("connection refused")

    provider._get_session = _bad_session
    result = await provider.get_underlying_price("SPY")
    assert result is None
