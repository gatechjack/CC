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
    EXIT_EXDIV, EXIT_PT, EXIT_STOP, EXIT_TIME,
    IVR_OK, IVR_STALE, IVR_UNAVAILABLE,
    SKIP_BLACKOUT, SKIP_BUDGET, SKIP_CAPACITY, SKIP_COOLDOWN, SKIP_CREDIT_FLOOR,
    SKIP_IVR, SKIP_NO_DELTA_STRIKE, SKIP_NO_EQUITY_SNAPSHOT, SKIP_NO_EXPIRY,
    SKIP_NO_WING, SKIP_RESERVE, SKIP_RISK_BAND, SKIP_RISK_REJECT,
    SKIP_STRIKE_COLLISION, SKIP_WEEKLY_BUDGET,
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


def net_option_positions(rungs: Sequence[RungState], symbol: str) -> dict:
    """(expiry, opt_type, rounded_strike) -> NET signed contracts (long +, short -)
    across this symbol's LIVE rungs. The collision guard reads this: opening a leg
    OPPOSITE an existing net (buy where net<0, sell where net>0) is what Robinhood
    atomically rejects (buy-to-open you already hold short / sell-to-open you already
    hold long). Same-direction or flat strikes are fine (they just add contracts)."""
    net: dict = {}
    for r in rungs:
        if r.symbol != symbol or r.status not in _LIVE_STATUSES or r.spec is None:
            continue
        c = r.contracts or 0
        s = r.spec
        for opt_type, strike, sign in (
            ("put", s.short_put, -1), ("put", s.long_put, +1),
            ("call", s.short_call, -1), ("call", s.long_call, +1),
        ):
            key = (s.expiry, opt_type, _k(strike))
            net[key] = net.get(key, 0) + sign * c
    return net


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
    detail: str = ""


# ── skip observability (diagnostic only — NEVER feeds selection/decisions) ──
# These annotate the detail string on a build skip so audit_event alone tells
# why (spot, expiry, nearest-delta candidate, wing strikes + reject reason).
# They read the chain; they do not change what build_condor decides.

def _fmt_spot(spot) -> str:
    return f"{spot:.2f}" if spot is not None else "none"


def _nearest_delta_strike(chain: "ChainView", expiry, opt_type: str, target: float):
    """(strike, delta) whose |delta| is closest to `target` among listed strikes
    carrying a delta; None if the side is empty. Diagnostic ONLY."""
    best = None
    best_dist = None
    for k in chain.strikes(expiry, opt_type):
        q = chain.get(expiry, opt_type, k)
        if q is None or q.delta is None:
            continue
        dist = abs(abs(float(q.delta)) - target)
        if best_dist is None or dist < best_dist:
            best, best_dist = (k, float(q.delta)), dist
    return best


def _cand(c) -> str:
    return f"{c[0]:g}@{c[1]:.2f}" if c else "none"


def _wing_leg_diag(chain: "ChainView", expiry, opt_type: str, strike: float,
                   band: float, spot) -> str:
    """Why one wing leg is unusable: unpriceable (listed, no mid), out-of-band
    (absent AND beyond +/-band of spot -> the chain() fetch clipped it), or
    unlisted (absent, inside band)."""
    tag = ("P" if opt_type == "put" else "C") + f"{strike:g}"
    q = chain.get(expiry, opt_type, strike)
    if q is not None:
        return f"{tag}=unpriceable" if q.mid is None else f"{tag}=ok"
    if spot and band and abs(float(strike) / float(spot) - 1.0) > band:
        return f"{tag}=out-of-band"
    return f"{tag}=unlisted"


def _wing_diag(chain: "ChainView", expiry, short_put: float, short_call: float,
               widths, band: float, spot) -> str:
    return " ".join(
        f"w{w:g}:" + _wing_leg_diag(chain, expiry, "put", short_put - w, band, spot)
        + "," + _wing_leg_diag(chain, expiry, "call", short_call + w, band, spot)
        for w in widths)


