"""Data & Execution Agent — non-LLM. Owns brokers, places orders, runs feeds.

Strategy/division agents emit `ProposedOrder`s. After Risk + Board approval,
the CEO graph hands the order here. This agent is also the home of the
FeedAggregator and broker registry.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Iterable

from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.brokers.paper import PaperBroker
from trading_corp.data.feeds import FeedAggregator
from trading_corp.persistence import db
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

    # ------------------------------------------------------------------
    # Multi-leg combo dispatch
    #
    # Strategy code constructs 4 ProposedOrders sharing a combo_id and
    # hands them here. We do a defense-in-depth combo_id sanity check
    # before the broker's full validator runs, route through
    # broker.place_multi_leg() (atomic at the venue / simulated atomic in
    # paper), then either record `combo_filled` with per-leg fill prices
    # and write 4 position rows tagged by combo_id, or record
    # `combo_unfilled` and return [].
    #
    # The full payload-shape and cohesion validation lives in
    # `brokers.base.validate_combo_cohesion`; this method's local check
    # exists only because the strategy might pass a bad list well before
    # the broker sees it (e.g., merging combos from different scans).
    # ------------------------------------------------------------------

    async def place_combo(
        self,
        orders: list[ProposedOrder],
        division: str = "default",
    ) -> list[FillEvent]:
        if not orders:
            return []

        # Defense-in-depth: confirm a single combo_id is present on every
        # leg. The broker-level validator will catch deeper mismatches.
        combo_ids = {(o.extra or {}).get("combo_id") for o in orders}
        if len(combo_ids) != 1 or None in combo_ids:
            raise ValueError(
                f"place_combo received orders with mixed/missing combo_ids: "
                f"{combo_ids}"
            )
        combo_id = combo_ids.pop()
        strategy = orders[0].strategy
        first_extra = orders[0].extra or {}
        direction = first_extra.get("combo_direction")
        net_limit = first_extra.get("net_limit_price")

        broker = self.brokers.get(division) or self.brokers.get("default")
        if broker is None:
            raise RuntimeError(f"No broker registered for division={division!r}")

        # Dry-run short-circuit. Synthesise 4 FillEvents at each leg's
        # limit_price so downstream consumers (web result panel, audit
        # log, Telegram receipt) render exactly as a real fill — only the
        # broker.place_multi_leg call is skipped.
        if self.dry_run:
            ts = iso(now_utc())
            fills = [
                FillEvent(
                    order_id=o.id,
                    symbol=o.symbol,
                    side=o.side,
                    qty=float(o.qty),
                    price=float(o.limit_price or 0),
                    ts=ts,
                    venue=f"{broker.name}:dry-run",
                )
                for o in orders
            ]
            for o, f in zip(orders, fills):
                o.status = "dry_run_skipped"
                o.fill_price = f.price
                o.fill_ts = f.ts
                self.logger.log_proposed_order(o)
            self.logger.log_event(
                actor="data_exec",
                kind="dry_run_skip_combo",
                payload={
                    "combo_id": combo_id,
                    "strategy": strategy,
                    "division": division,
                    "leg_count": len(fills),
                    "venue": broker.name,
                    "ts": ts,
                    "reason": "DataExecAgent.dry_run=True",
                },
            )
            log.warning(
                "DRY-RUN: skipped combo %s (%d legs) on %s",
                combo_id, len(fills), broker.name,
            )
            return fills

        fills = await broker.place_multi_leg(orders)

        if not fills:
            self.logger.log_event(
                actor="data_exec",
                kind="combo_unfilled",
                payload={
                    "combo_id": combo_id,
                    "strategy": strategy,
                    "division": division,
                    "direction": direction,
                    "net_limit_price": net_limit,
                    "leg_count": len(orders),
                    "reason": "broker returned no fills (see broker log)",
                },
            )
            log.info(
                "combo_unfilled combo=%s strategy=%s division=%s",
                combo_id, strategy, division,
            )
            return []

        if len(fills) != len(orders):
            # Should not happen — broker is contractually all-or-nothing.
            # Surface loudly rather than silently mis-aligning.
            raise RuntimeError(
                f"broker.place_multi_leg returned {len(fills)} fills for "
                f"{len(orders)} legs in combo {combo_id!r}"
            )

        # Compute signed cashflow → direction-aware "actual" net.
        # Mirrors PaperExecutionBroker.place_multi_leg's calculation so
        # both paper and live paths emit identical combo_filled payloads.
        cashflow = 0.0
        for o, f in zip(orders, fills):
            ratio = int((o.extra or {}).get("ratio_quantity", 1))
            signed = f.price if o.side == "sell" else -f.price
            cashflow += signed * ratio
        if direction == "debit":
            actual = -cashflow
        else:
            actual = cashflow
        slippage_vs_limit = (
            abs(actual - float(net_limit)) if net_limit is not None else None
        )

        # Update each ProposedOrder + write proposed_order rows.
        for o, f in zip(orders, fills):
            o.status = "filled"
            o.fill_price = f.price
            o.fill_ts = f.ts
            self.logger.log_proposed_order(o)

        # Persist position rows linked by combo_id.
        self._persist_combo_positions(orders, fills, division=division)

        # Emit combo_filled audit.
        leg_payload = [
            {
                "order_id": o.id,
                "combo_role": (o.extra or {}).get("combo_role"),
                "symbol": o.symbol,
                "side": o.side,
                "qty": float(o.qty),
                "strike": (o.extra or {}).get("strike"),
                "option_type": (o.extra or {}).get("option_type"),
                "expiration": (o.extra or {}).get("expiration"),
                "position_effect": (o.extra or {}).get("position_effect"),
                "fill_price": f.price,
                "venue": f.venue,
                "ts": f.ts,
            }
            for o, f in zip(orders, fills)
        ]
        self.logger.log_event(
            actor="data_exec",
            kind="combo_filled",
            payload={
                "combo_id": combo_id,
                "strategy": strategy,
                "division": division,
                "direction": direction,
                "net_limit_price": net_limit,
                "net_actual": actual,
                "actual_vs_limit_slippage_dollars": slippage_vs_limit,
                "leg_count": len(fills),
                "legs": leg_payload,
            },
        )
        log.info(
            "combo_filled combo=%s strategy=%s division=%s direction=%s "
            "actual=%.4f limit=%s legs=%d",
            combo_id, strategy, division, direction,
            actual, net_limit, len(fills),
        )
        return fills

    def _persist_combo_positions(
        self,
        orders: list[ProposedOrder],
        fills: list[FillEvent],
        *,
        division: str,
    ) -> None:
        """Write one position row per leg, linked by combo_id in extra_json.

        The `position` table is treated as a fill-grouped journal here:
        each leg gets a row regardless of effect (open or close). Live
        position state (used by the IC strategy's decision tree) lives
        in `agent_state` — the position table writes are for downstream
        journal queries, reconciliation, and the web dashboard's combo
        view (step 12).
        """
        rows = []
        for o, f in zip(orders, fills):
            ex = o.extra or {}
            # Signed qty matches PaperBroker convention: buys positive,
            # sells negative. Lets a future query SUM qty across legs
            # of a combo to sanity-check the net position.
            signed_qty = float(o.qty) if o.side == "buy" else -float(o.qty)
            extra_for_position = {
                "combo_id": ex.get("combo_id"),
                "combo_role": ex.get("combo_role"),
                "combo_direction": ex.get("combo_direction"),
                "is_option": True,
                "is_combo_leg": True,
                "underlying": ex.get("underlying") or o.symbol,
                "option_type": ex.get("option_type"),
                "strike": ex.get("strike"),
                "expiration": ex.get("expiration"),
                "position_effect": ex.get("position_effect"),
                "strategy": o.strategy,
                "division": division,
                "order_id": o.id,
            }
            rows.append({
                "account": division,
                "symbol": o.symbol,
                "qty": signed_qty,
                "avg_price": float(f.price),
                "opened_ts": f.ts,
                "extra_json": json.dumps(extra_for_position),
            })

        with db.connect(self.logger.db_url) as conn:
            conn.executemany(
                """INSERT INTO position(account, symbol, qty, avg_price, opened_ts, extra_json)
                   VALUES(:account,:symbol,:qty,:avg_price,:opened_ts,:extra_json)""",
                rows,
            )

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
