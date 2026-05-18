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

    `place_multi_leg` and `get_option_greeks` default to NotImplementedError
    rather than being abstract: only the brokers that need them (Robinhood
    for the iron-condor strategy in v1) override. Coinbase, Bitunix, Kalshi,
    Polymarket inherit the default and never see multi-leg traffic.
    """

    name: str = "base"

    @abstractmethod
    async def place_order(self, order: ProposedOrder) -> FillEvent: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    async def place_multi_leg(
        self, orders: list[ProposedOrder]
    ) -> list[FillEvent]:
        """Submit a multi-leg option combo as a single atomic order.

        All `orders` must share `extra["combo_id"]` and represent legs of
        one combo. `combo_direction` ("credit" | "debit") and
        `net_limit_price` are carried in `extra` on each order — see the
        iron-condor strategy design doc for the full extra-key contract.
        Returns one FillEvent per leg; all share `combo_id` in audit
        downstream. Atomic at the exchange: all legs fill or none do.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support multi-leg combo orders"
        )

    async def get_option_greeks(self, option_id: str) -> dict[str, float | None]:
        """Return Greeks + IV + mark for an option by ID.

        Keys: delta, gamma, theta, vega, iv, mark_price. Values may be
        None if the venue does not publish a field. No open-position
        context required — looks up market data by `option_id` alone.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not expose option Greeks"
        )


# ---------------------------------------------------------------------------
# Combo-cohesion validation helper
#
# Shared by every broker that implements `place_multi_leg` (live Robinhood
# and PaperExecutionBroker today). Centralising the checks here keeps
# error messages identical across live/paper paths, so strategy code can
# rely on the same exception text in tests regardless of which broker is
# wrapped.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComboParams:
    """Combo-level parameters extracted after cohesion validation."""
    combo_id: str
    direction: str          # "credit" | "debit"
    net_limit: float        # always positive
    underlying: str
    quantity: int


def validate_combo_cohesion(orders: list[ProposedOrder]) -> ComboParams:
    """Verify all `orders` represent legs of one combo and return the
    combo-level parameters. Raises ValueError on any inconsistency.

    Checks:
      - At least one order in the list.
      - All legs share `extra.combo_id`.
      - All legs share `extra.combo_direction` ∈ {"credit", "debit"}.
      - All legs share `extra.net_limit_price` (parseable as a positive float).
      - All legs share `qty`.
      - All legs share `underlying` (from `extra.underlying` or `order.symbol`).

    Per-leg fields needed for the venue payload (expiration, strike,
    option_type, position_effect) are validated by the specific broker's
    `place_multi_leg` implementation, not here — they're broker-specific
    payload concerns, not cross-leg cohesion.
    """
    if not orders:
        raise ValueError("validate_combo_cohesion requires at least one order")

    first_extra = orders[0].extra or {}
    combo_id = first_extra.get("combo_id")
    direction = first_extra.get("combo_direction")
    net_limit = first_extra.get("net_limit_price")
    underlying = first_extra.get("underlying") or orders[0].symbol
    quantity = int(orders[0].qty)

    if combo_id is None:
        raise ValueError("place_multi_leg requires extra.combo_id on every leg")
    if direction not in ("credit", "debit"):
        raise ValueError(
            f"combo_direction must be 'credit' or 'debit', got {direction!r}"
        )
    if net_limit is None:
        raise ValueError("place_multi_leg requires extra.net_limit_price")
    try:
        net_limit_f = float(net_limit)
    except (TypeError, ValueError) as e:
        raise ValueError(f"net_limit_price not a number: {net_limit!r}") from e
    if net_limit_f <= 0:
        raise ValueError(f"net_limit_price must be positive, got {net_limit_f}")

    for o in orders:
        ex = o.extra or {}
        if ex.get("combo_id") != combo_id:
            raise ValueError(
                f"mixed combo_ids in place_multi_leg: "
                f"{combo_id!r} vs {ex.get('combo_id')!r}"
            )
        if ex.get("combo_direction") != direction:
            raise ValueError(f"mixed combo_direction in {combo_id!r}")
        if float(ex.get("net_limit_price", 0)) != net_limit_f:
            raise ValueError(f"mismatched net_limit_price in {combo_id!r}")
        if int(o.qty) != quantity:
            raise ValueError(f"mismatched qty in {combo_id!r}")
        if (ex.get("underlying") or o.symbol) != underlying:
            raise ValueError(f"mixed underlying in {combo_id!r}")

    return ComboParams(
        combo_id=str(combo_id),
        direction=direction,
        net_limit=net_limit_f,
        underlying=str(underlying),
        quantity=quantity,
    )
