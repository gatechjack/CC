"""Provider-neutral IV / HV math helpers.

Lives here so providers can share IVR math without one provider importing
another's module.  Extracted from `yfinance_provider.py` 2026-05-22 so
`tastytrade_provider.py` no longer imports HV math via the yfinance
provider.
"""
from __future__ import annotations

import math


def _hv_to_rank(close_series) -> float | None:  # type: ignore[type-arg]
    """Compute IV rank from a historical close-price series.

    Approximation: 30-day rolling HV × √252, min/max normalised to [0, 1].
    Returns None on insufficient data (< 35 bars, < 5 rolling-HV values,
    or flat series where min == max).  Returns None, not 0.5 — callers
    decide what to do with missing data.

    Computes IVR from historical volatility — approximation unchanged from
    prior code.  Improvable later by using Tastytrade's real IV history.

    IMPORTANT: this is the SOLE None source for IVR values.
    `_is_degenerate_iv` MUST NOT be called on the output of this function —
    IVR is a [0, 1] rank, not an implied-volatility value.
    """
    import numpy as np  # type: ignore  # noqa: F401  (kept for parity)

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
