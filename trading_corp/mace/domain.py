"""MACE domain types — neutral, broker-free (plan § Architecture seam a).

No robin_stocks / broker types may appear here or in anything importing
this module above the broker layer. `option_id` fields are opaque string
handles only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# ---------------------------------------------------------------------------
# Rung lifecycle states (mace_rung.status)
# ---------------------------------------------------------------------------

RUNG_SUBMITTING = "submitting"
RUNG_OPEN = "open"
RUNG_CLOSING = "closing"
RUNG_CLOSED = "closed"
RUNG_ABANDONED = "abandoned"
RUNG_STATES = frozenset(
    {RUNG_SUBMITTING, RUNG_OPEN, RUNG_CLOSING, RUNG_CLOSED, RUNG_ABANDONED}
)

# ---------------------------------------------------------------------------
# Exit reasons (mace_rung.exit_reason)
# ---------------------------------------------------------------------------

EXIT_PT = "pt"
EXIT_STOP = "stop"
EXIT_TIME = "time"
EXIT_EXDIV = "exdiv"
EXIT_GAP = "gap"
EXIT_MANUAL = "manual"
EXIT_EXPIRED = "expired"   # booked by the reconcile expiry sweep: a dead-wing OTM rung that RODE
                           # to expiry and expired worthless (exit_debit 0, realized = full credit)
EXIT_REASONS = frozenset(
    {EXIT_PT, EXIT_STOP, EXIT_TIME, EXIT_EXDIV, EXIT_GAP, EXIT_MANUAL, EXIT_EXPIRED}
)

# ---------------------------------------------------------------------------
# Entry-pipeline skip reasons — audited BEFORE each branch (CLAUDE.md #2);
# the FIRST failing filter is the recorded reason (plan § Entry pipeline).
# ---------------------------------------------------------------------------

SKIP_CAPACITY = "capacity"
SKIP_WEEKLY_BUDGET = "weekly_budget"
SKIP_COOLDOWN = "cooldown"
SKIP_BLACKOUT = "blackout"
SKIP_IVR = "ivr"
SKIP_NO_EXPIRY = "no_expiry"
SKIP_NO_DELTA_STRIKE = "no_delta_strike"
# no_wing is UNCONDITIONAL for ALL symbols (Board-accepted 2026-08-09 off the
# stage-B $5-grid finding: SPY far-OTM calls list $5 strikes only — 838
# probe-proven unlisted while 835 resolved). FXI additionally retries at
# fallback_width_dollars before recording it.
SKIP_NO_WING = "no_wing"
SKIP_RISK_BAND = "risk_band"
SKIP_CREDIT_FLOOR = "credit_floor"
SKIP_BUDGET = "budget"
SKIP_RESERVE = "reserve"
SKIP_RISK_REJECT = "risk_reject"
SKIP_NO_EQUITY_SNAPSHOT = "no_equity_snapshot"
SKIP_CREDIT_FLOOR_DRIFT = "credit_floor_drift"  # entry-ladder stand-down
# strike_collision: every buildable in-band condor has a leg that would open
# OPPOSITE an existing same-expiry position (RH atomic reject: buy-to-open where
# short / sell-to-open where long). build_condor shifts the short within the
# delta band to avoid it; this is recorded only when no in-band shift clears.
SKIP_STRIKE_COLLISION = "strike_collision"
SKIP_REASONS = frozenset(
    {
        SKIP_CAPACITY, SKIP_WEEKLY_BUDGET, SKIP_COOLDOWN, SKIP_BLACKOUT,
        SKIP_IVR, SKIP_NO_EXPIRY, SKIP_NO_DELTA_STRIKE, SKIP_NO_WING,
        SKIP_RISK_BAND, SKIP_CREDIT_FLOOR, SKIP_BUDGET, SKIP_RESERVE,
        SKIP_RISK_REJECT, SKIP_NO_EQUITY_SNAPSHOT, SKIP_CREDIT_FLOOR_DRIFT,
        SKIP_STRIKE_COLLISION,
    }
)

# ---------------------------------------------------------------------------
# IVR filter annotations — NOT entry skips. When the IVR value can't be
# trusted the symbol takes the Tasty-unavailable path (IVR filter skipped;
# credit floor + blackouts still gate) with a DISTINCT annotation so
# per-symbol staleness patterns stay visible in eval history
# (Board ruling 2026-08-09: ivr_stale must carry symbol + age).
# ---------------------------------------------------------------------------

IVR_OK = "ok"
IVR_STALE = "ivr_stale"              # updated_at older than 2 sessions
IVR_UNAVAILABLE = "ivr_unavailable"  # fetch failed / symbol missing / field None


def iso_week(d: date) -> str:
    """ISO week label for weekly-budget derivation, e.g. '2026-W33'."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ---------------------------------------------------------------------------
