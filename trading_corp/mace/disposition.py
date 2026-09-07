"""MACE per-symbol terminal-disposition formatter (observability ONLY).

The entry pipeline (strategy.evaluate_entry) already decides entered/skip_reason.
This module turns an EvalResult into a single human-readable line with the
DECIDING NUMERIC, for the /mace Activity Pulse and the audit trail:

    [SPY]  SKIP capacity 25/25 open
    [IWM]  SKIP ivr 18.55 < 25
    [XLE]  SKIP credit_floor credit 0.27 < floor 0.30
    [GDX]  TAKEN 91.5-86.5-120-125 x1 credit 1.60        (order FILLED + booked)
    [IBIT] ATTEMPT 63-62-70-71 x1 credit 0.31            (decided to attempt; not placed yet)
    [IBIT] SKIP entry_standdown credit_floor_drift       (attempted, NO fill = no rung)

★ TAKEN vs ATTEMPT vs entry_standdown (fix 2026-08-25): `EvalResult.entered`
means the pure pipeline DECIDED to attempt this rung — it does NOT mean an order
filled. So the disposition for an entered symbol depends on what the placement
loop ACTUALLY did, passed in by the manager AFTER placement resolves:
  filled=True  -> a rung was booked (order filled)                -> "TAKEN …"
  filled=False -> attempted but no fill (floor-drift/cutoff/reject) -> "SKIP entry_standdown …"
  filled=None  -> not placed here (pre-placement/standby/halt)     -> "ATTEMPT …"
Previously an entered symbol rendered "TAKEN" BEFORE the executor ran, so a
stood-down entry (e.g. IBIT 8/25: credit_floor_drift, 0 fills, no rung) lied.

It is PURE and READ-ONLY: it re-derives each numeric from the SAME EntryContext
and MaceConfig the decision used, via the SAME strategy helpers. It NEVER feeds
selection/decisions — the manager already holds the EvalResult; this only
formats it. Decision logic (strategy.evaluate_entry) is byte-unchanged; this
change is purely WHAT THE LABEL SAYS and WHEN the manager emits it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from trading_corp.mace.domain import (
    SKIP_BLACKOUT,
    SKIP_BUDGET,
    SKIP_CAPACITY,
    SKIP_IVR,
    SKIP_NO_EQUITY_SNAPSHOT,
    SKIP_RESERVE,
    SKIP_RISK_REJECT,
    SKIP_WEEKLY_BUDGET,
)
from trading_corp.mace.strategy import (
    closures_before_today_this_week,
    deployment_base,
    entries_this_week,
    open_rung_count,
    reserve_committed,
    sum_open_max_risk,
)

if TYPE_CHECKING:  # avoid import cycles at runtime; these are type hints only
    from trading_corp.mace.config import MaceConfig
    from trading_corp.mace.domain import EvalResult
    from trading_corp.mace.strategy import EntryContext


def disposition_line(r: "EvalResult", cfg: "MaceConfig", ctx: "EntryContext",
                     *, filled: "bool | None" = None,
                     standdown_reason: "str | None" = None) -> str:
    """One terminal-disposition line for `r`. Pure; does not affect decisions.

    For an ENTERED result the label depends on the PLACEMENT outcome the manager
    passes AFTER the placement loop resolves (never the pre-placement intent):
      filled=True  -> "TAKEN …"                     (a rung was booked)
      filled=False -> "SKIP entry_standdown <why>"  (attempted, no fill = no rung)
      filled=None  -> "ATTEMPT …"                   (not placed here: pre-placement/
                                                     standby/halt latch)

    Reasons whose deciding numeric already rides on EvalResult.detail
    (cooldown, no_expiry, no_delta_strike, no_wing, risk_band, credit_floor,
    strike_collision) render that detail verbatim. The rest are re-derived from
    ctx/cfg with the same helpers evaluate_entry uses."""
    if r.entered:
        if r.detail:
            tail = r.detail
        else:
            c = r.credit_mid
            tail = f"x{r.contracts}" + (f" credit {c:.2f}" if c is not None else "")
        if filled is True:
            return f"TAKEN {tail}"          # order FILLED + booked -> a rung exists
        if filled is False:
            # decided to attempt but NO fill (credit_floor_drift/cutoff/reject/
            # window_budget/error/unconfirmed/partial/exhausted) -> no rung.
            return f"SKIP entry_standdown {standdown_reason or 'no_fill'}"
        # filled is None: not placed here (pre-placement / standby / halt latch).
        return f"ATTEMPT {tail}"

    reason = r.skip_reason or "skip"
    detail = (r.detail or "").strip()
    e = cfg.entry

    if reason == SKIP_CAPACITY:
        # detail is only set for the "no symbol config" edge; otherwise show count
        num = detail if detail else (
            f"{open_rung_count(ctx.rungs, r.symbol)}/{e.max_rungs_per_symbol} open")
    elif reason == SKIP_WEEKLY_BUDGET:
        budget = e.weekly_new_rungs_per_symbol + closures_before_today_this_week(
            ctx.rungs, r.symbol, ctx.session_date)
        used = entries_this_week(ctx.rungs, r.symbol, ctx.session_date)
        num = f"{used}/{budget} this wk"
    elif reason == SKIP_IVR:
        floor = f"{e.ivr_floor:g}"
        num = (f"{r.ivr_value:.2f} < {floor}" if r.ivr_value is not None
               else f"unavailable < {floor}")
    elif reason == SKIP_RESERVE:
        # Mirror the gate exactly: same base + same committed-risk model (Option A
        # available-BP counts only within-eval placements; equity counts all open).
        committed = reserve_committed(cfg, ctx)
        base = deployment_base(cfg, ctx)
        target = cfg.sizing.deployment_target_pct
        cap = target * base
        pct = (committed / base) if base else 0.0
        num = (f"committed ${committed:.0f}+new > cap ${cap:.0f} "
               f"({pct:.0%} of {target:.0%})")
    elif reason == SKIP_BUDGET:
        num = "0 contracts (sizing)"
    elif reason == SKIP_BLACKOUT:
        num = "economic-event window"
    elif reason == SKIP_RISK_REJECT:
        num = "risk gate rejected"
    elif reason == SKIP_NO_EQUITY_SNAPSHOT:
        num = "no equity snapshot"
    else:
        # detail-carrying reasons render their own numeric; empty detail -> bare
        num = detail

    return f"SKIP {reason} {num}".rstrip()


# ── exit disposition (observability ONLY; mirrors disposition_line for exits) ──
# The Activity Pulse renders `[symbol] <line>` per audit event. Entry events carry
# the rich disposition_line; exit events historically carried only a bare reason
# ("pt"). exit_disposition_line gives the EXIT events the same whole-story line, so
# the pulse reads the close at a glance. PURE + READ-ONLY: it formats values the
# executor already computed; it NEVER feeds a decision or an exit trigger.

_EXIT_REASON_WORDS = {
    "pt": "profit-target",
    "stop": "stop",
    "time": "time-exit",
    "exdiv": "ex-dividend",
    "gap": "gap-exit",
    "manual": "manual",
}


def _exit_strikes(spec) -> str:
    """`sp/lpP sc/lcC` -- the same shape the dashboard rungs / Recently-Closed use
    (mace_view._fmt_strikes), kept in sync here to avoid a web-layer import."""
    def g(v):
        return f"{float(v):g}" if v is not None else "?"
    return (f"{g(spec.short_put)}/{g(spec.long_put)}P "
            f"{g(spec.short_call)}/{g(spec.long_call)}C")


def exit_disposition_line(spec, reason: str, *, debit: "float | None" = None,
                          realized: "float | None" = None,
                          pct_of_credit: "float | None" = None,
                          phase: str = "fill") -> str:
    """One EXIT pulse line (PURE, observability ONLY; the exit analogue of
    disposition_line). Always carries strikes + expiry + the spelled-out exit
    reason. Once the close FILLS (phase='fill') it appends the debit-to-close,
    realized P&L, and % of entry credit captured. For phase 'start' (debit ladder
    opened) / 'decision' (manager chose to exit) there is no fill yet -> intent
    only. Does NOT touch decisions or exit triggers; symbol is rendered by the
    feed as the `[SYM]` prefix, so it is not repeated here."""
    word = _EXIT_REASON_WORDS.get(reason, reason)
    head = f"{_exit_strikes(spec)} exp {spec.expiry.isoformat()} {word}"
    if phase != "fill":
        return f"{head} closing"
    parts = [head]
    if debit is not None:
        parts.append(f"debit {debit:.2f}")
    if realized is not None:
        parts.append(f"pnl {realized:+.2f}")
    if pct_of_credit is not None:
        parts.append(f"({pct_of_credit:.0f}% of credit)")
    return " ".join(parts)
