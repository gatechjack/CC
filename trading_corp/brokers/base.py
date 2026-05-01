"""Abstract Broker interface. All concrete brokers (paper, robinhood, coinbase,
fidelity) implement this to keep agents/strategies broker-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from trading_corp.persistence.models import FillEvent, Position, ProposedOrder


@dataclass
class AccountSnapshot:
    account: str
    equity: float
    buying_power: float
    cash: float
    positions: list[Position]


class Broker(ABC):
    """Brokers are the only code allowed to talk to real venues."""

    name: str = "base"
    paper: bool = True  # subclasses set False when in live mode

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def snapshot(self) -> AccountSnapshot: ...

    @abstractmethod
    async def place_order(self, order: ProposedOrder) -> FillEvent: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def quote(self, symbol: str) -> float:
        """Return last trade or mid price for `symbol`."""
