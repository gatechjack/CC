"""Tests for YFinanceDataProvider — repurposes existing yfinance mock patterns.

Key verification: the 1e-5 degenerate IV bug.  Old code returned 1e-5;
new code returns None (filtered at boundary by _is_degenerate_iv).
"""
from __future__ import annotations

import math
from collections import namedtuple
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trading_corp.data._iv_math import _hv_to_rank
from trading_corp.data.yfinance_provider import YFinanceDataProvider


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

OptionChain = namedtuple("OptionChain", ["calls", "puts"])


def _hist_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes})


def _build_chain(strikes: list[float], ivs: list[float]) -> OptionChain:
    calls = pd.DataFrame({"strike": strikes, "impliedVolatility": ivs})
    puts = pd.DataFrame({"strike": strikes, "impliedVolatility": ivs})
    return OptionChain(calls=calls, puts=puts)


def _build_ticker(
    expirations: tuple[str, ...],
    spot: float,
    chain_by_expiry: dict[str, OptionChain],
) -> MagicMock:
    tk = MagicMock()
    tk.options = expirations
    tk.history.return_value = pd.DataFrame({"Close": [spot]})
    tk.option_chain.side_effect = lambda exp: chain_by_expiry[exp]
    return tk


def _exp_at(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).isoformat()


def _provider() -> YFinanceDataProvider:
    return YFinanceDataProvider()


# ---------------------------------------------------------------------------
# _hv_to_rank — pure helper tests
# ---------------------------------------------------------------------------


def test_hv_to_rank_high_when_current_vol_is_max():
    rng = np.random.default_rng(seed=7)
    flat = np.full(100, 100.0)
    spikes = 100.0 + rng.normal(0, 3.0, size=50).cumsum()
    closes = pd.Series(np.concatenate([flat, spikes]))
    rank = _hv_to_rank(closes)
    assert rank is not None
    assert 0.7 <= rank <= 1.0, f"expected high rank, got {rank}"


def test_hv_to_rank_low_when_current_vol_is_min():
    rng = np.random.default_rng(seed=11)
    spikes = 100.0 + rng.normal(0, 3.0, size=50).cumsum()
    flat = np.full(100, float(spikes[-1]))
    closes = pd.Series(np.concatenate([spikes, flat]))
    rank = _hv_to_rank(closes)
    assert rank is not None
    assert 0.0 <= rank <= 0.3, f"expected low rank, got {rank}"


def test_hv_to_rank_returns_none_on_insufficient_history():
    """Fewer than 35 bars → None (not 0.5 sentinel)."""
    closes = pd.Series([100.0] * 10)
    rank = _hv_to_rank(closes)
    assert rank is None


def test_hv_to_rank_returns_none_on_flat_series():
    """200 identical prices → min==max → None (not 0.5)."""
    closes = pd.Series([100.0] * 200)
    rank = _hv_to_rank(closes)
    assert rank is None


# ---------------------------------------------------------------------------
# YFinanceDataProvider.get_iv_rank
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_iv_rank_high():
    rng = np.random.default_rng(seed=7)
    flat = np.full(100, 100.0)
    spikes = 100.0 + rng.normal(0, 3.0, size=50).cumsum()
    closes = np.concatenate([flat, spikes]).tolist()

    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df(closes)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await _provider().get_iv_rank("SPY")

    assert rank is not None
    assert 0.7 <= rank <= 1.0, f"expected high rank, got {rank}"


@pytest.mark.asyncio
async def test_get_iv_rank_low():
    rng = np.random.default_rng(seed=11)
    spikes = 100.0 + rng.normal(0, 3.0, size=50).cumsum()
    flat = np.full(100, float(spikes[-1]))
    closes = np.concatenate([spikes, flat]).tolist()

    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df(closes)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await _provider().get_iv_rank("SPY")

    assert rank is not None
    assert 0.0 <= rank <= 0.3, f"expected low rank, got {rank}"


@pytest.mark.asyncio
async def test_get_iv_rank_returns_none_on_insufficient_history():
    """Fewer than 35 bars → None (NOT 0.5 sentinel)."""
    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df([100.0] * 10)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await _provider().get_iv_rank("SPY")

    assert rank is None, f"expected None on short history, got {rank}"


