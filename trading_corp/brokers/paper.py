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

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

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