def _short_candidates(chain: ChainView, expiry: date, opt_type: str,
                      target: float, band: tuple[float, float]):
    """Listed, PRICEABLE shorts with |delta| in `band`, ordered nearest-to-target
    first. The head == what select_short_strike returns. The tail is the SHIFT
    reservoir the collision guard walks when the nearest short's condor collides."""
    lo, hi = band
    out = []
    for k in chain.strikes(expiry, opt_type):
        q = chain.get(expiry, opt_type, k)
        if q is None or q.delta is None or q.mid is None:
            continue
        ad = abs(float(q.delta))
        if ad < lo or ad > hi:
            continue
        out.append((abs(ad - target), k, q))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(k, q) for _, k, q in out]


def _snap_wing(chain: ChainView, expiry: date, opt_type: str,
               short: float, min_width: float):
    """Nearest LISTED wing at least `min_width` out from `short` (down for puts,
    up for calls). Fine grids return short-/+min_width exactly; a coarse grid (GDX
    OTM calls are $5-spaced) snaps OUT to the next listed strike, so the ACTUAL
    width can exceed min_width. None if no listed strike is far enough out (or the
    fetch window clipped it). Returns the wing strike."""
    if opt_type == "put":
        tgt = short - min_width
        cands = [k for k in chain.strikes(expiry, "put") if k <= tgt]
        return max(cands) if cands else None       # closest listed at/below target
    tgt = short + min_width
    cands = [k for k in chain.strikes(expiry, "call") if k >= tgt]
    return min(cands) if cands else None            # closest listed at/above target


def _snap_condor_wings(chain: ChainView, expiry: date, short_put: float,
                       short_call: float, min_width: float):
    """Snap BOTH wings >= min_width out, then WIDEN the narrower side to the
    max-loss (eff) width so the condor is ~symmetric. Rationale: the credit floor
    and risk band scale with the max-side width, but iron-condor credit is driven
    by the (fixed ~20-delta) SHORT, not the width — so a $5 grid-forced call side
    with a $2 put side collects too little to clear 0.30x5. Widening the put side
    to $5 too (its cheaper, further-OTM long) restores the credit/width ratio.
    Returns (long_put, long_call, eff_width) or None if either side has no listed
    wing far enough out (fine grids are unchanged: short-/+min_width exactly)."""
    lp = _snap_wing(chain, expiry, "put", short_put, min_width)
    lc = _snap_wing(chain, expiry, "call", short_call, min_width)
    if lp is None or lc is None:
        return None
    eff = max(short_put - lp, lc - short_call)
    if short_put - lp < eff:
        lp2 = _snap_wing(chain, expiry, "put", short_put, eff)
        if lp2 is not None:
            lp = lp2
    if lc - short_call < eff:
        lc2 = _snap_wing(chain, expiry, "call", short_call, eff)
        if lc2 is not None:
            lc = lc2
    return (lp, lc, max(short_put - lp, lc - short_call))


def _leg_conflicts(net: dict | None, expiry: date, opt_type: str,
                   strike: float, side: str) -> bool:
    """A candidate leg conflicts if OPENING it opposes an existing net position at
    the same contract — RH rejects buy-to-open where net<0 (held short) or
    sell-to-open where net>0 (held long)."""
    if not net:
        return False
    n = net.get((expiry, opt_type, _k(strike)), 0)
    return (side == "buy" and n < 0) or (side == "sell" and n > 0)


def _separation_conflict(net: dict | None, expiry: date, short_put: float,
                         short_call: float, min_sep: float) -> bool:
    """Optional (min_sep>0) diversification guard: True if the candidate's short
    put/call lands within `min_sep` dollars of ANY existing same-expiry SHORT
    (net<0) on the same side. Default OFF (min_sep=0) -> never fires."""
    if not net or min_sep <= 0:
        return False
    for (e_exp, e_type, e_strike), n in net.items():
        if e_exp != expiry or n >= 0:
            continue
        cand = short_put if e_type == "put" else short_call
        if abs(float(cand) - float(e_strike)) < min_sep:
            return True
    return False


