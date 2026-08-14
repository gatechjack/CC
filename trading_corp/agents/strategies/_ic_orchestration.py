"""Orchestration helpers for the Robinhood Joint Iron Condor strategy.

Lives in a sibling module so the step-11 wiring stays testable in
isolation — main.py imports these and spawns them as asyncio tasks; the
web-app approval handler calls `dispatch_approved_ic_combo` after a
Board approve.

Public surface:
  - is_signal_scan_due(now, last_scan_date) -> bool
  - run_signal_scanner_loop(...) — runs forever; per-day single fire
  - run_position_manager_loop(...) — startup_catchup, then cadence loop
  - propose_ic_combo(...) — risk-gate each leg + emit `combo_proposed`
    audit + queue a Telegram ping; does NOT place the order. HITL
    approval happens via the web app, which then calls
    `dispatch_approved_ic_combo`.
  - dispatch_approved_ic_combo(...) — place_combo + on_combo_filled
    callback in the same code path (state-consistency contract from
    plan steps 7-9).

The schedulers depend on a small `clock_fn` indirection so tests can
inject a deterministic clock without monkey-patching `datetime.now`.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import date, datetime, time, timezone
from typing import Any, Awaitable, Callable, Iterable

from trading_corp.persistence.models import FillEvent, ProposedOrder

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                # pragma: no cover
    _ET = None
    logging.getLogger(__name__).warning(
        "zoneinfo unavailable; IC scheduler will treat local time as ET"
    )

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# US market holidays — refresh annually. Skipped by the signal scanner so we
# don't try to read closed chains on a market holiday.
# Source: NYSE 2026 holiday schedule.
# ---------------------------------------------------------------------------

_US_MARKET_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed; Jul 4 is Sat)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})


def is_us_market_day(d: date) -> bool:
    """Mon–Fri AND not a 2026 NYSE holiday."""
    return d.weekday() < 5 and d not in _US_MARKET_HOLIDAYS_2026


# ---------------------------------------------------------------------------
# Scheduling predicate
# ---------------------------------------------------------------------------

# Default scan-fire window: 09:45–09:50 ET. 5-minute window is the natural
# slop given the position-manager loop polls at this rate.
DEFAULT_SCAN_TIME_ET = time(9, 45)
DEFAULT_SCAN_WINDOW_END_ET = time(9, 50)


def is_signal_scan_due(
    now: datetime,
    last_scan_date: date | None,
    *,
    fire_start: time = DEFAULT_SCAN_TIME_ET,
    fire_end: time = DEFAULT_SCAN_WINDOW_END_ET,
) -> bool:
    """Return True iff `now` (an ET-localized datetime) falls inside
    today's scan-fire window AND we haven't already scanned today.

    Weekend and US-market-holiday days return False unconditionally.
    """
    today = now.date()
    if not is_us_market_day(today):
        return False
    if last_scan_date == today:
        return False
    return fire_start <= now.time() <= fire_end


# ---------------------------------------------------------------------------
# Combo dispatch helpers
#
# Two-stage dispatch matches the plan's HITL-on-every-action policy:
#
#   1. `propose_ic_combo` runs the per-leg risk gate, audits a
#      `combo_proposed` event (paper-default: would_have_placed_combo),
#      and pings the Telegram batcher with the combo summary. It does
#      NOT place the order — HITL has to approve first.
#
#   2. After the Board clicks Approve in the web app, the approval
#      handler calls `dispatch_approved_ic_combo`, which fires
#      `data_exec.place_combo` and — on success — synchronously calls
#      `strategy.on_combo_filled` so agent_state updates inside the
#      same code path as the action. On empty fills (combo unfilled),
#      we DO NOT call on_combo_filled; the strategy's _pending entry
#      remains and gets cleaned up by the next manage() tick or via
#      explicit cleanup if the implementer adds one later.
# ---------------------------------------------------------------------------


async def propose_ic_combo(
    combo: list[ProposedOrder],
    *,
    intent: str,
    strategy: Any,
    risk_agent: Any,
    logger_agent: Any,
    account: Any,
    strategy_state: Any,
    telegram_batcher: Any | None = None,
    pending_combo_registry: Any | None = None,
    division: str = "robinhood_joint",
    db_url: str | None = None,
) -> bool:
    """Risk-gate the combo (per leg) and emit a `combo_proposed` audit.

    Returns True iff the combo passed risk and was queued for HITL
    approval. False if any leg was rejected by RiskAgent (the whole
    combo aborts; no partial state).

    The web app's HITL approval handler will pick up the audit row
    (joined by combo_id) and call `dispatch_approved_ic_combo` on
    Board approve.
    """
    if not combo:
        return False
    combo_id = (combo[0].extra or {}).get("combo_id")
    if not combo_id:
        log.warning("propose_ic_combo: missing combo_id on leg 0 — skipping")
        return False

    # Per-leg risk evaluation. Strategy code sized each leg under the
    # per-trade cap independently — see step 9 module docstring.
    leg_verdicts = []
    for leg in combo:
        v = risk_agent.evaluate(leg, account, strategy_state, db_url=db_url)
        leg_verdicts.append(v)
        if getattr(v, "verdict", "") == "reject":
            logger_agent.log_event(
                strategy.SLUG, "combo_rejected_by_risk",
                {
                    "combo_id": combo_id,
                    "strategy": strategy.SLUG,
                    "division": division,
                    "intent": intent,
                    "rejected_leg_role": (leg.extra or {}).get("combo_role"),
                    "risk_reason": v.reason,
                },
            )
            log.info(
                "IC combo %s: risk REJECT leg %s — %s",
                combo_id, (leg.extra or {}).get("combo_role"), v.reason,
            )
            return False

    # Paper-default audit: would_have_placed_combo. Real placement
    # happens via dispatch_approved_ic_combo after Board approval.
    legs_payload = [
        {
            "order_id": leg.id,
            "combo_role": (leg.extra or {}).get("combo_role"),
            "side": leg.side,
            "qty": float(leg.qty),
            "symbol": leg.symbol,
            "strike": (leg.extra or {}).get("strike"),
            "option_type": (leg.extra or {}).get("option_type"),
            "expiration": (leg.extra or {}).get("expiration"),
            "position_effect": (leg.extra or {}).get("position_effect"),
            "limit_price": leg.limit_price,
        }
        for leg in combo
    ]
    first_extra = combo[0].extra or {}
    logger_agent.log_event(
        strategy.SLUG, "combo_proposed",
        {
            "combo_id": combo_id,
            "strategy": strategy.SLUG,
            "division": division,
            "intent": intent,
            "direction": first_extra.get("combo_direction"),
            "net_limit_price": first_extra.get("net_limit_price"),
            "underlying": first_extra.get("underlying"),
            "leg_count": len(combo),
            "legs": legs_payload,
        },
    )

    # Register the combo for HITL approval. The web /approvals page
    # reads this registry to render the combined card; on Board
    # approve the POST handler pops the entry and calls
    # dispatch_approved_ic_combo. Registration is optional so tests can
    # exercise the risk-gate/audit pipeline without spinning up a
    # registry instance.
    if pending_combo_registry is not None:
        try:
            pending_combo_registry.propose(
                combo_id, combo,
                intent=intent,
                strategy_slug=strategy.SLUG,
                division=division,
            )
        except Exception:
            log.exception(
                "propose_ic_combo: pending_combo_registry.propose raised "
                "— combo audit row written, registry entry NOT created; "
                "approval will not surface until manage() re-proposes."
            )

    if telegram_batcher is not None:
        # Tag with the intent so high-severity intents bypass the batch
        # window (catastrophic_stop, late_dte_force_close per the
        # strategy config's telegram_bypass_tags). startup_catchup is
        # handled by the position-manager loop tagging the actions
        # themselves; here we just forward the intent as a tag.
        symbol = first_extra.get("underlying") or "?"
        message = (
            f"🟧 IC {intent} proposed on {symbol} (combo {combo_id[:8]}) — "
            "open web app to approve."
        )
        try:
            await telegram_batcher.push(message, tags=[intent])
        except Exception as e:
            log.warning("propose_ic_combo: telegram push failed: %s", e)
    return True


def _dispatch_consent_bail(combo, combo_id, division, snapshot, reason, data_exec):
    """Audit + calmly alert a consent BAIL (combo NOT placed). Never raises."""
    first = (combo[0].extra or {}) if combo else {}
    symbol = first.get("underlying") or (combo[0].symbol if combo else "?")
    try:
        data_exec.logger.log_event(
            actor="data_exec", kind="combo_reprice_bail",
            payload={
                "combo_id": combo_id, "division": division, "reason": reason,
                "approved": snapshot,
                "dispatch_direction": first.get("combo_direction"),
                "dispatch_net_limit_price": first.get("net_limit_price"),
            },
        )
    except Exception:
        log.exception("consent bail: audit failed for combo %s", combo_id)
    try:
        from trading_corp.comms.exec_alert import ExecOutcome, emit_exec_alert
        emit_exec_alert(ExecOutcome(
            tier="ABORTED", symbol=str(symbol), strategy=str(division),
            reason=(f"held (not placed) — {reason}. No order sent, position "
                    "unchanged; re-approve on next scan."),
            combo_id=combo_id,
        ))
    except Exception:
        log.exception("consent bail: exec-alert failed for combo %s", combo_id)


async def dispatch_approved_ic_combo(
    combo: list[ProposedOrder],
    *,
    strategy: Any,
    data_exec: Any,
    division: str = "robinhood_joint",
) -> list[FillEvent]:
    """Place an HITL-approved combo and run the state-update callback.

    Synchronous chain (per the plan's state-consistency requirement):
      1. `data_exec.place_combo(combo, division=division)` — atomic
         place + position-row persist + combo_filled audit.
      2. On success (non-empty `fills`): `strategy.on_combo_filled(
         combo_id, fills)` BEFORE returning.
      3. On empty `fills` (combo_unfilled): return `[]` without calling
         on_combo_filled. The strategy's _pending registry entry
         persists; the next manage() tick will produce a fresh proposal
         if conditions still hold.

    Returns the FillEvent list from `place_combo`.
    """
    if not combo:
        return []
    # Board-approved HITL dispatch = USER-INITIATED — exec-alerts for this combo's
    # terminal outcome bypass dedupe (see comms/exec_alert).
    try:
        from trading_corp.comms.exec_alert import mark_user_origin
        mark_user_origin()
    except Exception:
        pass
    combo_id = (combo[0].extra or {}).get("combo_id")
    # Re-price from LIVE quotes at DISPATCH (not the stale proposal-time mid),
    # IDENTICAL to the dashboard route — one helper (strategy.reprice_combo), both
    # call sites. Gated to strategies that expose it (PMCC); the IC path has no
    # reprice_combo and is untouched. Fail-safe: any error → dispatch at the
    # proposal-time limit.
    _reprice = getattr(strategy, "reprice_combo", None)
    if callable(_reprice):
        try:
            _broker = (data_exec.brokers.get(division)
                       or data_exec.brokers.get("default"))
        except Exception:
            _broker = None
        if _broker is not None and hasattr(_broker, "get_option_quote"):
            # Snapshot the operator-APPROVED shape BEFORE reprice mutates it, so
            # the consent guard can bail if dispatch drifts adversely from what
            # was approved (sign flip, credit collapse, stale/wide quotes).
            _snapshot = None
            try:
                from trading_corp.agents.strategies._pmcc_combo import (
                    snapshot_combo_for_consent,
                )
                _snapshot = snapshot_combo_for_consent(combo)
            except Exception:
                _snapshot = None
            try:
                await _reprice(combo, _broker)
            except Exception as e:
                log.warning(
                    "dispatch_approved_ic_combo: reprice_combo failed for %s: %s "
                    "— dispatching at proposal-time limit", combo_id, e,
                )
            # CONSENT / adverse-deviation guard (defense-in-depth; PMCC exposes
            # assess_combo_consent, IC does not). A bail books NOTHING and lets
            # the next scan re-propose for fresh re-approval — never silently
            # place a credit approval as a debit / off garbage quotes / worse.
            _consent = getattr(strategy, "assess_combo_consent", None)
            if callable(_consent) and _snapshot is not None:
                try:
                    _ok, _reason = _consent(combo, _snapshot)
                except Exception:
                    _ok, _reason = True, ""     # never block on guard failure
                if not _ok:
                    _dispatch_consent_bail(
                        combo, combo_id, division, _snapshot, _reason, data_exec,
                    )
                    return []
    fills = await data_exec.place_combo(combo, division=division)
    if fills:
        # State-update callback — synchronous with the action.
        try:
            strategy.on_combo_filled(combo_id, fills)
        except Exception:
            log.exception(
                "dispatch_approved_ic_combo: on_combo_filled raised for combo %s "
                "— combo IS filled at broker but strategy state may diverge; "
                "investigate via audit log",
                combo_id,
            )
            raise
    return fills


# ---------------------------------------------------------------------------
# Async loops
# ---------------------------------------------------------------------------


def _now_et(clock_fn: Callable[[], datetime] | None) -> datetime:
    if clock_fn is not None:
        return clock_fn()
    if _ET is not None:
        return datetime.now(_ET)
    return datetime.now()


async def run_signal_scanner_loop(
    *,
    division: Any,
    broker: Any,
    strategy: Any,
    risk_agent: Any,
    logger_agent: Any,
    data_exec: Any,
    account_factory: Callable[[], Awaitable[Any] | Any],
    strategy_state_factory: Callable[[], Any],
    telegram_batcher: Any | None = None,
    pending_combo_registry: Any | None = None,
    poll_interval_sec: float = 60.0,
    fire_start: time = DEFAULT_SCAN_TIME_ET,
    fire_end: time = DEFAULT_SCAN_WINDOW_END_ET,
    clock_fn: Callable[[], datetime] | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Daily signal-scan async loop. Fires `division.scan()` once per US
    market day at the configured ET time, skipping weekends and 2026
    NYSE holidays. Cancels cleanly on `stop_event.set()` or task cancel.
    """
    last_scan_date: date | None = None
    log.info(
        "IC signal scanner online: weekdays %02d:%02d–%02d:%02d ET "
        "(poll every %.0fs)",
        fire_start.hour, fire_start.minute,
        fire_end.hour, fire_end.minute, poll_interval_sec,
    )

    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("IC signal scanner stop_event set — exiting")
            return
        try:
            now = _now_et(clock_fn)
            if is_signal_scan_due(
                now, last_scan_date,
                fire_start=fire_start, fire_end=fire_end,
            ):
                last_scan_date = now.date()
                log.info("IC scanner firing daily scan at %s ET", now.time())
                try:
                    combos = await division.scan(broker)
                except Exception:
                    log.exception("IC scan failed")
                    combos = []
                for combo in combos:
                    try:
                        await propose_ic_combo(
                            combo, intent="open",
                            strategy=strategy,
                            risk_agent=risk_agent,
                            logger_agent=logger_agent,
                            account=await _maybe_await(account_factory()),
                            strategy_state=strategy_state_factory(),
                            telegram_batcher=telegram_batcher,
                            pending_combo_registry=pending_combo_registry,
                            division=division.slug,
                        )
                    except Exception:
                        log.exception(
                            "IC scan: propose_ic_combo raised — skipping combo"
                        )

            await asyncio.sleep(poll_interval_sec)

        except asyncio.CancelledError:
            log.info("IC signal scanner cancelled.")
            return
        except Exception:
            log.exception("IC signal scanner loop error (continuing)")
            await asyncio.sleep(poll_interval_sec)


async def run_position_manager_loop(
    *,
    division: Any,
    broker: Any,
    strategy: Any,
    risk_agent: Any,
    logger_agent: Any,
    data_exec: Any,
    account_factory: Callable[[], Awaitable[Any] | Any],
    strategy_state_factory: Callable[[], Any],
    telegram_batcher: Any | None = None,
    pending_combo_registry: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Dynamic-cadence Position Manager loop.

    Runs `strategy.startup_catchup(broker)` once before entering the
    loop body so overdue exits fire on bot startup. Then repeatedly:

      actions, cadence = await strategy.manage(broker)
      for combo in actions:
          propose_ic_combo(combo, ...)
      await asyncio.sleep(cadence)

    The cadence returned from `manage()` is the sole source of truth for
    sleep duration — never hardcoded.
    """
    log.info("IC position manager online — running startup catch-up first")

    # Startup catch-up pass.
    try:
        actions, cadence = await strategy.startup_catchup(broker)
    except Exception:
        log.exception("IC startup_catchup failed; entering main loop with idle cadence")
        actions, cadence = [], 1800
    await _dispatch_action_combos(
        actions, intent_for_dispatch="startup_catchup",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=logger_agent, division=division,
        account_factory=account_factory,
        strategy_state_factory=strategy_state_factory,
        telegram_batcher=telegram_batcher,
        pending_combo_registry=pending_combo_registry,
    )
    await asyncio.sleep(cadence)

    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("IC position manager stop_event set — exiting")
            return
        try:
            actions, cadence = await strategy.manage(broker)
            await _dispatch_action_combos(
                actions, intent_for_dispatch="manage",
                strategy=strategy, risk_agent=risk_agent,
                logger_agent=logger_agent, division=division,
                account_factory=account_factory,
                strategy_state_factory=strategy_state_factory,
                telegram_batcher=telegram_batcher,
                pending_combo_registry=pending_combo_registry,
            )
            await asyncio.sleep(cadence)
        except asyncio.CancelledError:
            log.info("IC position manager cancelled.")
            return
        except Exception:
            log.exception("IC position manager loop error (continuing)")
            await asyncio.sleep(60)


async def _dispatch_action_combos(
    actions: Iterable[list[ProposedOrder]],
    *,
    intent_for_dispatch: str,
    strategy: Any,
    risk_agent: Any,
    logger_agent: Any,
    division: Any,
    account_factory: Callable[[], Awaitable[Any] | Any],
    strategy_state_factory: Callable[[], Any],
    telegram_batcher: Any | None,
    pending_combo_registry: Any | None = None,
) -> None:
    """Internal helper: proposes each combo via the standard pipeline.

    Per-combo intent is read from `extra.combo_intent`; the
    `intent_for_dispatch` arg is fallback metadata for logging when a
    combo doesn't carry an explicit intent (shouldn't happen — every
    strategy-produced combo sets it).
    """
    for combo in actions:
        leg0_extra = (combo[0].extra or {}) if combo else {}
        intent = leg0_extra.get("combo_intent") or intent_for_dispatch
        try:
            await propose_ic_combo(
                combo, intent=intent,
                strategy=strategy,
                risk_agent=risk_agent,
                logger_agent=logger_agent,
                account=await _maybe_await(account_factory()),
                strategy_state=strategy_state_factory(),
                telegram_batcher=telegram_batcher,
                pending_combo_registry=pending_combo_registry,
                division=division.slug,
            )
        except Exception:
            log.exception(
                "_dispatch_action_combos: propose_ic_combo raised — skipping"
            )


async def _maybe_await(value: Any) -> Any:
    """Accept both sync and async factories. If value is awaitable, await
    it; otherwise return as-is."""
    if inspect.isawaitable(value):
        return await value
    return value
