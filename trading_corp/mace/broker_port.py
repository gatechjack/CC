"""MACE options broker port — the single broker surface (plan § Architecture).

`OptionsBrokerPort` is the ONLY seam between MACE's pure/execution logic and a
concrete broker. Today `rh_broker.RobinhoodOptionsBroker` is the only impl and
the ONLY mace file that imports `trading_corp.brokers.*`; a future Tasty impl
replaces `rh_broker` alone. Everything here is NEUTRAL — no robin_stocks types
cross this boundary (option handles are opaque strings).

The port is intentionally thin and stateless: execution.py owns the lifecycle
(ladders, PT, reconcile) and the mace_rung writes; the port only talks to the
broker and returns neutral results. Fake-fill guard lives in execution — the
port reports what the broker said; it never fabricates a fill.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date

from trading_corp.mace.domain import CondorSpec, OptionQuote

# Terminal order states (neutral; brokers map their own strings onto these).
STATE_FILLED = "filled"
STATE_PARTIAL = "partially_filled"
STATE_REJECTED = "rejected"
STATE_CANCELLED = "cancelled"
STATE_FAILED = "failed"
STATE_VOIDED = "voided"
STATE_QUEUED = "queued"
STATE_CONFIRMED = "confirmed"
STATE_UNCONFIRMED = "unconfirmed"
TERMINAL_STATES = frozenset(
    {STATE_FILLED, STATE_PARTIAL, STATE_REJECTED, STATE_CANCELLED,
     STATE_FAILED, STATE_VOIDED}
)
DEAD_STATES = frozenset(
    {STATE_REJECTED, STATE_CANCELLED, STATE_FAILED, STATE_VOIDED}
)

# Combo direction (net credit vs net debit) — marketability direction is
# execution's concern; the port just forwards the label + limit.
DIR_CREDIT = "credit"
DIR_DEBIT = "debit"


@dataclass(frozen=True)
class PortSnapshot:
    """Account snapshot on the MACE-bound account (acct-scoped). equity is the
    settled-cash sizing basis owner (execution decides how to use it)."""

    equity: float | None
    cash: float | None = None
    market_value: float | None = None


@dataclass(frozen=True)
class AccountInfo:
    """Account metadata for the fail-closed startup assertions (plan § Startup
    assertion + [A2026-08-09] exclusivity/foreign-position guards)."""

    account_number: str | None
    option_level: int | None
    account_type: str | None = None
    margin: bool = False


@dataclass(frozen=True)
class OrderResult:
    """Neutral order status. `order_id` is the broker's order id (opaque);
    `state` is one of the STATE_* constants (lower-cased, best-effort mapped);
    `processed_quantity` > 0 means at least a partial fill happened — the
    fake-fill guard in execution books ONLY on a confirmed terminal `filled`."""

    order_id: str | None
    state: str
    processed_quantity: float = 0.0
    pending_quantity: float = 0.0
    time_in_force: str | None = None
    account_url: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.state == STATE_FILLED

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_dead(self) -> bool:
        return self.state in DEAD_STATES


@dataclass(frozen=True)
class OpenOrder:
    """One resting/working option order on the account (for the foreign-position
    guard + reconcile matching). `ref_id`/`combo_id` let reconcile match a
    submitting rung by its deterministic id."""

    order_id: str
    state: str
    time_in_force: str | None = None
    ref_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OpenOptionPosition:
    """One open option leg on the account (foreign-position baseline)."""

    symbol: str
    option_id: str | None
    quantity: float
    raw: dict = field(default_factory=dict)


class OptionsBrokerPort(abc.ABC):
    """The single broker surface MACE drives. All methods are neutral in/out.

    Concurrency: implementations may block on network I/O; execution awaits them
    off the event loop (the RH impl uses asyncio.to_thread). Errors: an
    implementation MUST raise on a failed broker call — it must NEVER return a
    synthetic 'filled' (the fake-fill guard depends on truthful reporting)."""

    # ── market data ──────────────────────────────────────────────────────
    @abc.abstractmethod
    async def chain(self, symbol: str) -> "object":
        """Return a neutral chain snapshot for `symbol` (mace.strategy.ChainView
        shape: expiries + per-(expiry,type,strike) OptionQuote + spot). Bounded
        to the strikes execution needs is an implementation choice."""

    @abc.abstractmethod
    async def leg_quote(self, symbol: str, expiry: date, opt_type: str,
                  strike: float) -> OptionQuote | None:
        """A single fresh leg quote (bid/ask/delta), or None if unlisted/no data.
        Used for fresh-mid re-pricing each ladder attempt + management marks."""

    # ── order placement ──────────────────────────────────────────────────
    @abc.abstractmethod
    async def place_condor(self, spec: CondorSpec, contracts: int, net_limit: float,
                     combo_id: str, *, direction: str, time_in_force: str,
                     fill_timeout_s: float) -> OrderResult:
        """Atomically submit the 4-leg condor (single ref_id = combo_id). Polls to
        a terminal state within fill_timeout_s and returns the OrderResult. On a
        no-id/rejected combo the impl RAISES — it never books. direction is
        DIR_CREDIT (open) or DIR_DEBIT (close)."""

    @abc.abstractmethod
    async def place_resting_close(self, spec: CondorSpec, contracts: int,
                            net_debit_limit: float, ref_id: str) -> str:
        """Place the resting GTC buy-to-close (profit target) and return its
        order id WITHOUT polling (it rests). Net-debit close direction."""

    @abc.abstractmethod
    async def cancel(self, order_id: str) -> None:
        """Request cancel of a working order (idempotent; caller polls to terminal)."""

    @abc.abstractmethod
    async def order_status(self, order_id: str) -> OrderResult:
        """Fetch the current status of an order (reconcile + cancel-race polling)."""

    @abc.abstractmethod
    async def open_orders(self) -> list[OpenOrder]:
        """All working option orders on the MACE-bound account."""

    @abc.abstractmethod
    async def open_positions(self) -> list[OpenOptionPosition]:
        """All open option positions on the account (foreign-position baseline)."""

    # ── account ──────────────────────────────────────────────────────────
    @abc.abstractmethod
    async def snapshot(self) -> PortSnapshot:
        """Account snapshot on the MACE-bound account (acct-scoped)."""

    @abc.abstractmethod
    async def account_assertions(self) -> AccountInfo:
        """Resolve account metadata for the fail-closed startup assertions."""
