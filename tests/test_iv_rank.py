"""Tests for trading_corp.utils.iv — calc_iv_rank and calc_atm_iv.

These functions are thin wrappers around the configured provider.  Tests
patch the provider via `_get_configured_provider` so no network calls occur.

The old 0.5 sentinel is gone — tests for insufficient history / flat series /
exception now expect None.
"""
from __future__ import annotations

import math
from collections import namedtuple
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trading_corp.utils.iv import calc_iv_rank, calc_atm_iv


# ---------------------------------------------------------------------------
# Helpers for provider mocking
# ---------------------------------------------------------------------------

def _mock_provider(iv_rank=None, atm_iv=None):
    """Build a fake provider that returns given values."""
    provider = MagicMock()
    provider.get_iv_rank = AsyncMock(return_value=iv_rank)
    provider.get_atm_iv = AsyncMock(return_value=atm_iv)
    return provider


# ---------------------------------------------------------------------------
# calc_iv_rank — delegates to provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calc_iv_rank_returns_high_value():
    provider = _mock_provider(iv_rank=0.85)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        rank = await calc_iv_rank("SPY")
    assert rank == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_calc_iv_rank_returns_low_value():
    provider = _mock_provider(iv_rank=0.12)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        rank = await calc_iv_rank("SPY")
    assert rank == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_calc_iv_rank_returns_none_on_insufficient_history():
    """Insufficient history → None (NOT 0.5 sentinel)."""
    provider = _mock_provider(iv_rank=None)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        rank = await calc_iv_rank("SPY")
    assert rank is None, f"expected None on insufficient history, got {rank}"


@pytest.mark.asyncio
async def test_calc_iv_rank_returns_none_on_flat_series():
    """Flat price series → None (NOT 0.5 sentinel)."""
    provider = _mock_provider(iv_rank=None)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        rank = await calc_iv_rank("SPY")
    assert rank is None, f"expected None on flat series, got {rank}"


@pytest.mark.asyncio
async def test_calc_iv_rank_returns_none_on_exception():
    """Provider exception → None (NOT 0.5 sentinel)."""
    provider = MagicMock()
    provider.get_iv_rank = AsyncMock(side_effect=RuntimeError("network error"))
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        rank = await calc_iv_rank("SPY")
    assert rank is None, f"expected None on exception, got {rank}"


@pytest.mark.asyncio
async def test_calc_iv_rank_returns_none_when_provider_factory_fails():
    """If _get_configured_provider raises (e.g. missing env vars), return None."""
    with patch(
        "trading_corp.utils.iv._get_configured_provider",
        side_effect=ValueError("missing TASTYTRADE_PROVIDER_SECRET"),
    ):
        rank = await calc_iv_rank("SPY")
    assert rank is None


# ---------------------------------------------------------------------------
# calc_atm_iv — delegates to provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calc_atm_iv_returns_value():
    provider = _mock_provider(atm_iv=0.22)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        iv = await calc_atm_iv("SPY", target_dte=45, tolerance_days=7)
    assert iv == pytest.approx(0.22)


@pytest.mark.asyncio
async def test_calc_atm_iv_returns_none_on_no_expiration():
    provider = _mock_provider(atm_iv=None)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        iv = await calc_atm_iv("SPY", target_dte=45, tolerance_days=7)
    assert iv is None


@pytest.mark.asyncio
async def test_calc_atm_iv_returns_none_on_degenerate_iv():
    """Degenerate IV (1e-5) → provider returns None → wrapper returns None."""
    provider = _mock_provider(atm_iv=None)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        iv = await calc_atm_iv("SPY", target_dte=45)
    assert iv is None


@pytest.mark.asyncio
async def test_calc_atm_iv_returns_none_on_exception():
    provider = MagicMock()
    provider.get_atm_iv = AsyncMock(side_effect=RuntimeError("blip"))
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        iv = await calc_atm_iv("SPY", target_dte=45)
    assert iv is None


@pytest.mark.asyncio
async def test_calc_atm_iv_default_tolerance_days():
    """Default tolerance_days=7 is forwarded correctly."""
    provider = _mock_provider(atm_iv=0.25)
    with patch("trading_corp.utils.iv._get_configured_provider", return_value=provider):
        await calc_atm_iv("IWM", target_dte=45)
    provider.get_atm_iv.assert_awaited_once_with("IWM", 45, 7)
