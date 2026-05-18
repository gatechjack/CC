"""Implied-volatility helpers — IV-rank proxy and ATM IV lookup.

Shared by Fidelity options division (existing) and the Robinhood-joint iron
condor strategy (new). Both pull from yfinance, which gives end-of-day data
that is fine for daily entry decisions but not intraday.

`calc_iv_rank` returns a [0, 1] HV-proxy rank — preserved from
`fidelity_options._calc_iv_rank` for backwards compatibility. Callers that
think in percentage points (e.g. config `min_ivr: 30`) divide by 100 at the
call site.

`calc_atm_iv` returns the at-the-money implied volatility (decimal, e.g.
0.23 = 23% annualized) for an expiration within `tolerance_days` of
`target_dte`. Used by the iron condor's term-structure gate.
"""
from __future__ import annotations

import asyncio
import logging
import math

log = logging.getLogger(__name__)


async def calc_iv_rank(symbol: str) -> float:
    """Approximate IV rank via 30-day rolling historical volatility.

    Returns [0, 1]: 1 = historically high vol (sell premium).
    Actual IV rank requires historical IV data; we use HV as a proxy.

    Returns 0.5 on insufficient data or any failure (neutral fallback so
    callers don't crash). Extracted from
    `agents/divisions/fidelity_options.py` so multiple strategies can share.
    """
    import yfinance as yf  # type: ignore
    import numpy as np     # type: ignore

    def _fn() -> float:
        hist = yf.Ticker(symbol).history(period="1y")
        if len(hist) < 35:
            return 0.5
        log_ret = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
        hv30 = log_ret.rolling(30).std() * math.sqrt(252)
        hv30 = hv30.dropna()
        if len(hv30) < 5:
            return 0.5
        cur = float(hv30.iloc[-1])
        mn = float(hv30.min())
        mx = float(hv30.max())
        return max(0.0, min(1.0, (cur - mn) / (mx - mn))) if mx > mn else 0.5

    try:
        return await asyncio.to_thread(_fn)
    except Exception as e:
        log.warning("calc_iv_rank: %s failed: %s", symbol, e)
        return 0.5


async def calc_atm_iv(
    symbol: str,
    target_dte: int,
    tolerance_days: int = 7,
) -> float | None:
    """Return at-the-money implied volatility for `symbol` at the expiration
    closest to `target_dte` (within ±tolerance_days).

    Returns a decimal (0.23 = 23% annualized). None on any failure:
    yfinance unavailable, no qualifying expiration, empty chain, missing IV.

    ATM = strike closest to current spot, taken from the call chain. Put-call
    parity guarantees the put-side ATM IV is within a fraction of a vol point
    for vanilla options; the call side alone is sufficient for the IC
    term-structure gate.
    """
    def _fn() -> float | None:
        import yfinance as yf  # type: ignore
        from datetime import date

        tk = yf.Ticker(symbol)
        expirations = tk.options or ()
        if not expirations:
            return None

        today = date.today()

        def _dte(exp: str) -> int:
            try:
                return (date.fromisoformat(exp) - today).days
            except (ValueError, TypeError):
                return -10_000  # treat unparseable as far-past so it gets filtered

        candidates = [
            (e, _dte(e)) for e in expirations
            if abs(_dte(e) - target_dte) <= tolerance_days
        ]
        if not candidates:
            return None
        # Closest to target_dte.
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

        # Closest strike to spot.
        diffs = (calls["strike"] - spot).abs()
        atm_idx = diffs.idxmin()
        iv = calls.loc[atm_idx, "impliedVolatility"]
        if iv is None:
            return None
        iv = float(iv)
        if not math.isfinite(iv) or iv <= 0:
            return None
        return iv

    try:
        return await asyncio.to_thread(_fn)
    except Exception as e:
        log.warning(
            "calc_atm_iv: %s target_dte=%d ±%d failed: %s",
            symbol, target_dte, tolerance_days, e,
        )
        return None
