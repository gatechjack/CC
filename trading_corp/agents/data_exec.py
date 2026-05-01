"""Data & Execution Agent — non-LLM. Owns brokers, places orders, runs feeds.

Strategy/division agents emit `ProposedOrder`s. After Risk + Board approval,
the CEO graph hands the order here. This agent is also the home of the
FeedAggregator and broker registry.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.brokers.paper import PaperBroker
from trading_corp.data.feeds import FeedAggregator
from trading_corp.persistence.models import FillEvent, ProposedOrder
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)


class DataExecAgent:
    def __init__(self, logger: LoggerAgent, *, dry_run: bool = False) -> None:
        self.logger = logger
        self.brokers: dict[str, Broker] = {}      # division -> Broker
        self.feeds = FeedAggregator()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        # When True, place() short-circuits before broker.place_order() —
        # builds a synthetic FillEvent at the limit price and logs a
        # `dry_run_skip` audit event instead of actually routing the order.
        # Used for first-time LIVE validation: real auth + real reads + real
        # risk gates + real order construction, but no actual fills.
        self.dry_run = dry_run

    def register_broker(self, division: str, broker: Broker) -> None:
        self.brokers[division] = broker
        log.info("Registered %s broker for division=%s (paper=%s)",
                 broker.name, division, broker.paper)

    async def connect_all(self) -> None:
        for div, b in self.brokers.items():
            try:
                await b.connect()
            except Exception as e:
                log.error("Broker connect failed for division=%s broker=%s: %s",
                          div, b.name, e)
                # Replace with paper fallback so the system stays runnable.
                # CRITICAL: starting_equity=0 — a paper-fallback broker means
                # the real broker FAILED. Showing $100k would mask the failure
                # and look like the account has $100k of equity. Better to
                # show $0 so the dashboard signals "this division is down".
                fallback = PaperBroker(account=f"paper_{div}", starting_equity=0.0)
                await fallback.connect()
                self.brokers[div] = fallback
                self.logger.log_event(
                    actor="data_exec",
                    kind="broker_fallback_to_paper",
                    payload={"division": div, "error": str(e)},
                )

    async def disconnect_all(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for b in self.brokers.values():
            try:
                await b.disconnect()
            except Exception:
                pass

    async def snapshot(self, division: str) -> AccountSnapshot:
        return await self.brokers[division].snapshot()

    async def place(self, order: ProposedOrder, division: str = "default") -> FillEvent:
        broker = self.brokers.get(division) or self.brokers.get("default")
        if broker is None:
            raise RuntimeError(f"No broker registered for division={division!r}")

        # ── Dry-run short-circuit ──────────────────────────────────────
        # Validates the entire pipeline (auth → snapshot → risk → order
        # build → serialization) WITHOUT actually placing the order at the
        # broker. The synthetic FillEvent uses the limit price so downstream
        # consumers (audit log, web result panel, Telegram receipt) render
        # exactly as they would in a real fill — only the broker call is
        # skipped.
        if self.dry_run:
            ts = iso(now_utc())
            # Synthetic price source:
            #   limit orders  → use the limit price (what we'd pay if filled)
            #   market orders → fetch a live quote from the broker so the
            #                   audit/UI shows the price the order WOULD
            #                   have filled at. Without this, market orders
            #                   show $0 and the result panel falls back to
            #                   "awaiting fill" — defeating dry-run's whole
            #                   point of validating real numbers end-to-end.
            # If the quote fails, fall back to 0.0 with a warning rather
            # than crashing — dry-run is a validation tool and should
            # always produce *some* output.
            if order.limit_price:
                synth_price = float(order.limit_price)
            else:
                try:
                    quoted = await broker.quote(order.symbol)
                    synth_price = float(quoted) if quoted and quoted > 0 else 0.0
                except Exception as e:
                    log.warning(
                        "dry-run: broker.quote(%s) failed: %s "
                        "— synthetic price 0.0",
                        order.symbol, e,
                    )
                    synth_price = 0.0
            fill = FillEvent(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                qty=float(order.qty),
                price=synth_price,
                ts=ts,
                venue=f"{broker.name}:dry-run",
            )
            order.status = "dry_run_skipped"
            order.fill_price = synth_price
            order.fill_ts = ts
            self.logger.log_proposed_order(order)
            self.logger.log_event(
                actor="data_exec",
                kind="dry_run_skip",
                payload={
                    "order_id": order.id,
                    "strategy": order.strategy,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "would_be_price": synth_price,
                    "venue": broker.name,
                    "ts": ts,
                    "reason": "DataExecAgent.dry_run=True",
                },
            )
            log.warning(
                "DRY-RUN: skipped placing %s %s x%s @ $%.2f on %s",
                order.side.upper(), order.symbol, order.qty, synth_price, broker.name,
            )
            return fill

        fill = await broker.place_order(order)
        order.status = "filled"
        order.fill_price = fill.price
        order.fill_ts = fill.ts
        self.logger.log_proposed_order(order)
        self.logger.log_event(
            actor="data_exec",
            kind="filled",
            payload={
                "order_id": order.id,
                "strategy": order.strategy,
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "fill_price": fill.price,
                "venue": fill.venue,
                "ts": fill.ts,
            },
        )
        return fill

    def start_feeds(self, stocks: Iterable[str], crypto_pairs: Iterable[str]) -> None:
        """Must be called from inside a running event loop."""
        from trading_corp.data.feeds import yfinance_poll, ccxt_poll
        loop = asyncio.get_running_loop()
        if stocks:
            self._tasks.append(loop.create_task(
                yfinance_poll(stocks, self.feeds, stop_event=self._stop)
            ))
        if crypto_pairs:
            self._tasks.append(loop.create_task(
                ccxt_poll(crypto_pairs, self.feeds, stop_event=self._stop)
            ))