# Quotes and structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionQuote:
    """One leg's fresh quote. bid/ask may be None off-hours."""

    symbol: str
    expiry: date
    strike: float
    opt_type: str                 # "put" | "call"
    bid: float | None
    ask: float | None
    delta: float | None = None
    option_id: str | None = None  # opaque broker handle — never interpreted here
    mark: float | None = None     # RH adjusted_mark_price; wing buy price when one-sided

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def long_price(self) -> float | None:
        """Price to BUY this leg (a long / protective wing). Unlike `mid` — used
        for SHORT legs, which must SELL at a real bid — a far-OTM long wing is
        purchasable with no resting bid, so this does NOT require a two-sided
        market. It prefers the two-sided mid when present (parity with the short
        basis and with liquid names), else RH's adjusted_mark (a fair one-sided
        estimate), else the ask (the actual price we'd pay). None ONLY when there
        is truly no market (no ask and no mark). SHORT-leg pricing is unchanged;
        nothing on the short/exit path calls this."""
        if self.mid is not None:
            return self.mid
        if self.mark is not None and self.mark > 0.0:
            return self.mark
        if self.ask is not None and self.ask > 0.0:
            return self.ask
        return None


@dataclass(frozen=True)
class QuoteSnapshot:
    """The four fresh leg quotes of one condor, fetched ONCE per manage tick (2026-10-09
    exit-redesign) and shared by the trigger (mark), the PT mark-trust guard (leg_mids), and the
    closeability gate (natural_debit / wings_two_sided) -- so they can never disagree across two
    separate fetches (the pre-redesign mark()-vs-leg_mids() race). The derived properties mirror
    execution._credit_mid / _natural_debit EXACTLY (the ladder still refetches per attempt)."""

    sp: "OptionQuote | None"
    lp: "OptionQuote | None"
    sc: "OptionQuote | None"
    lc: "OptionQuote | None"

    @staticmethod
    def _wing_bid(q: "OptionQuote | None") -> float:
        """A long wing is SOLD at its bid to close; a dead wing (no bid) is given away at 0 --
        never a blocker for a risk close, and never an inflated mid."""
        return q.bid if (q is not None and q.bid is not None) else 0.0

    @property
    def mark(self) -> float | None:
        """Cost-to-close at MID = (short mids) - (long mids). None if any leg is unpriceable.
        Byte-identical to execution._credit_mid -- the management MARK used by the triggers."""
        legs = (self.sp, self.lp, self.sc, self.lc)
        if any(x is None or x.mid is None for x in legs):
            return None
        return (self.sp.mid - self.lp.mid) + (self.sc.mid - self.lc.mid)

    @property
    def natural_debit(self) -> float | None:
        """Executable cost-to-close = buy shorts @ ask, sell wings @ bid. None if any required
        side is missing (a dead wing with no bid -> None). Byte-identical to _natural_debit."""
        if None in (self.sp, self.lp, self.sc, self.lc):
            return None
        if self.sp.ask is None or self.sc.ask is None or self.lp.bid is None or self.lc.bid is None:
            return None
        return (self.sp.ask + self.sc.ask) - (self.lp.bid + self.lc.bid)

    @property
    def stop_natural(self) -> float | None:
        """Executable cost-to-close for a RISK close: shorts @ ask, wings @ (bid or 0). A dead
        wing is given away (floored to 0) so a stop is NEVER un-fillable on a no-bid wing. None
        only when a SHORT ask is missing (can't buy the short back at all)."""
        if self.sp is None or self.sc is None or self.sp.ask is None or self.sc.ask is None:
            return None
        return (self.sp.ask + self.sc.ask) - (self._wing_bid(self.lp) + self._wing_bid(self.lc))

    @property
    def stop_mark(self) -> float | None:
        """Conservative STOP basis: shorts @ mid, wings @ (bid or 0). A garbage/dead wing mid can
        only DEFLATE the plain `mark` and HIDE a stop (the latent dead-wing bug); flooring wings to
        their real bid removes that -> stop_mark >= mark always, so a stop fires no later and is
        never blinded. None only when a SHORT mid is missing (a threatened condor's near-money
        shorts are liquid, so it is computable exactly when it matters)."""
        if self.sp is None or self.sc is None or self.sp.mid is None or self.sc.mid is None:
            return None
        return (self.sp.mid - self._wing_bid(self.lp)) + (self.sc.mid - self._wing_bid(self.lc))

    @property
    def wings_two_sided(self) -> bool:
        """Both long wings have a REAL bid -> the protective wings can actually be SOLD to close.
        A dead wing (RH bid 0 -> None) fails this deterministically every tick -- no wiggle-through
        (the positive closeability check that the point-in-time mid-inversion guard could not be)."""
        return (self.lp is not None and self.lp.bid is not None
                and self.lc is not None and self.lc.bid is not None)

    @property
    def leg_mids(self) -> dict:
        """{sp,lp,sc,lc} per-leg mids for the PT mark-trust guard's intra-condor leg-sanity check."""
        return {"sp": self.sp.mid if self.sp is not None else None,
                "lp": self.lp.mid if self.lp is not None else None,
                "sc": self.sc.mid if self.sc is not None else None,
                "lc": self.lc.mid if self.lc is not None else None}


