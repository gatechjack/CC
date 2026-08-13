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
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Callable, Optional

from trading_corp.mace import ivr_provider as ivr
from trading_corp.mace import strategy as st
from trading_corp.mace.config import MaceConfig
from trading_corp.mace.domain import EvalResult, RungState
from trading_corp.mace.execution import EntryOutcome, ExitOutcome, MaceExecutor, RungStore
from trading_corp.mace.notify import MaceNotifier
from trading_corp.utils.time import now_et, now_utc

_LOG = logging.getLogger("mace.manager")

# Statuses whose rungs the management loop marks (open positions + those mid-close
# so a crash-interrupted exit keeps being driven).
_MANAGED_STATUSES = ("open", "closing")


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

    # ── small helpers ────────────────────────────────────────────────────
    def _audit(self, kind: str, **payload) -> None:
        if self._audit_fn is not None:
            try:
                self._audit_fn(kind, **payload)
            except Exception:  # noqa: BLE001
                _LOG.exception("mace manager audit hook failed: %s", kind)
        _LOG.info("mace.%s %s", kind, payload)

    def _enabled_symbols(self) -> list[str]:
        return [s for s, c in self.cfg.symbols.items() if c.enabled]

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

        if self._fetch_metrics is not None:
            # Run the (blocking, internally-async) Tasty fetch OFF the event loop:
            # read_metrics -> _mace_fetch_metrics calls asyncio.run(), which is
            # illegal on the running loop (it failed EVERY round since launch, so the
            # >=25 floor fooled OPEN). to_thread gives the worker a loop-less context
            # so asyncio.run() is legal.
            ivr_readings = await asyncio.to_thread(
                ivr.read_metrics, self._fetch_metrics, symbols, now=self._now_utc())
            # A TOTAL IVR outage must NOT pass silently — the floor fails open, so a
            # silent outage means no IVR gate at all. Fires at most once per round.
            if symbols and all(r.status == ivr.IVR_UNAVAILABLE
                               for r in ivr_readings.values()):
                self._audit("mace_ivr_outage", symbols=symbols,
                            detail=next((r.detail for r in ivr_readings.values()), ""))
                try:
                    self.notifier.error(loop="ivr", exc=RuntimeError(
                        f"IVR unavailable for all {len(symbols)} symbols "
                        f"(floor fails open)"))
                except Exception:  # noqa: BLE001 — an alert must never break eval
                    pass
        else:
            ivr_readings = {s: ivr.IvrReading(
                s, ivr.IVR_UNAVAILABLE, None, None, None, None,
                "no IVR fetch wired") for s in symbols}

        rungs = self.store.load_all()
        events = self._load_events()
        equity = self._load_equity()

        # IV snapshot corpus from day 1 (self-sufficiency) — never blocks eval.
        try:
            ivr.snapshot_readings(self.store.conn, ivr_readings, session_date)
        except Exception as exc:  # noqa: BLE001
            self._audit("mace_iv_snapshot_error", error=str(exc))

        return st.EntryContext(
            session_date=session_date, equity=equity, rungs=rungs, events=events,
            ivr=ivr_readings, chains=chains, risk_gate=self._risk_gate)

    async def evaluate_and_enter(self, session_date: date) -> EntryRoundResult:
        ctx = await self.build_entry_context(session_date)
        primary = [st.evaluate_entry(sym, self.cfg, ctx) for sym in self.cfg.universe]
        overflow = st.route_overflow(primary, self.cfg, ctx)

        # Per-symbol eval record — the ONLY durable "why did X (not) enter" trail for
        # a no-HITL division (skipped symbols leave no rung). Emitted regardless of
        # auto_execute so standby/halt rounds are diagnosable too.
        for r in list(primary) + list(overflow):
            self._audit("mace_entry_eval", symbol=r.symbol, entered=r.entered,
                        skip_reason=r.skip_reason, ivr_status=r.ivr_status,
                        ivr_value=r.ivr_value, credit_mid=r.credit_mid,
                        contracts=r.contracts, max_risk_usd=r.max_risk_usd,
                        overflow=r.overflow, detail=r.detail)

        auto = bool(self._auto_execute_fn())
        result = EntryRoundResult(session_date=session_date, primary=primary,
                                  overflow=overflow, auto_execute=auto)
        if not auto:
            self._audit("mace_entry_halted", reason="auto_execute=false",
                        entered=sum(1 for r in primary + overflow if r.entered))
            return result

        # Place each ENTER in order, RE-VALIDATING against post-placement state: the
        # capacity/reserve/duplicate gates depend on `rungs`, so reload it before each
        # placement (cheap: reuse the round's chains/IVR via dataclasses.replace,
        # refresh only rungs). Without this, a rung placed earlier this round is
        # invisible to the next placement's gates (the stale pre-placement snapshot).
        for res in [r for r in primary + overflow if r.entered]:
            try:
                recheck = st.evaluate_entry(
                    res.symbol, self.cfg,
                    replace(ctx, rungs=self.store.load_all()),
                    is_overflow=res.overflow)
                if not recheck.entered:
                    self._audit("mace_entry_superseded", symbol=res.symbol,
                                skip_reason=recheck.skip_reason, detail=res.detail)
                    continue
                out = await self.executor.run_entry(recheck, session_date)
                result.outcomes.append(out)
            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                self._audit("mace_entry_exception", symbol=res.symbol, error=str(exc))
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
        # A rung already CLOSING (a prior exit exhausted) keeps being driven toward close.
        if rung.status == "closing":
            return await self.executor.close_rung(rung, rung.exit_reason or "manual")

        mark = await self.executor.mark(rung.spec)
        if rung.symbol not in spot_cache:
            spot_cache[rung.symbol] = await self._spot(rung.symbol)
        spot = spot_cache[rung.symbol]

        exdiv_within = False
        if sym_cfg.exdiv_guard and self._exdiv is not None:
            try:
                exdiv_within = self._exdiv.within_window(
                    rung.symbol, now, self.cfg.management.exdiv_guard_sessions)
            except Exception as exc:  # noqa: BLE001
                self._audit("mace_exdiv_error", symbol=rung.symbol, error=str(exc))

        decision = st.evaluate_management(rung, mark, spot, now, self.cfg, sym_cfg,
                                          exdiv_within=exdiv_within)
        if not decision.should_exit:
            return None
        self._audit("mace_manage_exit", rung_id=rung.rung_id,
                    reason=decision.exit_reason, detail=decision.detail)
        return await self.executor.close_rung(rung, decision.exit_reason)

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
        self.store.conn.execute(
            "INSERT OR REPLACE INTO mace_equity_snapshot "
            "(snap_date, equity, cash, market_value, ts) VALUES (?,?,?,?,?)",
            (sd.isoformat(), snap.equity, snap.cash, snap.market_value, ts))
        self._audit("mace_equity_snapshot", snap_date=sd.isoformat(), equity=snap.equity)
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
