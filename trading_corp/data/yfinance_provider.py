"""YFinance-backed MarketDataProvider — LABELED FALLBACK / DEGRADED.

Do NOT use this as a primary provider for production options strategies.
Known limitations:
  - EOD data only (not intraday).
  - Intermittent missing back-month chains on illiquid names.
  - Degenerate impliedVolatility (e.g. 1e-5) under some market
    conditions — filtered at the boundary by `_is_degenerate_iv`.
  - Historical data gaps cause IVR to return None (no 0.5 sentinel).

This provider wraps the original utils/iv.py logic verbatim with the
`_is_degenerate_iv` boundary fix applied so the 1e-5 bug is caught in
this path too.  Config selects the primary provider; no automatic
failover from Tastytrade to yfinance occurs.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date

from trading_corp.data.market_data_provider import (
    MarketDataProvider,
    OptionContract,
    _is_degenerate_iv,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure IVR math — extracted from utils/iv.py lines 38-50
# ---------------------------------------------------------------------------

def _hv_to_rank(close_series) -> float | None:  # type: ignore[type-arg]
    """Compute IV rank from a historical close-price series.

    Approximation: 30-day rolling HV × √252, min/max normalised to [0, 1].
    Returns None on insufficient data (< 35 bars, < 5 rolling-HV values,
    or flat series where min == max).  Returns None, not 0.5 — callers
    decide what to do with missing data.

    Computes IVR from historical volatility — approximation unchanged from
    prior code.  Improvable later by using Tastytrade's real IV history.
    Follow-up, not in scope here.

    IMPORTANT: this is the SOLE None source for IVR values.
    `_is_degenerate_iv` MUST NOT be called on the output of this function —
    IVR is a [0, 1] rank, not an implied-volatility value.
    """
    import numpy as np  # type: ignore

    if len(close_series) < 35:
        return None
    log_ret = (close_series / close_series.shift(1)).apply(math.log).dropna()
    hv30 = log_ret.rolling(30).std() * math.sqrt(252)
    hv30 = hv30.dropna()
    if len(hv30) < 5:
        return None
    cur = float(hv30.iloc[-1])
    mn = float(hv30.min())
    mx = float(hv30.max())
    if mx <= mn:
        return None
    return max(0.0, min(1.0, (cur - mn) / (mx - mn)))


class YFinanceDataProvider(MarketDataProvider):
    """YFinance-backed market data provider.

    LABELED FALLBACK.  See module docstring for limitations.
    """

    async def get_iv_rank(self, symbol: str) -> float | None:
        """Approximate IV rank via 30-day rolling historical volatility.

        Returns [0, 1] or None.  None replaces the old 0.5 sentinel —
        callers must handle None explicitly (skip symbol + tally
        ivr_data_unavailable).
        """
        def _fn() -> float | None:
            import yfinance as yf  # type: ignore
            hist = yf.Ticker(symbol).history(period="1y")
            return _hv_to_rank(hist["Close"])

        try:
            return await asyncio.to_thread(_fn)
        except Exception as e:
            log.warning("YFinanceDataProvider.get_iv_rank: %s failed: %s", symbol, e)
            return None

    async def get_atm_iv(
        self,
        symbol: str,
        target_dte: int,
        tolerance_days: int = 7,
    ) -> float | None:
        """Return ATM IV from yfinance option chain.

        Applies `_is_degenerate_iv` at the boundary — returns None if the
        chain reports a degenerate value like 1e-5.
        """
        def _fn() -> float | None:
            import yfinance as yf  # type: ignore

            tk = yf.Ticker(symbol)
            expirations = tk.options or ()
            if not expirations:
                return None

            today = date.today()

            def _dte(exp: str) -> int:
                try:
                    return (date.fromisoformat(exp) - today).days
                except (ValueError, TypeError):
                    return -10_000

            candidates = [
                (e, _dte(e)) for e in expirations
                if abs(_dte(e) - target_dte) <= tolerance_days
            ]
            if not candidates:
                return None
            expiry = min(candidates, key=lambda x: abs(x[1] - target_dte))[0]

            hist = tk.history(period="5d")
            if hist is None or hist.empty:
                return None
            spot = float(hist["Close"].iloc[-1])
            if spot <= 0:
                return None

            chain = tk.option_chain(expiry)
            calls = getattr(chain, "calls", None)
            if calls is None or len(calls) == 0:
                return None

            diffs = (calls["strike"] - spot).abs()
            atm_idx = diffs.idxmin()
            iv_raw = calls.loc[atm_idx, "impliedVolatility"]
            if iv_raw is None:
                return None
            iv = float(iv_raw)
            # Boundary guard: filters 1e-5 degenerate values
            if _is_degenerate_iv(iv):
                log.debug(
                    "YFinanceDataProvider.get_atm_iv: %s degenerate IV %.2e — returning None",
                    symbol, iv,
                )
                return None
            return iv

        try:
            return await asyncio.to_thread(_fn)
        except Exception as e:
            log.warning(
                "YFinanceDataProvider.get_atm_iv: %s target_dte=%d ±%d failed: %s",
                symbol, target_dte, tolerance_days, e,
            )
            return None

    async def get_underlying_price(self, symbol: str) -> float | None:
        """Return last close price from yfinance."""
        def _fn() -> float | None:
            import yfinance as yf  # type: ignore
            hist = yf.Ticker(symbol).history(period="5d")
            if hist is None or hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            return price if price > 0 else None

        try:
            return await asyncio.to_thread(_fn)
        except Exception as e:
            log.warning("YFinanceDataProvider.get_underlying_price: %s failed: %s", symbol, e)
            return None
