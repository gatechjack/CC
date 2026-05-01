"""Convenience wrappers around yfinance for one-off snapshot reads (used by
agents that need historical bars on demand, e.g. Trend Agent).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

log = logging.getLogger(__name__)

Period = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]
Interval = Literal["1m", "5m", "15m", "1h", "1d"]


def history(symbol: str, period: Period = "3mo", interval: Interval = "1d"):
    """Return a pandas DataFrame of OHLCV bars; empty DataFrame on failure."""
    try:
        import yfinance as yf  # type: ignore
        import pandas as pd  # type: ignore
    except ImportError:
        log.warning("yfinance/pandas not installed; returning empty history.")
        return None

    try:
        return yf.download(
            symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        log.warning("yfinance download failed for %s: %s", symbol, e)
        return None
