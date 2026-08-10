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
EXIT_REASONS = frozenset(
    {EXIT_PT, EXIT_STOP, EXIT_TIME, EXIT_EXDIV, EXIT_GAP, EXIT_MANUAL}
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
SKIP_REASONS = frozenset(
    {
        SKIP_CAPACITY, SKIP_WEEKLY_BUDGET, SKIP_COOLDOWN, SKIP_BLACKOUT,
        SKIP_IVR, SKIP_NO_EXPIRY, SKIP_NO_DELTA_STRIKE, SKIP_NO_WING,
        SKIP_RISK_BAND, SKIP_CREDIT_FLOOR, SKIP_BUDGET, SKIP_RESERVE,
        SKIP_RISK_REJECT, SKIP_NO_EQUITY_SNAPSHOT, SKIP_CREDIT_FLOOR_DRIFT,
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

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0


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
        return (
            CondorLeg("put", self.short_put, "buy", "close"),
            CondorLeg("put", self.long_put, "sell", "close"),
            CondorLeg("call", self.short_call, "sell", "close"),
            CondorLeg("call", self.long_call, "buy", "close"),
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
