"""Abstract broker interfaces.

Two ABCs, parent → child:

  ReadOnlyBroker
      connect / disconnect / snapshot / quote
      No order-placement methods. Adapters that subclass this CANNOT
      place orders — enforced by the type system, not by runtime flags.
      Use this for venues we want to read but never write to (e.g.
      Polymarket Phase 1, future read-only views of any broker).

  Broker(ReadOnlyBroker)
      Adds place_order + cancel_order.
      Use this for venues that route real orders.

Why split: read-only-by-missing-methods is a stronger guarantee than
read-only-by-config-flag. A code path that calls `broker.place_order`
on a `ReadOnlyBroker` is a static type error, not a runtime
NotImplementedError. CLAUDE.md §1 "Code path isolation" makes this
the rule for new read-only adapters.

Existing concrete brokers (paper, robinhood, coinbase, fidelity,
bitunix) all subclass `Broker` directly. They keep the full surface
including place_order; behavior is unchanged. The Fidelity-specific
migration to ReadOnlyBroker is tracked separately (CLAUDE.md §7
"Known sharp edges") and is NOT part of this split.
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


class ReadOnlyBroker(ABC):
    """Brokers that can read venue state but cannot place orders.

    Subclass this when the venue is observe-only (Polymarket Phase 1,
    any read-only data feed). Order-placement methods are deliberately
    absent — calling `place_order` on a ReadOnlyBroker is a static
    type error, not a runtime exception.
    """

    name: str = "base-readonly"
    paper: bool = True  # subclasses set False when in live mode

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def snapshot(self) -> AccountSnapshot: ...

    @abstractmethod
    async def quote(self, symbol: str) -> float:
        """Return last trade or mid price for `symbol`."""


class Broker(ReadOnlyBroker):
    """Full broker — read state AND place orders.

    Existing concrete brokers (paper, robinhood, coinbase, fidelity,
    bitunix) all subclass this. New adapters that need to place orders
    subclass this; new adapters that don't subclass `ReadOnlyBroker`
    above.
    """

    name: str = "base"

    @abstractmethod
    async def place_order(self, order: ProposedOrder) -> FillEvent: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...
