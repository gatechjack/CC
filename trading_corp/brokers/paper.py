"""Paper-mode broker: in-memory account, deterministic fills against a price source.

Used as default in PAPER mode and as the fallback for any division whose live
broker creds are not configured. Trades are journaled exactly like live trades
so end-to-end flows (Risk → CEO → Board approval → fill → Logger) are exercised.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable

from trading_corp.brokers.base import AccountSnapshot, Broker, validate_combo_cohesion
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

# Default per-leg adverse slippage when the strategy doesn't set
# `extra.paper_per_leg_slippage_dollars`. Matches
# config/strategies.yaml: robinhood_joint_iron_condor.paper_simulation.
_DEFAULT_PAPER_PER_LEG_SLIPPAGE = 0.03

log = logging.getLogger(__name__)

PriceFn = Callable[[str], Awaitable[float]]


class PaperBroker(Broker):
    name = "paper"
    paper = True

    def __init__(
        self,
        account: str = "paper",
        starting_equity: float = 100_000.0,
        price_fn: PriceFn | None = None,
    ) -> None:
        self.account = account
        self._equity = starting_equity
        self._cash = starting_equity
        self._positions: dict[str, Position] = {}
        self._price_fn = price_fn or self._default_price_fn
        self._connected = False

    async def _default_price_fn(self, symbol: str) -> float:
        # Deterministic stub price — used if no real feed is wired.
        # Real feeds replace this in PAPER mode too (yfinance/ccxt sandbox).
        return 100.0

    async def connect(self) -> None:
        self._connected = True
        log.info("PaperBroker connected (account=%s, equity=$%.2f)", self.account, self._equity)

    async def disconnect(self) -> None:
        self._connected = False

    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account=self.account,
            equity=self._equity,
            buying_power=self._cash,
            cash=self._cash,
            positions=list(self._positions.values()),
        )

    async def quote(self, symbol: str) -> float:
        return await self._price_fn(symbol)

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        if not self._connected:
            raise RuntimeError("PaperBroker not connected")

        # Determine fill price.
        if order.order_type == "market" or order.limit_price is None:
            price = await self._price_fn(order.symbol)
        else:
            price = order.limit_price

        signed_qty = order.qty if order.side == "buy" else -order.qty
        notional = signed_qty * price

        # Update cash + position.
        self._cash -= notional
        existing = self._positions.get(order.symbol)
        if existing is None:
            if signed_qty != 0:
                self._positions[order.symbol] = Position(
                    account=self.account,
                    symbol=order.symbol,
                    qty=signed_qty,
                    avg_price=price,
                    opened_ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
        else:
            new_qty = existing.qty + signed_qty
            if new_qty == 0:
                del self._positions[order.symbol]
            else:
                # Weighted average for adds; for reductions keep avg_price.
                if (existing.qty > 0 and signed_qty > 0) or (existing.qty < 0 and signed_qty < 0):
                    existing.avg_price = (
                        existing.avg_price * existing.qty + price * signed_qty
                    ) / new_qty
                existing.qty = new_qty

        # Mark equity (simplified: cash + sum(qty * last_price))
        position_value = 0.0
        for pos in self._positions.values():
            mark = await self._price_fn(pos.symbol)
            position_value += pos.qty * mark
        self._equity = self._cash + position_value

        fill = FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=price,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="paper",
        )
        log.info(
            "PaperBroker FILL %s %s %s @ $%.2f (eq=$%.2f cash=$%.2f)",
            order.side, order.qty, order.symbol, price, self._equity, self._cash,
        )
        # Tiny await so concurrent paper orders interleave realistically.
        await asyncio.sleep(0)
        return fill

    async def cancel_order(self, order_id: str) -> bool:
        # Paper fills are synchronous; nothing to cancel.
        return False


class PaperExecutionBroker(Broker):
    """PAPER mode wrapper: reads from a live broker, executes via PaperBroker.

    Registered as the division broker so the portfolio agent and CEO see the
    real account balance and positions, while all order fills are simulated.
    Every proposed order still flows through Risk gates + Board approval before
    reaching place_order — and place_order here hits PaperBroker, not the venue.
    """
    name = "paper-exec"
    paper = True

    def __init__(self, live: Broker, paper: PaperBroker) -> None:
        self._live = live
        self._paper = paper
        # Rebind the paper broker's price source to the live broker's quote.
        # Without this, every paper market order fills at the PaperBroker's
        # $100 default stub, regardless of the actual venue price — which
        # makes PAPER mode useless for verifying dollar amounts. The live
        # broker's snapshot/quote already passes through unmodified, so
        # this just plumbs the same data into the fill engine.
        paper._price_fn = self._live_price_fn

    async def _live_price_fn(self, symbol: str) -> float:
        """Look up market price via the live broker, with a safe fallback.

        Used as the inner PaperBroker's price source. If the live quote
        fails (network blip, unrecognized symbol, broker offline), fall
        back to 100.0 — the same stub the bare PaperBroker uses — so paper
        flows degrade rather than crash. A warning is logged so the
        Board can see the price source went stale.
        """
        try:
            p = await self._live.quote(symbol)
            if p and p > 0:
                return float(p)
        except Exception as e:
            log.warning(
                "PaperExecutionBroker: live quote failed for %s: %s "
                "(falling back to stub price)",
                symbol, e,
            )
        return 100.0

    async def connect(self) -> None:
        await self._live.connect()
        await self._paper.connect()

    async def disconnect(self) -> None:
        try:
            await self._live.disconnect()
        except Exception:
            pass
        await self._paper.disconnect()

    async def snapshot(self) -> AccountSnapshot:
        return await self._live.snapshot()

    async def quote(self, symbol: str) -> float:
        return await self._live.quote(symbol)

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        return await self._paper.place_order(order)

    async def cancel_order(self, order_id: str) -> bool:
        return await self._paper.cancel_order(order_id)

    # OptionBroker protocol — delegate to live broker
    async def get_option_positions_detail(self) -> list[dict]:
        if hasattr(self._live, "get_option_positions_detail"):
            return await self._live.get_option_positions_detail()  # type: ignore[attr-defined]
        return []

    async def get_expiration_dates(self, symbol: str) -> list[str]:
        if hasattr(self._live, "get_expiration_dates"):
            return await self._live.get_expiration_dates(symbol)  # type: ignore[attr-defined]
        return []

    async def get_calls_for_expiry(self, symbol: str, expiry: str) -> list[dict]:
        if hasattr(self._live, "get_calls_for_expiry"):
            return await self._live.get_calls_for_expiry(symbol, expiry)  # type: ignore[attr-defined]
        return []

    async def get_puts_for_expiry(self, symbol: str, expiry: str) -> list[dict]:
        if hasattr(self._live, "get_puts_for_expiry"):
            return await self._live.get_puts_for_expiry(symbol, expiry)  # type: ignore[attr-defined]
        return []

    async def get_option_quote(self, symbol: str, expiration: str, strike: float,
                               option_type: str) -> dict[str, float | None]:
        # Reads are LIVE even in paper — delegate to the wrapped real broker so
        # combo re-pricing sees the true bid/ask.
        if hasattr(self._live, "get_option_quote"):
            return await self._live.get_option_quote(symbol, expiration, strike, option_type)  # type: ignore[attr-defined]
        return {"bid": None, "ask": None, "mark": None}

    # ------------------------------------------------------------------
    # Multi-leg combo simulation
    #
    # PaperExecutionBroker simulates combo execution locally rather than
    # routing to Robinhood paper accounts (the venue's combo handling
    # for paper is unverified and the design doc resolves this question
    # by keeping the simulator in-process). Slippage model:
    #
    #   sell leg fill = mid - slippage
    #   buy  leg fill = mid + slippage
    #
    # `mid` is read via self._live.get_option_greeks(option_id); falls
    # back to order.limit_price when the option_id isn't stashed in
    # extra or the live read fails. The combo fills only when the
    # simulated net credit/debit satisfies the combo's net_limit_price.
    #
    # FillEvents are returned but PaperBroker's internal _positions
    # dict is NOT updated: stock-shaped position tracking conflates with
    # option positions, and IC position state is tracked in agent_state
    # by the strategy. The audit trail comes from the structured log.info
    # lines below, which step 7's data_exec.place_combo promotes to a
    # real audit_event row.
    # ------------------------------------------------------------------

    async def place_multi_leg(
        self, orders: list[ProposedOrder], *, ref_id: str | None = None,
    ) -> list[FillEvent]:
        # ref_id is accepted for interface parity with the live broker; the paper
        # simulator has no venue to dedupe against, so it is ignored.
        if not orders:
            return []

        combo = validate_combo_cohesion(orders)
        slippage = float(
            (orders[0].extra or {}).get(
                "paper_per_leg_slippage_dollars",
                _DEFAULT_PAPER_PER_LEG_SLIPPAGE,
            )
        )

        # Resolve simulated fill price per leg.
        sim_fills: list[tuple[ProposedOrder, float, float | None]] = []
        for o in orders:
            ex = o.extra or {}
            option_id = ex.get("option_id")
            mid: float | None = None
            if option_id and hasattr(self._live, "get_option_greeks"):
                try:
                    gk = await self._live.get_option_greeks(option_id)
                    raw_mid = gk.get("mark_price") if gk else None
                    if raw_mid is not None:
                        mid = float(raw_mid)
                except Exception as e:
                    log.warning(
                        "PaperExecutionBroker: get_option_greeks(%s) failed "
                        "for combo %s leg: %s — falling back to limit_price",
                        option_id, combo.combo_id, e,
                    )
                    mid = None
            if mid is None or mid <= 0:
                # Fall back to per-leg limit_price; this still lets the
                # simulator exercise the audit/HITL path even without a
                # live quote source.
                mid = float(o.limit_price or 0)
            # Adverse slippage based on the leg's action (buy/sell), NOT
            # on combo role — closes flip the action vs opens.
            sim = mid - slippage if o.side == "sell" else mid + slippage
            sim_fills.append((o, sim, mid))

        # Signed cashflow: + means we received cash net (collected
        # premium); - means we paid net (debit). Per-share / per-contract
        # units; matches the per-share units that robin_stocks's `price`
        # argument expects and that net_limit_price carries.
        cashflow = 0.0
        for o, sim_price, _ in sim_fills:
            ratio = int((o.extra or {}).get("ratio_quantity", 1))
            signed = sim_price if o.side == "sell" else -sim_price
            cashflow += signed * ratio

        # Translate to direction-specific actual + satisfaction check.
        # Tiny epsilon absorbs float-precision drift on the "exactly at
        # limit" boundary — at 1e-9 it's six orders of magnitude below
        # a penny, so it never gives away meaningful money.
        _EPS = 1e-9
        if combo.direction == "credit":
            actual = cashflow                     # expected > 0
            satisfied = actual >= combo.net_limit - _EPS
        else:
            actual = -cashflow                    # debit-as-positive
            satisfied = actual <= combo.net_limit + _EPS

        actual_vs_limit_slippage_dollars = abs(actual - combo.net_limit)

        if not satisfied:
            log.info(
                "paper_combo_unfilled combo=%s direction=%s actual=%.4f "
                "limit=%.4f slippage_per_leg=%.4f legs=%d",
                combo.combo_id, combo.direction, actual,
                combo.net_limit, slippage, len(orders),
            )
            return []

        fill_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fills: list[FillEvent] = [
            FillEvent(
                order_id=o.id,
                symbol=o.symbol,
                side=o.side,
                qty=float(o.qty),
                price=sim_price,
                ts=fill_ts,
                venue="paper-exec",
            )
            for o, sim_price, _ in sim_fills
        ]

        log.info(
            "paper_combo_filled combo=%s direction=%s actual=%.4f "
            "limit=%.4f actual_vs_limit_slippage_dollars=%.4f "
            "slippage_per_leg=%.4f legs=%d",
            combo.combo_id, combo.direction, actual, combo.net_limit,
            actual_vs_limit_slippage_dollars, slippage, len(orders),
        )
        return fills

    async def get_option_greeks(
        self, option_id: str
    ) -> dict[str, float | None]:
        """Pass-through to the wrapped live broker's Greeks lookup.

        If the wrapped broker doesn't expose Greeks (e.g., a PaperBroker
        with no real quote source), returns all-None — caller treats as
        "undetermined" and the IC strategy's tested-side identification
        returns 'neither' (no adjustment fires).
        """
        if hasattr(self._live, "get_option_greeks"):
            return await self._live.get_option_greeks(option_id)
        return {
            "delta": None, "gamma": None, "theta": None,
            "vega": None,  "iv": None,    "mark_price": None,
        }
