"""MACE strategy — PURE decision logic (plan § Behavior specifications).

No I/O: no broker, no DB, no yaml re-reads, no clock reads. Everything is a
function of injected neutral inputs (MaceConfig, domain types, ChainView,
IvrReading, RungState, economic_event dicts). The manager/execution layer
(Phase 3) fetches data, calls these functions, and performs the side effects.

Contents:
  - Entry pipeline (10 filters, first-failure-wins skip reason)
  - Condor build (expiry / delta strikes / UNIVERSAL wing-listing / FXI
    fallback width / risk band) + sizing + reserve
  - Overflow routing (T6 as ruled 2026-08-09) — inert at launch (universe=[SPY])
  - Management precedence (stop > time > exdiv)
  - Breaker math (alert-only; enforcement branches exist, ship 'off')

Marketability is NOT decided here (that is the execution ladder). This module
only decides WHICH condor, WHAT size, and PASS/SKIP.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Mapping, Sequence

from trading_corp.mace.config import MaceConfig, SymbolConfig
from trading_corp.mace.domain import (
    EXIT_EXDIV, EXIT_STOP, EXIT_TIME,
    IVR_OK, IVR_STALE, IVR_UNAVAILABLE,
    SKIP_BLACKOUT, SKIP_BUDGET, SKIP_CAPACITY, SKIP_COOLDOWN, SKIP_CREDIT_FLOOR,
    SKIP_IVR, SKIP_NO_DELTA_STRIKE, SKIP_NO_EQUITY_SNAPSHOT, SKIP_NO_EXPIRY,
    SKIP_NO_WING, SKIP_RESERVE, SKIP_RISK_BAND, SKIP_RISK_REJECT,
    SKIP_WEEKLY_BUDGET,
    BreakerState, CondorSpec, EvalResult, OptionQuote, RungState, iso_week,
)

# Rung statuses that occupy a capacity slot (submitting/open/closing are live).
_LIVE_STATUSES = ("submitting", "open", "closing")


# ── neutral chain input ──────────────────────────────────────────────────

def _k(strike: float) -> float:
    return round(float(strike), 4)


@dataclass(frozen=True)
class ChainView:
    """Neutral per-symbol option-chain snapshot at eval time. The broker fetches
    it; strategy never calls the broker. `quotes` is keyed by
    (expiry, opt_type, rounded_strike)."""

    symbol: str
    spot: float | None
    expiries: tuple[date, ...]
    quotes: Mapping[tuple[date, str, float], OptionQuote]

    def listed(self, expiry: date, opt_type: str, strike: float) -> bool:
        return (expiry, opt_type, _k(strike)) in self.quotes

    def get(self, expiry: date, opt_type: str, strike: float) -> OptionQuote | None:
        return self.quotes.get((expiry, opt_type, _k(strike)))

    def strikes(self, expiry: date, opt_type: str) -> list[float]:
        return sorted(k for (e, t, k) in self.quotes if e == expiry and t == opt_type)


# ── shared entry context ─────────────────────────────────────────────────

@dataclass(frozen=True)
class EntryContext:
    """Everything the entry pipeline needs, injected. `rungs` is ALL rungs
    (every symbol/status) — per-symbol aggregates are derived here (pure).
    `events` are economic_event dicts (event_type/symbol_scope/event_date)."""

    session_date: date
    equity: float | None
    rungs: Sequence[RungState]
    events: Sequence[Mapping]
    ivr: Mapping[str, "object"]                 # symbol -> IvrReading
    chains: Mapping[str, ChainView]
    next_session_date: date | None = None
    risk_gate: Callable[[str, CondorSpec, int], bool] | None = None

    def next_session(self) -> date:
        return self.next_session_date or next_session(self.session_date)


# ── date helpers (business-day, holidays NOT modeled — safe over-count) ───

def next_session(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def business_sessions_between(start: date, end: date) -> int:
    """Mon-Fri days strictly after `start` up to and including `end`. 0 if
    end <= start. Mirrors data.ex_dividend_calendar semantics."""
    if end <= start:
        return 0
    n, cur = 0, start + timedelta(days=1)
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def _exit_date(rung: RungState):
    from trading_corp.utils.time import to_et
    et = to_et(rung.exit_ts)
    return et.date() if et else None


# ── per-symbol rung aggregates (derived, no marker tables) ───────────────

def open_rung_count(rungs: Sequence[RungState], symbol: str) -> int:
    return sum(1 for r in rungs if r.symbol == symbol and r.status in _LIVE_STATUSES)


def entries_this_week(rungs: Sequence[RungState], symbol: str, session_date: date) -> int:
    wk = iso_week(session_date)
    return sum(1 for r in rungs if r.symbol == symbol and r.entry_iso_week == wk)


def closures_before_today_this_week(
    rungs: Sequence[RungState], symbol: str, session_date: date,
) -> int:
    """Rungs for `symbol` that CLOSED earlier this ISO week (exit date < today).
    Refill: a close frees weekly budget from the NEXT session."""
    wk = iso_week(session_date)
    n = 0
    for r in rungs:
        if r.symbol != symbol or r.status != "closed":
            continue
        ed = _exit_date(r)
        if ed is not None and ed < session_date and iso_week(ed) == wk:
            n += 1
    return n


def sessions_since_last_stop(
    rungs: Sequence[RungState], symbol: str, session_date: date,
) -> int | None:
    """Business-day age of the most recent STOP exit for `symbol`, or None."""
    dates = [
        _exit_date(r) for r in rungs
        if r.symbol == symbol and r.exit_reason == EXIT_STOP
    ]
    dates = [d for d in dates if d is not None and d <= session_date]
    if not dates:
        return None
    return business_sessions_between(max(dates), session_date)


def sum_open_max_risk(rungs: Sequence[RungState]) -> float:
    return sum(float(r.max_risk_usd or 0.0)
               for r in rungs if r.status in _LIVE_STATUSES)


def day_realized(rungs: Sequence[RungState], session_date: date) -> float:
    """Realized P&L from rungs that CLOSED today (derived; no marker table)."""
    return sum(float(r.realized_pnl or 0.0) for r in rungs
               if r.status == "closed" and _exit_date(r) == session_date)


def week_realized(rungs: Sequence[RungState], session_date: date) -> float:
    """Realized P&L from rungs that CLOSED this ISO week (derived)."""
    wk = iso_week(session_date)
    total = 0.0
    for r in rungs:
        if r.status != "closed":
            continue
        ed = _exit_date(r)
        if ed is not None and iso_week(ed) == wk:
            total += float(r.realized_pnl or 0.0)
    return total


def is_blackout(
    symbol: str, symbol_cfg: SymbolConfig, events: Sequence[Mapping],
    session_date: date, next_session_date: date,
) -> bool:
    """True iff any economic_event with a type in the symbol's blackout list,
    scope in {ALL, symbol}, falls on today OR the next trading session."""
    if not symbol_cfg.blackout_event_types:
        return False
    window = {session_date.isoformat(), next_session_date.isoformat()}
    types = set(symbol_cfg.blackout_event_types)
    for e in events:
        if str(e.get("event_type", "")).upper() not in types:
            continue
        scope = str(e.get("symbol_scope", "")).upper()
        if scope not in ("ALL", symbol.upper()):
            continue
        if str(e.get("event_date", "")) in window:
            return True
    return False


# ── condor build ─────────────────────────────────────────────────────────

def choose_expiry(chain: ChainView, dte_min: int, dte_max: int, session_date: date):
    """Highest DTE within [dte_min, dte_max] (prefer more time). None if empty."""
    best = None
    for e in chain.expiries:
        dte = (e - session_date).days
        if dte_min <= dte <= dte_max:
            if best is None or dte > (best - session_date).days:
                best = e
    return best


def select_short_strike(chain: ChainView, expiry: date, opt_type: str,
                        target: float, band: tuple[float, float]):
    """Listed strike whose |delta| is nearest `target` within `band`. Returns
    (strike, OptionQuote) or None. Requires a delta on the quote."""
    lo, hi = band
    best = None
    best_dist = None
    for k in chain.strikes(expiry, opt_type):
        q = chain.get(expiry, opt_type, k)
        if q is None or q.delta is None:
            continue
        ad = abs(float(q.delta))
        if ad < lo or ad > hi:
            continue
        dist = abs(ad - target)
        if best_dist is None or dist < best_dist:
            best, best_dist = (k, q), dist
    return best


@dataclass(frozen=True)
class BuildResult:
    spec: CondorSpec | None = None
    credit_mid: float | None = None
    width: float | None = None
    skip_reason: str | None = None


def build_condor(symbol: str, symbol_cfg: SymbolConfig, chain: ChainView,
                 cfg: MaceConfig, session_date: date) -> BuildResult:
    """Build a priced iron condor per the entry filter-6 spec. Returns a
    BuildResult with a spec+credit_mid+width or a skip_reason
    (no_expiry|no_delta_strike|no_wing|risk_band)."""
    e = cfg.entry
    expiry = choose_expiry(chain, e.dte_min, e.dte_max, session_date)
    if expiry is None:
        return BuildResult(skip_reason=SKIP_NO_EXPIRY)

    sp = select_short_strike(chain, expiry, "put", e.short_delta_target, e.short_delta_band)
    sc = select_short_strike(chain, expiry, "call", e.short_delta_target, e.short_delta_band)
    if sp is None or sc is None:
        return BuildResult(skip_reason=SKIP_NO_DELTA_STRIKE)
    (short_put, sp_q), (short_call, sc_q) = sp, sc
    if sp_q.mid is None or sc_q.mid is None:      # short selected but unpriceable
        return BuildResult(skip_reason=SKIP_NO_DELTA_STRIKE)

    # UNIVERSAL wing-listing check (Board 2026-08-09 off the $5-grid finding);
    # FXI additionally retries at fallback_width_dollars.
    widths = [symbol_cfg.width_dollars]
    if symbol_cfg.fallback_width_dollars:
        widths.append(symbol_cfg.fallback_width_dollars)

    for width in widths:
        long_put = short_put - width
        long_call = short_call + width
        lp_q = chain.get(expiry, "put", long_put)
        lc_q = chain.get(expiry, "call", long_call)
        if lp_q is None or lc_q is None:
            continue                              # wing unlisted — try fallback
        if lp_q.mid is None or lc_q.mid is None:
            continue                              # wing unpriceable — try fallback
        credit_mid = (sp_q.mid - lp_q.mid) + (sc_q.mid - lc_q.mid)
        if e.enforce_risk_band:
            max_risk = (width - credit_mid) * 100.0
            # Width-scaled band (Board ruling risk-band-width-scaling 2026-08-09):
            # min = 50 * width (w3->150, w2->100, w1->50), max = 250 absolute.
            lo = e.risk_band_min_per_width_usd * width
            hi = e.risk_band_max_usd
            if not (lo <= max_risk <= hi):
                return BuildResult(skip_reason=SKIP_RISK_BAND)
        spec = CondorSpec(symbol=symbol, expiry=expiry, short_put=short_put,
                          long_put=long_put, short_call=short_call,
                          long_call=long_call, width_dollars=width)
        return BuildResult(spec=spec, credit_mid=credit_mid, width=width)

    return BuildResult(skip_reason=SKIP_NO_WING)


# ── sizing + reserve ─────────────────────────────────────────────────────

def size_contracts(equity: float, width: float, credit_mid: float,
                   rung_risk_pct: float, max_contracts: int) -> int:
    per_contract_risk = (width - credit_mid) * 100.0
    if per_contract_risk <= 0:
        return 0
    raw = math.floor(rung_risk_pct * equity / per_contract_risk)
    return max(0, min(raw, max_contracts))


def max_risk_usd(width: float, credit_mid: float, contracts: int) -> float:
    return (width - credit_mid) * 100.0 * contracts


# ── entry pipeline ───────────────────────────────────────────────────────

def _skip(symbol: str, reason: str, *, ivr_status: str = IVR_OK,
          ivr_value=None, overflow=False, detail="") -> EvalResult:
    return EvalResult(symbol=symbol, entered=False, skip_reason=reason,
                      ivr_status=ivr_status, ivr_value=ivr_value,
                      overflow=overflow, detail=detail)


def evaluate_entry(symbol: str, cfg: MaceConfig, ctx: EntryContext,
                   *, is_overflow: bool = False) -> EvalResult:
    """Run the 10-filter entry pipeline for one symbol. First failing filter is
    the recorded skip reason. `is_overflow` skips ONLY the weekly-budget filter
    (T6). Returns an EvalResult (entered=True carries spec/credit/contracts)."""
    sc = cfg.symbols.get(symbol)
    if sc is None:
        return _skip(symbol, SKIP_CAPACITY, detail="no symbol config")
    e = cfg.entry

    # 0. equity snapshot must exist (sizing basis)
    if ctx.equity is None or ctx.equity <= 0:
        return _skip(symbol, SKIP_NO_EQUITY_SNAPSHOT)

    # 1. capacity
    if open_rung_count(ctx.rungs, symbol) >= e.max_rungs_per_symbol:
        return _skip(symbol, SKIP_CAPACITY, overflow=is_overflow)

    # 2. weekly budget (+ refill) — EXEMPT for overflow entries (T6)
    if not is_overflow:
        budget = e.weekly_new_rungs_per_symbol + closures_before_today_this_week(
            ctx.rungs, symbol, ctx.session_date)
        if entries_this_week(ctx.rungs, symbol, ctx.session_date) >= budget:
            return _skip(symbol, SKIP_WEEKLY_BUDGET)

    # 3. cooldown (no stop-loss exit within the last stop_cooldown_sessions)
    since = sessions_since_last_stop(ctx.rungs, symbol, ctx.session_date)
    if since is not None and since < e.stop_cooldown_sessions:
        return _skip(symbol, SKIP_COOLDOWN, overflow=is_overflow,
                     detail=f"{since} sessions since last stop")

    # 4. blackout (today OR next trading session)
    if is_blackout(symbol, sc, ctx.events, ctx.session_date, ctx.next_session()):
        return _skip(symbol, SKIP_BLACKOUT, overflow=is_overflow)

    # 5. IVR — stale/unavailable SKIP THE FILTER (annotate), do not reject
    r = ctx.ivr.get(symbol)
    ivr_status = getattr(r, "status", IVR_UNAVAILABLE) if r is not None else IVR_UNAVAILABLE
    ivr_value = getattr(r, "ivr", None) if r is not None else None
    if ivr_status == IVR_OK:
        if ivr_value is None or ivr_value < e.ivr_floor:
            return _skip(symbol, SKIP_IVR, ivr_status=ivr_status,
                         ivr_value=ivr_value, overflow=is_overflow)
    # IVR_STALE / IVR_UNAVAILABLE: continue (credit floor + blackout still gate)

    # 6. build (expiry / delta strikes / universal wing / FXI fallback / risk band)
    chain = ctx.chains.get(symbol)
    if chain is None:
        return _skip(symbol, SKIP_NO_EXPIRY, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow,
                     detail="no chain")
    b = build_condor(symbol, sc, chain, cfg, ctx.session_date)
    if b.skip_reason is not None:
        return _skip(symbol, b.skip_reason, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow)

    # 7. credit floor
    if b.credit_mid < e.credit_floor_pct_of_width * b.width:
        return _skip(symbol, SKIP_CREDIT_FLOOR, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow,
                     detail=f"credit {b.credit_mid:.2f} < floor "
                            f"{e.credit_floor_pct_of_width * b.width:.2f}")

    # 8. size
    contracts = size_contracts(ctx.equity, b.width, b.credit_mid,
                               cfg.sizing.rung_risk_pct, cfg.max_contracts)
    if contracts <= 0:
        return _skip(symbol, SKIP_BUDGET, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow)
    mr = max_risk_usd(b.width, b.credit_mid, contracts)

    # 9. reserve
    if sum_open_max_risk(ctx.rungs) + mr > cfg.sizing.deployment_target_pct * ctx.equity:
        return _skip(symbol, SKIP_RESERVE, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow)

    # 10. risk gate (per-leg; any reject aborts the whole condor)
    if ctx.risk_gate is not None and not ctx.risk_gate(symbol, b.spec, contracts):
        return _skip(symbol, SKIP_RISK_REJECT, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow)

    return EvalResult(symbol=symbol, entered=True, skip_reason=None, spec=b.spec,
                      credit_mid=b.credit_mid, contracts=contracts,
                      max_risk_usd=mr, ivr_status=ivr_status, ivr_value=ivr_value,
                      overflow=is_overflow,
                      detail=f"{b.spec.strikes_label()} x{contracts} credit {b.credit_mid:.2f}")


# ── overflow routing (T6 as ruled 2026-08-09) ────────────────────────────

# Skip reasons that FORFEIT capital a primary would have deployed (they route
# to overflow). Structural/no-data skips do NOT forfeit deployable capital.
_FORFEITING_SKIPS = frozenset({
    SKIP_WEEKLY_BUDGET, SKIP_COOLDOWN, SKIP_BLACKOUT, SKIP_IVR,
    SKIP_NO_DELTA_STRIKE, SKIP_NO_WING, SKIP_RISK_BAND, SKIP_CREDIT_FLOOR,
})


def route_overflow(primary_results: Sequence[EvalResult], cfg: MaceConfig,
                   ctx: EntryContext) -> list[EvalResult]:
    """T6: capital forfeited by a failed primary filter routes to IBIT first
    (enabled + overflow_only, bounded by ibit_overflow_cap rungs), then the
    highest-IVR ENABLED primary that did NOT already enter this session.
    Overflow entries skip the weekly-budget filter; all OTHER filters apply;
    capped at overflow_max_per_symbol_session per receiver.

    Single pass: each receiver is attempted at most once (correct for the
    shipped cap of 1/symbol/session). Multi-placement reserve/capacity
    accounting across a session is the manager's job (re-evaluate with updated
    rungs after each placement). Inert at launch (universe=[SPY], IBIT
    disabled) — no receivers, so forfeits go nowhere."""
    forfeits = sum(1 for r in primary_results
                   if not r.entered and r.skip_reason in _FORFEITING_SKIPS)
    if forfeits <= 0:
        return []

    def ivr_of(sym: str) -> float:
        rr = ctx.ivr.get(sym)
        v = getattr(rr, "ivr", None) if rr is not None else None
        return v if v is not None else -1.0

    # A symbol that FORFEITED must not receive its own capital back — that would
    # bypass the filter it just failed (weekly-budget above all, which overflow
    # exempts). Entered primaries with spare capacity MAY receive (plan: "highest
    # -IVR primary with capacity").
    forfeiting = {r.symbol for r in primary_results
                  if not r.entered and r.skip_reason in _FORFEITING_SKIPS}
    ibit = [s for s, c in cfg.symbols.items() if c.enabled and c.overflow_only]
    primaries = sorted(
        (s for s, c in cfg.symbols.items()
         if c.enabled and not c.overflow_only and s not in forfeiting),
        key=ivr_of, reverse=True)
    ordered = ibit + primaries

    results: list[EvalResult] = []
    remaining = forfeits
    for sym in ordered:
        if remaining <= 0:
            break
        if (cfg.symbols[sym].overflow_only
                and open_rung_count(ctx.rungs, sym) >= cfg.entry.ibit_overflow_cap):
            continue
        res = evaluate_entry(sym, cfg, ctx, is_overflow=True)
        if res.entered:
            results.append(res)
            remaining -= 1
    return results


# ── management precedence ────────────────────────────────────────────────

@dataclass(frozen=True)
class ManageDecision:
    rung_id: str
    exit_reason: str | None      # EXIT_STOP | EXIT_TIME | EXIT_EXDIV | None (hold)
    detail: str = ""

    @property
    def should_exit(self) -> bool:
        return self.exit_reason is not None


def evaluate_management(rung: RungState, mark: float | None, spot: float | None,
                        now_et: datetime, cfg: MaceConfig, symbol_cfg: SymbolConfig,
                        *, exdiv_within: bool) -> ManageDecision:
    """Precedence: stop > time > exdiv. `mark` = cost-to-close at mid (net debit
    to exit). The 09:35 tick IS the gap rule (no separate branch). `exdiv_within`
    is the calendar side (caller uses mace.exdiv.MaceExDiv)."""
    m = cfg.management
    # stop: mark >= stop_multiple x credit received
    if mark is not None and rung.credit_actual is not None:
        if mark >= m.stop_multiple * rung.credit_actual:
            return ManageDecision(rung.rung_id, EXIT_STOP,
                                  f"mark {mark:.2f} >= {m.stop_multiple}x credit "
                                  f"{rung.credit_actual:.2f}")
    # time: DTE <= time_exit_dte AND now >= time_exit_at_et
    dte = (rung.expiry - now_et.date()).days
    if dte <= m.time_exit_dte and now_et.time() >= dtime.fromisoformat(m.time_exit_at_et):
        return ManageDecision(rung.rung_id, EXIT_TIME, f"DTE {dte} at/after {m.time_exit_at_et}")
    # exdiv: guard on AND short call ITM (spot > short-call strike) AND ex-div near
    if (symbol_cfg.exdiv_guard and exdiv_within and spot is not None
            and spot > rung.spec.short_call):
        return ManageDecision(rung.rung_id, EXIT_EXDIV,
                              f"short call ITM (spot {spot} > {rung.spec.short_call}) + ex-div near")
    return ManageDecision(rung.rung_id, None, "hold")


# ── breakers (alert-only) ────────────────────────────────────────────────

def evaluate_breakers(day_realized: float, week_realized: float,
                      equity: float | None, hwm: float | None,
                      cfg: MaceConfig) -> BreakerState:
    """Signed realized P&L in (day_realized, week_realized) — negative = loss.
    A loss >= day/week pct of E fires; equity below hwm_soft/hwm_hard x HWM fires.
    Alert-only: BreakerState is advisory; enforcement is a separate switch."""
    b = cfg.breakers
    e_ok = equity is not None and equity > 0
    h_ok = hwm is not None and hwm > 0
    day_hit = bool(e_ok and day_realized <= -b.day_loss_pct * equity)
    week_hit = bool(e_ok and week_realized <= -b.week_loss_pct * equity)
    soft_hit = bool(h_ok and equity < b.hwm_soft_pct * hwm)
    hard_hit = bool(h_ok and equity < b.hwm_hard_pct * hwm)
    return BreakerState(
        day_loss_hit=day_hit, week_loss_hit=week_hit,
        hwm_soft_hit=soft_hit, hwm_hard_hit=hard_hit,
        day_realized=day_realized, week_realized=week_realized,
        equity=equity, hwm=hwm,
    )
