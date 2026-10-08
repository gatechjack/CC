"""MaceManager — the MACE engine orchestrator (plan § Architecture).

Constructed from a `MaceConfig` + INJECTED deps (port, store, executor, notifier,
risk gate, IVR fetch, clocks) — NO module-level singletons, NO yaml re-reads in
the decision path (the future-extraction seam: MaceManager is reconstructible for
a Tasty impl by swapping the port alone). It owns the four operations the main.py
loops (Phase 4) call on a schedule; it does NOT own the asyncio loops themselves.

  evaluate_and_enter — build the EntryContext from live chains + IVR + the DB,
      run the pure strategy pipeline (+ overflow), snapshot IVR, and (only when
      auto_execute) hand each ENTER to the execution entry ladder.
  manage_tick       — per open rung: fresh mark + spot + ex-div, the pure
      management precedence, and (regardless of auto_execute — exits always run)
      the execution exit ladder on a decision to close.
  reconcile_tick    — delegate to the execution reconcile state machine.
  snapshot_equity   — the 15:40 settled-cash snapshot that is the sizing basis.

Marketability / laddering / booking / the fake-fill guard live in execution.py;
the pure filters live in strategy.py; the manager only WIRES data to decisions to
side effects.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Optional

from trading_corp.mace import ivr_provider as ivr
from trading_corp.mace import strategy as st
from trading_corp.mace.config import MaceConfig
from trading_corp.mace.disposition import disposition_line, exit_disposition_line
from trading_corp.mace.domain import (
    EXIT_EXDIV, EXIT_GAP, EXIT_MANUAL, EXIT_PT, EXIT_STOP, EXIT_TIME,
    EvalResult, RungState,
)
from trading_corp.mace.execution import EntryOutcome, ExitOutcome, MaceExecutor, RungStore
from trading_corp.mace.notify import MaceNotifier
from trading_corp.utils.time import now_et, now_utc

_LOG = logging.getLogger("mace.manager")

# Statuses whose rungs the management loop marks (open positions + those mid-close
# so a crash-interrupted exit keeps being driven).
_MANAGED_STATUSES = ("open", "closing")

# NOTE (2026-10-09): the CLOSING re-drive cap + park-pulse cadence are config knobs on
# CloseabilityConfig (management.closeability.max_closing_redrives / park_retry_ticks), read in
# _drive_closing. They bound the pre-redesign infinite ~15-min re-drive loop.


@dataclass
class EntryRoundResult:
    session_date: date
    primary: list[EvalResult] = field(default_factory=list)
    overflow: list[EvalResult] = field(default_factory=list)
    outcomes: list[EntryOutcome] = field(default_factory=list)
    auto_execute: bool = True


class MaceManager:
    def __init__(
        self,
        cfg: MaceConfig,
        port,
        store: RungStore,
        executor: MaceExecutor,
        notifier: MaceNotifier,
        *,
        risk_gate: Optional[Callable[[str, "object", int], bool]] = None,
        fetch_metrics: Optional[Callable[[list[str]], list]] = None,
        exdiv=None,
        auto_execute_fn: Callable[[], bool] = lambda: True,
        audit: Optional[Callable[..., None]] = None,
        now_utc_fn: Callable[[], datetime] = now_utc,
        now_et_fn: Callable[[], datetime] = now_et,
    ) -> None:
        self.cfg = cfg
        self.port = port
        self.store = store
        self.executor = executor
        self.notifier = notifier
        self._risk_gate = risk_gate
        self._fetch_metrics = fetch_metrics
        self._exdiv = exdiv
        self._auto_execute_fn = auto_execute_fn
        self._audit_fn = audit
        self._now_utc = now_utc_fn
        self._now_et = now_et_fn
        # PT mark-trust guard (2026-09-18): per-rung {last_mark, repeat, last_trusted} for the
        # frozen (timeliness) + fallback-baseline checks. In-memory (resets on restart — benign:
        # the sibling/structural checks are stateless; frozen also seeds off persisted mace_rung_live).
        self._pt_mark_trust: dict[str, dict] = {}
        # CLOSING-park pulse counters (2026-10-09): per-rung tick count while a committed close is
        # PARKED, so a parked rung re-drives only every park_retry_ticks ticks. In-memory (resets
        # on restart -> a parked rung simply gets one more re-drive attempt post-restart; benign).
        self._park_ticks: dict[str, int] = {}
        # PT-held alert de-dupe (2026-10-05): rung_ids CURRENTLY in a held (alert-worthy) episode.
        # Throttles the Telegram push ONLY -- one alert when a rung ENTERS held, suppressed while it
        # stays held (incl leg_inversion<->frozen flips on the same dead wing = one episode), and a
        # re-alert only AFTER it RESOLVES (passes the guard) then re-enters. In-memory (resets on
        # restart -> at most one re-alert per rung per restart, accepted). The per-tick
        # mace_pt_mark_reject audit row is UNAFFECTED -- the audit trail stays complete.
        self._pt_held: set[str] = set()

    # ── small helpers ────────────────────────────────────────────────────
    def _audit(self, kind: str, **payload) -> None:
        if self._audit_fn is not None:
            try:
                self._audit_fn(kind, **payload)
            except Exception:  # noqa: BLE001
                _LOG.exception("mace manager audit hook failed: %s", kind)
        _LOG.info("mace.%s %s", kind, payload)

    def _emit_entry_eval(self, r: EvalResult, reason: str) -> None:
        """Emit ONE per-symbol `mace_entry_eval` audit — the durable "why did X
        (not) enter" trail (skipped symbols leave no rung). `entered`/`skip_reason`
        are the PURE pipeline DECISION (byte-unchanged); `reason` is the human
        terminal disposition. For an entered symbol `reason` is filled in AFTER its
        placement resolves so it reflects the REAL outcome (TAKEN=filled+booked,
        entry_standdown=attempted-no-fill, ATTEMPT=decided-but-not-placed) instead
        of the pre-placement intent (the premature-"TAKEN" fix, 2026-08-25).
        Exactly one record per symbol per round."""
        self._audit("mace_entry_eval", symbol=r.symbol, entered=r.entered,
                    skip_reason=r.skip_reason, reason=reason,
                    ivr_status=r.ivr_status, ivr_value=r.ivr_value,
                    credit_mid=r.credit_mid, contracts=r.contracts,
                    max_risk_usd=r.max_risk_usd, overflow=r.overflow, detail=r.detail)

    def _enabled_symbols(self) -> list[str]:
        return [s for s, c in self.cfg.symbols.items() if c.enabled]

    def _snapshot_symbols(self, rungs) -> list[str]:
        """A4 (UI rebuild 2026-08-14): the daily IV-snapshot symbol set — EVERY
        defined symbol plus any symbol still holding an open/closing rung even if
        disabled (e.g. SPY, retired from entries but managing 2 rungs). This gives
        a retired-but-managed name fresh daily ATM IV for its payoff, and
        generalises to any future retired-with-open-rungs symbol. Superset of
        _enabled_symbols(); config order first, then any extra managed symbols.
        Used ONLY to widen the (single) market-metrics call + the snapshot write —
        chains/entries stay on the enabled universe (no extra chain fetches)."""
        out = list(self.cfg.symbols.keys())
        seen = set(out)
        for r in rungs:
            if r.status in _MANAGED_STATUSES and r.symbol not in seen:
                out.append(r.symbol)
                seen.add(r.symbol)
        return out

    # ── DB reads/writes not owned by the RungStore (events / equity / IVR) ─
    def _load_events(self) -> list[dict]:
        try:
            rows = self.store.conn.execute(
                "SELECT event_type, symbol_scope, event_date FROM economic_event"
            ).fetchall()
        except Exception:  # noqa: BLE001 — table absent -> no blackouts
            return []
        return [{"event_type": r["event_type"], "symbol_scope": r["symbol_scope"],
                 "event_date": r["event_date"]} for r in rows]

    def _load_equity(self) -> Optional[float]:
        try:
            row = self.store.conn.execute(
                "SELECT equity FROM mace_equity_snapshot ORDER BY snap_date DESC LIMIT 1"
            ).fetchone()
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        try:
            return float(row["equity"])
        except (TypeError, ValueError):
            return None

    def _load_available_bp(self) -> Optional[float]:
        """Latest snapshot's free/available BP (the reserve-cap basis when
        deployment_basis='available_buying_power'). None on a legacy row (column
        absent/NULL) or read error -> the reserve gate falls back to equity."""
        try:
            row = self.store.conn.execute(
                "SELECT available_buying_power FROM mace_equity_snapshot "
                "ORDER BY snap_date DESC LIMIT 1"
            ).fetchone()
        except Exception:  # noqa: BLE001 — legacy DB w/o the column, etc.
            return None
        if row is None:
            return None
        try:
            v = row["available_buying_power"]
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _entry_halted(self) -> bool:
        """The /mace UI halt latch (agent_state robinhood_mace/entry_halt).

        FAIL-SAFE = NOT halted: an absent row or a read error must not halt
        entries (auto_execute stays the primary kill-switch); the latch only
        ADDS a halt. Checked at round start, per symbol, and per attempt
        (via run_entry's halt_fn) — an already-resting order still completes
        its fill-or-cancel cycle (the honest latency shown in the UI)."""
        try:
            row = self.store.conn.execute(
                "SELECT value_json FROM agent_state WHERE agent=? AND key=?",
                ("robinhood_mace", "entry_halt")).fetchone()
            if row is None:
                return False
            return bool(json.loads(row["value_json"]).get("halted"))
        except Exception:  # noqa: BLE001
            _LOG.exception("mace entry_halt latch read failed (fail-safe: NOT halted)")
            return False

    # ── entry ────────────────────────────────────────────────────────────
    async def build_entry_context(self, session_date: date) -> st.EntryContext:
        symbols = self._enabled_symbols()
        chains: dict[str, st.ChainView] = {}
        for sym in symbols:
            try:
                chains[sym] = await self.port.chain(sym)
            except Exception as exc:  # noqa: BLE001 — one symbol's fetch must not sink eval
                self._audit("mace_chain_error", symbol=sym, error=str(exc))
                chains[sym] = st.ChainView(sym, None, (), {})

        rungs = self.store.load_all()
        # A4 (UI rebuild): the daily IV snapshot covers a WIDER set than the traded
        # (enabled) universe — every defined symbol plus any symbol still holding
        # open rungs even if disabled — so a retired-but-managed name (e.g. SPY)
        # gets fresh daily ATM IV for its payoff. This widens ONLY the single
        # market-metrics call + the snapshot write; chains/entries are unaffected.
        snap_symbols = self._snapshot_symbols(rungs)

        if self._fetch_metrics is not None:
            # Run the (blocking, internally-async) Tasty fetch OFF the event loop:
            # read_metrics -> _mace_fetch_metrics calls asyncio.run(), which is
            # illegal on the running loop (it failed EVERY round since launch, so the
            # >=25 floor fooled OPEN). to_thread gives the worker a loop-less context
            # so asyncio.run() is legal.
            ivr_readings = await asyncio.to_thread(
                ivr.read_metrics, self._fetch_metrics, snap_symbols, now=self._now_utc())
            # A TOTAL IVR outage must NOT pass silently — the floor fails open, so a
            # silent outage means no IVR gate at all. Fires at most once per round.
            if snap_symbols and all(r.status == ivr.IVR_UNAVAILABLE
                                    for r in ivr_readings.values()):
                self._audit("mace_ivr_outage", symbols=snap_symbols,
                            detail=next((r.detail for r in ivr_readings.values()), ""))
                try:
                    self.notifier.error(loop="ivr", exc=RuntimeError(
                        f"IVR unavailable for all {len(snap_symbols)} symbols "
                        f"(floor fails open)"))
                except Exception:  # noqa: BLE001 — an alert must never break eval
                    pass
        else:
            ivr_readings = {s: ivr.IvrReading(
                s, ivr.IVR_UNAVAILABLE, None, None, None, None,
                "no IVR fetch wired") for s in snap_symbols}

        events = self._load_events()
        equity = self._load_equity()
        available_bp = self._load_available_bp()

        # IV snapshot corpus from day 1 (self-sufficiency) — never blocks eval.
        try:
            ivr.snapshot_readings(self.store.conn, ivr_readings, session_date)
        except Exception as exc:  # noqa: BLE001
            self._audit("mace_iv_snapshot_error", error=str(exc))

        return st.EntryContext(
            session_date=session_date, equity=equity, rungs=rungs, events=events,
            ivr=ivr_readings, chains=chains, risk_gate=self._risk_gate,
            available_buying_power=available_bp,
            # Option A baseline: open max_risk at eval start. The per-placement
            # recheck reloads rungs (via replace) but keeps this baseline, so the
            # available-BP reserve gate counts only rungs placed WITHIN THIS EVAL
            # (pre-existing risk is already netted from the available-BP snapshot).
            eval_start_open_max_risk=st.sum_open_max_risk(rungs))

    async def evaluate_and_enter(self, session_date: date) -> EntryRoundResult:
        ctx = await self.build_entry_context(session_date)
        primary = [st.evaluate_entry(sym, self.cfg, ctx) for sym in self.cfg.universe]
        overflow = st.route_overflow(primary, self.cfg, ctx)

        # Per-symbol eval record — the ONLY durable "why did X (not) enter" trail for
        # a no-HITL division (skipped symbols leave no rung), rendered payload.reason
        # first by the /mace Activity Pulse. SKIPPED symbols are terminal at eval
        # time (no rung will ever exist) so they are recorded now. ENTERED symbols
        # get their record AFTER their placement resolves (below) so the disposition
        # reflects the ACTUAL outcome (filled -> TAKEN, no fill -> entry_standdown)
        # rather than the pre-placement intent — a stood-down entry must not log
        # "TAKEN". Emitted regardless of auto_execute so standby/halt rounds stay
        # diagnosable. Exactly one record per symbol per round. Decisions above are
        # byte-unchanged; this is formatting + WHEN it is emitted only.
        entered_evals = [r for r in list(primary) + list(overflow) if r.entered]
        for r in list(primary) + list(overflow):
            if not r.entered:
                self._emit_entry_eval(r, disposition_line(r, self.cfg, ctx))

        auto = bool(self._auto_execute_fn())
        result = EntryRoundResult(session_date=session_date, primary=primary,
                                  overflow=overflow, auto_execute=auto)
        if not auto:
            # Standby: nothing is placed -> each entered symbol WOULD have attempted
            # (ATTEMPT, filled=None), which is the honest terminal disposition here.
            for r in entered_evals:
                self._emit_entry_eval(r, disposition_line(r, self.cfg, ctx))
            self._audit("mace_entry_halted", reason="auto_execute=false",
                        entered=len(entered_evals))
            return result
        if self._entry_halted():
            for r in entered_evals:
                self._emit_entry_eval(r, disposition_line(r, self.cfg, ctx))
            self._audit("mace_entry_halted", reason="operator_halt_latch",
                        entered=len(entered_evals))
            return result

        # Place each ENTER, RE-VALIDATING against post-placement state: the
        # capacity/reserve/duplicate gates depend on `rungs`, so reload it before each
        # placement (cheap: reuse the round's chains/IVR via dataclasses.replace,
        # refresh only rungs). Without this, a rung placed earlier this round is
        # invisible to the next placement's gates (the stale pre-placement snapshot).
        #
        # OQ-2 (entry-window serialization, Board-approved 2026-08-13): placements
        # are PRIORITIZED — entered primaries highest-IVR first (the same
        # missing-IVR=-1.0 convention route_overflow's ivr_of uses; the sort is
        # stable, so IVR-less symbols keep config order), overflow entries after
        # primaries — and each symbol gets a DYNAMIC time budget:
        #   deadline = now + (cutoff - now) / symbols_remaining
        # recomputed from the ACTUAL clock before each ladder, so an early fill
        # donates its unused window to later symbols. A symbol whose turn arrives
        # with no window left is SKIPPED with a mace_entry_window_skip audit
        # (never silently starved); inside the ladder the deadline stands down as
        # "window_budget" through the same clean stand-down path as the 15:58
        # cutoff. Ladders stay strictly SEQUENTIAL — one in flight, ever (the
        # dup-entry recheck above and the reserve gate depend on it).
        to_place = sorted(
            [r for r in primary if r.entered],
            key=lambda r: r.ivr_value if r.ivr_value is not None else -1.0,
            reverse=True) + [r for r in overflow if r.entered]
        cutoff_t = dtime.fromisoformat(self.cfg.entry.entry_cutoff_et)
        for i, res in enumerate(to_place):
            try:
                # Operator halt latch re-checked before EVERY ladder (not just at
                # round start) — a mid-round /mace HALT stops the NEXT symbol.
                if self._entry_halted():
                    self._audit("mace_entry_halted_midround",
                                remaining=[r.symbol for r in to_place[i:]],
                                reason="operator_halt_latch")
                    # The remaining entered symbols were never placed -> ATTEMPT
                    # (decided to enter, halted before their turn), not TAKEN.
                    for r in to_place[i:]:
                        self._emit_entry_eval(r, disposition_line(r, self.cfg, ctx))
                    break
                now = self._now_et()
                # P1.5: anchor the cutoff to the SESSION date, not now's — an
                # off-hours restart runs this after midnight, where now.replace()
                # would build the NEXT day's cutoff and see a full stale window
                # "remaining" (00:06 -> 15:58 same-day), admitting a STALE entry.
                # Same-day (now.date() == session_date): byte-identical to before.
                cutoff_dt = now.replace(
                    year=session_date.year, month=session_date.month,
                    day=session_date.day, hour=cutoff_t.hour,
                    minute=cutoff_t.minute, second=0, microsecond=0)
                remaining = (cutoff_dt - now).total_seconds()
                if remaining <= 0:
                    self._audit("mace_entry_window_skip", symbol=res.symbol,
                                position=i + 1, of=len(to_place),
                                reason="window_exhausted")
                    self._emit_entry_eval(res, disposition_line(
                        res, self.cfg, ctx, filled=False,
                        standdown_reason="window_exhausted"))
                    continue
                deadline = now + timedelta(seconds=remaining / (len(to_place) - i))
                recheck = st.evaluate_entry(
                    res.symbol, self.cfg,
                    replace(ctx, rungs=self.store.load_all()),
                    is_overflow=res.overflow)
                if not recheck.entered:
                    self._audit("mace_entry_superseded", symbol=res.symbol,
                                skip_reason=recheck.skip_reason,
                                reason=f"SKIP superseded -> {recheck.skip_reason}",
                                detail=res.detail)
                    # Superseded by post-placement state (e.g. capacity/reserve
                    # taken by an earlier fill this round) -> the re-eval SKIP is
                    # this symbol's honest terminal disposition.
                    self._emit_entry_eval(recheck, disposition_line(recheck, self.cfg, ctx))
                    continue
                # A3: capture the symbol's fresh ATM IV as this rung's permanent
                # entry IV. None when IV was unavailable this round (promote_open
                # then leaves entry_atm_iv NULL — never a bogus 0).
                reading = ctx.ivr.get(recheck.symbol)
                entry_iv = reading.atm_iv if reading is not None else None
                out = await self.executor.run_entry(recheck, session_date,
                                                    deadline=deadline,
                                                    halt_fn=self._entry_halted,
                                                    entry_atm_iv=entry_iv)
                result.outcomes.append(out)
                # Terminal disposition NOW reflects the REAL executor outcome:
                # filled -> TAKEN, no fill (floor-drift/cutoff/reject/…) ->
                # entry_standdown. This is the premature-"TAKEN" fix.
                self._emit_entry_eval(recheck, disposition_line(
                    recheck, self.cfg, ctx, filled=out.filled,
                    standdown_reason=out.standdown_reason))
            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                self._audit("mace_entry_exception", symbol=res.symbol,
                            reason=f"EXCEPTION {exc}", error=str(exc))
                self._emit_entry_eval(res, disposition_line(
                    res, self.cfg, ctx, filled=False, standdown_reason="error"))
                self.notifier.error(loop="entry", exc=exc)
        return result

    # ── management ───────────────────────────────────────────────────────
    async def manage_tick(self, now_et_dt: Optional[datetime] = None) -> list[ExitOutcome]:
        now = now_et_dt or self._now_et()
        outcomes: list[ExitOutcome] = []
        rungs = self.store.load_by_status(*_MANAGED_STATUSES)
        # cache one spot per distinct symbol (ex-div ITM test)
        spot_cache: dict[str, Optional[float]] = {}
        for rung in rungs:
            try:
                out = await self._manage_one(rung, now, spot_cache)
                if out is not None:
                    outcomes.append(out)
            except Exception as exc:  # noqa: BLE001 — one rung must not sink the loop
                self._audit("mace_manage_error", rung_id=rung.rung_id, error=str(exc))
                self.notifier.error(loop="manage", exc=exc)
        return outcomes

    async def _manage_one(self, rung: RungState, now: datetime,
                          spot_cache: dict) -> Optional[ExitOutcome]:
        sym_cfg = self.cfg.symbols.get(rung.symbol)
        if sym_cfg is None:
            return None
        # A rung already CLOSING (a prior exit exhausted/latched). 2026-10-09: no longer a blind
        # infinite re-drive -- _drive_closing caps committed re-drives (park) and UN-LATCHES a
        # winner-class/legacy CLOSING rung with no booked fill + no working order (the self-heal for
        # the wedged XLE rung; the closeability gate -> ride). 2026-10-08: it also re-evaluates a
        # STOP-class CLOSING rung (fetching its own fresh mark+spot) and reopens it if the stop is no
        # longer valid (spurious-stop heal). Kept BEFORE the snapshot so a closing rung's path is
        # unchanged for executors that don't implement quote_snapshot.
        if rung.status == "closing":
            return await self._drive_closing(rung, now)

        # ONE fresh 4-leg snapshot per tick (2026-10-09): mark (triggers), leg_mids (PT mark-trust
        # guard) and natural/wings (closeability gate) all derive from it, so trigger + guard + gate
        # can never disagree across two fetches (the old mark()-vs-leg_mids race).
        snap = await self.executor.quote_snapshot(rung.spec)
        mark = snap.mark
        if rung.symbol not in spot_cache:
            spot_cache[rung.symbol] = await self._spot(rung.symbol)
        spot = spot_cache[rung.symbol]

        # PT mark-trust guard (2026-09-18): capture the PRIOR persisted mark BEFORE set_live_state
        # overwrites it, for the guard's frozen/timeliness check (restart-robust seed). Read-only.
        try:
            _prior_live = self.store.get_live_state(rung.rung_id)
        except Exception:  # noqa: BLE001 — a read miss must not sink the tick
            _prior_live = None
        prior_persisted_mark = _prior_live[0] if _prior_live else None

        # UI read-model (A1/A2): persist this tick's live mark + spot to
        # mace_rung_live so the /mace GET can render them WITHOUT ever touching the
        # broker. FAIL-SAFE: a dashboard-write error is logged and swallowed — it
        # must NEVER be able to sink a manage tick or block an exit decision.
        try:
            self.store.set_live_state(
                rung.rung_id, rung.symbol, mark, spot,
                self._now_utc().isoformat(timespec="seconds"))
        except Exception as exc:  # noqa: BLE001 — dashboard write is non-load-bearing
            self._audit("mace_live_state_error", rung_id=rung.rung_id, error=str(exc))

        exdiv_within = False
        if sym_cfg.exdiv_guard and self._exdiv is not None:
            try:
                exdiv_within = self._exdiv.within_window(
                    rung.symbol, now, self.cfg.management.exdiv_guard_sessions)
            except Exception as exc:  # noqa: BLE001
                self._audit("mace_exdiv_error", symbol=rung.symbol, error=str(exc))

        # RIDE-TO-EXPIRY disposition (2026-10-09): a dead-wing OTM winner whose close is not
        # executable rides to expiry (Jack's ruling) rather than looping an unfillable close. A ride
        # is STICKY -- it clears ONLY by resolving the position: (a) the real gate passes again (close
        # now executable -> close it), (b) a STOP/EXDIV fires (risk overrides), (c) a SHORT is
        # THREATENED (spot within ride_shorts_buffer_pct -> drop ride + re-manage now, pin/assignment),
        # or (d) the reconcile expiry sweep books it. While riding, PT/TIME that STILL fail the gate
        # are suppressed SILENTLY (one alert, at ride-enter) -- no liveness-revive (a tiny-bid garbage
        # wing is "two-sided" yet uncloseable, so a liveness check would flap ride<->revive).
        riding = (rung.extra or {}).get("disposition") == "riding"
        if riding and self._ride_shorts_threatened(rung, spot):
            self.store.clear_riding(rung.rung_id)
            self._audit("mace_ride_escape", rung_id=rung.rung_id, symbol=rung.symbol, spot=spot,
                        detail="short within buffer of spot -> drop ride, re-manage this tick")
            riding = False

        decision = st.evaluate_management(rung, mark, spot, now, self.cfg, sym_cfg,
                                          exdiv_within=exdiv_within)
        if not decision.should_exit:
            return None     # hold; a riding rung stays riding silently (sticky to close/escape/expiry)
        # PT MARK-TRUST GUARD (2026-09-18): gate ONLY the synthetic PT fire on a TIMELY + SANE mark.
        # An untrusted mark HOLDS the rung (return None) + alerts; it does NOT log mace_manage_exit
        # (that would misreport a close that never happened). stop/time/exdiv are unguarded (they must
        # fire — risk-reducing). Fed the SAME snapshot's leg_mids (no second fetch -> no race).
        if decision.exit_reason == EXIT_PT:
            if not await self._pt_mark_guard(rung, mark, now, prior_persisted_mark,
                                             leg_marks=snap.leg_mids):
                return None
        # CLOSEABILITY GATE (2026-10-09): a PT/TIME winner close must be EXECUTABLE -- long wings
        # two-sided (sellable) + a natural debit within the ladder's cap. A dead-wing OTM condor
        # fails deterministically -> do NOT attempt an unfillable close (the 10/02+10/05 loop); HOLD
        # (Commit 3 turns this hold into a first-class RIDE-to-expiry). STOP/EXDIV are risk-reducing
        # -> NEVER gated (they price off stop_natural, dead wings given away). The mark-trust guard
        # above stays as defense-in-depth for a corrupt SHORT-leg mid the gate cannot see.
        if decision.exit_reason in (EXIT_PT, EXIT_TIME):
            target = self._closeability_target(decision.exit_reason, rung, snap, now)
            gate = st.assess_closeability(rung, snap, self.cfg,
                                          exit_reason=decision.exit_reason, target_debit=target)
            if not gate.closeable:
                if riding:
                    return None                       # already riding -> suppress re-alert, stay riding
                self._enter_ride(rung, gate, decision.exit_reason)   # open -> riding (one alert)
                return None
        # Proceeding to an actual close (PT/TIME now executable, or an ungated STOP/EXDIV): if the
        # rung was riding, the position is being resolved -> clear the disposition.
        if riding:
            self.store.clear_riding(rung.rung_id)
            self._audit("mace_ride_cleared", rung_id=rung.rung_id, symbol=rung.symbol,
                        reason=decision.exit_reason,
                        detail="close executable again / risk override -> resolving ride")
        self._audit("mace_manage_exit", rung_id=rung.rung_id,
                    reason=decision.exit_reason, detail=decision.detail,
                    symbol=rung.symbol,
                    line=exit_disposition_line(rung.spec, decision.exit_reason,
                                               phase="decision"))
        pricing, defer = self._close_pricing(decision.exit_reason, rung, now)
        # trigger_mid = the mid the exit decision was made on (this tick's mark). The winner
        # ladder anchors its mid+band cap to THIS value and holds it fixed for the whole walk
        # (2026-09-18), so an adverse re-inflation during the ~2-min close cannot chase the
        # cap up. None (unpriceable TIME mark) -> close_rung falls back to its first mid.
        return await self.executor.close_rung(
            rung, decision.exit_reason, pricing=pricing, defer_on_unfilled=defer,
            trigger_mid=mark)

    async def _drive_closing(self, rung: RungState, now: datetime) -> Optional[ExitOutcome]:
        """Drive a rung in status=CLOSING (2026-10-09 un-latch). Replaces the old blind
        `close_rung(rung, exit_reason or 'manual')` that re-drove EVERY tick forever.

          STOP-class (stop/exdiv/gap): a genuine risk close MUST keep trying, CAPPED
          (max_closing_redrives) then PARKED (one URGENT + occasional pulse) instead of a ~15-min
          flood. 2026-10-08: FIRST re-evaluate a STOP on its OWN fresh mark+spot -- if it is no
          longer valid (position healthy again; the 2026-10-07 stop_mark-flooring spurious stop) and
          nothing is booked/working, REOPEN to open rather than re-drive an unwarranted/unfillable
          close. Guarded on credit_actual present (an unsizable rung's stop can't be re-evaluated ->
          keep driving, never spuriously reopen).
          WINNER-class (pt/time) or a LEGACY NULL-reason wedge: a profit close that cannot fill
          must not loop. With NO booked fill and NO working close order, REOPEN the rung to `open`
          (self-heals the wedged XLE rung; the closeability gate then routes a dead-wing winner to RIDE).
          A persisted in-flight order / a working {rid}-x* order -> drive close_rung ONCE so its
          crash-recovery preamble reconciles the live order (never reopen underneath a live close).

        A rung whose exit fields are already set is a booked close mislabelled `closing` (anomaly) ->
        audit + no-op (never destructive)."""
        rid = rung.rung_id
        reason = rung.exit_reason
        extra = rung.extra or {}
        if rung.exit_ts is not None or rung.exit_debit is not None or rung.realized_pnl is not None:
            self._audit("mace_closing_anomaly", rung_id=rid, reason=reason,
                        detail="CLOSING rung carries booked exit fields; left untouched")
            return None

        cc = self.cfg.management.closeability
        if reason in (EXIT_STOP, EXIT_EXDIV, EXIT_GAP):
            # SPURIOUS-STOP SELF-HEAL (2026-10-08): a STOP that is no longer valid on fresh quotes
            # (position healthy again) must not keep re-driving an unwarranted close. Re-evaluate on
            # this rung's OWN fresh mark+spot (closing rungs don't carry the tick snapshot). With
            # nothing booked and no working order, REOPEN to open. Only EXIT_STOP (the stop_mark
            # regression) + credit present (can't re-evaluate an unsizable stop -> keep driving);
            # exdiv/gap are structural and keep driving.
            if (reason == EXIT_STOP and rung.credit_actual is not None
                    and not extra.get("exit_order_id")):
                try:
                    chk_mark = await self.executor.mark(rung.spec)
                except Exception:  # noqa: BLE001 — a quote miss must not sink the drive
                    chk_mark = None
                chk_spot = await self._spot(rung.symbol)
                if not st.stop_triggered(rung, chk_mark, chk_spot, self.cfg.management):
                    working = await self.executor._working_exit_order(rid)
                    if working is None and self.store.reopen_from_closing(rid):
                        self._audit("mace_closing_reopen", rung_id=rid, reason=reason,
                                    detail="stop no longer valid on fresh quotes (spurious-stop "
                                           "self-heal) -> reopened to open")
                        self.notifier.breaker(
                            condition=f"{rung.symbol} CLOSING rung reopened (stop no longer valid)",
                            lines=[f"rung {rid}", f"reason {reason}"],
                            suggested_action="spurious stop healed; back to OPEN, re-managed next tick",
                            urgent=False)
                        return None
            blk = extra.get("closing") or {}
            if blk.get("parked"):
                n = self._park_ticks.get(rid, 0) + 1
                self._park_ticks[rid] = n
                if n % cc.park_retry_ticks != 0:
                    return None
                return await self.executor.close_rung(rung, reason)
            count = self.store.bump_closing_redrive(
                rid, self._now_utc().isoformat(timespec="seconds"))
            if count > cc.max_closing_redrives:
                self.store.set_closing_parked(rid, self._now_utc().isoformat(timespec="seconds"))
                self._audit("mace_close_parked", rung_id=rid, reason=reason, redrives=count)
                self.notifier.breaker(
                    condition=f"{rung.symbol} CLOSE BLOCKED — {count} re-drives unfilled",
                    lines=[f"rung {rid}", f"reason {reason}"],
                    suggested_action="manual close needed; auto re-drive PARKED (hourly pulse)")
                return None
            return await self.executor.close_rung(rung, reason)

        # winner-class (pt/time) OR legacy NULL-reason wedge.
        if extra.get("exit_order_id"):
            # An in-flight close is persisted -> let close_rung's preamble reconcile it (drive once).
            return await self.executor.close_rung(rung, reason or EXIT_MANUAL)
        working = await self.executor._working_exit_order(rid)   # None | OpenOrder | sentinel
        if working is None:
            if self.store.reopen_from_closing(rid):
                self._audit("mace_closing_reopen", rung_id=rid, reason=reason,
                            detail="winner-class/legacy CLOSING, no booked fill + no working order "
                                   "-> reopened to open (re-evaluated next tick)")
                self.notifier.breaker(
                    condition=f"{rung.symbol} CLOSING rung reopened (no fill, no working order)",
                    lines=[f"rung {rid}", f"reason {reason or 'none'}"],
                    suggested_action="back to OPEN; re-evaluated next manage tick", urgent=False)
                return None
            # reopen guard refused (exit fields set between load and now) -> anomaly, no-op.
            self._audit("mace_closing_reopen_refused", rung_id=rid, reason=reason)
            return None
        # a working close order exists (or the sweep errored) -> drive once; preamble owns it.
        return await self.executor.close_rung(rung, reason or EXIT_MANUAL)

    def _closeability_target(self, reason: str, rung: RungState, snap, now: datetime):
        """Target debit for the closeability gate's winner at-cap check. PT -> the synthetic PT
        debit (the ladder caps at pt_debit + band). TIME above the defer floor -> the trigger mid
        (winner cap anchor); TIME at/below the floor (forced marketable) -> None = liveness-only
        (the marketable ladder may pay up to width, so only wings-two-sided + computable matter)."""
        m = self.cfg.management
        if reason == EXIT_PT:
            if rung.pt_debit is not None:
                return rung.pt_debit
            return (m.pt_pct_of_credit * rung.credit_actual
                    if rung.credit_actual is not None else None)
        dte = (rung.expiry - now.date()).days
        return snap.mark if dte > m.time_exit_defer_floor_dte else None

    def _enter_ride(self, rung: RungState, gate, trigger_reason: str) -> None:
        """Transition an OPEN rung -> RIDING (disposition, not status). Called ONLY on the first
        gate-fail of a PT/TIME close (a riding rung's PT/TIME is suppressed before the gate), so the
        alert fires exactly ONCE per ride episode. The rung stays `open` and keeps stop protection."""
        self.store.set_riding(rung.rung_id, why=gate.reason,
                              ts=self._now_utc().isoformat(timespec="seconds"), detail=gate.detail)
        self._audit("mace_ride_enter", rung_id=rung.rung_id, symbol=rung.symbol,
                    reason=trigger_reason, gate=gate.reason,
                    natural=(round(gate.natural, 4) if gate.natural is not None else None),
                    detail=gate.detail)
        self.notifier.breaker(
            condition=f"{rung.symbol} RIDING to expiry — close not executable ({gate.reason})",
            lines=[f"rung {rung.rung_id}", f"trigger {trigger_reason}", gate.detail],
            suggested_action="holding OTM defined-risk to expiry; auto-revives if the wing market returns",
            urgent=False)

    def _ride_shorts_threatened(self, rung: RungState, spot: Optional[float]) -> bool:
        """A riding condor is OTM; if spot approaches/crosses EITHER short (within
        ride_shorts_buffer_pct), pin/assignment risk means we must drop the ride and re-manage
        (a stop may be due, and near-money legs are liquid so the gate will pass). spot None (quote
        outage) -> not threatened (hold the ride; a blind close helps nothing)."""
        if spot is None or rung.spec is None:
            return False
        buf = self.cfg.management.closeability.ride_shorts_buffer_pct * spot
        return spot <= rung.spec.short_put + buf or spot >= rung.spec.short_call - buf

    async def _pt_mark_guard(self, rung: RungState, mark: Optional[float], now: datetime,
                             prior_persisted_mark: Optional[float], *,
                             leg_marks: "dict | None" = None) -> bool:
        """PT mark-trust guard (2026-09-18). Returns True if the PT-eligible `mark` is TRUSTED
        (fire the profit-target close), False to HOLD the rung. Fetches the fresh marks of any
        same-strike shorter-dated sibling rungs (the arbitrage lower bound), tracks the per-rung
        frozen/trust state, calls the PURE `strategy.assess_pt_mark_trust`, and on an untrusted mark
        emits the reuse alert (Activity Pulse audit + Telegram) — except the silent fail-closed
        no-baseline one-cycle hold. Never raises into the manage tick.

        2026-10-09: `leg_marks` (the rung's per-leg mids for the intra-condor leg-sanity check) is
        now passed IN from the manager's single tick snapshot (QuoteSnapshot.leg_mids) rather than
        re-fetched here -- killing the old mark()-vs-leg_mids() two-snapshot race. Falls back to a
        fetch only if not supplied (defensive; the manager always supplies it)."""
        all_rungs = self.store.load_all()
        sibs = st.shorter_dated_same_strike_siblings(all_rungs, rung)
        sib_marks: list[Optional[float]] = []
        for s in sibs:
            try:
                sib_marks.append(await self.executor.mark(s.spec))
            except Exception as exc:  # noqa: BLE001 — a sibling quote miss must not sink the guard
                self._audit("mace_pt_sibling_mark_error", rung_id=rung.rung_id,
                            sibling=s.rung_id, error=str(exc))

        if leg_marks is None:   # defensive fallback (manager supplies from the shared snapshot)
            leg_marks = await self.executor.leg_mids(rung.spec)

        stt = self._pt_mark_trust.get(rung.rung_id)
        # Cold start (first touch / post-restart) falls back to the persisted prior mark so the frozen
        # check is restart-robust; last_trusted stays None on cold start to preserve fail-closed-no-baseline.
        eff_last = stt["last_mark"] if stt else prior_persisted_mark
        prior_repeat = stt["repeat"] if stt else (1 if prior_persisted_mark is not None else 0)
        last_trusted = stt["last_trusted"] if stt else None
        identical = (eff_last is not None and mark is not None and abs(mark - eff_last) < 0.005)
        unchanged_repeat = (prior_repeat + 1) if identical else 1
        dte = (rung.expiry - now.date()).days

        trust = st.assess_pt_mark_trust(
            rung, mark, self.cfg, sibling_marks=sib_marks, unchanged_repeat=unchanged_repeat,
            last_trusted_mark=last_trusted, dte=dte, leg_marks=leg_marks)

        # Seed last_trusted on a trusted mark OR a no_baseline hold (so the next tick has a baseline).
        new_trusted = mark if (trust.trusted or trust.reason == "no_baseline") else last_trusted
        self._pt_mark_trust[rung.rung_id] = {
            "last_mark": mark, "repeat": unchanged_repeat, "last_trusted": new_trusted}

        if trust.trusted:
            self._pt_held.discard(rung.rung_id)  # resolved -> a later re-entry re-alerts (alert-path only)
            return True
        if trust.alert:
            # Decision + HOLD + audit row UNCHANGED: the per-tick mace_pt_mark_reject row is always
            # written (complete trail). Only the Telegram push below is de-duped per held episode.
            self._audit("mace_pt_mark_reject", rung_id=rung.rung_id, symbol=rung.symbol,
                        mark=(round(mark, 4) if mark is not None else None), reason=trust.reason,
                        sibling_marks=[round(x, 4) for x in sib_marks if x is not None],
                        dte=dte, detail=trust.detail)
            if rung.rung_id not in self._pt_held:  # first held tick of this episode -> alert once
                self._pt_held.add(rung.rung_id)
                self.notifier.reject(
                    symbol=rung.symbol,
                    detail=(f"PT held - untrusted mark "
                            f"{('%.2f' % mark) if mark is not None else 'None'} "
                            f"({trust.reason}); {trust.detail}"))
        return False

    def _close_pricing(self, reason: str, rung: RungState,
                       now: datetime) -> "tuple[str, bool]":
        """Per-reason close pricing (GDX P1 fix 2026-09-11). Returns (pricing,
        defer_on_unfilled). WINNERS (TIME/PT) close at MID capped at mid+exit_winner_band --
        never cross the whole spread on a profitable close. STOP/exdiv/gap cross the spread (a
        loser/risk MUST fill). TIME defers if unfilled within the band until
        time_exit_defer_floor_dte, then forces natural (some close beats carrying a
        defined-risk condor to expiry); PT defers with NO floor (a winner is fine to keep --
        retries next tick)."""
        m = self.cfg.management
        if reason == EXIT_PT:
            return "winner", True
        if reason == EXIT_TIME:
            dte = (rung.expiry - now.date()).days
            if dte > m.time_exit_defer_floor_dte:
                return "winner", True
            return "marketable", False   # DTE floor: FORCE the close (natural / cross-spread)
        return "marketable", False       # stop / exdiv / gap / manual: unchanged

    async def _spot(self, symbol: str) -> Optional[float]:
        try:
            return float(await self.port.quote(symbol))  # type: ignore[attr-defined]
        except AttributeError:
            try:
                return (await self.port.chain(symbol)).spot
            except Exception:  # noqa: BLE001
                return None
        except Exception:  # noqa: BLE001
            return None

    # ── reconcile ────────────────────────────────────────────────────────
    async def reconcile_tick(self, session_date: Optional[date] = None) -> None:
        sd = session_date or self._now_et().date()
        await self.executor.reconcile(sd)

    # ── equity snapshot (sizing basis) ───────────────────────────────────
    async def snapshot_equity(self, session_date: Optional[date] = None):
        sd = session_date or self._now_et().date()
        snap = await self.port.snapshot()
        ts = self._now_utc().isoformat(timespec="seconds")
        # available_buying_power (free BP) persists alongside equity so the reserve
        # gate at 15:45 reads the same 15:40 basis. NULL when the broker doesn't
        # expose it -> the gate falls back to equity (see strategy.deployment_base).
        avail_bp = getattr(snap, "available_buying_power", None)
        self.store.conn.execute(
            "INSERT OR REPLACE INTO mace_equity_snapshot "
            "(snap_date, equity, cash, market_value, available_buying_power, ts) "
            "VALUES (?,?,?,?,?,?)",
            (sd.isoformat(), snap.equity, snap.cash, snap.market_value, avail_bp, ts))
        # Audit the basis figures so a box/live run reveals whether the deployment
        # cap actually ran on available BP (not a silent fall-back to equity).
        self._audit("mace_equity_snapshot", snap_date=sd.isoformat(),
                    equity=snap.equity, available_buying_power=avail_bp,
                    deployment_basis=self.cfg.sizing.deployment_basis)
        return snap

    # ── daily summary (15:50 slot) ───────────────────────────────────────
    async def daily_summary(self, session_date: Optional[date] = None):
        sd = session_date or self._now_et().date()
        rungs = self.store.load_all()
        equity = self._load_equity()
        open_rungs = [r for r in rungs if r.status in _MANAGED_STATUSES]
        day_pnl = st.day_realized(rungs, sd)
        week_pnl = st.week_realized(rungs, sd)
        # TODO(go-live): persist HWM in agent_state; equity-as-HWM is a scaffolding stand-in.
        hwm = equity
        breakers = st.evaluate_breakers(day_pnl, week_pnl, equity, hwm, self.cfg)
        open_lines = [
            f"{r.symbol} {r.spec.strikes_label()} x{r.contracts} exp {r.expiry.isoformat()}"
            for r in open_rungs
        ]
        breaker_states = [n for n, hit in (
            ("day_loss", breakers.day_loss_hit), ("week_loss", breakers.week_loss_hit),
            ("hwm_soft", breakers.hwm_soft_hit), ("hwm_hard", breakers.hwm_hard_hit)) if hit]
        self.notifier.daily_summary(
            session_date=sd.isoformat(), equity=equity, hwm=hwm, open_rungs=open_lines,
            day_pnl=day_pnl, breaker_states=breaker_states, next_blackouts=[])
        self._audit("mace_daily_summary", session_date=sd.isoformat(), equity=equity,
                    open=len(open_rungs), day_pnl=day_pnl, breakers=breaker_states)
        return breakers

    # ── weekly calendar refresh (Sun slot) — idempotent re-seed ──────────
    async def refresh_calendar(self, macro_path: str = "config/macro_calendar.yaml"):
        from trading_corp.mace import calendar as mace_cal
        try:
            result = mace_cal.weekly_refresh(self.store.conn, macro_path,
                                             today=self._now_et().date())
            self._audit("mace_calendar_refresh", detail=str(result))
            return result
        except Exception as exc:  # noqa: BLE001 — never sink the loop
            self._audit("mace_calendar_refresh_error", error=str(exc))
            return None
