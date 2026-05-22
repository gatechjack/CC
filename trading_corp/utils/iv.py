"""Implied-volatility helpers — IV-rank proxy and ATM IV lookup.

Shared by Fidelity options division (existing) and the Robinhood-joint iron
condor strategy (new).

These functions are thin wrappers that delegate to the configured
MarketDataProvider (see `config/data_providers.yaml`).  The provider is
resolved lazily on first call and mtime-cached so config changes are picked
up automatically.

`calc_iv_rank` returns a [0, 1] HV-proxy rank or None.  The old 0.5 sentinel
is gone — callers must handle None explicitly (skip symbol, tally
`ivr_data_unavailable`).

`calc_atm_iv` returns the at-the-money implied volatility (decimal, e.g.
0.23 = 23% annualized) or None on any failure or degenerate value.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy provider — resolved once, mtime-cached via provider_factory
# ---------------------------------------------------------------------------

_PROVIDER_CONFIG_PATH = Path("config/data_providers.yaml")


def _get_configured_provider():
    """Return the globally-configured MarketDataProvider.

    Reads config/data_providers.yaml lazily on first call.  mtime-cache
    in provider_factory handles config hot-reload.
    """
    from trading_corp.data.provider_factory import get_provider
    return get_provider(strategy_slug=None, config_path=_PROVIDER_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Public interface — unchanged function names and parameter names
# ---------------------------------------------------------------------------

async def calc_iv_rank(symbol: str) -> float | None:
    """Return HV-proxy IV rank for `symbol` as [0, 1] or None.

    None means insufficient history or data failure.  The old 0.5 sentinel
    is gone — callers must add an explicit None branch:
        ivr = await calc_iv_rank(symbol)
        if ivr is None:
            # tally ivr_data_unavailable, skip symbol
            return None
        if ivr * 100 < min_ivr:
            ...
    """
    try:
        provider = _get_configured_provider()
        return await provider.get_iv_rank(symbol)
    except Exception as e:
        log.warning("calc_iv_rank: %s failed: %s", symbol, e)
        return None


async def calc_atm_iv(
    symbol: str,
    target_dte: int,
    tolerance_days: int = 7,
) -> float | None:
    """Return ATM implied volatility for `symbol` or None.

    Picks the expiration closest to `target_dte` within ±tolerance_days.
    Returns None on any failure, missing expiration, or degenerate IV value
    (including the 1e-5 yfinance bug — filtered at the provider boundary).
    """
    try:
        provider = _get_configured_provider()
        return await provider.get_atm_iv(symbol, target_dte, tolerance_days)
    except Exception as e:
        log.warning(
            "calc_atm_iv: %s target_dte=%d ±%d failed: %s",
            symbol, target_dte, tolerance_days, e,
        )
        return None
