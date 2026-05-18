"""Tests for trading_corp.utils.iv — calc_iv_rank and calc_atm_iv.

yfinance is mocked at the module level so tests run offline. Both functions
should fail gracefully (0.5 fallback for rank, None for atm_iv) on any
upstream failure — these tests pin that behaviour as well as the happy path.
"""
from __future__ import annotations

from collections import namedtuple
from unittest.mock import MagicMock, patch

import math

import numpy as np
import pandas as pd
import pytest

from trading_corp.utils.iv import calc_iv_rank, calc_atm_iv


# ---------------------------------------------------------------------------
# calc_iv_rank
# ---------------------------------------------------------------------------


def _hist_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes})


@pytest.mark.asyncio
async def test_iv_rank_returns_high_when_current_vol_is_max():
    # Build a series whose 30-day rolling vol peaks at the last bar.
    # First 100 bars: flat (low vol). Last 50 bars: large noise (high vol).
    rng = np.random.default_rng(seed=7)
    flat = np.full(100, 100.0)
    spikes = 100.0 + rng.normal(0, 3.0, size=50).cumsum()
    closes = np.concatenate([flat, spikes]).tolist()

    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df(closes)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await calc_iv_rank("SPY")

    assert 0.7 <= rank <= 1.0, f"expected high rank, got {rank}"


@pytest.mark.asyncio
async def test_iv_rank_returns_low_when_current_vol_is_min():
    # First 50 bars: noisy (high vol). Last 100 bars: flat (low current vol).
    rng = np.random.default_rng(seed=11)
    spikes = 100.0 + rng.normal(0, 3.0, size=50).cumsum()
    flat = np.full(100, float(spikes[-1]))
    closes = np.concatenate([spikes, flat]).tolist()

    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df(closes)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await calc_iv_rank("SPY")

    assert 0.0 <= rank <= 0.3, f"expected low rank, got {rank}"


@pytest.mark.asyncio
async def test_iv_rank_returns_neutral_on_insufficient_history():
    # Fewer than 35 bars → 0.5 sentinel (per docstring).
    closes = [100.0] * 10
    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df(closes)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await calc_iv_rank("SPY")

    assert rank == 0.5


@pytest.mark.asyncio
async def test_iv_rank_returns_neutral_on_flat_series():
    # 200 bars of identical price → log returns all zero, rolling std all zero,
    # min == max → neutral 0.5 per the (mx > mn) guard.
    closes = [100.0] * 200
    fake_tk = MagicMock()
    fake_tk.history.return_value = _hist_df(closes)
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await calc_iv_rank("SPY")

    assert rank == 0.5


@pytest.mark.asyncio
async def test_iv_rank_returns_neutral_on_exception():
    fake_tk = MagicMock()
    fake_tk.history.side_effect = RuntimeError("yfinance is sad today")
    with patch("yfinance.Ticker", return_value=fake_tk):
        rank = await calc_iv_rank("SPY")

    assert rank == 0.5


# ---------------------------------------------------------------------------
# calc_atm_iv
# ---------------------------------------------------------------------------


OptionChain = namedtuple("OptionChain", ["calls", "puts"])


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
    from datetime import date, timedelta
    return (date.today() + timedelta(days=days_from_today)).isoformat()


@pytest.mark.asyncio
async def test_atm_iv_picks_closest_expiration_and_atm_strike():
    exp_30 = _exp_at(30)
    exp_45 = _exp_at(45)
    exp_60 = _exp_at(60)

    chain_45 = _build_chain(
        strikes=[95.0, 100.0, 105.0, 110.0],
        ivs=[0.30, 0.22, 0.25, 0.28],   # ATM (100) has 0.22
    )
    chain_30 = _build_chain([100.0], [0.99])   # decoy — should NOT be picked
    chain_60 = _build_chain([100.0], [0.99])

    tk = _build_ticker(
        expirations=(exp_30, exp_45, exp_60),
        spot=100.0,
        chain_by_expiry={exp_30: chain_30, exp_45: chain_45, exp_60: chain_60},
    )

    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=45, tolerance_days=7)

    assert iv == pytest.approx(0.22)


@pytest.mark.asyncio
async def test_atm_iv_returns_none_when_no_expiration_in_tolerance():
    # Only expirations far from target_dte=45 ± 7.
    exp_10 = _exp_at(10)
    exp_120 = _exp_at(120)
    chain = _build_chain([100.0], [0.20])

    tk = _build_ticker(
        expirations=(exp_10, exp_120),
        spot=100.0,
        chain_by_expiry={exp_10: chain, exp_120: chain},
    )

    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=45, tolerance_days=7)

    assert iv is None


@pytest.mark.asyncio
async def test_atm_iv_returns_none_on_empty_expirations():
    tk = MagicMock()
    tk.options = ()
    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_atm_iv_returns_none_on_empty_chain():
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
        iv = await calc_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_atm_iv_returns_none_on_non_finite_iv():
    exp_45 = _exp_at(45)
    nan_chain = _build_chain([100.0], [math.nan])
    tk = _build_ticker(
        expirations=(exp_45,),
        spot=100.0,
        chain_by_expiry={exp_45: nan_chain},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_atm_iv_returns_none_on_zero_spot():
    exp_45 = _exp_at(45)
    chain = _build_chain([100.0], [0.20])
    tk = MagicMock()
    tk.options = (exp_45,)
    tk.history.return_value = pd.DataFrame({"Close": [0.0]})
    tk.option_chain.side_effect = lambda exp: chain
    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_atm_iv_returns_none_on_exception():
    tk = MagicMock()
    tk.options = (_exp_at(45),)
    tk.history.side_effect = RuntimeError("network blip")
    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=45)

    assert iv is None


@pytest.mark.asyncio
async def test_atm_iv_tolerance_window_is_respected():
    # target_dte=75, tolerance=15 → expirations in [60, 90] qualify.
    exp_55 = _exp_at(55)   # 20 days off — excluded
    exp_70 = _exp_at(70)   # 5 days off — qualifies
    exp_85 = _exp_at(85)   # 10 days off — qualifies but farther than 70
    chain_55 = _build_chain([100.0], [0.99])
    chain_70 = _build_chain([100.0], [0.21])
    chain_85 = _build_chain([100.0], [0.25])

    tk = _build_ticker(
        expirations=(exp_55, exp_70, exp_85),
        spot=100.0,
        chain_by_expiry={exp_55: chain_55, exp_70: chain_70, exp_85: chain_85},
    )
    with patch("yfinance.Ticker", return_value=tk):
        iv = await calc_atm_iv("SPY", target_dte=75, tolerance_days=15)

    # 70 is closer to 75 than 85 is.
    assert iv == pytest.approx(0.21)