@dataclass(frozen=True)
class CondorLeg:
    opt_type: str        # "put" | "call"
    strike: float
    side: str            # "sell" | "buy"
    effect: str = "open"  # "open" | "close"


@dataclass(frozen=True)
class CondorSpec:
    """The four strikes of one iron condor. Wings are exactly width beyond
    the shorts (entry filter 6); both spreads share width_dollars."""

    symbol: str
    expiry: date
    short_put: float
    long_put: float
    short_call: float
    long_call: float
    width_dollars: float

    def opening_legs(self) -> tuple[CondorLeg, ...]:
        return (
            CondorLeg("put", self.short_put, "sell", "open"),
            CondorLeg("put", self.long_put, "buy", "open"),
            CondorLeg("call", self.short_call, "sell", "open"),
            CondorLeg("call", self.long_call, "buy", "open"),
        )

    def closing_legs(self) -> tuple[CondorLeg, ...]:
        # Flattening reverses EVERY opening side: buy back the shorts, sell the
        # longs. (The call side was inverted in the Phase-1 draft — sell/buy,
        # which re-OPENS the call spread; corrected to buy/sell 2026-08-10,
        # surfaced from Phase-3 rh_broker as the first consumer.)
        return (
            CondorLeg("put", self.short_put, "buy", "close"),
            CondorLeg("put", self.long_put, "sell", "close"),
            CondorLeg("call", self.short_call, "buy", "close"),
            CondorLeg("call", self.long_call, "sell", "close"),
        )

    def strikes_label(self) -> str:
        def s(x: float) -> str:
            return f"{x:g}"

        return (
            f"{s(self.short_put)}-{s(self.long_put)}-"
            f"{s(self.short_call)}-{s(self.long_call)}"
        )

    def rung_id(self, entry_date: date) -> str:
        """Deterministic id: mace-{sym}-{expiry}-{strikes}-{yyyymmdd}.
        Determinism is load-bearing — the reconcile loop matches
        `submitting` rungs against broker orders by combo_id prefix."""
        return (
            f"mace-{self.symbol}-{self.expiry.isoformat()}-"
            f"{self.strikes_label()}-{entry_date.strftime('%Y%m%d')}"
        )


@dataclass(frozen=True)
class EvalResult:
    """Outcome of the entry pipeline for one symbol on one session."""

    symbol: str
    entered: bool                     # all filters passed — entry ladder authorized
    skip_reason: str | None = None    # first failing filter (None when entered)
    spec: CondorSpec | None = None
    credit_mid: float | None = None
    contracts: int = 0
    max_risk_usd: float | None = None
    ivr_status: str = IVR_OK          # IVR_OK | IVR_STALE | IVR_UNAVAILABLE
    ivr_value: float | None = None    # 0–100 normalized (audit even when stale)
    overflow: bool = False            # authorized via overflow routing (T6)
    detail: str = ""                  # free-form audit detail (e.g. staleness age)


@dataclass(frozen=True)
class RungState:
    """In-memory image of one mace_rung row."""

    rung_id: str
    symbol: str
    status: str
    expiry: date
    spec: CondorSpec
    width_dollars: float
    contracts: int
    credit_actual: float | None = None
    max_risk_usd: float | None = None
    entry_ts: str | None = None
    entry_order_id: str | None = None
    pt_order_id: str | None = None
    pt_debit: float | None = None
    exit_ts: str | None = None
    exit_reason: str | None = None
    exit_debit: float | None = None
    realized_pnl: float | None = None
    entry_iso_week: str | None = None
    # Parsed mace_rung.extra_json (2026-10-09 exit-redesign): a small dict of
    # out-of-band disposition state that does NOT warrant its own column --
    # {"closing": {"since", "redrives", "parked"}} (un-latch bookkeeping),
    # {"exit_order_id","exit_order_limit","exit_order_reason"} (crash-recovery of
    # an in-flight close), {"disposition": "riding", "ride": {...}} (ride-to-expiry),
    # and the legacy {"abandon_detail"}. None when the column is NULL/unparseable.
    extra: dict | None = None


@dataclass(frozen=True)
class BreakerState:
    """Alert-only at launch (Board memo); enforcement branches exist but ship 'off'."""

    day_loss_hit: bool = False
    week_loss_hit: bool = False
    hwm_soft_hit: bool = False
    hwm_hard_hit: bool = False
    day_realized: float = 0.0
    week_realized: float = 0.0
    equity: float | None = None
    hwm: float | None = None

    @property
    def any_hit(self) -> bool:
        return (
            self.day_loss_hit
            or self.week_loss_hit
            or self.hwm_soft_hit
            or self.hwm_hard_hit
        )
