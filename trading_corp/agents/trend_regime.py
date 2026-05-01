"""Trend / Regime Agent.

Computes a market regime label (uptrend / downtrend / chop) from a benchmark
symbol's recent price history. Uses simple, well-known indicators (SMA cross,
realized volatility band) so the signal is debuggable; an optional LLM call
provides a short narrative.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

Regime = Literal["uptrend", "downtrend", "chop", "unknown"]


@dataclass
class TrendReading:
    regime: Regime
    confidence: float            # 0..1
    benchmark: str
    sma_50: float | None
    sma_200: float | None
    realized_vol: float | None   # annualized, decimal
    notes: str = ""


class TrendAgent:
    def __init__(self, benchmark: str = "SPY") -> None:
        self.benchmark = benchmark

    def read(self) -> TrendReading:
        try:
            from trading_corp.data.yfinance_fallback import history
            df = history(self.benchmark, period="1y", interval="1d")
        except Exception as e:
            log.warning("TrendAgent failed to fetch history: %s", e)
            df = None

        if df is None or len(df) < 50:
            return TrendReading(
                regime="unknown",
                confidence=0.0,
                benchmark=self.benchmark,
                sma_50=None, sma_200=None, realized_vol=None,
                notes="no history available",
            )

        try:
            import numpy as np  # type: ignore
            close = df["Close"].astype(float).values.flatten()
            sma50 = float(close[-50:].mean()) if len(close) >= 50 else None
            sma200 = float(close[-200:].mean()) if len(close) >= 200 else None
            rets = np.diff(close[-30:]) / close[-30:-1] if len(close) >= 31 else None
            rvol = float(np.std(rets) * (252 ** 0.5)) if rets is not None and len(rets) > 0 else None
        except Exception as e:
            log.warning("TrendAgent calc failed: %s", e)
            return TrendReading("unknown", 0.0, self.benchmark, None, None, None, str(e))

        last = float(close[-1])
        if sma50 is not None and sma200 is not None:
            if last > sma50 > sma200:
                regime: Regime = "uptrend"
                conf = min(1.0, (last - sma200) / sma200 * 4)
            elif last < sma50 < sma200:
                regime = "downtrend"
                conf = min(1.0, (sma200 - last) / sma200 * 4)
            else:
                regime = "chop"
                conf = 0.5
        else:
            regime = "chop"
            conf = 0.3

        return TrendReading(
            regime=regime,
            confidence=round(conf, 3),
            benchmark=self.benchmark,
            sma_50=round(sma50, 2) if sma50 else None,
            sma_200=round(sma200, 2) if sma200 else None,
            realized_vol=round(rvol, 4) if rvol else None,
            notes=f"last={last:.2f}",
        )
