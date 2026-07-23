"""Data & Execution Agent — non-LLM. Owns brokers, places orders, runs feeds.

Strategy/division agents emit `ProposedOrder`s. After Risk + Board approval,
the CEO graph hands the order here. This agent is also the home of the
FeedAggregator and broker registry.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Iterable

from trading_corp.agents.logger import LoggerAgent
from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.brokers.bitunix_exceptions import (
    BitunixPositionModeMismatch,
    BitunixStaleSnapshot,
)
from trading_corp.brokers.paper import PaperBroker
from trading_corp.data.feeds import FeedAggregator
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent, ProposedOrder
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)


class AdvisoryOrderError(RuntimeError):
    """Raised when an ADVISORY order reaches a dispatch chokepoint.

    Fail-closed guard: a roll_leap / LEAP-roll leg is a capital-reallocation the
    operator executes MANUALLY — the agent must NEVER place it. Mirrors the
    single-leg-path guard in RobinhoodBroker._place_option_order (never partially
    act on something that must not execute). Detection checks BOTH the typed
    `dispatch` field AND the `extra["action"]` prefix: the action survives
    ceo_graph's `_order_from_state` reconstruction (which drops typed fields but
    preserves `extra`), so it is the load-bearing signal on the single-leg path.
    """


def _is_advisory_order(order: ProposedOrder) -> bool:
    """True if `order` must never be placed by the agent (fail-closed).

    Checks the typed dispatch marker first (authoritative where the original
    object flows, e.g. the combo path) then the action prefix (load-bearing on
    the ceo_graph single-leg path, which reconstructs the order without the typed
    field). Either condition ⇒ advisory.
    """
    if getattr(order, "dispatch", "executable") == "advisory":
        return True
    action = str((order.extra or {}).get("action") or "")
    return action.startswith("roll_leap")


class DataExecAgent:
    def __init__(
        self,
        logger: LoggerAgent,
        *,
        dry_run: bool = False,
        safety_notifier: Any = None,
    ) -> None:
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
        # Safety-event notifier used for `safety_alert` Telegram pushes
        # raised by the mode-mismatch consumer in `place()` and by
        # `flatten_division`. Optional — when None, safety paths still
        # audit + re-raise as configured but skip the Telegram side-effect.
        # Duck-typed contract:
        #   async def push(text: str, *,
        #                  audit_path: str = "other",
        #                  audit_context: dict | None = None) -> bool
        # `True` = HTTP 2xx + ok:true (confirmed delivery); `False` =
        # send failed. Push never raises (per `comms.telegram_bot.push`).
        self.safety_notifier = safety_notifier

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
        # Phase A fail-closed guard: an ADVISORY order (roll_leap / LEAP-roll leg)
        # is operator-executed MANUALLY — refuse it BEFORE any broker call, dry-run,
        # or staleness gate. Cannot be bypassed by a future caller, refactor, or an
        # auto_execute flip.
        if _is_advisory_order(order):
            raise AdvisoryOrderError(
                f"refusing to place ADVISORY order {order.id} "
                f"(dispatch={getattr(order, 'dispatch', '?')!r}, "
                f"action={(order.extra or {}).get('action')!r}) — roll_leap / "
                "LEAP-roll legs are executed MANUALLY by the operator; the agent "
                "never places them."
            )
        broker = self.brokers.get(division) or self.brokers.get("default")
        if broker is None:
            raise RuntimeError(f"No broker registered for division={division!r}")

        # E2·5 — classify the execution path from the REAL broker (not a config
        # guess). PaperExecutionBroker.paper is True (paper mode); placement-legal
        # live brokers (PolymarketLiveBroker, BitunixBroker, robinhood/…) are False.
        # This is the generic set point the live path flows through — E2·6's
        # polymarket-live placement auto-populates execution_mode='live' here with
        # no further wiring. (The paper would_have_placed path never reaches place()
        # and defaults to 'paper'.) Set before any log_proposed_order below.
        order.execution_mode = "paper" if getattr(broker, "paper", True) else "live"

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

        # #5-C: exits (reduce_only) are EXEMPT from the staleness gate — a
        # position must always be closable even on a stale snapshot. The gate
        # blocks ENTRIES only. Keyed strictly on reduce_only=True. (The broker's
        # halt latch likewise exempts reduce_only — #5-B.)
        _is_exit = bool((order.extra or {}).get("reduce_only", False))
        try:
            # Defense-in-depth snapshot-staleness re-check (gate (a)
            # sub-item 2). The bitunix observer's pre-trade gate already
            # called this, but observer-check-passed-then-snapshot-went-
            # stale-before-data_exec.place is a real race. Duck-typed —
            # non-bitunix brokers don't have this method. ENTRY-only (#5-C).
            if not _is_exit and hasattr(broker, "_assert_snapshot_fresh"):
                await broker._assert_snapshot_fresh()
            fill = await broker.place_order(order)
        except BitunixStaleSnapshot as exc:
            # Safety side-effects (audit + telegram); broker already
            # self-latched `_halt_new_orders=True` before raising. Re-raise.
            await self._handle_stale_snapshot(exc, order, division, broker)
            raise
        except BitunixPositionModeMismatch as exc:
            # Safety side-effects (audit + telegram); broker already
            # self-latched `_halt_new_orders=True` before raising. Re-raise
            # so the caller's path is clearly broken — matches today's
            # no-catch propagation semantics + adds the safety effects.
            await self._handle_position_mode_mismatch(exc, order, division, broker)
            raise

        # #3 CORE: `broker.place_order` is the ONLY call whose raise means the
        # order was REJECTED. Past this point the fill is CONFIRMED + REAL on the
        # venue, so NO persistence error may convert it into a rejection — the
        # caller must register it. log_proposed_order is now lock-resilient
        # (retries; never raises on a transient lock), but we still wrap the
        # post-fill writes so even a non-lock persistence bug can't lose the fill.
        order.status = "filled"
        order.fill_price = fill.price
        order.fill_ts = fill.ts
        try:
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
        except Exception as pe:
            log.error(
                "data_exec.place: post-fill persistence failed for %s — the fill "
                "is REAL on the venue; returning it so the caller registers the "
                "position (NOT a rejection): %s", order.id, pe,
            )
        return fill

    # ------------------------------------------------------------------
    # Safety handlers (Session N defensive scaffolding)
    #
    # Two consumers wired here at the broker-call chokepoint:
    #   * `_handle_position_mode_mismatch` — invoked from `place()` when the
    #     broker raises `BitunixPositionModeMismatch`. Audits + telegrams +
    #     re-raises. The broker's own `_halt_new_orders` self-latch is the
    #     halt mechanism (per Phase 2a sub-diagnostic on
    #     `bitunix-orderpath-safety-2026-05-29`); the consumer's job is the
    #     response side only.
    #   * `flatten_division` — invoked by the bitunix observer (or any future
    #     caller) when `RiskVerdict.flatten_account=True`. Calls
    #     `broker.flatten()` if available, verifies positions=0 post-call
    #     via `broker.snapshot()` (NOT just "function returned without
    #     raising"), audits + telegrams.
    #
    # Confirmed-delivery discipline (per
    # `[[telegram-audit-success-is-confirmed-delivery]]`): audit row IDs are
    # used for re-read confirmation; telegram `push()` return-bool is what
    # we trust, not exception-absence; a `push()`-returned-False is itself
    # audited (`telegram_notification_failed`) but does NOT block the
    # safety path. Strategy-level halt persistence is a known gap — see
    # BACKLOG #N+1 follow-up.
    # ------------------------------------------------------------------

    async def _handle_position_mode_mismatch(
        self,
        exc: BitunixPositionModeMismatch,
        order: ProposedOrder,
        division: str,
        broker: Broker,
    ) -> None:
        """Audit + telegram side-effects for a caught `BitunixPositionModeMismatch`.

        Does NOT set the halt — the broker self-latches `_halt_new_orders`
        before raising. This handler confirms the latch state, writes the
        `position_mode_mismatch_detected` audit row (re-read via the
        returned audit id), and pushes a `safety_alert` telegram. Caller
        re-raises the exception after this returns.
        """
        broker_halt_latched = bool(getattr(broker, "_halt_new_orders", False))
        ts = iso(now_utc())
        payload = {
            "current": exc.current,
            "expected": exc.expected,
            "division": division,
            "broker_class": type(broker).__name__,
            "order_id": order.id,
            "broker_halt_latched": broker_halt_latched,
            "ts": ts,
        }
        audit_id = self.logger.log_event(
            actor="data_exec",
            kind="position_mode_mismatch_detected",
            payload=payload,
        )

        # Re-read confirmation — independent of LoggerAgent's connection.
        # Failure to confirm is itself a divergence: log loudly but do not
        # block the safety path on the re-read.
        if audit_id is not None:
            try:
                with db.connect(self.logger.db_url) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM audit_event WHERE id = ?", (audit_id,)
                    ).fetchone()
                    if row is None:
                        log.error(
                            "position_mode_mismatch_detected audit_id=%s "
                            "could NOT be re-read — possible silent drop",
                            audit_id,
                        )
            except Exception as e:
                log.warning("audit re-read after mode-mismatch failed: %s", e)

        if self.safety_notifier is not None:
            text = (
                f"⚠️ BitUnix position mode MISMATCH on `{division}`: "
                f"account is `{exc.current}`, expected `{exc.expected}`. "
                f"Broker halt latched: {broker_halt_latched}. Refusing new orders. "
                f"(out-of-band UI change?)"
            )
            await self._safety_push(
                text,
                audit_path="safety_alert",
                audit_context={
                    "division": division,
                    "kind": "position_mode_mismatch_detected",
                    "current": str(exc.current),
                },
            )

    async def _on_rh_auth_change(self, down: bool, info: dict) -> None:
        # ITEM3-AUTH-HOOK: RobinhoodBroker auth-state hook, fired ONCE per transition (latch de-dups).
        kind = "rh_auth_failed" if down else "rh_auth_recovered"
        ts = iso(now_utc())
        payload = {"reason": info.get("reason"), "since": info.get("since"),
                   "last_good": info.get("last_good"),
                   "accounts": ["680725082", "461391328", "934310442", "116637293063"], "ts": ts}
        audit_id = self.logger.log_event(actor="data_exec", kind=kind, payload=payload)
        if audit_id is not None:
            try:
                with db.connect(self.logger.db_url) as conn:
                    if conn.execute("SELECT 1 FROM audit_event WHERE id=?", (audit_id,)).fetchone() is None:
                        log.error("%s audit_id=%s could NOT be re-read", kind, audit_id)
            except Exception as e:
                log.warning("audit re-read after %s failed: %s", kind, e)
        if down:
            text = ("RH SESSION DOWN — Robinhood auth failing (401). NO new entries AND NO exits "
                    "on live positions; broker-wide (PEAD / PMCC / IRA / joint). "
                    f"since {info.get('since')}. In-process reload could not recover (pickle likely "
                    "stale) — approve a refresh: dashboard 'Refresh RH pickle' button.")
        else:
            text = f"RH session RECOVERED (auth restored). last good {info.get('last_good')}."
        await self._safety_push(text, audit_path="safety_alert",
                                audit_context={"division": "robinhood", "kind": kind})

    async def _handle_stale_snapshot(
        self,
        exc: BitunixStaleSnapshot,
        order: ProposedOrder,
        division: str,
        broker: Broker,
    ) -> None:
        """Audit + telegram side-effects for a caught `BitunixStaleSnapshot`.

        Mirror of `_handle_position_mode_mismatch`. Does NOT set the halt —
        the broker self-latches `_halt_new_orders=True` before raising in
        `_assert_snapshot_fresh()`. This handler confirms the latch state,
        writes the `snapshot_stale_halt` audit row (re-read via the returned
        audit id), and pushes a `safety_alert` telegram. Caller re-raises
        the exception after this returns.
        """
        broker_halt_latched = bool(getattr(broker, "_halt_new_orders", False))
        ts = iso(now_utc())
        payload = {
            "age_s": round(float(exc.age_s), 3) if exc.age_s != float("inf") else None,
            "threshold_s": round(float(exc.threshold_s), 3),
            "division": division,
            "broker_class": type(broker).__name__,
            "order_id": order.id,
            "broker_halt_latched": broker_halt_latched,
            "ts": ts,
        }
        audit_id = self.logger.log_event(
            actor="data_exec",
            kind="snapshot_stale_halt",
            payload=payload,
        )

        # Re-read confirmation (mirrors mode-mismatch consumer pattern).
        if audit_id is not None:
            try:
                with db.connect(self.logger.db_url) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM audit_event WHERE id = ?", (audit_id,)
                    ).fetchone()
                    if row is None:
                        log.error(
                            "snapshot_stale_halt audit_id=%s could NOT be "
                            "re-read — possible silent drop",
                            audit_id,
                        )
            except Exception as e:
                log.warning("audit re-read after stale-snapshot failed: %s", e)

        if self.safety_notifier is not None:
            age_repr = (
                "never (no successful snapshot yet)"
                if exc.age_s == float("inf")
                else f"{exc.age_s:.1f}s ago"
            )
            text = (
                f"⚠️ BitUnix snapshot STALE on `{division}`: "
                f"last successful snapshot {age_repr}, "
                f"threshold {exc.threshold_s:.0f}s. "
                f"Broker halt latched: {broker_halt_latched}. Refusing new orders."
            )
            await self._safety_push(
                text,
                audit_path="safety_alert",
                audit_context={
                    "division": division,
                    "kind": "snapshot_stale_halt",
                    "age_s": payload["age_s"],
                },
            )

    async def _safety_push(
        self,
        text: str,
        *,
        audit_path: str,
        audit_context: dict,
    ) -> None:
        """Push a safety alert via the notifier. On failure (returned False
        or raised), write `telegram_notification_failed` audit but DO NOT
        block the safety path — the primary audit row is the load-bearing
        record; comms is best-effort."""
        if self.safety_notifier is None:
            return
        ok = False
        try:
            ok = await self.safety_notifier.push(
                text, audit_path=audit_path, audit_context=audit_context,
            )
        except Exception as e:
            log.warning("safety_notifier.push raised: %s", e)
            ok = False
        if not ok:
            try:
                self.logger.log_event(
                    actor="data_exec",
                    kind="telegram_notification_failed",
                    payload={
                        "for_audit_kind": audit_context.get("kind"),
                        "division": audit_context.get("division"),
                        "reason": "push returned False or raised",
                        "ts": iso(now_utc()),
                    },
                )
            except Exception as e:
                log.warning("failed to audit telegram failure: %s", e)

    async def flatten_division(self, division: str) -> None:
        """Flatten all open positions on the broker for `division`.

        Bitunix-only effective scope: the call is no-op-with-audit for any
        broker that doesn't expose `flatten()` (currently every broker
        except `BitunixBroker` on the broker-write branch). Use cases:
        - `RiskAgent.evaluate()` returned `flatten_account=True` (drawdown
          cap breached); the bitunix observer awaits this.
        - Manual kill-switch invocation from a future operator surface.

        Idempotent: already-flat → `flatten_account_noop_already_flat` audit,
        no broker call, no error.

        Verification: post-flatten, `broker.snapshot()` is re-read; if any
        positions remain, the action is treated as FAILED (escalated
        telegram + audit + re-raise), per the Phase 2a discipline that
        "verification queries broker truth (positions=0), not just
        'function returned without raising'".
        """
        broker = self.brokers.get(division) or self.brokers.get("default")
        if broker is None:
            raise RuntimeError(
                f"flatten_division: no broker registered for division={division!r}"
            )

        if not hasattr(broker, "flatten"):
            self.logger.log_event(
                actor="data_exec",
                kind="flatten_account_skipped_no_flatten_method",
                payload={
                    "division": division,
                    "broker_class": type(broker).__name__,
                    "reason": "broker has no flatten() method (paper-wrapped or non-bitunix)",
                    "ts": iso(now_utc()),
                },
            )
            return

        # Snapshot BEFORE attempting flatten.
        try:
            snap_before = await broker.snapshot()
            positions_before = len(snap_before.positions or [])
        except Exception as e:
            log.warning(
                "flatten_division(%s): pre-flatten snapshot failed: %s — "
                "treating positions_before as unknown",
                division, e,
            )
            positions_before = -1

        # Idempotent no-op when already flat.
        if positions_before == 0:
            self.logger.log_event(
                actor="data_exec",
                kind="flatten_account_noop_already_flat",
                payload={
                    "division": division,
                    "positions": 0,
                    "ts": iso(now_utc()),
                },
            )
            if self.safety_notifier is not None:
                await self._safety_push(
                    f"ℹ️ flatten_account on `{division}`: account already "
                    f"flat (0 positions). No-op.",
                    audit_path="safety_alert",
                    audit_context={
                        "division": division,
                        "kind": "flatten_noop_already_flat",
                    },
                )
            return

        # Attempt the flatten.
        flatten_error: Exception | None = None
        try:
            await broker.flatten()
        except Exception as e:
            flatten_error = e

        # Verify via snapshot (broker truth). This must NOT read a cached
        # pre-flatten snapshot: BitunixBroker.snapshot() carries a short TTL
        # poll-cache, but its flatten()/close primitives invalidate that cache
        # on every mutation, so this post-flatten read always refetches fresh
        # broker truth (positions=0). See `_invalidate_snapshot_cache` in
        # trading_corp/brokers/bitunix.py.
        try:
            snap_after = await broker.snapshot()
            positions_after = len(snap_after.positions or [])
        except Exception as e:
            log.warning(
                "flatten_division(%s): post-flatten snapshot failed: %s — "
                "treating positions_after as unknown (escalating)",
                division, e,
            )
            positions_after = -1

        # Failure path: flatten raised OR positions remain OR verification
        # failed. Audit + escalated telegram + re-raise.
        if flatten_error is not None or positions_after > 0:
            self.logger.log_event(
                actor="data_exec",
                kind="flatten_account_failed",
                payload={
                    "division": division,
                    "positions_before": positions_before,
                    "positions_after": positions_after,
                    "error": str(flatten_error) if flatten_error else None,
                    "ts": iso(now_utc()),
                },
            )
            if self.safety_notifier is not None:
                await self._safety_push(
                    f"🚨 FLATTEN FAILED on `{division}`: "
                    f"positions_before={positions_before}, "
                    f"positions_after={positions_after}, "
                    f"error={flatten_error}",
                    audit_path="safety_alert",
                    audit_context={
                        "division": division,
                        "kind": "flatten_failed",
                    },
                )
            if flatten_error is not None:
                raise flatten_error
            raise RuntimeError(
                f"flatten_division({division}): post-flatten verification "
                f"failed — {positions_after} positions remain after "
                f"broker.flatten()"
            )

        # Success.
        self.logger.log_event(
            actor="data_exec",
            kind="flatten_account_executed",
            payload={
                "division": division,
                "positions_before": positions_before,
                "positions_after": positions_after,
                "ts": iso(now_utc()),
            },
        )
        if self.safety_notifier is not None:
            await self._safety_push(
                f"✅ Flatten executed on `{division}`: "
                f"{positions_before} → {positions_after} positions.",
                audit_path="safety_alert",
                audit_context={
                    "division": division,
                    "kind": "flatten_executed",
                },
            )

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

        # Phase A fail-closed guard: no ADVISORY leg may ride the combo path
        # either (roll_leap is never combo-tagged, but guard belt-and-braces).
        for _o in orders:
            if _is_advisory_order(_o):
                raise AdvisoryOrderError(
                    f"refusing to place ADVISORY combo leg {_o.id} "
                    f"(action={(_o.extra or {}).get('action')!r}) — roll_leap legs "
                    "are executed MANUALLY by the operator; the agent never places them."
                )

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
        # Deterministic client ref_id so a transient retry of THIS combo dedupes
        # at the venue instead of double-placing (order_option_spread otherwise
        # mints a fresh uuid4 per call).
        from trading_corp.agents.strategies._pmcc_combo import combo_ref_id
        _ref_id = combo_ref_id(str(combo_id))

        broker = self.brokers.get(division) or self.brokers.get("default")
        if broker is None:
            raise RuntimeError(f"No broker registered for division={division!r}")

        # Tag every leg with the REAL broker's execution mode (mirrors place()'s
        # single-leg line) so combo rows are labelled live/paper accurately even
        # if placement raises below.
        _combo_mode = "paper" if getattr(broker, "paper", True) else "live"
        for _o in orders:
            _o.execution_mode = _combo_mode

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

        try:
            fills = await broker.place_multi_leg(orders, ref_id=_ref_id)
        except Exception as e:
            # A combo that submitted but did not confirm `filled` in the poll
            # window (RobinhoodComboPending) must book NOTHING — record it as
            # pending/unconfirmed and re-raise so the route surfaces it. Any hard
            # error (reject / no-id) also re-raises; neither books a position.
            if type(e).__name__ == "RobinhoodComboPending":
                self.logger.log_event(
                    actor="data_exec", kind="combo_pending_unconfirmed",
                    payload={"combo_id": combo_id, "strategy": strategy,
                             "division": division,
                             "broker_order_id": getattr(e, "order_id", None),
                             "reason": str(e)},
                )
            raise

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
                # The ONE Robinhood spread-order id both legs share (all FillEvents
                # carry the same broker_order_id from order_option_spread's response).
                "broker_order_id": getattr(fills[0], "broker_order_id", None),
                "ref_id": _ref_id,
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
