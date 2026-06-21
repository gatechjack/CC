"""Market data provider abstraction.

Boundary contract: returns None/empty on missing or degenerate data — no
floored sentinels. Every IV-bearing value is filtered through
`_is_degenerate_iv` at the provider boundary before returning to callers.

`MarketDataProvider` is an ABC mirroring `brokers/base.py:Broker`. All
methods have `NotImplementedError` defaults (no `@abstractmethod`) so
partial implementations can be instantiated for testing or incremental
rollout.
"""
from __future__ import annotations

import math
import logging
from abc import ABC
from dataclasses import dataclass
from datetime import date
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from trading_corp.data.earnings_provider import QuarterlyEPS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OptionContract — provider surface only; broker adapters remain list[dict]
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptionContract:
    """Normalised option row returned by MarketDataProvider.get_option_chain.

    All numeric fields are float | None.  Consumers must handle None on any
    field; provider implementations fill what the upstream data source
    actually returns.
    """
    option_id: str
    expiration_date: str                  # ISO-8601 date string
    strike: float | None
    option_type: str                      # "call" or "put"
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    iv: float | None
    mark: float | None
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    open_interest: float | None
    volume: float | None
    dte: float | None


# ---------------------------------------------------------------------------
# Degenerate-IV guard — single source of truth for the 1e-5 fix
# ---------------------------------------------------------------------------

def _is_degenerate_iv(iv: float | None) -> bool:
    """Return True if `iv` is not a plausible implied-volatility value.

    IMPORTANT: Applies to implied volatility values ONLY — never to
    normalised IVR.  IVR is a [0, 1] rank; a legitimate IVR of 0.005
    means current IV is at the rock-bottom of its 252-day range and is a
    valid reading.  The only `None` source for IVR is `_hv_to_rank`
    returning None on insufficient data — a different failure mode.
    Never call `_is_degenerate_iv` on IVR output.

    Checks performed:
    - iv is None
    - not finite (inf, nan)
    - iv <= 0
    - iv < 0.01  # an order of magnitude below the lowest plausible real
                 # ETF IV (~0.06); catches 1e-5 without false-positives on
                 # real low-vol instruments; not a tunable threshold
    """
    if iv is None:
        return True
    if not math.isfinite(iv):
        return True
    if iv <= 0:
        return True
    if iv < 0.01:  # catches 1e-5 degenerate yfinance values
        return True
    return False


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class MarketDataProvider(ABC):
    """Abstract market-data provider.

    All methods raise NotImplementedError by default — NOT marked
    @abstractmethod so partial implementations can be instantiated.
    Concrete providers override only the methods they support.

    Boundary contract: return None / empty list on any data gap; never
    return floored sentinel values.
    """

    async def get_option_chain(
        self,
        symbol: str,
        expiration: date,
    ) -> list[OptionContract]:
        """Return all option contracts for `symbol` at `expiration`.

        Returns an empty list on any failure.  Degenerate-IV rows are
        dropped at the provider boundary.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_option_chain not implemented"
        )

    async def get_greeks(
        self,
        option_id: str,
    ) -> dict[str, float | None] | None:
        """Return Greeks dict for a single option.

        Keys mirror Broker.get_option_greeks:
        delta, gamma, theta, vega, iv, mark_price.
        Returns None on any failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_greeks not implemented"
        )

    async def get_atm_iv(
        self,
        symbol: str,
        target_dte: int,
        tolerance_days: int = 7,
    ) -> float | None:
        """Return ATM implied volatility (decimal, e.g. 0.23 = 23 %).

        Picks the expiration closest to `target_dte` within ±tolerance_days.
        Returns None on any failure or if the IV is degenerate.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_atm_iv not implemented"
        )

    async def get_iv_rank(self, symbol: str) -> float | None:
        """Return IV rank proxy as [0, 1].

        Returns None on insufficient history or any failure.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_iv_rank not implemented"
        )

    async def get_underlying_price(self, symbol: str) -> float | None:
        """Return current underlying price.

        Returns None if price is unavailable, zero, or negative.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_underlying_price not implemented"
        )

    def get_quarterly_eps(
        self,
        symbol: str,
    ) -> "list[QuarterlyEPS] | None":
        """Return >=8 most-recent quarterly EPS rows (oldest→newest), or None.

        None means no data available — callers should treat as "don't block"
        rather than fail-safe escalating (thinly-traded names often lack data).
        Implemented by EarningsProvider; raises NotImplementedError on the base.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_quarterly_eps not implemented"
        )

    def get_recent_announcements(
        self,
        on_date: date,
        lookback_days: int = 1,
    ) -> list[str]:
        """Return symbols that reported earnings on/within lookback_days of on_date.

        Returns empty list on any failure or if provider does not implement it.
        Implemented by EarningsProvider; raises NotImplementedError on the base.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.get_recent_announcements not implemented"
        )