@pytest.mark.asyncio
async def test_get_iv_rank_returns_none_on_flat_series():
    """200 identical prices → None (NOT 0.5 sentinel)."""
    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df([100.0] * 200)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await _provider().get_iv_rank("SPY")

    assert rank is None, f"expected None on flat series, got {rank}"


@pytest.mark.asyncio
async def test_get_iv_rank_returns_none_on_exception():
    """yfinance exception → None (NOT 0.5 sentinel)."""
    fake_tk = MagicMock()
    fake_tk.history.side_effect = RuntimeError("yfinance is sad today")
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await _provider().get_iv_rank("SPY")

    assert rank is None, f"expected None on exception, got {rank}"


# ---------------------------------------------------------------------------
# YFinanceDataProvider.get_atm_iv — 1e-5 degenerate fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_atm_iv_returns_none_for_degenerate_1e5_iv():
    """THE CORE FIX: impliedVolatility=1e-5 must return None, not 1e-5."""
    exp_45 = _exp_at(45)
    # 1e-5 is the exact degenerate value that was passing the old guard
    degenerate_chain = _build_chain([100.0], [1e-5])
    tk = _build_ticker(
        expirations=(exp_45,),
        spot=100.0,
        chain_by_expiry={exp_45: degenerate_chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45)

    assert iv is None, f"expected None for degenerate 1e-5 IV, got {iv}"


@pytest.mark.asyncio
async def test_get_atm_iv_returns_none_for_zero_iv():
    """IV=0.0 must return None."""
    exp_45 = _exp_at(45)
    zero_chain = _build_chain([100.0], [0.0])
    tk = _build_ticker(
        expirations=(exp_45,),
        spot=100.0,
        chain_by_expiry={exp_45: zero_chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45)

    assert iv is None, f"expected None for zero IV, got {iv}"


@pytest.mark.asyncio
async def test_get_atm_iv_returns_value_for_plausible_iv():
    """Normal IV value (0.22) passes through correctly."""
    exp_45 = _exp_at(45)
    chain = _build_chain(
        strikes=[95.0, 100.0, 105.0],
        ivs=[0.30, 0.22, 0.25],
    )
    tk = _build_ticker(
        expirations=(exp_45,),
        spot=100.0,
        chain_by_expiry={exp_45: chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45)

    assert iv == pytest.approx(0.22)


@pytest.mark.asyncio
async def test_get_atm_iv_returns_none_when_no_qualifying_expiration():
    exp_10 = _exp_at(10)
    exp_120 = _exp_at(120)
    chain = _build_chain([100.0], [0.20])
    tk = _build_ticker(
        expirations=(exp_10, exp_120),
        spot=100.0,
        chain_by_expiry={exp_10: chain, exp_120: chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45, tolerance_days=7)

    assert iv is None


@pytest.mark.asyncio
async def test_get_atm_iv_returns_none_on_empty_chain():
    exp_45 = _exp_at(45)
    empty_chain = OptionChain(
        calls=pd.DataFrame({"strike": [], "impliedVolatility": []}),
        puts=pd.DataFrame({"strike": [], "impliedVolatility": []}),
    )
    tk = _build_ticker(
        expirations=(exp_45,),
        spot=100.0,
        chain_by_expiry={exp_45: empty_chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_get_atm_iv_returns_none_on_nan_iv():
    exp_45 = _exp_at(45)
    nan_chain = _build_chain([100.0], [math.nan])
    tk = _build_ticker(
        expirations=(exp_45,),
        spot=100.0,
        chain_by_expiry={exp_45: nan_chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_get_atm_iv_returns_none_on_exception():
    tk = MagicMock()
    tk.options = (_exp_at(45),)
    tk.history.side_effect = RuntimeError("network blip")
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_get_atm_iv_picks_closest_expiration():
    exp_30 = _exp_at(30)
    exp_45 = _exp_at(45)
    exp_60 = _exp_at(60)

    chain_45 = _build_chain(
        strikes=[95.0, 100.0, 105.0],
        ivs=[0.30, 0.22, 0.25],
    )
    chain_30 = _build_chain([100.0], [0.99])
    chain_60 = _build_chain([100.0], [0.99])

    tk = _build_ticker(
        expirations=(exp_30, exp_45, exp_60),
        spot=100.0,
        chain_by_expiry={exp_30: chain_30, exp_45: chain_45, exp_60: chain_60},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await _provider().get_atm_iv("SPY", target_dte=45, tolerance_days=7)

    assert iv == pytest.approx(0.22)
