"""Tastytrade broker — order placement on top of the existing data provider.

Auth: env vars TASTYTRADE_PROVIDER_SECRET and TASTYTRADE_REFRESH_TOKEN
(same as TastytradeDataProvider). OAuth-refresh-token model: the SDK's
`Session.refresh()` exchanges the long-lived refresh_token for a
short-lived access (session) token but **does NOT self-rotate the
refresh_token itself** (verified 2026-05-29 against tastytrade SDK
source — `Session.refresh()` only updates `self.session_token` +
`self.session_expiration`; `self.refresh_token` is never written).
Refresh token rotation is manual per
`runbooks/tastytrade_oauth_rotation.md`. See also
`[[tastytrade-refresh-token-no-self-rotation]]`.

Mode flag: `is_test=True` routes Session to Tastytrade's cert/sandbox
environment (CERT_URL). Live trading uses `is_test=False` (default).

Greeks/IV: NOT served by this broker directly — a TastytradeDataProvider
instance is injected at construction and `get_option_greeks` delegates
to it, so we don't open a second dxFeed subscription per division.

Multi-leg combos: `place_multi_leg` builds a single NewOrder with 4
Leg children (NOT NewComplexOrder — that's for OCO/OTO bundles; iron
condors are a single 4-leg limit order). Price sign carries credit/debit:
positive Decimal = credit (we receive), negative = debit (we pay).
Submission is atomic at the venue — either all 4 legs fill or none.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from trading_corp.brokers.base import (
    AccountSnapshot, Broker, validate_combo_cohesion,
)
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

log = logging.getLogger(__name__)

# Poll cadence for PlacedOrderResponse status after submission. TT order
# routing is sub-second on a clean market; 10s cap covers slow-fill liquidity.
_FILL_POLL_INTERVAL_SEC = 0.5
_FILL_POLL_TIMEOUT_SEC = 10.0

# Terminal OrderStatus values (poll exit conditions).
_TERMINAL_STATUSES = frozenset({
    "Filled", "Cancelled", "Rejected", "Expired", "Removed", "Partially Removed",
})


def _occ_symbol(
    underlying: str, expiration: date | str, option_type: str, strike: float,
) -> str:
    """Build a 21-char OCC option symbol from leg components.

    Standard format: ``ROOT(6) + YYMMDD(6) + C|P(1) + STRIKE*1000(8)``.
    Root is left-justified and padded with spaces to 6 chars.

    Example: ``SPY   240920C00500000`` (SPY 2024-09-20 $500 call).
    """
    if isinstance(expiration, str):
        # IC strategy passes ISO date strings; parse.
        expiration = date.fromisoformat(expiration[:10])
    root = underlying.upper().ljust(6, " ")
    exp = expiration.strftime("%y%m%d")
    cp = "C" if str(option_type).lower().startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    return f"{root}{exp}{cp}{strike_int:08d}"


def _order_action(side: str, position_effect: str) -> str:
    """Map (side, position_effect) to a Tastytrade OrderAction string.

    TT enums: Buy to Open / Buy to Close / Sell to Open / Sell to Close.
    """
    side = side.lower()
    effect = position_effect.lower()
    if side == "buy" and effect == "open":
        return "Buy to Open"
    if side == "buy" and effect == "close":
        return "Buy to Close"
    if side == "sell" and effect == "open":
        return "Sell to Open"
    if side == "sell" and effect == "close":
        return "Sell to Close"
    raise ValueError(f"unrecognized (side={side!r}, effect={effect!r})")


class TastytradeBroker(Broker):
    """Tastytrade order placement + account read. Pairs with TastytradeDataProvider for data."""

    name = "tastytrade"
    paper = False

    def __init__(
        self,
        provider_secret: str | None = None,
        refresh_token: str | None = None,
        account_filter: str | None = None,
        is_test: bool = False,
        data_provider: Optional[Any] = None,
    ) -> None:
        ps = provider_secret or os.environ.get("TASTYTRADE_PROVIDER_SECRET")
        rt = refresh_token or os.environ.get("TASTYTRADE_REFRESH_TOKEN")
        if not ps:
            raise ValueError(
                "TastytradeBroker requires TASTYTRADE_PROVIDER_SECRET env var "
                "(or provider_secret constructor arg)"
            )
        if not rt:
            raise ValueError(
                "TastytradeBroker requires TASTYTRADE_REFRESH_TOKEN env var "
                "(or refresh_token constructor arg)"
            )
        self._provider_secret = ps
        self._refresh_token = rt
        self._account_filter = (account_filter or "").strip() or None
        self._is_test = is_test
        self._data_provider = data_provider
        self._session: Any = None
        self._account: Any = None       # resolved Account object after connect()
        self._account_number: str = ""
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        from tastytrade import Session
        from tastytrade.account import Account

        self._session = await asyncio.to_thread(
            Session,
            provider_secret=self._provider_secret,
            refresh_token=self._refresh_token,
            is_test=self._is_test,
        )
        accounts = await Account.get(self._session)
        if not accounts:
            raise RuntimeError(
                "TastytradeBroker.connect: no accounts on this Tastytrade session "
                f"(is_test={self._is_test})"
            )
        self._account = self._resolve_account(accounts)
        self._account_number = self._account.account_number
        self._connected = True
        log.info(
            "TastytradeBroker connected: account=%s, is_test=%s, n_accounts=%d",
            self._account_number, self._is_test, len(accounts),
        )

    async def disconnect(self) -> None:
        # SDK manages its own httpx client lifecycle; just drop our reference.
        self._session = None
        self._account = None
        self._connected = False

    def _resolve_account(self, accounts: list[Any]) -> Any:
        """Pick the account matching `account_filter` (substring or exact match).

        Filter matches against account_number first, then nickname.
        Empty filter → first account.
        """
        if not self._account_filter:
            return accounts[0]
        needle = self._account_filter.lower()
        for acct in accounts:
            num = (getattr(acct, "account_number", "") or "").lower()
            nick = (getattr(acct, "nickname", "") or "").lower()
            if needle in num or needle in nick:
                return acct
        raise RuntimeError(
            f"TastytradeBroker: account_filter={self._account_filter!r} matched "
            f"none of {[getattr(a, 'account_number', '?') for a in accounts]}"
        )

    def _require_connected(self) -> None:
        if not self._connected or self._account is None:
            raise RuntimeError(
                "TastytradeBroker not connected — call connect() first"
            )

    # ------------------------------------------------------------------
    # Account state
    # ------------------------------------------------------------------

    async def snapshot(self) -> AccountSnapshot:
        self._require_connected()

        balances = await self._account.get_balances(self._session)
        positions_raw = await self._account.get_positions(self._session)

        # net_liquidating_value is TT's "equity" (account value if liquidated now).
        equity = float(balances.net_liquidating_value or 0)
        buying_power = float(balances.derivative_buying_power or balances.equity_buying_power or 0)
        cash = float(balances.cash_balance or 0)

        positions: list[Position] = []
        for cp in (positions_raw or []):
            qty_raw = float(cp.quantity or 0)
            if qty_raw == 0:
                continue
            # Sign by direction — TT stores quantity as magnitude with a
            # separate direction field. Strategy code expects signed qty.
            direction = (getattr(cp, "quantity_direction", "") or "").lower()
            signed_qty = qty_raw if direction != "short" else -qty_raw
            positions.append(Position(
                account=self._account_number,
                symbol=cp.symbol,  # OCC for options, ticker for equities
                qty=signed_qty,
                avg_price=float(cp.average_open_price or 0),
                opened_ts=str(cp.created_at or ""),
                extra={
                    "instrument_type": str(cp.instrument_type or ""),
                    "underlying_symbol": str(getattr(cp, "underlying_symbol", "") or ""),
                    "multiplier": float(getattr(cp, "multiplier", 1) or 1),
                },
            ))

        return AccountSnapshot(
            account=self._account_number,
            equity=equity,
            buying_power=buying_power,
            cash=cash,
            positions=positions,
        )

    async def quote(self, symbol: str) -> float:
        """Last/mark price for an equity symbol. Options not supported here —
        use get_option_greeks (which carries mark)."""
        self._require_connected()
        from tastytrade.market_data import get_market_data
        from tastytrade.order import InstrumentType
        try:
            md = await get_market_data(
                self._session, symbol, InstrumentType.EQUITY,
            )
        except Exception as e:
            log.warning("TastytradeBroker.quote(%s) failed: %s", symbol, e)
            return 0.0
        # MarketData carries last + bid + ask + mark; use mark when present.
        for field in ("mark", "last", "ask"):
            val = getattr(md, field, None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return 0.0

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        """Single-leg equity or option order.

        For iron-condor work, use place_multi_leg instead — single-leg
        opens/closes are reserved for non-combo paths (rare in this division).
        """
        self._require_connected()
        leg = self._build_leg_from_order(order)
        new_order = self._build_new_order(
            legs=[leg],
            price=Decimal(str(order.limit_price or 0)),
        )
        return (await self._submit_and_wait([order], new_order))[0]

    async def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            log.warning("TastytradeBroker.cancel_order: non-int order_id %r", order_id)
            return False
        try:
            await self._account.delete_order(self._session, oid)
            return True
        except Exception as e:
            log.warning("TastytradeBroker.cancel_order(%s) failed: %s", oid, e)
            return False

    async def place_multi_leg(
        self, orders: list[ProposedOrder], *, dry_run: bool = False,
        ref_id: str | None = None,
    ) -> list[FillEvent]:
        """Submit a multi-leg option combo as a single atomic NewOrder.

        `ref_id` is accepted for Broker-interface parity; the tastytrade adapter
        does not thread a client order id today (no-op).

        All `orders` share combo_id / combo_direction / net_limit_price /
        underlying / qty (validated by validate_combo_cohesion). Each
        leg provides its own expiration / strike / option_type /
        position_effect / side via `extra`.

        Price sign: positive Decimal = credit (we receive premium),
        negative = debit (we pay). The IC strategy passes combo.direction
        as "credit" | "debit" and combo.net_limit as an unsigned price.

        Atomic at TT — partial fills on a single 4-leg order do not
        happen (TT either fills the whole combo at the net price or
        rejects). Partial-fill exception raised if observed (defensive).

        `dry_run=True` asks TT to validate the order without placing it.
        Used by the Phase-0 sandbox smoke to verify broker-shape
        end-to-end without risking a working order on the account; not
        used by the live IC strategy path.
        """
        self._require_connected()
        if not orders:
            return []

        combo = validate_combo_cohesion(orders)

        legs = [self._build_leg_from_order(o) for o in orders]
        signed_price = (
            Decimal(str(combo.net_limit))
            if combo.direction == "credit"
            else -Decimal(str(combo.net_limit))
        )
        new_order = self._build_new_order(legs=legs, price=signed_price)
        return await self._submit_and_wait(orders, new_order, dry_run=dry_run)

    async def get_option_greeks(
        self, option_id: str,
    ) -> dict[str, float | None]:
        """Greeks for an option by ID, delegated to the injected data provider.

        We deliberately do not open a second dxFeed subscription here —
        the data provider already streams Greeks. If no data provider
        was injected, NotImplementedError (matches base-class default).
        """
        if self._data_provider is None:
            raise NotImplementedError(
                "TastytradeBroker.get_option_greeks: no data_provider injected; "
                "construct TastytradeBroker(data_provider=TastytradeDataProvider(...))"
            )
        return await self._data_provider.get_greeks(option_id)

    # ------------------------------------------------------------------
    # Internals — order construction + submission
    # ------------------------------------------------------------------

    def _build_leg_from_order(self, order: ProposedOrder) -> Any:
        """Construct a tastytrade.order.Leg from a ProposedOrder.

        Builds the OCC symbol from extra.expiration/strike/option_type;
        action is derived from order.side + extra.position_effect.
        """
        from tastytrade.order import Leg, OrderAction, InstrumentType

        extra = order.extra or {}
        for required in ("expiration", "strike", "option_type", "position_effect"):
            if required not in extra:
                raise ValueError(
                    f"TastytradeBroker leg missing required extra key {required!r} "
                    f"on order {order.id}"
                )
        # Underlying root: prefer extra.underlying (combo carries it explicitly)
        # else fall back to order.symbol.
        underlying = extra.get("underlying") or order.symbol
        occ = _occ_symbol(
            underlying=underlying,
            expiration=extra["expiration"],
            option_type=extra["option_type"],
            strike=float(extra["strike"]),
        )
        action_str = _order_action(order.side, extra["position_effect"])
        qty = int(extra.get("ratio_quantity", 1)) * int(order.qty)
        return Leg(
            instrument_type=InstrumentType.EQUITY_OPTION,
            symbol=occ,
            action=OrderAction(action_str),
            quantity=Decimal(qty),
        )

    def _build_new_order(self, *, legs: list[Any], price: Decimal) -> Any:
        """Build a NewOrder for limit submission.

        time_in_force=Day (matches RH Joint's gfd — no resting GTC).
        order_type=Limit.
        """
        from tastytrade.order import NewOrder, OrderType, OrderTimeInForce
        return NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=legs,
            price=price,
        )

    async def _submit_and_wait(
        self,
        source_orders: list[ProposedOrder],
        new_order: Any,
        *,
        dry_run: bool = False,
    ) -> list[FillEvent]:
        """Place `new_order` and poll until terminal, then map to FillEvents.

        Returns one FillEvent per input order (preserves input order).
        Raises on rejection or timeout — caller (strategy) treats this
        as a failed combo and does not record fills.

        `dry_run=True` asks TT to validate-only. No order is placed; no
        polling; returns an empty list. Smoke probes use this to verify
        end-to-end broker shape without risking a working order.
        """
        response = await self._account.place_order(
            self._session, new_order, dry_run,
        )
        if dry_run:
            log.info(
                "TastytradeBroker dry_run: TT validated combo (no order placed); "
                "warnings=%s errors=%s",
                getattr(response, "warnings", None),
                getattr(response, "errors", None),
            )
            return []
        placed_order = response.order
        order_id = placed_order.id

        terminal = await self._poll_to_terminal(order_id)
        status = str(terminal.status.value if hasattr(terminal.status, "value") else terminal.status)
        if status != "Filled":
            raise RuntimeError(
                f"TastytradeBroker order {order_id} terminal status={status!r}; "
                f"no fills recorded"
            )

        fill_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Per-leg fill price: prefer the per-leg fills array if TT populates it,
        # else fall back to the order's per-leg price field, else the input
        # order's limit_price (combo-net is authoritative for P&L anyway).
        per_leg_prices = self._extract_leg_fill_prices(terminal, len(source_orders))

        return [
            FillEvent(
                order_id=str(order_id),
                symbol=o.symbol,
                side=o.side,
                qty=float(o.qty),
                price=per_leg_prices[i] if per_leg_prices[i] is not None else float(o.limit_price or 0),
                ts=fill_ts,
                venue="tastytrade",
            )
            for i, o in enumerate(source_orders)
        ]

    async def _poll_to_terminal(self, order_id: int) -> Any:
        """Poll get_order until status is terminal or timeout elapses."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _FILL_POLL_TIMEOUT_SEC
        while True:
            placed = await self._account.get_order(self._session, order_id)
            status_val = placed.status.value if hasattr(placed.status, "value") else str(placed.status)
            if status_val in _TERMINAL_STATUSES:
                return placed
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"TastytradeBroker order {order_id} did not reach terminal "
                    f"state within {_FILL_POLL_TIMEOUT_SEC}s (last status={status_val!r})"
                )
            await asyncio.sleep(_FILL_POLL_INTERVAL_SEC)

    @staticmethod
    def _extract_leg_fill_prices(
        placed: Any, n_legs: int,
    ) -> list[float | None]:
        """Best-effort per-leg fill price extraction from a PlacedOrder.

        Returns a list of length n_legs; None for legs where price can't
        be resolved. Strategy uses combo-net for P&L so per-leg precision
        is best-effort.
        """
        out: list[float | None] = [None] * n_legs
        legs = getattr(placed, "legs", None) or []
        for i in range(min(n_legs, len(legs))):
            leg = legs[i]
            fills = getattr(leg, "fills", None) or []
            if fills:
                # Average across fills (single-fill is typical for limit orders).
                prices = []
                for f in fills:
                    p = getattr(f, "fill_price", None)
                    if p is not None:
                        try:
                            prices.append(float(p))
                        except (TypeError, ValueError):
                            pass
                if prices:
                    out[i] = sum(prices) / len(prices)
        return out