def build_condor(symbol: str, symbol_cfg: SymbolConfig, chain: ChainView,
                 cfg: MaceConfig, session_date: date,
                 net_positions: dict | None = None) -> BuildResult:
    """Build a priced iron condor per the entry filter-6 spec. Returns a
    BuildResult with a spec+credit_mid+width or a skip_reason
    (no_expiry|no_delta_strike|no_wing|risk_band|credit_floor|strike_collision).

    `net_positions` (from evaluate_entry via net_option_positions) drives the
    COLLISION guard (P1.1): the short is SHIFTED within the delta band so no leg
    opens opposite an existing same-expiry position; strike_collision only when no
    in-band shift clears. Wings SNAP to the nearest listed strike >= width out
    (P1.3), so GDX/XLE build on their real $5-spaced OTM-call grid; the actual
    (max-side) width drives the risk band, credit floor, and sizing."""
    e = cfg.entry
    expiry = choose_expiry(chain, e.dte_min, e.dte_max, session_date)
    if expiry is None:
        return BuildResult(skip_reason=SKIP_NO_EXPIRY,
                           detail=f"spot={_fmt_spot(chain.spot)} "
                                  f"no-expiry-in-DTE[{e.dte_min},{e.dte_max}] "
                                  f"listed={len(chain.expiries)}")

    put_cands = _short_candidates(chain, expiry, "put", e.short_delta_target, e.short_delta_band)
    call_cands = _short_candidates(chain, expiry, "call", e.short_delta_target, e.short_delta_band)
    if not put_cands or not call_cands:
        lo, hi = e.short_delta_band
        return BuildResult(
            skip_reason=SKIP_NO_DELTA_STRIKE,
            detail=f"spot={_fmt_spot(chain.spot)} exp={expiry} "
                   f"delta_band=[{lo:g},{hi:g}] "
                   f"put_near={_cand(_nearest_delta_strike(chain, expiry, 'put', e.short_delta_target))} "
                   f"call_near={_cand(_nearest_delta_strike(chain, expiry, 'call', e.short_delta_target))}")

    widths = [symbol_cfg.width_dollars]
    if symbol_cfg.fallback_width_dollars:
        widths.append(symbol_cfg.fallback_width_dollars)
    min_sep = getattr(e, "min_strike_separation_usd", 0.0)

    def _price_pair(short_put, sp_q, short_call, sc_q):
        """Price ONE short pair across the width list with snap-to-grid wings.
        Returns ('ok', spec, credit, width) for a clean condor; ('collide', ...)
        for a valid condor that would be atomically rejected (opposite an existing
        same-expiry leg, or within min_sep); else ('skip', reason, detail) with the
        furthest-progress reason for THIS pair. No cross-short shifting here — that
        is the caller's job, and ONLY when a pair collides."""
        p_wing = p_band = False
        p_cf_detail = ""
        p_cf_gap = None
        for width in widths:
            snapped = _snap_condor_wings(chain, expiry, short_put, short_call, width)
            if snapped is None:
                continue                              # no listed wing far enough out
            long_put, long_call, eff_width = snapped
            lp_q = chain.get(expiry, "put", long_put)
            lc_q = chain.get(expiry, "call", long_call)
            if lp_q is None or lc_q is None or lp_q.mid is None or lc_q.mid is None:
                continue                              # wing unpriceable
            p_wing = True
            credit_mid = (sp_q.mid - lp_q.mid) + (sc_q.mid - lc_q.mid)
            if e.enforce_risk_band:
                max_risk = (eff_width - credit_mid) * 100.0
                if not (e.risk_band_min_per_width_usd * eff_width <= max_risk <= e.risk_band_max_usd):
                    continue                          # out of risk band
            p_band = True
            floor = e.credit_floor_pct_of_width * eff_width
            if credit_mid < floor:
                gap = credit_mid - floor
                if p_cf_gap is None or gap > p_cf_gap:
                    p_cf_gap, p_cf_detail = gap, f"credit {credit_mid:.2f} < floor {floor:.2f}"
                continue                              # below credit floor
            spec = CondorSpec(symbol=symbol, expiry=expiry, short_put=short_put,
                              long_put=long_put, short_call=short_call,
                              long_call=long_call, width_dollars=eff_width)
            legs = (("put", short_put, "sell"), ("put", long_put, "buy"),
                    ("call", short_call, "sell"), ("call", long_call, "buy"))
            if any(_leg_conflicts(net_positions, expiry, t, k, s) for t, k, s in legs) \
                    or _separation_conflict(net_positions, expiry, short_put, short_call, min_sep):
                return ("collide", spec, credit_mid, eff_width)  # valid but would reject
            return ("ok", spec, credit_mid, eff_width)
        # no valid condor at any width for this pair
        _diag = (f"spot={_fmt_spot(chain.spot)} exp={expiry} SP={short_put:g} "
                 f"SC={short_call:g} | "
                 + _wing_diag(chain, expiry, short_put, short_call, widths,
                              e.strike_band_pct, chain.spot))
        if p_band:
            return ("skip", SKIP_CREDIT_FLOOR, p_cf_detail, None)
        if p_wing:
            return ("skip", SKIP_RISK_BAND, _diag + " (all widths out of risk band)", None)
        return ("skip", SKIP_NO_WING, _diag, None)

    # Try short pairs nearest-to-target first. The nearest pair decides the outcome
    # UNLESS it collides — only then do we SHIFT to alternative shorts (P1.1). This
    # keeps a plain no_wing/risk_band/credit_floor identical to the old single-short
    # behavior (snap-to-grid, not the short-shift, is what unblocks GDX/XLE wings).
    saw_collision = False
    for pi, (short_put, sp_q) in enumerate(put_cands):
        for ci, (short_call, sc_q) in enumerate(call_cands):
            kind, a, b_, c = _price_pair(short_put, sp_q, short_call, sc_q)
            if kind == "ok":
                return BuildResult(spec=a, credit_mid=b_, width=c)
            if kind == "collide":
                saw_collision = True
                continue                              # shift to the next short pair
            # kind == "skip": if NO collision has forced a shift, the nearest pair's
            # skip IS the answer (do not explore other shorts for a plain skip).
            if not saw_collision and pi == 0 and ci == 0:
                return BuildResult(skip_reason=a, detail=b_)
            # else we are shifting (a collision was seen) — keep scanning for an 'ok'.

    # Exhausted all shifts with no clean condor: a valid condor existed but every
    # one collided / violated separation.
    return BuildResult(
        skip_reason=SKIP_STRIKE_COLLISION,
        detail=f"spot={_fmt_spot(chain.spot)} exp={expiry} "
               f"every in-band condor overlaps an existing same-expiry leg "
               f"(shift exhausted across {len(put_cands)}x{len(call_cands)} shorts; "
               f"min_sep={min_sep:g})")


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

    # 6. build (expiry / delta strikes / universal wing + snap-to-grid / width
    #    fallback / risk band / credit floor / strike-collision shift — build_condor
    #    returns a spec only when a short pair clears ALL of them; b.detail carries
    #    the furthest-progress miss). net_positions drives the collision guard so a
    #    new long leg never opens opposite an existing same-expiry short (RH reject).
    chain = ctx.chains.get(symbol)
    if chain is None:
        return _skip(symbol, SKIP_NO_EXPIRY, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow,
                     detail="no chain")
    b = build_condor(symbol, sc, chain, cfg, ctx.session_date,
                     net_positions=net_option_positions(ctx.rungs, symbol))
    if b.skip_reason is not None:
        return _skip(symbol, b.skip_reason, ivr_status=ivr_status,
                     ivr_value=ivr_value, overflow=is_overflow, detail=b.detail)

    # 7. credit floor: now enforced INSIDE build_condor's width loop (filter 6)
    #    so the fallback width is tried when the primary is below floor. A
    #    returned spec has already cleared the floor at b.width (2026-08-14 fix).

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

    # A receiver must be NEITHER a symbol that ENTERED this round NOR one that
    # FORFEITED it. Excluding ENTERED is the 2026-08-12 fix: routing forfeited
    # capital onto a just-placed symbol re-enters it (the pre-placement rung
    # snapshot hides its new rung) -> a duplicate order fires (live: SPY entered,
    # GLD forfeited, router re-picked SPY, RH rejected the duplicate ref_id).
    # Excluding FORFEITED keeps a symbol from receiving its own capital back and
    # bypassing the filter it just failed. Net: overflow routes only to genuinely
    # idle receivers (IBIT-style overflow_only, or a primary that neither entered
    # nor forfeited).
    forfeiting = {r.symbol for r in primary_results
                  if not r.entered and r.skip_reason in _FORFEITING_SKIPS}
    entered = {r.symbol for r in primary_results if r.entered}
    excluded = forfeiting | entered
    ibit = [s for s, c in cfg.symbols.items() if c.enabled and c.overflow_only]
    primaries = sorted(
        (s for s, c in cfg.symbols.items()
         if c.enabled and not c.overflow_only and s not in excluded),
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
    exit_reason: str | None      # EXIT_STOP | EXIT_PT | EXIT_TIME | EXIT_EXDIV | None (hold)
    detail: str = ""

    @property
    def should_exit(self) -> bool:
        return self.exit_reason is not None


def evaluate_management(rung: RungState, mark: float | None, spot: float | None,
                        now_et: datetime, cfg: MaceConfig, symbol_cfg: SymbolConfig,
                        *, exdiv_within: bool) -> ManageDecision:
    """Precedence: stop > PT > time > exdiv. `mark` = cost-to-close at mid (net
    debit to exit). The 09:35 tick IS the gap rule (no separate branch).
    `exdiv_within` is the calendar side (caller uses mace.exdiv.MaceExDiv).

    PT is the T9 SYNTHETIC profit target (Board ruling 2026-08-10, on the T9 basis):
    there is NO resting-GTC PT order — the manage tick IS the PT. When the
    cost-to-close (`mark`) has decayed to <= pt_pct_of_credit x credit received, we
    close via the emulated-market exit ladder (reason `pt`); since mark <= the PT
    target, the ladder's natural debit is already at/under target, so the close
    books at/inside the profit target. stop (a loss) and PT (a win) are mutually
    exclusive on `mark`; PT is ordered before time/exdiv so a hit target closes
    favorably regardless of DTE, and the exit is labelled `pt` (not `time`)."""
    m = cfg.management
    # stop: mark >= stop_multiple x credit received
    if mark is not None and rung.credit_actual is not None:
        if mark >= m.stop_multiple * rung.credit_actual:
            return ManageDecision(rung.rung_id, EXIT_STOP,
                                  f"mark {mark:.2f} >= {m.stop_multiple}x credit "
                                  f"{rung.credit_actual:.2f}")
    # PT (T9 synthetic): mark <= pt_pct_of_credit x credit received -> lock the win.
    if mark is not None and rung.credit_actual is not None:
        pt_target = m.pt_pct_of_credit * rung.credit_actual
        if mark <= pt_target:
            return ManageDecision(rung.rung_id, EXIT_PT,
                                  f"mark {mark:.2f} <= {m.pt_pct_of_credit}x credit "
                                  f"{rung.credit_actual:.2f} (synthetic PT)")
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
