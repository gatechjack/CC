"""Web data accessors — DB queries + live broker snapshots, async-friendly.

All sync DB / broker work is pushed onto a thread via `asyncio.to_thread`
so the FastAPI event loop never blocks. Functions return plain dicts/lists
so Jinja templates can iterate them directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from trading_corp.persistence import db
from trading_corp.utils.time import format_et_full, format_et_hm, format_et_short
from trading_corp.utils.divisions import (
    Division, InvestmentGroup, group_by_investment_type, load_divisions,
)
from trading_corp.utils.market_data import (
    get_benchmark_change, get_market_intraday, get_market_quote, get_vix,
)

# Market-overview ribbon — shown at the very top of the dashboard.
# (symbol, label, kind) — kind is just for display formatting (price vs index).
_MARKET_RIBBON_TICKERS = [
    ("SPY",     "S&P 500",     "etf"),
    ("QQQ",     "Nasdaq 100",  "etf"),
    ("BTC-USD", "Bitcoin",     "crypto"),
    ("^VIX",    "VIX",         "index"),
]

log = logging.getLogger(__name__)


# ── Snapshot dataclasses (template-friendly) ──────────────────────────────

@dataclass
class IntentBucket:
    """Aggregated equity/P&L for one intent (aggressive | retirement | balanced)."""
    intent: str
    label: str                      # "Aggressive", "Retirement", "Balanced"
    equity: float = 0.0
    pnl_today: float = 0.0
    division_count: int = 0


@dataclass
class StockHolding:
    """One stock position, enriched with current price + unrealized P&L."""
    symbol: str
    qty: float
    avg_price: float          # per share
    last: float | None         # current price per share
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    day_change_pct: float | None


@dataclass
class OptionLeg:
    """One option position, enriched with intrinsic/extrinsic + P&L.

    Per-share / per-contract conventions from robin_stocks (memo to self):
      - avg_price (from get_open_option_positions): per CONTRACT (e.g. $420 for $4.20 option)
      - mark_price (from option market_data): per SHARE
      We normalize everything to per-share for display.

    Sign convention for avg_per_share:
      Robinhood's `avg_price` field is signed — POSITIVE for longs (cost
      paid per share), NEGATIVE for shorts (credit received per share).
      We strip that sign at construction so `avg_per_share` is *always
      positive* and represents the magnitude of cost-or-credit per share.
      Direction is conveyed by `qty` (positive long, negative short),
      not by the sign of `avg_per_share`. All downstream code assumes
      this invariant — see test_option_leg_pnl.py for canonical examples.
    """
    underlying: str
    option_type: str           # "call" | "put"
    expiry: str
    strike: float
    dte: int | None
    qty: float                 # signed — positive long, negative short
    avg_per_share: float       # always positive — see class docstring
    mark_per_share: float | None
    delta: float | None
    underlying_price: float | None

    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_leap(self) -> bool:
        return self.is_long and (self.dte or 0) >= 180

    @property
    def is_short(self) -> bool:
        return self.qty < 0

    @property
    def market_value(self) -> float | None:
        if self.mark_per_share is None:
            return None
        return self.mark_per_share * 100 * self.qty   # qty is signed

    @property
    def cost_basis(self) -> float:
        # avg_per_share is always positive (see class docstring), so this
        # returns a positive notional regardless of long/short direction.
        return self.avg_per_share * 100 * abs(self.qty)

    @property
    def unrealized_pnl(self) -> float | None:
        if self.mark_per_share is None:
            return None
        # avg_per_share is always positive (see class docstring).
        # Long  P&L = (mark - avg) × 100 × |qty| — gain when mark > avg.
        # Short P&L = (avg - mark) × 100 × |qty| — gain when mark < avg
        #   (the seller pockets the difference between credit received
        #   and current cost-to-close).
        if self.is_long:
            return (self.mark_per_share - self.avg_per_share) * 100 * abs(self.qty)
        else:
            return (self.avg_per_share - self.mark_per_share) * 100 * abs(self.qty)

    @property
    def unrealized_pnl_pct(self) -> float | None:
        cb = self.cost_basis
        pnl = self.unrealized_pnl
        if cb <= 0 or pnl is None:
            return None
        return pnl / cb

    @property
    def intrinsic_per_share(self) -> float:
        """In-the-money value (calls only for now; PMCC is calls)."""
        if self.option_type == "call" and self.underlying_price is not None:
            return max(0.0, self.underlying_price - self.strike)
        if self.option_type == "put" and self.underlying_price is not None:
            return max(0.0, self.strike - self.underlying_price)
        return 0.0

    @property
    def extrinsic_per_share(self) -> float | None:
        """Time value = mark − intrinsic."""
        if self.mark_per_share is None:
            return None
        return max(0.0, self.mark_per_share - self.intrinsic_per_share)


@dataclass
class PMCCPair:
    """One LEAP + matching short-call group (the PMCC structure).

    Looser than strictly "PMCC": any underlying with at least one long or
    short call gets a pair entry. The structure_type and priority_score
    properties classify how healthy / how urgent each one is.
    """
    underlying: str
    underlying_price: float | None
    leap: OptionLeg | None
    short_call: OptionLeg | None
    extras: list[OptionLeg]

    @property
    def has_full_pair(self) -> bool:
        return self.leap is not None and self.short_call is not None

    @property
    def combined_pnl(self) -> float | None:
        """Combined unrealized P&L across both legs + extras."""
        parts = []
        if self.leap and self.leap.unrealized_pnl is not None:
            parts.append(self.leap.unrealized_pnl)
        if self.short_call and self.short_call.unrealized_pnl is not None:
            parts.append(self.short_call.unrealized_pnl)
        for x in self.extras:
            if x.unrealized_pnl is not None:
                parts.append(x.unrealized_pnl)
        return sum(parts) if parts else None

    @property
    def structure_type(self) -> str:
        """Classify the structure at a glance.

        Returns one of:
          'pmcc'           — LEAP (DTE >= 180, long call) + short call against it
          'covered_call'   — long call (DTE < 180) + short call
          'uncovered_leap' — LEAP only, no short
          'naked_call'     — long call only, short DTE
          'short_only'     — short call only (rare; usually leg of something else)
          'other'          — unusual combination (extras only, or mixed)
        """
        if self.leap and self.short_call:
            return "pmcc" if (self.leap.dte or 0) >= 180 else "covered_call"
        if self.leap and not self.short_call:
            return (
                "uncovered_leap" if (self.leap.dte or 0) >= 180
                else "naked_call"
            )
        if self.short_call and not self.leap:
            return "short_only"
        return "other"

    @property
    def priority_score(self) -> int:
        """Risk score — higher = needs attention sooner.

        Sum of independent signals from each leg. The values aren't
        calibrated against P&L impact; they're calibrated against
        management urgency: a 2-DTE ITM short scores higher than a
        slowly-eroding LEAP because it needs action TODAY.
        """
        score = 0

        # Uncovered LEAP — strategy says cover it
        if self.leap and not self.short_call:
            score += 50

        if self.short_call:
            s = self.short_call
            # ITM short call → assignment risk
            if self.underlying_price is not None and s.strike > 0 and self.underlying_price > s.strike:
                depth = (self.underlying_price - s.strike) / s.strike
                score += 30 + min(int(depth * 100), 30)
            # Roll triggers by DTE
            if s.dte is not None:
                if s.dte <= 2:
                    score += 40
                elif s.dte <= 7:
                    score += 20
                elif s.dte <= 14:
                    score += 10
            # Profit-capture candidates
            if s.unrealized_pnl_pct is not None:
                if s.unrealized_pnl_pct >= 0.70:
                    score += 15
                elif s.unrealized_pnl_pct >= 0.50:
                    score += 5

        if self.leap:
            l = self.leap
            # Coverage erosion via delta
            if l.delta is not None:
                if l.delta < 0.50:
                    score += 25
                elif l.delta < 0.65:
                    score += 10
            # LEAP DTE — needs to be rolled out
            if l.dte is not None:
                if l.dte < 60:
                    score += 30
                elif l.dte < 120:
                    score += 15

        # Combined P&L stress (% of LEAP cost basis)
        if self.combined_pnl is not None and self.leap:
            cb = self.leap.cost_basis
            if cb > 0:
                pnl_pct = self.combined_pnl / cb
                if pnl_pct < -0.25:
                    score += 15
                elif pnl_pct < -0.10:
                    score += 5

        return score

    @property
    def priority_label(self) -> str:
        s = self.priority_score
        if s >= 60: return "urgent"
        if s >= 30: return "elevated"
        if s >= 10: return "routine"
        return "healthy"

    @property
    def recommended_action(self) -> tuple[str, str]:
        """Deterministic preview of the recommended action for the position.

        Returns (label, urgency) where urgency ∈ 'urgent' | 'elevated' | 'routine'.
        Designed to match the LLM expert analysis on the same position so
        the tile pill and the right-panel verdict don't disagree on routine
        cases. Click the row for full LLM rationale.

        Decision tree (in order):
          1. Empty position → "—"
          2. LEAP only (no short) → OPEN SHORT
          3. Short call → ITM/OTM analysis using config thresholds:
                terminal_dte_rules.short_dte_threshold (≤2 DTE)
                breach_policy.classification (3% / 8%)
                management.profit_take_pct (50% / 70%)
          4. LEAP-side concerns → ROLL LEAP (only when truly urgent —
             delta < 0.40 or DTE < 30, not the soft "consider" thresholds
             of 0.50 and 120 which are flagged in warnings, not actions)
          5. Default: WATCH

        Bug-fix history:
          - Removed blanket `DTE ≤ 7 → ROLL SHORT` trigger — caused false
            ROLL SHORT on deep-OTM near-expiry shorts (e.g. ASTS 19% OTM 3DTE)
          - Tightened LEAP DTE roll threshold 120 → 30 — the 120-DTE config
            value is "monitor/consider next cycle", not "act now" (e.g. IREN
            51 DTE shouldn't show ROLL LEAP when the short is healthy)
          - Tightened LEAP delta roll threshold 0.50 → 0.40 to match the
            yaml's roll_down_trigger_delta
        """
        # 1. Empty position
        if not self.leap and not self.short_call:
            return ("—", "routine")

        # 2. Uncovered LEAP — strategy says cover it
        if self.leap and not self.short_call:
            return ("OPEN SHORT", "elevated")

        s = self.short_call
        spot = self.underlying_price

        # 3. Short-call analysis (when we have spot + short strike)
        if s and s.strike > 0 and spot is not None:
            breach_pct = (spot - s.strike) / s.strike   # signed; +ve = ITM

            # Runaway-breach handler (≥8% ITM) used by both terminal- and
            # non-terminal branches. CLOSE SHORT (close the short and hold
            # LEAP naked) is the strategy-yaml preferred_action ONLY when
            # the LEAP has enough runway left — config requires `leap_dte
            # > 270`. Below that threshold, holding a short-dated LEAP
            # unhedged is worse than rolling defensively for a credit.
            #
            # Calibrated against LLM behavior:
            #   - TSLA 14% ITM, LEAP DTE 625 → CLOSE SHORT (LEAP has runway)
            #   - BULL 9.2% ITM, LEAP DTE 262 → ROLL SHORT EARLY (LEAP too short)
            #   - OPEN 9.6% ITM, LEAP DTE 262 → ROLL SHORT EARLY (LEAP too short)
            _RUNAWAY_LEAP_DTE_FLOOR = 270
            def _runaway_action() -> tuple[str, str]:
                leap_dte = (
                    self.leap.dte if (self.leap and self.leap.dte is not None)
                    else 0
                )
                if leap_dte > _RUNAWAY_LEAP_DTE_FLOOR:
                    return ("CLOSE SHORT", "urgent")          # LEAP has runway → close + hold naked
                return ("ROLL SHORT EARLY", "urgent")         # LEAP too short → roll defensively

            # Terminal-DTE rules — the LLM treats DTE ≤ 3 as terminal in
            # practice. Calibrated against four observed LLM judgments:
            #   - MARA 3.0% ITM, mark $0.59 → HOLD  (intrinsic locked; capture extrinsic)
            #   - MARA 4.1% ITM, mark $0.68 → ROLL SHORT EARLY (escape major breach)
            #   - HOOD 8.3% OTM, mark $1.32 → ROLL SHORT (75% profit + meaningful mark)
            #   - ASTS 19% OTM,  mark $0.13 → HOLD  (let expire — premium too small to roll)
            #
            # The unifying rule: at terminal DTE,
            #   ITM means intrinsic is locked; only the extrinsic is avoidable.
            #     → small breach (<4%) = HOLD; ≥4% = act (roll/close).
            #   OTM means the entire mark is "profit not yet captured".
            #     → if mark is meaningful (≥$0.20/sh), ROLL to capture it cleanly.
            #     → if mark is tiny, HOLD and let expire (write fresh next cycle).
            if s.dte is not None and s.dte <= 3:
                # Runaway breach — gated by LEAP DTE (see _runaway_action above)
                if breach_pct >= 0.08:
                    return _runaway_action()
                # Major breach + terminal — must act
                if breach_pct >= 0.04:
                    return ("ROLL SHORT EARLY", "urgent")
                # ITM but below 4%: HOLD — intrinsic is locked either way,
                # extrinsic will decay free of charge
                if breach_pct >= 0:
                    return ("HOLD", "elevated")

                # OTM territory at terminal DTE — depends on remaining mark
                remaining_mark = s.mark_per_share if s.mark_per_share is not None else 0.0
                profit_pct = s.unrealized_pnl_pct if s.unrealized_pnl_pct is not None else 0.0
                # Meaningful profit + meaningful premium → roll for clean credit
                # (HOOD case: 75.7% profit, $1.32 mark = $132/contract worth rolling)
                if profit_pct >= 0.50 and remaining_mark >= 0.20:
                    return ("ROLL SHORT", "elevated")
                # ATM zone with no profit-take signal — let theta finish
                if breach_pct >= -0.015:
                    return ("HOLD", "elevated")
                # Deep OTM with tiny premium — let expire worthless
                # (ASTS case: 87% profit but only $0.13 mark — not worth rolling)
                return ("HOLD", "routine")

            # Non-terminal breach classification (DTE > 3)
            if breach_pct >= 0.08:
                return _runaway_action()                      # runaway — gated by LEAP DTE
            if breach_pct >= 0.03:
                return ("ROLL SHORT EARLY", "urgent")         # major breach (3-8%)
            if breach_pct >= 0:
                return ("ROLL SHORT", "elevated")             # minor breach (0-3%)

            # Short is OTM (breach_pct < 0). Fall through to profit-take.

        # 4. Profit-take triggers — non-terminal (DTE > 3) only.
        # Terminal-DTE positions handle profit-take inside the terminal branch
        # above (where mark-size matters more than DTE).
        # config: management.profit_take_pct=0.50, profit_take_pct_late=0.75
        if s and s.unrealized_pnl_pct is not None:
            if s.unrealized_pnl_pct >= 0.70:
                return ("ROLL SHORT EARLY", "elevated")
            if s.unrealized_pnl_pct >= 0.50:
                return ("ROLL SHORT", "elevated")

        # 5. LEAP-side concerns — only trigger when truly urgent.
        # Soft thresholds (delta < 0.50, DTE < 120) are MONITORING signals,
        # not action signals — they belong in the right-panel warnings, not
        # the action pill. Use the hard-rule thresholds here.
        if self.leap:
            l = self.leap
            # Hard-rule delta threshold (config: long_leg.roll_down_trigger_delta)
            if l.delta is not None and l.delta < 0.40:
                return ("ROLL LEAP", "elevated")
            # Hard-rule DTE threshold — only when LEAP is truly running out
            # (well below the "consider on next cycle" 120-DTE soft signal)
            if l.dte is not None and l.dte < 30:
                return ("ROLL LEAP", "elevated")

        # 6. Default: nothing actionable, just monitor
        return ("WATCH", "routine")


@dataclass
class CoveredCallPosition:
    """One underlying with shares + a short call sold against them.

    Retirement-account variant of PMCC: no LEAP — the shares ARE the
    cover. Allowed in IRAs because the short call is fully secured by
    owned shares (vs. PMCC which uses a LEAP as cover — disallowed in
    IRA per IRS Reg 1.401(a) options-on-options rules).
    """
    underlying: str
    underlying_price: float | None
    shares_qty: float
    shares_avg_price: float
    shares_market_value: float | None
    shares_cost_basis: float
    shares_pnl: float | None
    shares_pnl_pct: float | None
    short_call: OptionLeg
    coverage_pct: float                # shares / (|short_qty| * 100), 1.0 = fully covered

    @property
    def is_fully_covered(self) -> bool:
        return self.coverage_pct >= 1.0

    @property
    def days_to_expiry(self) -> int | None:
        return self.short_call.dte

    @property
    def is_itm(self) -> bool:
        if self.underlying_price is None:
            return False
        return self.underlying_price > self.short_call.strike

    @property
    def breach_pct(self) -> float | None:
        """How far ITM as % of strike. Negative = OTM."""
        if self.underlying_price is None or self.short_call.strike <= 0:
            return None
        return (self.underlying_price - self.short_call.strike) / self.short_call.strike

    @property
    def combined_pnl(self) -> float | None:
        """Combined P&L: share appreciation + short-call credit/cost."""
        if self.shares_pnl is None or self.short_call.unrealized_pnl is None:
            return None
        return self.shares_pnl + self.short_call.unrealized_pnl

    @property
    def call_status(self) -> str:
        """Plain-English status for the short call."""
        if self.short_call.dte is not None and self.short_call.dte <= 1:
            return "expiring_today" if self.short_call.dte == 0 else "expiring_tomorrow"
        if self.is_itm:
            return "itm"
        if self.short_call.unrealized_pnl_pct is not None and self.short_call.unrealized_pnl_pct >= 0.70:
            return "profit_take_candidate"
        return "open"

    @property
    def priority_score(self) -> int:
        """Risk/urgency score for sorting. Higher = more urgent.

        Mirrors the PMCCPair priority idea but simpler — IRA has only
        one short leg per pair (no LEAP coverage erosion to track).
        """
        score = 0
        s = self.short_call
        # ITM short → assignment risk
        if self.is_itm:
            depth = self.breach_pct or 0.0
            score += 30 + min(int(depth * 100), 30)
        # Roll triggers by DTE
        if s.dte is not None:
            if s.dte == 0:
                score += 50
            elif s.dte <= 2:
                score += 40
            elif s.dte <= 7:
                score += 15
            elif s.dte <= 14:
                score += 5
        # Profit-capture candidates (close-and-resell)
        if s.unrealized_pnl_pct is not None:
            if s.unrealized_pnl_pct >= 0.85:
                score += 20
            elif s.unrealized_pnl_pct >= 0.70:
                score += 10
        return score

    @property
    def priority_label(self) -> str:
        """Bucket the score into urgent / elevated / routine / healthy."""
        s = self.priority_score
        if s >= 50:
            return "urgent"
        if s >= 20:
            return "elevated"
        if s >= 5:
            return "routine"
        return "healthy"

    @property
    def recommended_action(self) -> tuple[str, str]:
        """Deterministic preview of the next action for this pair.

        Returns (label, urgency). Used to render the action pill on the
        collapsed row. Order matters — first matching rule wins.
        """
        s = self.short_call
        # Same-day expiry with ITM → assignment imminent
        if s.dte == 0 and self.is_itm:
            return ("Roll or accept assignment", "urgent")
        if s.dte == 0:
            return ("Let expire", "elevated")
        # Profit take (regardless of DTE)
        if s.unrealized_pnl_pct is not None and s.unrealized_pnl_pct >= 0.85:
            return ("Close (≥85% profit)", "elevated")
        # Short DTE
        if s.dte is not None and s.dte <= 2:
            if self.is_itm:
                return ("Roll up & out", "urgent")
            return ("Roll out", "elevated")
        # Profit candidate
        if s.unrealized_pnl_pct is not None and s.unrealized_pnl_pct >= 0.70:
            return ("Close (≥70%)", "elevated")
        # ITM but not expiring
        if self.is_itm:
            return ("Watch (ITM)", "elevated")
        return ("Hold", "routine")


@dataclass
class WheelPutPosition:
    """One short put sold to acquire shares (cash-secured put / wheel).

    In an IRA the put must be cash-secured — the broker holds the
    assignment cash. Shown alongside owned positions because if
    assigned, the user will own `strike * 100 * |qty|` worth of the
    underlying.
    """
    short_put: OptionLeg

    @property
    def underlying(self) -> str:
        return self.short_put.underlying

    @property
    def strike(self) -> float:
        return self.short_put.strike

    @property
    def expiry(self) -> str:
        return self.short_put.expiry

    @property
    def days_to_expiry(self) -> int | None:
        return self.short_put.dte

    @property
    def credit_received(self) -> float:
        """Total credit received in dollars (per share × 100 × |qty|)."""
        return self.short_put.avg_per_share * 100 * abs(self.short_put.qty)

    @property
    def cost_to_close(self) -> float | None:
        if self.short_put.mark_per_share is None:
            return None
        return self.short_put.mark_per_share * 100 * abs(self.short_put.qty)

    @property
    def is_itm(self) -> bool:
        """ITM put = underlying < strike (assignment risk)."""
        if self.short_put.underlying_price is None:
            return False
        return self.short_put.underlying_price < self.short_put.strike

    @property
    def assignment_cost(self) -> float:
        """If assigned, what the user will pay for the shares."""
        return self.strike * 100 * abs(self.short_put.qty)

    @property
    def effective_basis_if_assigned(self) -> float:
        """Net basis per share if assigned, accounting for credit kept."""
        return self.strike - self.short_put.avg_per_share


@dataclass
class DivisionViewSnapshot:
    """Everything the /division/{slug} page needs to render."""
    division: Any          # Division dataclass

    equity: float | None
    cash: float | None
    buying_power: float | None
    todays_pnl: float | None
    todays_pnl_pct: float | None

    stock_holdings: list[StockHolding]
    pmcc_pairs: list[PMCCPair]
    other_options: list[OptionLeg]   # options not paired into PMCC

    recent_activity: list[dict]
    equity_curve: list[dict]
    paper_trade_summary: dict | None = None
    # Coinbase BTC Donchian — only populated for `coinbase_spot`
    # division. Shape: {state: 'cash'|'btc', cost_basis: float|None,
    # last_decision_ts: str|None, last_decision_age: str|None,
    # next_bar_ts: str, next_bar_countdown: str, enabled: bool,
    # auto_execute: bool, donchian: {entry_lookback,exit_lookback,
    # trend_filter_lookback,granularity_seconds}, decisions: list,
    # round_trips: list}.
    donchian: dict | None = None
    # BitUnix Phase 3.2.3 score panel — only populated for
    # `bitunix_futures` division. Shape documented on
    # `build_bitunix_score_view`.
    bitunix_score: dict | None = None
    # Robinhood IRA dashboard — only populated for `robinhood_ira`
    # division. Shape:
    #   {covered_calls: list[CoveredCallPosition],
    #    pure_assets:   list[StockHolding],
    #    wheel_puts:    list[WheelPutPosition]}
    # When set, division.html renders the IRA dashboard partial instead
    # of the generic PMCC / Holdings sections.
    ira_view: dict | None = None
    # BitUnix HTF (higher-timeframe) regime panel — only populated for
    # `bitunix_futures`. Shape documented on `build_bitunix_htf_view`.
    # PR 2 ships this as a read-only display; the values are computed
    # but the observer does NOT yet consult them for trade decisions
    # (PR 3 wires the gate). Renders an "off" state when the HTF
    # provider is missing (e.g., test environments).
    bitunix_htf: dict | None = None


@dataclass
class CommandCenterSnapshot:
    mode: str
    total_equity: float
    open_positions: int
    pending_approvals: int
    vix: float | None
    regime: str
    buckets: list[IntentBucket]     # one per intent (in order)
    investment_groups: list[InvestmentGroup]
    health: dict
    equity_curve: list[dict]
    # Market overview ribbon at top: SPY / QQQ / BTC-USD / VIX
    market_ribbon: list[dict]       # [{symbol, label, kind, price, change_pct}]
    # Stub for future BTC-holdings feed; populated from a real source later.
    btc_owned: float = 0.0
    # Dry-run mode: LIVE pipeline runs end-to-end but broker.place_order() is
    # skipped. Templates render an extra badge to flag this.
    dry_run: bool = False


# ── DB helpers ────────────────────────────────────────────────────────────

def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _query(db_url: str, sql: str, params: tuple = ()) -> list[dict]:
    with db.connect(db_url) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Command Center snapshot (the big aggregator) ──────────────────────────

async def build_command_center(deps) -> CommandCenterSnapshot:
    """Build the full top-level snapshot in parallel.

    `deps` is the WebDeps dataclass from app.py.
    """
    db_url = deps.db_url

    # Load divisions and prepare investment-type groups
    divisions = load_divisions()
    investment_groups = group_by_investment_type(divisions)

    # Run parallel data fetches
    db_results = await asyncio.gather(
        asyncio.to_thread(_query_open_orders, db_url),
        asyncio.to_thread(_query_pending_approvals, db_url),
        asyncio.to_thread(_query_recent_audit, db_url, 10),
        asyncio.to_thread(_query_equity_curve, db_url, 30),
        asyncio.to_thread(_safe_get_vix),
        asyncio.to_thread(_safe_regime, deps.trend_agent),
        _build_market_ribbon(),
        return_exceptions=True,
    )
    open_orders, pending, recent_audit, eq_curve, vix, regime, ribbon = (
        r if not isinstance(r, Exception) else None for r in db_results
    )
    open_orders = open_orders or []
    pending = pending or []
    recent_audit = recent_audit or []
    eq_curve = eq_curve or []
    ribbon = ribbon or []

    # Hydrate each division with broker snapshot data
    await _hydrate_division_metrics(divisions, deps)

    # Donchian overview (CASH/BTC badge + state-aware dial on the home tile).
    # Only attaches to divisions running a Donchian strategy — coinbase_spot
    # today; other divisions stay at .donchian = None.
    try:
        _hydrate_donchian_overview(divisions, db_url)
    except Exception as e:
        log.debug("donchian overview hydration failed (continuing): %s", e)

    # Prediction-market tile overview (K2.4). Attaches n_resolved + n_pending
    # + win_rate + total_realized to the 4 (later 5) prediction-market
    # divisions so the home tile renders performance stats inline. Single
    # DB sweep — pulls counts for every prediction-market division in one
    # pass to avoid N round-trips.
    try:
        _hydrate_pm_overview(divisions, db_url)
    except Exception as e:
        log.debug("pm overview hydration failed (continuing): %s", e)

    # Note: benchmark hydration disabled — was only consumed by per-tile
    # YTD comparison, which is now redundant with the top market ribbon.
    # Keep _hydrate_benchmarks around for the Phase 2 division drill-down
    # page where a benchmark comparison makes more sense at scale.
    # await _hydrate_benchmarks(divisions)

    # Aggregate by investment-type group + by intent bucket
    for grp in investment_groups:
        grp.total_equity = sum(d.equity or 0.0 for d in grp.divisions)
        grp.total_pnl_today = sum(d.pnl_today or 0.0 for d in grp.divisions)

    buckets = _aggregate_intent_buckets(divisions)

    total_equity = sum(d.equity or 0.0 for d in divisions)
    open_positions = sum(d.position_count for d in divisions)

    health = {
        "brokers": _broker_health(deps.data_exec),
        "scheduler": _scheduler_health(db_url),
        "mode": deps.mode,
    }

    return CommandCenterSnapshot(
        mode=deps.mode,
        total_equity=total_equity,
        open_positions=open_positions,
        pending_approvals=len(pending),
        vix=vix if isinstance(vix, (int, float)) else None,
        regime=regime if isinstance(regime, str) else "unknown",
        buckets=buckets,
        investment_groups=investment_groups,
        health=health,
        equity_curve=eq_curve,
        market_ribbon=ribbon,
        btc_owned=0.0,           # stub — wire to live feed in Phase 1.5c+
        dry_run=bool(getattr(deps, "dry_run", False)),
    )


async def _build_market_ribbon() -> list[dict]:
    """Fetch quote + 24h intraday bars for each ribbon ticker, in parallel.

    Each entry includes a precomputed SVG `path` string for the sparkline
    so the template doesn't have to do math (Jinja arithmetic over lists is
    awkward and the renders are repeated every refresh).
    """
    async def _one(symbol: str, label: str, kind: str) -> dict:
        # Quote and intraday bars run in parallel for each ticker
        quote, bars = await asyncio.gather(
            asyncio.to_thread(get_market_quote, symbol),
            asyncio.to_thread(get_market_intraday, symbol),
        )
        spark_path = _sparkline_path(bars) if bars else ""
        return {
            "symbol": symbol,
            "label": label,
            "kind": kind,
            "price": quote.get("price"),
            "change_pct": quote.get("change_pct"),
            "spark_path": spark_path,
            "spark_n": len(bars),
        }

    results = await asyncio.gather(
        *[_one(sym, label, kind) for sym, label, kind in _MARKET_RIBBON_TICKERS],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, dict)]


def _sparkline_path(prices: list[float], width: float = 100.0, height: float = 28.0) -> str:
    """Generate an SVG path `d` attribute for a sparkline.

    Coordinates are normalized into a 100×28 viewBox; the SVG element should
    use `preserveAspectRatio="none"` to stretch to whatever the container is.
    Returns empty string if there's nothing to draw.
    """
    n = len(prices)
    if n < 2:
        return ""
    p_min = min(prices)
    p_max = max(prices)
    rng = (p_max - p_min) or 1.0
    parts: list[str] = []
    for i, p in enumerate(prices):
        x = i * width / (n - 1)
        # Invert Y: SVG y=0 is top; we want high prices at the top of the chart
        y = height - ((p - p_min) / rng) * height
        parts.append(f"{x:.1f},{y:.2f}")
    return "M " + " L ".join(parts)


# ── Division hydration ────────────────────────────────────────────────────
# Phase 1.5a interim: existing brokers are single-account, so we can populate
# at most one division per broker family. The remaining divisions render as
# "not_wired" placeholders until the multi-account refactor (Phase 1.5b).

async def _hydrate_division_metrics(divisions: list[Division], deps) -> None:
    """Fill equity/positions/pnl on each division using its slug-keyed broker.

    Each division's broker is registered under its slug
    (e.g. 'robinhood_pmcc', 'fidelity_401k'). We fan out snapshot calls
    in parallel so 8 divisions render in roughly the slowest broker's time.
    """
    if deps.data_exec is None:
        for d in divisions:
            d.status = "not_wired"
        return

    brokers_by_slug = getattr(deps.data_exec, "brokers", {})

    async def _snap(division: Division) -> tuple[Division, Any]:
        broker = brokers_by_slug.get(division.slug)
        if broker is None:
            return division, None
        try:
            snap = await broker.snapshot()
            return division, snap
        except Exception as e:
            log.debug("snapshot for division=%s failed: %s", division.slug, e)
            return division, None

    results = await asyncio.gather(*[_snap(d) for d in divisions], return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            continue
        division, snap = r
        if snap is None:
            division.status = "not_wired"
            continue
        equity = _equity_from_snap(snap)
        positions = _positions_from_snap(snap)
        division.equity = equity
        division.position_count = len(positions)
        division.pnl_today = None       # will compute in Phase 2 from cost basis
        division.status = "online" if equity is not None else "offline"


def _hydrate_donchian_overview(divisions: list[Division], db_url: str) -> None:
    """Attach a small Donchian overview dict to each division running a
    Donchian strategy (today: coinbase_spot). Reads agent_state for the
    CASH/BTC state and the most recent `donchian_evaluated` audit row for
    the channel high/low + current close. Pre-computes a 0..1 dial
    position so the template stays dumb.

    Tolerant of missing data — pre-first-eval the audit row may not exist
    yet; in that case state still renders (from agent_state) but
    dial_position is None and the dial chrome hides.
    """
    target = next((d for d in divisions if d.slug == "coinbase_spot"), None)
    if target is None:
        return

    import json
    state, cost_basis = None, None
    state_rows = _query(
        db_url,
        "SELECT value_json FROM agent_state "
        "WHERE agent='coinbase_btc_donchian' AND key='state'",
    )
    if state_rows:
        try:
            v = json.loads(state_rows[0]["value_json"])
            state = v.get("state")
            cost_basis = v.get("cost_basis")
        except (ValueError, TypeError):
            pass
    if state is None:
        return  # strategy not configured / no state — leave .donchian = None

    current_close, donchian_high, donchian_low, last_ts = None, None, None, None
    audit_rows = _query(
        db_url,
        "SELECT ts, payload_json FROM audit_event "
        "WHERE actor='coinbase_btc_donchian' AND kind='donchian_evaluated' "
        "ORDER BY ts DESC LIMIT 1",
    )
    if audit_rows:
        try:
            p = json.loads(audit_rows[0]["payload_json"])
            current_close = p.get("current_close")
            donchian_high = p.get("donchian_high")
            donchian_low = p.get("donchian_low")
            last_ts = audit_rows[0]["ts"]
        except (ValueError, TypeError):
            pass

    dial_position: float | None = None
    if (
        current_close is not None
        and donchian_high is not None
        and donchian_low is not None
        and donchian_high > donchian_low
    ):
        raw = (current_close - donchian_low) / (donchian_high - donchian_low)
        dial_position = max(0.0, min(1.0, raw))

    target.donchian = {
        "state": state,
        "cost_basis": cost_basis,
        "current_close": current_close,
        "donchian_high": donchian_high,
        "donchian_low": donchian_low,
        "dial_position": dial_position,
        "last_eval_ts": last_ts,
    }


def _hydrate_pm_overview(divisions: list[Division], db_url: str) -> None:
    """Attach `pm_overview` dict to each prediction-market division for the
    home tile (K2.4). Single sweep — three aggregate queries (polymarket
    round-trips, kalshi round-trips grouped by division, pending counts)
    rather than one query per division.

    Keys on the resulting dict:
        n_resolved, n_pending, n_wins, n_losses, n_voids,
        win_rate_pct (None pre-first-resolve), total_realized_pnl.
    """
    from trading_corp.utils.divisions import classify_investment_type

    pm_divisions = [
        d for d in divisions
        if classify_investment_type(d) == "prediction_markets"
    ]
    if not pm_divisions:
        return

    # Init zero-state for every prediction-market division so even ones
    # with no rows render "0 resolved / 0 pending" rather than dashes.
    stats: dict[str, dict] = {
        d.slug: {
            "n_resolved": 0,
            "n_pending": 0,
            "n_wins": 0,
            "n_losses": 0,
            "n_voids": 0,
            "win_rate_pct": None,
            "total_realized_pnl": 0.0,
        }
        for d in pm_divisions
    }

    # Polymarket round-trips — one row aggregate.
    if "polymarket_arbitrage" in stats:
        try:
            rows = _query(
                db_url,
                "SELECT COUNT(*) AS n, "
                "       SUM(won) AS w, "
                "       SUM(realized_pnl) AS pnl "
                "FROM polymarket_round_trips",
            )
            if rows and rows[0].get("n"):
                n = int(rows[0].get("n") or 0)
                w = int(rows[0].get("w") or 0)
                pnl = float(rows[0].get("pnl") or 0.0)
                s = stats["polymarket_arbitrage"]
                s["n_resolved"] = n
                s["n_wins"] = w
                s["n_losses"] = max(0, n - w)   # polymarket has no void column
                s["total_realized_pnl"] = pnl
        except Exception as e:
            log.debug("pm_overview: polymarket roll-up failed: %s", e)

    # Kalshi round-trips — grouped by division (the table has the column).
    try:
        rows = _query(
            db_url,
            "SELECT division, "
            "       COUNT(*) AS n, "
            "       SUM(won) AS w, "
            "       SUM(CASE WHEN market_result='void' THEN 1 ELSE 0 END) AS v, "
            "       SUM(realized_pnl) AS pnl "
            "FROM kalshi_round_trips GROUP BY division",
        )
        for r in rows:
            div = r.get("division") or ""
            if div not in stats:
                continue
            n = int(r.get("n") or 0)
            w = int(r.get("w") or 0)
            v = int(r.get("v") or 0)
            pnl = float(r.get("pnl") or 0.0)
            s = stats[div]
            s["n_resolved"] = n
            s["n_wins"] = w
            s["n_voids"] = v
            s["n_losses"] = max(0, n - w - v)
            s["total_realized_pnl"] = pnl
    except Exception as e:
        log.debug("pm_overview: kalshi roll-up failed: %s", e)

    # Pending counts (per-division). One query per division — small;
    # could be batched if it becomes hot.
    for d in pm_divisions:
        try:
            stats[d.slug]["n_pending"] = _query_pm_pending_count(db_url, [d.slug])
        except Exception as e:
            log.debug("pm_overview: pending count failed for %s: %s", d.slug, e)

    # Compute win rate per division, then attach.
    for d in pm_divisions:
        s = stats[d.slug]
        decisive = s["n_wins"] + s["n_losses"]
        s["win_rate_pct"] = (100.0 * s["n_wins"] / decisive) if decisive > 0 else None
        d.pm_overview = s


async def _hydrate_benchmarks(divisions: list[Division]) -> None:
    """Fetch each unique benchmark's YTD change and write it onto divisions."""
    symbols = sorted({d.benchmark for d in divisions if d.benchmark})
    if not symbols:
        return

    async def _one(sym: str) -> tuple[str, float | None]:
        pct = await asyncio.to_thread(get_benchmark_change, sym, "ytd")
        return sym, pct

    results = await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)
    bench_pct: dict[str, float | None] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        sym, pct = r
        bench_pct[sym] = pct

    for d in divisions:
        d.benchmark_pct = bench_pct.get(d.benchmark)


def _aggregate_intent_buckets(divisions: list[Division]) -> list[IntentBucket]:
    """One bucket per intent, in stable order: aggressive → balanced → retirement."""
    order = ["aggressive", "balanced", "retirement"]
    labels = {"aggressive": "Aggressive", "balanced": "Balanced", "retirement": "Retirement"}
    buckets: dict[str, IntentBucket] = {}
    for d in divisions:
        intent = d.intent if d.intent in labels else "balanced"
        b = buckets.setdefault(intent, IntentBucket(intent=intent, label=labels[intent]))
        if d.equity is not None:
            b.equity += d.equity
        if d.pnl_today is not None:
            b.pnl_today += d.pnl_today
        b.division_count += 1
    return [buckets[k] for k in order if k in buckets]


# ── DB queries ────────────────────────────────────────────────────────────

def _query_open_orders(db_url: str) -> list[dict]:
    return _query(
        db_url,
        """SELECT id, ts, strategy, symbol, side, qty, status, rationale
           FROM proposed_order
           WHERE status IN ('proposed','risk_approved','board_approved')
           ORDER BY ts DESC LIMIT 50""",
    )


def _query_pending_approvals(db_url: str) -> list[dict]:
    return _query(
        db_url,
        """SELECT id, ts, strategy, symbol, side, qty, rationale
           FROM proposed_order
           WHERE status='risk_approved'
           ORDER BY ts DESC""",
    )


def _query_recent_audit(db_url: str, limit: int) -> list[dict]:
    return _query(
        db_url,
        """SELECT ts, actor, kind, payload_json
           FROM audit_event
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )


def _query_equity_curve(db_url: str, days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = _query(
        db_url,
        """SELECT ts, account, equity FROM account_state
           WHERE ts >= ? ORDER BY ts ASC""",
        (cutoff,),
    )
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
        date = (r["ts"] or "")[:10]
        if not date:
            continue
        by_date.setdefault(date, {})[r["account"]] = float(r["equity"] or 0.0)
    return [
        {"date": d, "equity": sum(accts.values())}
        for d, accts in sorted(by_date.items())
    ]


def _broker_health(data_exec) -> list[dict]:
    """Compact broker-family health row for the footer.

    Multiple divisions on the same broker family (Robinhood, Fidelity)
    share one underlying login session, so we de-dupe by family in the
    UI: green dot = at least one division is connected on that family.
    """
    out: list[dict] = []
    if data_exec is None or not hasattr(data_exec, "brokers"):
        return out
    families: dict[str, dict] = {}
    for slug, broker in data_exec.brokers.items():
        # Family = first underscore-segment of the slug, falling back to the
        # broker's `name` attribute. "default" / "paper_*" stay separate.
        family = slug.split("_", 1)[0] if "_" in slug else slug
        # Surface real underlying broker name for paper-execution wrappers
        bname = getattr(broker, "name", type(broker).__name__)
        connected = bool(getattr(broker, "_connected", False)) or bool(getattr(broker, "connected", False))
        is_paper = bool(getattr(broker, "paper", False))
        entry = families.setdefault(family, {
            "division": family,
            "broker": bname,
            "connected": False,
            "paper": True,
            "count": 0,
        })
        entry["count"] += 1
        entry["connected"] = entry["connected"] or connected
        # Family is "paper" only if every member is paper
        entry["paper"] = entry["paper"] and is_paper
    return list(families.values())


def _scheduler_health(db_url: str) -> dict:
    rows = _query(
        db_url,
        """SELECT ts, kind, payload_json FROM audit_event
           WHERE actor='scheduler' ORDER BY id DESC LIMIT 1""",
    )
    if not rows:
        return {"last_run": None, "status": "no scans yet"}
    r = rows[0]
    try:
        payload = json.loads(r.get("payload_json") or "{}")
    except Exception:
        payload = {}
    return {
        "last_run": r["ts"],
        "status": r["kind"],
        "result": payload.get("result"),
    }


def _safe_get_vix() -> float | None:
    try:
        return get_vix()
    except Exception:
        return None


def _safe_regime(trend_agent) -> str:
    try:
        if trend_agent is None:
            return "unknown"
        reading = trend_agent.read()
        return getattr(reading, "regime", "unknown") or "unknown"
    except Exception:
        return "unknown"


# ── Snapshot helpers (work with both AccountSnapshot dataclass & dict) ────

def _equity_from_snap(snap: Any) -> float | None:
    if snap is None:
        return None
    if isinstance(snap, dict):
        return float(snap.get("equity") or 0.0)
    return float(getattr(snap, "equity", 0.0) or 0.0)


def _positions_from_snap(snap: Any) -> list[Any]:
    if snap is None:
        return []
    if isinstance(snap, dict):
        return list(snap.get("positions") or [])
    return list(getattr(snap, "positions", []) or [])


# ── Paper-trade summary (Phase C of would_have_placed enrichment) ────────


def paper_trade_summary(db_url: str, division: str) -> dict:
    """Per-division paper-trade win-rate summary for the dashboard panel.

    Returns a dict shaped:
      {
        "division": "<slug>",
        "windows": [
          {"label": "7d",  "rows": [...]},
          {"label": "30d", "rows": [...]},
          {"label": "all", "rows": [...]},
        ],
        "totals": {
          "7d":  {"n": N, "wins": W, "losses": L, "expired": E,
                  "open": O, "win_rate_pct": float, "sim_pnl": $, ...},
          "30d": {...},
          "all": {...},
        },
      }

    `pre_phase_a` rows are excluded from win-rate math (we have no TP/SL
    on them, so they're not part of the "what would my track record
    look like?" answer). They're still counted in the raw row count
    under `n_pre_phase_a` for transparency.
    """
    cutoffs = {
        "7d":  (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
        "30d": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "all": "1970-01-01T00:00:00+00:00",
    }
    out = {"division": division, "windows": [], "totals": {}}
    for label, cutoff in cutoffs.items():
        rows = _query(
            db_url,
            """SELECT tier, result,
                      COUNT(*) AS n,
                      COALESCE(SUM(actual_pnl_dollars), 0) AS sim_pnl
               FROM paper_trade_record
               WHERE division = ? AND ts >= ?
               GROUP BY tier, result
               ORDER BY tier ASC""",
            (division, cutoff),
        )
        out["windows"].append({"label": label, "rows": rows})

        wins = sum(r["n"] for r in rows if r["result"] == "win")
        losses = sum(r["n"] for r in rows if r["result"] == "loss")
        expired = sum(r["n"] for r in rows if r["result"] == "expired")
        open_n = sum(r["n"] for r in rows if r["result"] is None)
        pre_a = sum(r["n"] for r in rows if r["result"] == "pre_phase_a")
        decided = wins + losses
        total_n = sum(r["n"] for r in rows)
        sim_pnl = sum(r["sim_pnl"] or 0.0 for r in rows)
        out["totals"][label] = {
            "n": total_n,
            "wins": wins,
            "losses": losses,
            "expired": expired,
            "open": open_n,
            "n_pre_phase_a": pre_a,
            "win_rate_pct": (100.0 * wins / decided) if decided else None,
            "sim_pnl": round(sim_pnl, 2),
        }
    return out


# ── Trade flow ────────────────────────────────────────────────────────────

def trade_flow(db_url: str, limit: int = 20) -> list[dict]:
    rows = _query(
        db_url,
        """SELECT id, ts, actor, kind, payload_json
           FROM audit_event
           WHERE kind IN (
             'risk_approved','risk_rejected',
             'board_approved','board_rejected','auto_executed',
             'fill','execution_error',
             'scan_order_result','scheduled_scan_done','scheduled_scan_error',
             -- Lord Otter "would have placed" surfaces on the home rail
             -- because it represents an action decision. Lower-noise kinds
             -- (webhook_received, alert_ignored) only render on the
             -- per-division page so the home rail isn't flooded.
             'would_have_placed'
           )
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r.get("payload_json") or "{}")
        except Exception:
            payload = {}
        out.append({
            "id": r["id"],
            "ts": r["ts"],
            "ts_short": _humanize_ts(r["ts"]),
            "actor": r["actor"],
            "kind": r["kind"],
            "symbol": payload.get("symbol", ""),
            "side": payload.get("side", ""),
            "qty": payload.get("qty", ""),
            "reason": payload.get("reason", "")[:120],
            "color": _color_for(r["kind"]),
            "payload_pretty": json.dumps(payload, indent=2, default=str, sort_keys=True),
        })
    return out


def _humanize_ts(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        sec = int(delta.total_seconds())
        if sec < 60: return f"{sec}s"
        if sec < 3600: return f"{sec // 60}m"
        if sec < 86400: return f"{sec // 3600}h"
        return f"{sec // 86400}d"
    except Exception:
        return ts[:16]


def _color_for(kind: str) -> str:
    if kind in ("fill", "board_approved", "auto_executed", "scheduled_scan_done"):
        return "text-gain"
    if kind in ("risk_rejected", "board_rejected", "execution_error", "scheduled_scan_error"):
        return "text-loss"
    if kind in ("risk_approved", "scan_order_result"):
        return "text-warn"
    return "text-muted"


# ── Division drill-down view builder ──────────────────────────────────────

def build_ira_view(
    stock_holdings: list[StockHolding],
    legs: list[OptionLeg],
    prices: dict[str, float],
) -> dict:
    """Group IRA positions into 3 buckets — covered calls, pure assets,
    wheel puts. Pure-function over already-fetched data; the caller
    (build_division_view) supplies `stock_holdings`, `legs`, `prices`
    so we don't double-fetch.

    Returns the shape `DivisionViewSnapshot.ira_view` expects:
      {covered_calls: [CoveredCallPosition], pure_assets: [StockHolding],
       wheel_puts: [WheelPutPosition]}.
    """
    # Index shares by underlying for O(1) lookup
    shares_by_symbol: dict[str, StockHolding] = {
        h.symbol.upper(): h for h in stock_holdings
    }

    # Partition option legs
    short_calls_by_underlying: dict[str, list[OptionLeg]] = {}
    short_puts: list[OptionLeg] = []
    for leg in legs:
        if leg.is_long:
            # Longs (LEAPs etc) shouldn't appear in an IRA per the
            # strategy. If they do, leave them out of all 3 buckets —
            # the dashboard's "wheel puts" + "covered calls" sections
            # render only what they understand.
            continue
        if leg.option_type == "call":
            short_calls_by_underlying.setdefault(leg.underlying.upper(), []).append(leg)
        elif leg.option_type == "put":
            short_puts.append(leg)

    # Build covered calls — one CoveredCallPosition per (underlying, short_call).
    # If multiple short calls exist on the same underlying (different
    # strikes / expiries), each becomes its own row but they all share
    # the underlying shares for "coverage" math. We compute coverage
    # against the TOTAL short contracts on that underlying.
    covered_calls: list[CoveredCallPosition] = []
    for underlying, calls in short_calls_by_underlying.items():
        sh = shares_by_symbol.get(underlying)
        if sh is None or sh.qty <= 0:
            # Short call without underlying shares isn't legal in an IRA
            # (the broker would block it), but defensively skip.
            continue
        total_short_qty = sum(abs(c.qty) for c in calls)
        shares_covered = total_short_qty * 100
        coverage_pct = (sh.qty / shares_covered) if shares_covered > 0 else 0.0
        for call in calls:
            cost_basis = sh.avg_price * sh.qty if sh.avg_price else 0.0
            # Filter out the RH crypto cost_basis=0 noise — if avg_price
            # is 0, share P&L is meaningless (RH reports crypto cost
            # basis as 0). Show market value but suppress P&L %.
            if cost_basis > 0 and sh.market_value is not None:
                pnl = sh.market_value - cost_basis
                pnl_pct = pnl / cost_basis if cost_basis > 0 else None
            else:
                pnl = None
                pnl_pct = None
            covered_calls.append(CoveredCallPosition(
                underlying=underlying,
                underlying_price=prices.get(underlying),
                shares_qty=sh.qty,
                shares_avg_price=sh.avg_price,
                shares_market_value=sh.market_value,
                shares_cost_basis=cost_basis,
                shares_pnl=pnl,
                shares_pnl_pct=pnl_pct,
                short_call=call,
                coverage_pct=coverage_pct,
            ))

    # Sort by priority score descending (urgent first), DTE asc as tiebreaker.
    covered_calls.sort(key=lambda cc: (
        -cc.priority_score,
        cc.short_call.dte if cc.short_call.dte is not None else 999,
    ))

    # Portfolio — shares NOT used to back a covered call. Simple list.
    underlyings_with_calls = set(short_calls_by_underlying.keys())
    portfolio: list[StockHolding] = [
        h for h in stock_holdings
        if h.symbol.upper() not in underlyings_with_calls
    ]
    portfolio.sort(key=lambda h: (h.market_value or 0.0), reverse=True)

    # Open puts (no wheel framing — these are just open short puts).
    puts = [WheelPutPosition(short_put=p) for p in short_puts]
    puts.sort(key=lambda w: (
        w.days_to_expiry if w.days_to_expiry is not None else 999,
    ))

    return {
        "covered_calls": covered_calls,
        "portfolio": portfolio,
        "puts": puts,
    }


def build_bitunix_htf_view(deps: Any) -> dict | None:
    """PR 2 — read-only display of the live HTF regime classification.

    Reads `deps.bitunix_htf_provider`, calls its synchronous
    `regime_snapshot()` with default config, and shapes the result for
    the dashboard. Returns None when the provider isn't wired (test
    envs).

    PR 2 ships this purely observationally — the observer does NOT
    consult the same data for trade decisions yet. PR 3 wires the gate
    and adds the `mode: shadow|enforce` flag.

    Shape:
      {
        gate_mode: "off",                         # "off" until PR 3
        regime: "BULL" | ... | "SAFE_MODE",
        composite_score: float,
        h1: {regime, ema_alignment, structure, adx, macd_hist, reason},
        h4: {...},
        d1: {...},
        volatility_tier: str,
        atr_pct_d1: float | None,
        nearest_support: float | None,
        nearest_resistance: float | None,
        distance_to_support_pct: float | None,
        distance_to_resistance_pct: float | None,
        session: str,
        funding_rate: float | None,
        funding_extreme: bool,
        safe_mode_reason: str | None,
        cache_health: {
          h1: {bars, last_close, last_refresh_error},
          h4: {...},
          d1: {...},
        },
      }
    """
    provider = getattr(deps, "bitunix_htf_provider", None)
    if provider is None:
        return None

    from trading_corp.agents.strategies.bitunix_htf_regime import HTFRegimeConfig
    config = HTFRegimeConfig.defaults()

    try:
        verdict = provider.regime_snapshot(config)
    except Exception as e:
        log.warning("HTF regime snapshot failed: %s", e)
        return None

    def _tf_block(tf_class) -> dict:
        return {
            "regime": tf_class.regime.value,
            "ema_alignment": tf_class.ema_alignment,
            "structure": tf_class.structure,
            "ema20": tf_class.ema20,
            "ema50": tf_class.ema50,
            "ema200": tf_class.ema200,
            "adx": tf_class.adx,
            "macd_hist": tf_class.macd_hist,
            "reason": tf_class.reason,
        }

    def _cache_health(cache) -> dict:
        return {
            "bars": len(cache.bars),
            "last_close": cache.bars[-1].close if cache.bars else None,
            "last_refresh_error": cache.last_refresh_error,
        }

    return {
        "gate_mode": "off",                # PR 3 will read from config
        "regime": verdict.regime.value,
        "composite_score": round(verdict.score, 3),
        "h1": _tf_block(verdict.h1),
        "h4": _tf_block(verdict.h4),
        "d1": _tf_block(verdict.d1),
        "volatility_tier": verdict.volatility_tier.value,
        "atr_pct_d1": verdict.atr_pct_d1,
        "nearest_support": verdict.nearest_support,
        "nearest_resistance": verdict.nearest_resistance,
        "distance_to_support_pct": verdict.distance_to_support_pct,
        "distance_to_resistance_pct": verdict.distance_to_resistance_pct,
        "session": verdict.session.value,
        "funding_rate": verdict.funding_rate,
        "funding_extreme": verdict.funding_extreme,
        "safe_mode_reason": verdict.safe_mode_reason,
        "cache_health": {
            "h1": _cache_health(provider.h1_cache),
            "h4": _cache_health(provider.h4_cache),
            "d1": _cache_health(provider.d1_cache),
        },
    }


def build_bitunix_score_view(db_url: str, deps: Any) -> dict | None:
    """Phase 3.2.3 — compose the BitUnix Futures score panel block.

    Reads from the audit log + cooldown table + (when available) the
    live `bitunix_observer`'s bar cache + scoring config. Returns None
    if scoring isn't configured at all.

    Shape:
      {
        scoring_enabled: bool,
        thresholds: {premium, standard, weak, min_fire, cooldown_sec},
        last_eval: {ts, signal, tier, side, net, fb, fs, bc, sc, reason,
                    outcome, age_sec} | None,
        cooldown: [{side, last_fire_ts, last_tier, remaining_sec}],
        recent_evals: [...],
        recent_fires: [{ts, tier, side, qty, entry, stop, tp, net_score}],
        bar_cache: {bars, last_close, atr_14, last_refresh_error} | None,
        ledger_window: {rows_last_24h, oldest_live_signal_age_sec},
      }
    """
    observer = getattr(deps, "bitunix_observer", None)
    scoring = getattr(observer, "scoring_config", None) if observer else None
    if scoring is None:
        return None

    now = datetime.now(timezone.utc)

    last_eval: dict | None = None
    recent_evals: list[dict] = []
    recent_fires: list[dict] = []
    cooldown: list[dict] = []
    ledger_window: dict = {"rows_last_24h": 0, "oldest_live_signal_age_sec": None}

    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='bitunix_score_decided' "
                "ORDER BY ts DESC LIMIT 20"
            ).fetchall()
            for i, r in enumerate(rows):
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    p = {}
                row_ts = r["ts"]
                row_dt = _parse_audit_ts(row_ts)
                age_sec = int((now - row_dt).total_seconds()) if row_dt else None
                entry = {
                    "ts": row_ts,
                    "ts_et": format_et_short(row_ts),
                    "signal": p.get("trigger_signal"),
                    "tier": p.get("tier"),
                    "side": p.get("side"),
                    "net": p.get("net_score"),
                    "fb": p.get("final_buy_score"),
                    "fs": p.get("final_sell_score"),
                    "bg": p.get("buy_guard_penalty"),
                    "sg": p.get("sell_guard_penalty"),
                    "bc": p.get("buy_contributions") or [],
                    "sc": p.get("sell_contributions") or [],
                    "outcome": p.get("outcome"),
                    "reason": p.get("reason"),
                    "cooldown_blocked": p.get("cooldown_blocked", False),
                    "age_sec": age_sec,
                }
                if i == 0:
                    last_eval = entry
                recent_evals.append(entry)

            fire_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='would_have_placed' "
                "ORDER BY ts DESC LIMIT 50"
            ).fetchall()
            for r in fire_rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    p = {}
                if p.get("via") != "bitunix_score":
                    continue
                recent_fires.append({
                    "ts": r["ts"],
                    "ts_et": format_et_short(r["ts"]),
                    "tier": p.get("tier"),
                    "side": p.get("side"),
                    "qty": p.get("qty"),
                    "entry": p.get("entry_price"),
                    "stop": p.get("stop_price"),
                    "tp": p.get("tp_price"),
                    "net_score": p.get("net_score"),
                    "trigger_signal": p.get("trigger_signal"),
                })
                if len(recent_fires) >= 10:
                    break

            try:
                cd_rows = conn.execute(
                    "SELECT side, last_fire_ts, last_tier FROM bitunix_score_cooldown"
                ).fetchall()
                for r in cd_rows:
                    fire_dt = _parse_audit_ts(r["last_fire_ts"])
                    elapsed = (now - fire_dt).total_seconds() if fire_dt else None
                    remaining = max(0, scoring.cooldown_seconds - int(elapsed)) if elapsed is not None else None
                    cooldown.append({
                        "side": r["side"],
                        "last_fire_ts": r["last_fire_ts"],
                        "last_tier": r["last_tier"],
                        "remaining_sec": remaining,
                    })
            except Exception:
                pass  # table may not exist yet on first deploy

            try:
                cutoff = (now - timedelta(hours=24)).isoformat()
                row = conn.execute(
                    "SELECT COUNT(*) AS n, MIN(ts) AS oldest FROM bitunix_signal_ledger WHERE ts >= ?",
                    (cutoff,),
                ).fetchone()
                ledger_window["rows_last_24h"] = int(row["n"] or 0)
                if row["oldest"]:
                    oldest_dt = _parse_audit_ts(row["oldest"])
                    if oldest_dt:
                        ledger_window["oldest_live_signal_age_sec"] = int(
                            (now - oldest_dt).total_seconds()
                        )
            except Exception:
                pass
    except Exception as e:
        log.warning("bitunix score panel query failed: %s", e)

    bar_cache_info: dict | None = None
    bar_cache = getattr(observer, "bar_cache", None) if observer else None
    if bar_cache is not None:
        try:
            status = bar_cache.status()
            bar_cache_info = {
                "bars_cached": status.get("bars_cached"),
                "last_close": status.get("last_close"),
                "atr_14": status.get("atr_14"),
                "last_refresh_error": status.get("last_refresh_error"),
            }
        except Exception as e:
            log.warning("bar_cache.status() failed: %s", e)

    # Compute current live PriceContext for display (best-effort)
    live_pctx: dict | None = None
    try:
        from trading_corp.data.bitunix_price_context import compute_price_context
        ctx = compute_price_context(
            bar_cache,
            sell_on_rush_window_minutes=scoring.sell_on_rush.window_minutes,
            buy_on_fall_window_minutes=scoring.buy_on_fall.window_minutes,
        )
        if ctx is not None:
            live_pctx = {
                "current_price": ctx.current_price,
                "above_session_vwap": ctx.above_session_vwap,
                "below_session_vwap": ctx.below_session_vwap,
                "higher_highs_4h": ctx.higher_highs_4h,
                "lower_lows_4h": ctx.lower_lows_4h,
                "volume_above_20bar_avg": ctx.volume_above_20bar_avg,
                "pct_change_sell": ctx.pct_change_in_window_sell,
                "pct_change_buy": ctx.pct_change_in_window_buy,
            }
    except Exception as e:
        log.warning("live PriceContext for panel failed: %s", e)

    return {
        "scoring_enabled": bool(scoring.enabled),
        "thresholds": {
            "premium": scoring.premium_threshold,
            "standard": scoring.standard_threshold,
            "weak": scoring.weak_threshold,
            "min_fire": scoring.min_score_to_fire,
            "cooldown_sec": scoring.cooldown_seconds,
            "dedupe_within_ttl": scoring.dedupe_within_ttl,
        },
        "last_eval": last_eval,
        "cooldown": cooldown,
        "recent_evals": recent_evals,
        "recent_fires": recent_fires,
        "bar_cache": bar_cache_info,
        "live_pctx": live_pctx,
        "ledger_window": ledger_window,
        "factor_count": len(scoring.factors),
    }


def _parse_audit_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def build_donchian_view(db_url: str) -> dict | None:
    """Compose the `coinbase_btc_donchian` block for the
    coinbase_spot division page.

    Returns None if the strategy isn't configured. Otherwise a dict
    with: state / cost_basis / last_decision_ts / next_bar_ts /
    decisions (per-bar log) / round_trips (realized).

    Phase 2 status: until the live agent is wired in `main.py`, the
    `decisions` and `round_trips` lists will be empty — that's the
    correct empty state. The card scaffolding is ready so the wiring
    deploy immediately surfaces real data.
    """
    import sqlite3
    from datetime import datetime, timezone, timedelta
    from pathlib import Path
    import yaml

    # Load YAML to confirm strategy is configured + read params
    cfg_path = Path("config/strategies.yaml")
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    block = (cfg or {}).get("coinbase_btc_donchian")
    if not block:
        return None
    donchian_params = block.get("donchian", {}) or {}

    # Read state from agent_state table (best-effort)
    state_value: dict | None = None
    last_bar_ts: str | None = None
    db_path = db_url.replace("sqlite:///", "")
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value_json FROM agent_state "
                "WHERE agent='coinbase_btc_donchian' AND key='state'",
            ).fetchone()
            if row:
                import json
                state_value = json.loads(row[0])
            row2 = conn.execute(
                "SELECT value_json FROM agent_state "
                "WHERE agent='coinbase_btc_donchian' AND key='last_bar_ts'",
            ).fetchone()
            if row2:
                import json
                last_bar_ts = json.loads(row2[0]).get("ts")
    except Exception as e:
        log.debug("donchian state read failed: %s", e)

    # Compute next 6h-bar boundary (00:00 / 06:00 / 12:00 / 18:00 UTC)
    now = datetime.now(timezone.utc)
    granularity_sec = int(donchian_params.get("granularity_seconds", 21600))
    bucket_hour = (now.hour // (granularity_sec // 3600)) * (granularity_sec // 3600)
    next_bar = now.replace(hour=bucket_hour, minute=0, second=0, microsecond=0) \
        + timedelta(seconds=granularity_sec)
    countdown = next_bar - now
    hours, remainder = divmod(int(countdown.total_seconds()), 3600)
    minutes = remainder // 60
    countdown_str = f"{hours}h {minutes}m"

    # Per-bar decision log. Two row kinds interleaved chronologically:
    #   - `donchian_evaluated`: per-bar SKIP/BUY/SELL evaluation with
    #     channel snapshot + reason text.
    #   - `balance_change`: Board-driven cash/BTC delta detected at the
    #     start of a bar (recurring deposits, manual purchases). State
    #     is NOT auto-flipped — this is observation only.
    # Both kinds emit from actor=`coinbase_btc_donchian`, so a single
    # query captures both and the template branches on `kind`.
    decisions: list[dict] = []
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT ts, kind, payload_json FROM audit_event "
                "WHERE actor='coinbase_btc_donchian' "
                "AND kind IN ('donchian_evaluated','balance_change') "
                "ORDER BY ts DESC LIMIT 60",
            )
            import json
            for r in cur.fetchall():
                p = json.loads(r["payload_json"])
                if r["kind"] == "balance_change":
                    # Pin to the bar's open time so this row aligns
                    # visually with the sibling donchian_evaluated row
                    # from the same evaluation cycle. Falls back to
                    # the audit-row write time for legacy rows that
                    # pre-date the bar_ts stamp (orchestrator started
                    # writing it 2026-05-10 to fix the cosmetic
                    # 6h-visual-gap that made same-cycle rows read
                    # as independent events).
                    decisions.append({
                        "kind": "balance_change",
                        "ts": r["ts"],
                        "ts_short": format_et_short(p.get("bar_ts") or r["ts"]),
                        "attribution": p.get("attribution", "board"),
                        "state_at_observation": p.get("state_at_observation", "?"),
                        "delta_cash": p.get("delta_cash"),
                        "delta_btc": p.get("delta_btc"),
                        "new_cash": p.get("new_cash"),
                        "new_btc_qty": p.get("new_btc_qty"),
                    })
                else:
                    decisions.append({
                        "kind": "donchian_evaluated",
                        "ts": r["ts"],
                        # Display the bar's open time (canonical bar identifier,
                        # matches the timestamp embedded in `reason`), not the
                        # audit-row write time which is bar close + ~2 min.
                        "ts_short": format_et_short(p.get("bar_ts") or r["ts"]),
                        "decision": p.get("decision", "skip"),
                        "current_close": p.get("current_close"),
                        "donchian_high": p.get("donchian_high"),
                        "donchian_low": p.get("donchian_low"),
                        "trend_filter_sma": p.get("trend_filter_sma"),
                        "trend_filter_passed": p.get("trend_filter_passed", False),
                        "reason": p.get("reason", ""),
                    })
    except Exception as e:
        log.debug("donchian decisions read failed: %s", e)

    # Realized round-trips: pair BUY → SELL fills via the strategy
    # tag. We use `would_have_placed` (paper) + `filled` (live) audit
    # kinds, both filtered to strategy='coinbase_btc_donchian'. The
    # round-trip pairs SELLs to the most recent prior BUY.
    round_trips: list[dict] = []
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT ts, kind, payload_json FROM audit_event "
                "WHERE actor='coinbase_btc_donchian' "
                "AND kind IN ('would_have_placed','filled') "
                "ORDER BY ts ASC",
            )
            import json
            open_buy: dict | None = None
            for r in cur.fetchall():
                p = json.loads(r["payload_json"])
                side = p.get("side")
                price = p.get("fill_price") or p.get("price") or p.get("limit_price")
                qty = p.get("qty")
                if side == "buy" and open_buy is None:
                    open_buy = {
                        "buy_ts": r["ts"],
                        "buy_price": price,
                        "qty": qty,
                    }
                elif side == "sell" and open_buy is not None:
                    realized = (
                        ((price or 0) - (open_buy["buy_price"] or 0))
                        * (qty or open_buy["qty"] or 0)
                    )
                    round_trips.append({
                        "buy_ts": open_buy["buy_ts"],
                        "buy_ts_short": format_et_short(open_buy["buy_ts"]),
                        "buy_price": open_buy["buy_price"],
                        "sell_ts": r["ts"],
                        "sell_ts_short": format_et_short(r["ts"]),
                        "sell_price": price,
                        "qty": qty or open_buy["qty"],
                        "realized_pnl": realized,
                        "pct_return": (
                            ((price - open_buy["buy_price"]) / open_buy["buy_price"] * 100)
                            if open_buy["buy_price"] else None
                        ),
                    })
                    open_buy = None
    except Exception as e:
        log.debug("donchian trades read failed: %s", e)

    return {
        "enabled": bool(block.get("enabled", False)),
        "auto_execute": bool(block.get("auto_execute", False)),
        "state": (state_value or {}).get("state", "cash"),
        "cost_basis": (state_value or {}).get("cost_basis"),
        "last_bar_ts": last_bar_ts,
        "last_bar_short": format_et_short(last_bar_ts) if last_bar_ts else None,
        "next_bar_ts": next_bar.isoformat(),
        "next_bar_short": format_et_hm(next_bar),
        "next_bar_countdown": countdown_str,
        "donchian": {
            "entry_lookback": donchian_params.get("entry_lookback"),
            "exit_lookback": donchian_params.get("exit_lookback"),
            "trend_filter_lookback": donchian_params.get("trend_filter_lookback"),
            "granularity_hours": granularity_sec // 3600,
        },
        "decisions": decisions,                  # most recent first; max 60
        "round_trips": list(reversed(round_trips)),   # most recent first
    }


async def build_donchian_chart_data(db_url: str, display_bars: int = 50) -> dict | None:
    """OHLCV + Donchian band overlays + fill markers for the
    `/division/coinbase_spot` chart tile.

    Fetches recent 6h bars via Coinbase's public ccxt endpoint (the same
    path the strategy orchestrator uses), computes the rolling 20-bar
    high / 6-bar low / 168-bar SMA exactly as `donchian_btc.evaluate`
    does (preceding-window — current bar excluded), and pairs in
    BUY/SELL fills from `audit_event` so they render as markers on the
    chart at the right timestamp.

    Returns None if the strategy isn't configured, the OHLCV fetch
    fails, or there aren't enough bars to satisfy any lookback. The
    caller should treat None as "show empty state."
    """
    import sqlite3
    from pathlib import Path
    import yaml

    cfg_path = Path("config/strategies.yaml")
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    block = (cfg or {}).get("coinbase_btc_donchian")
    if not block:
        return None
    dp = block.get("donchian", {}) or {}
    entry_lookback = int(dp.get("entry_lookback", 20))
    exit_lookback = int(dp.get("exit_lookback", 6))
    sma_lookback = int(dp.get("trend_filter_lookback", 168))
    symbol = block.get("symbol", "BTC/USD")

    # OHLCV fetch — async ccxt against Coinbase public endpoint. Pull
    # enough history that the SMA(168) window has data on the oldest
    # display bar (display_bars + sma_lookback).
    fetch_limit = display_bars + sma_lookback + 5
    try:
        import ccxt.async_support as ccxt_async
        exchange = ccxt_async.coinbase({"enableRateLimit": True})
        try:
            raw = await exchange.fetch_ohlcv(symbol, timeframe="6h", limit=fetch_limit)
        finally:
            await exchange.close()
    except Exception as e:
        log.warning("donchian chart: OHLCV fetch failed: %s", e)
        return None

    granularity_sec = 6 * 3600
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bars: list[dict] = []
    for row in raw or []:
        ts_ms, o, h, l, c, v = row
        # Drop any in-progress bar (close still in the future). Mirrors
        # the orchestrator's `_fetch_recent_btc_6h_bars` filter.
        if int(ts_ms) + granularity_sec * 1000 > now_ms:
            continue
        bars.append({
            "ts": int(ts_ms) // 1000,   # unix-seconds — Lightweight Charts time
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v),
        })
    if len(bars) < max(entry_lookback, exit_lookback) + 1:
        return None

    # Rolling Donchian high/low/SMA — preceding-window semantics
    # (excludes current bar) to mirror donchian_btc.evaluate.
    n = len(bars)
    donchian_high: list[float | None] = [None] * n
    donchian_low: list[float | None] = [None] * n
    sma: list[float | None] = [None] * n
    for i in range(n):
        if i >= entry_lookback:
            donchian_high[i] = max(b["high"] for b in bars[i - entry_lookback:i])
        if i >= exit_lookback:
            donchian_low[i] = min(b["low"] for b in bars[i - exit_lookback:i])
        if i >= sma_lookback:
            sma[i] = sum(b["close"] for b in bars[i - sma_lookback:i]) / sma_lookback

    # Trim to the display window (most-recent N bars).
    start = max(0, n - display_bars)
    candles = [
        {
            "time": b["ts"],
            "open": b["open"], "high": b["high"],
            "low": b["low"], "close": b["close"],
        }
        for b in bars[start:]
    ]
    high_line = [
        {"time": bars[i]["ts"], "value": donchian_high[i]}
        for i in range(start, n) if donchian_high[i] is not None
    ]
    low_line = [
        {"time": bars[i]["ts"], "value": donchian_low[i]}
        for i in range(start, n) if donchian_low[i] is not None
    ]
    sma_line = [
        {"time": bars[i]["ts"], "value": sma[i]}
        for i in range(start, n) if sma[i] is not None
    ]

    # Fill markers — BUY/SELL events from the strategy's audit log.
    # `would_have_placed` (paper mode) and `filled` (live) both qualify;
    # snap each to its bar's open time so the marker lines up cleanly
    # with the candle, not 2-3 minutes downstream of bar close.
    markers: list[dict] = []
    db_path = db_url.replace("sqlite:///", "")
    visible_ts = {c["time"] for c in candles}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT ts, kind, payload_json FROM audit_event "
                "WHERE actor='coinbase_btc_donchian' "
                "AND kind IN ('would_have_placed','filled') "
                "ORDER BY ts ASC",
            )
            for r in cur.fetchall():
                p = json.loads(r["payload_json"])
                side = (p.get("side") or "").lower()
                if side not in ("buy", "sell"):
                    continue
                # Snap to bar open: prefer payload bar_ts (set by the
                # orchestrator on `donchian_evaluated`; some order rows
                # also have it). Fall back to the audit row's `ts`
                # quantized to the nearest preceding 6h boundary.
                bar_ts_iso = p.get("bar_ts")
                if bar_ts_iso:
                    try:
                        bar_unix = int(datetime.fromisoformat(bar_ts_iso).timestamp())
                    except Exception:
                        bar_unix = None
                else:
                    bar_unix = None
                if bar_unix is None:
                    try:
                        ts_dt = datetime.fromisoformat(r["ts"])
                        if ts_dt.tzinfo is None:
                            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                        u = int(ts_dt.timestamp())
                        bar_unix = u - (u % granularity_sec)
                    except Exception:
                        continue
                if bar_unix not in visible_ts:
                    continue
                markers.append({
                    "time": bar_unix,
                    "side": side,
                    "price": p.get("fill_price") or p.get("price") or p.get("limit_price"),
                    "qty": p.get("qty"),
                })
    except Exception as e:
        log.debug("donchian chart: markers read failed: %s", e)

    # The "current" bar is the most recent fully-closed bar — same
    # one the strategy will evaluate next. Exposed so the chart JS
    # can highlight it.
    current_bar_ts = candles[-1]["time"] if candles else None

    return {
        "symbol": symbol,
        "entry_lookback": entry_lookback,
        "exit_lookback": exit_lookback,
        "sma_lookback": sma_lookback,
        "candles": candles,
        "donchian_high": high_line,
        "donchian_low": low_line,
        "sma": sma_line,
        "markers": markers,
        "current_bar_ts": current_bar_ts,
    }


async def build_division_view(deps, slug: str) -> DivisionViewSnapshot | None:
    """Fan out everything needed for the /division/{slug} page.

    Returns None if the slug isn't a known division. Otherwise returns a
    DivisionViewSnapshot ready to render. The LLM analysis is fetched
    separately by /division/{slug}/llm-analysis (lazy via HTMX) so this
    builder can stay fast — multi-LLM-call analysis would block the page
    paint by 10-15s otherwise.
    """
    divs = load_divisions()
    division = next((d for d in divs if d.slug == slug), None)
    if division is None:
        return None

    broker = (
        getattr(deps.data_exec, "brokers", {}).get(slug)
        if deps.data_exec is not None
        else None
    )

    # Parallel fetches
    snap_task = _safe_call(broker, "snapshot") if broker else None
    opts_task = (
        _safe_call(broker, "get_option_positions_detail")
        if broker and hasattr(broker, "get_option_positions_detail")
        else None
    )

    snap, raw_opts = await asyncio.gather(
        snap_task or _none(),
        opts_task or _none(),
        return_exceptions=True,
    )
    if isinstance(snap, Exception):
        log.warning("division_view: snapshot for %s failed: %s", slug, snap)
        snap = None
    if isinstance(raw_opts, Exception):
        log.warning("division_view: opts for %s failed: %s", slug, raw_opts)
        raw_opts = []
    raw_opts = raw_opts or []

    equity = _equity_from_snap(snap)
    buying_power = (
        getattr(snap, "buying_power", None) if snap is not None
        else None
    )
    cash = getattr(snap, "cash", None) if snap is not None else None
    snap_positions = _positions_from_snap(snap)

    # Fetch underlying prices for every option's underlying + every stock symbol
    underlyings = sorted({
        op.get("chain_symbol", "")
        for op in raw_opts if op.get("chain_symbol")
    })
    stock_symbols = sorted({
        p.symbol for p in snap_positions
        if " " not in p.symbol and "#" not in p.symbol
    })
    all_syms = list({*underlyings, *stock_symbols})
    prices = await _fetch_prices_async(all_syms) if all_syms else {}

    # Build stock holdings
    stock_holdings: list[StockHolding] = []
    for p in snap_positions:
        if " " in p.symbol or "#" in p.symbol:
            continue
        last = prices.get(p.symbol)
        mv = (last * p.qty) if (last is not None and p.qty) else None
        cost = p.avg_price * p.qty
        pnl = (mv - cost) if (mv is not None) else None
        pnl_pct = (pnl / cost) if (pnl is not None and cost > 0) else None
        stock_holdings.append(StockHolding(
            symbol=p.symbol,
            qty=p.qty,
            avg_price=p.avg_price,
            last=last,
            market_value=mv,
            unrealized_pnl=pnl,
            unrealized_pnl_pct=pnl_pct,
            day_change_pct=None,    # filled in by caller if benchmark/quote available
        ))

    # Build option legs from get_option_positions_detail
    legs: list[OptionLeg] = []
    for op in raw_opts:
        chain_symbol = (op.get("chain_symbol") or "").upper()
        # Robinhood signs avg_price: positive for longs (cost paid), negative
        # for shorts (credit received). We invariant this to always-positive
        # at construction — see OptionLeg class docstring for why. Direction
        # lives on `qty` alone.
        raw_avg_per_share = float(op.get("avg_price") or 0) / 100.0  # contract → share
        legs.append(OptionLeg(
            underlying=chain_symbol,
            option_type=(op.get("option_type") or "call").lower(),
            expiry=op.get("expiration_date") or "",
            strike=float(op.get("strike_price") or 0),
            dte=op.get("dte"),
            qty=float(op.get("quantity") or 0),
            avg_per_share=abs(raw_avg_per_share),
            mark_per_share=op.get("mark_price"),
            delta=op.get("delta"),
            underlying_price=prices.get(chain_symbol),
        ))

    # Group into PMCC pairs: per underlying, pick the longest-DTE long call
    # as the LEAP and the nearest-DTE short call as the short leg.
    pmcc_pairs, other_options = _group_pmcc_pairs(legs, prices)

    # Activity feed for this division
    activity = _query_division_activity(deps.db_url, slug, division.strategy, limit=20)

    # Equity curve from account_state — best-effort; only populated if
    # PortfolioAgent (or similar) periodically logs account_state for this
    # account. Returns empty list if no rows.
    equity_curve = _query_account_equity_curve(deps.db_url, slug, days=30)

    # Today's P&L: best-effort against most-recent prior account_state row
    todays_pnl, todays_pnl_pct = _approx_todays_pnl(equity_curve, equity)

    # Paper-trade win-rate panel (Phase C). Cheap query, only meaningful
    # for divisions that emit `would_have_placed` rows (Otter / Cypher
    # today on `coinbase_spot`). Other divisions return zeros silently.
    try:
        pt_summary = paper_trade_summary(deps.db_url, slug)
    except Exception as e:
        log.warning("paper_trade_summary for %s failed: %s", slug, e)
        pt_summary = None

    # Coinbase BTC Donchian view block — populated only for the
    # coinbase_spot division and only if the strategy is configured
    # in strategies.yaml. Tiles render empty states until the live
    # agent is wired in main.py and starts writing audit rows.
    donchian_view: dict | None = None
    if slug == "coinbase_spot":
        try:
            donchian_view = build_donchian_view(deps.db_url)
        except Exception as e:
            log.warning("donchian view for %s failed: %s", slug, e)

    # BitUnix Phase 3.2.3 score panel — only populated for
    # `bitunix_futures`. Reads from audit log + bitunix_score_cooldown
    # + the live observer's bar cache. Returns None if scoring config
    # is unavailable (observer not wired, or YAML scoring block missing).
    bitunix_score_view: dict | None = None
    bitunix_htf_view: dict | None = None
    if slug == "bitunix_futures":
        try:
            bitunix_score_view = build_bitunix_score_view(deps.db_url, deps)
        except Exception as e:
            log.warning("bitunix score view for %s failed: %s", slug, e)
        try:
            bitunix_htf_view = build_bitunix_htf_view(deps)
        except Exception as e:
            log.warning("bitunix HTF view for %s failed: %s", slug, e)

    # Robinhood IRA dashboard — group shares + short calls into covered
    # calls, identify pure assets (shares without calls), surface short
    # puts as wheel positions. Reuses the stock_holdings + legs already
    # built above so no double-fetch. Renders only when slug matches.
    ira_view_block: dict | None = None
    if slug == "robinhood_ira":
        try:
            ira_view_block = build_ira_view(stock_holdings, legs, prices)
        except Exception as e:
            log.warning("ira view for %s failed: %s", slug, e)

    return DivisionViewSnapshot(
        division=division,
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        todays_pnl=todays_pnl,
        todays_pnl_pct=todays_pnl_pct,
        stock_holdings=stock_holdings,
        pmcc_pairs=pmcc_pairs,
        other_options=other_options,
        recent_activity=activity,
        equity_curve=equity_curve,
        paper_trade_summary=pt_summary,
        donchian=donchian_view,
        bitunix_score=bitunix_score_view,
        ira_view=ira_view_block,
        bitunix_htf=bitunix_htf_view,
    )


# ── Prediction-markets dashboard (K2.4 Option C) ──────────────────────────
#
# Single dashboard parameterized by division (or None for "All Prediction
# Markets" combined view). One template, one route, one data builder; venue
# (polymarket vs kalshi) inferred per division. Round-trip + equity-snapshot
# rows from both venues normalized into a common shape so the template
# doesn't branch on venue.

@dataclass
class PMRoundTrip:
    """One resolved paper trade, normalized across venues."""
    order_id: str
    venue: str                       # 'polymarket' | 'kalshi'
    division: str
    strategy: str                    # e.g. 'kalshi_llm_arbitrage', 'polymarket_arbitrage'
    market_title: str                # market_question (poly) or event_title (kalshi)
    market_id: str                   # slug (poly) or ticker (kalshi)
    category: str | None
    outcome_bet: str                 # 'yes' | 'no'
    qty: float
    entry_price: float
    notional: float
    entry_ts: str
    resolved_ts: str
    market_result: str               # 'yes' | 'no' | 'void'
    won: int                         # 0|1
    realized_pnl: float
    roi_pct: float
    implied_at_entry: float | None
    llm_prob: float | None
    divergence_pct: float | None
    arb_type: str | None             # kalshi only; None for polymarket
    # Analysis fields parsed from extra_json (kalshi) — None if absent for
    # legacy rows pre-resolver-enrichment.
    rationale: str | None
    llm_reasoning: str | None
    key_unknowns: list[str]
    llm_confidence: str | None
    subtitle: str | None
    whale_handle: str | None = None  # copy_trader rows only — see PMOpenTrade


@dataclass
class PMOpenTrade:
    """A would_have_placed paper trade still awaiting market resolution.
    Sourced from `audit_event` rows that have no corresponding
    {polymarket,kalshi}_round_trips row yet."""
    order_id: str
    venue: str                       # 'polymarket' | 'kalshi'
    division: str
    strategy: str
    emit_ts: str                     # audit-event ts
    market_title: str
    market_id: str
    category: str | None
    outcome_bet: str                 # 'yes' | 'no'
    qty: float
    entry_price: float
    notional: float
    divergence_pct: float | None     # llm strategies only
    edge_cents: float | None         # structural strategies only
    arb_type: str | None             # kalshi only
    resolves_at: str | None          # ISO; expires_at (kalshi) / resolves_at (polymarket)
    age_hours: float                 # convenience for template; computed at query time
    # Analysis fields surfaced by the expandable row UI.
    rationale: str | None            # short one-liner; every strategy emits this
    llm_reasoning: str | None        # full LLM analysis text (LLM strategies only)
    key_unknowns: list[str]          # LLM-identified gaps in reasoning
    llm_confidence: str | None       # 'low' | 'medium' | 'high'
    subtitle: str | None             # kalshi yes/no sub-title (e.g. "-1° or below")
    leg_date: str | None             # temporal arb leg date
    # Copy-trader-specific: source whale's handle/wallet so the SIGNAL
    # column can render `@whale` instead of N/A for copy rows. None for
    # arb-family strategies. Normalized: whale_user_name (PM payload)
    # and whale_handle (K3 payload) both surface as this field.
    whale_handle: str | None = None
    side_detection_confidence: str | None = None


@dataclass
class PMEquityPoint:
    """One equity snapshot, normalized. In All-mode, multiple divisions at
    the same ts are summed BEFORE this dataclass is built."""
    ts: str
    division: str | None             # None when in All-mode (aggregated)
    equity: float
    cash: float
    positions_value: float


@dataclass
class PMSummary:
    """Aggregate cards rendered above the tabs."""
    current_equity: float | None
    todays_pnl: float | None
    todays_pnl_pct: float | None
    n_resolved: int
    n_pending: int                   # would_have_placed without round-trip row
    n_wins: int
    n_losses: int
    n_voids: int
    win_rate_pct: float | None       # over (wins+losses), voids excluded
    total_realized_pnl: float


@dataclass
class PMDivisionOption:
    """Dropdown entry."""
    slug: str
    display_name: str
    venue: str                       # 'polymarket' | 'kalshi'


@dataclass
class PMDashboardView:
    """Everything the prediction_markets_dashboard.html template needs."""
    selected: str | None             # None == 'All Prediction Markets'
    selected_label: str
    available_divisions: list[PMDivisionOption]
    summary: PMSummary
    equity_curve: list[PMEquityPoint]
    round_trips: list[PMRoundTrip]   # most-recent first
    open_trades: list[PMOpenTrade]   # most-recent emit first


_POLYMARKET_PREFIX = "polymarket_"
_KALSHI_PREFIX = "kalshi_"


def _pm_venue(slug: str) -> str:
    if slug.startswith(_KALSHI_PREFIX):
        return "kalshi"
    return "polymarket"   # default; polymarket_* slugs fall here


def _pm_divisions_all() -> list[Division]:
    """Active prediction-market divisions from divisions.yaml, in declared order."""
    from trading_corp.utils.divisions import classify_investment_type
    return [
        d for d in load_divisions()
        if classify_investment_type(d) == "prediction_markets"
    ]


def _query_pm_round_trips(
    db_url: str, division_slugs: list[str], limit: int,
) -> list[PMRoundTrip]:
    """Pull round-trip rows from both venue tables, filter to the selected
    divisions, normalize, sort by resolved_ts DESC, cap to `limit`.

    Two SELECTs (polymarket + kalshi) UNIONed in Python — keeps the SQL
    simple, schema differences explicit, and lets each side use its own
    indexes.
    """
    if not division_slugs:
        return []
    out: list[PMRoundTrip] = []

    # polymarket_round_trips gained a `division` column 2026-05-11 when the
    # polymarket_copy_trader strategy shipped — resolver now stamps it as
    # 'polymarket_arbitrage' or 'polymarket_copy_trading' per the producing
    # actor. Legacy pre-column rows may be NULL; COALESCE treats them as
    # arbitrage (their historical origin) so no backfill migration is needed.
    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        poly_ph = ",".join("?" for _ in poly_slugs)
        poly_rows = _query(
            db_url,
            f"SELECT order_id, condition_id, slug, market_question, category, "
            f"       outcome_bet, qty, entry_price, notional, entry_ts, "
            f"       resolved_ts, yes_won, won, realized_pnl, roi_pct, "
            f"       implied_at_entry, llm_prob, divergence_pct, extra_json, "
            f"       COALESCE(division, 'polymarket_arbitrage') AS division "
            f"FROM polymarket_round_trips "
            f"WHERE COALESCE(division, 'polymarket_arbitrage') IN ({poly_ph}) "
            f"ORDER BY resolved_ts DESC LIMIT ?",
            (*poly_slugs, limit),
        )
        for r in poly_rows:
            yes_won = int(r.get("yes_won") or 0)
            div = str(r.get("division") or "polymarket_arbitrage")
            strat = (
                "polymarket_copy_trader" if div == "polymarket_copy_trading"
                else "polymarket_arbitrage"
            )
            # extra_json carries whale-closed override + rationale + whale handle
            # (resolver writes it for whale-closed rows; older market-settle rows
            # don't have it but query still returns NULL cleanly).
            try:
                extra = json.loads(r.get("extra_json") or "{}")
            except (TypeError, ValueError):
                extra = {}
            mr = extra.get("market_result")
            if mr == "whale_closed":
                market_result = "whale_closed"
            else:
                market_result = "yes" if yes_won else "no"
            out.append(PMRoundTrip(
                order_id=str(r.get("order_id") or ""),
                venue="polymarket",
                division=div,
                strategy=strat,
                market_title=str(r.get("market_question") or r.get("slug") or ""),
                market_id=str(r.get("slug") or r.get("condition_id") or ""),
                category=r.get("category"),
                outcome_bet=str(r.get("outcome_bet") or ""),
                qty=float(r.get("qty") or 0.0),
                entry_price=float(r.get("entry_price") or 0.0),
                notional=float(r.get("notional") or 0.0),
                entry_ts=str(r.get("entry_ts") or ""),
                resolved_ts=str(r.get("resolved_ts") or ""),
                market_result=market_result,
                won=int(r.get("won") or 0),
                realized_pnl=float(r.get("realized_pnl") or 0.0),
                roi_pct=float(r.get("roi_pct") or 0.0),
                implied_at_entry=(
                    float(r["implied_at_entry"]) if r.get("implied_at_entry") is not None
                    else None
                ),
                llm_prob=(
                    float(r["llm_prob"]) if r.get("llm_prob") is not None else None
                ),
                divergence_pct=(
                    float(r["divergence_pct"]) if r.get("divergence_pct") is not None
                    else None
                ),
                arb_type=None,
                # Whale-closed rows carry exit rationale + whale handle.
                # Market-settle rows still have these as None.
                rationale=extra.get("rationale_exit") or extra.get("rationale"),
                llm_reasoning=None,
                key_unknowns=[],
                llm_confidence=None,
                subtitle=None,
                whale_handle=extra.get("whale_user_name"),
            ))

    # kalshi_round_trips DOES have a division column (one table covers all 3
    # kalshi strategies across both kalshi_arbitrage and kalshi_llm_arbitrage).
    kalshi_slugs = [s for s in division_slugs if s.startswith(_KALSHI_PREFIX)]
    if kalshi_slugs:
        kalshi_ph = ",".join("?" for _ in kalshi_slugs)
        kalshi_rows = _query(
            db_url,
            f"SELECT order_id, ticker, event_ticker, event_title, category, "
            f"       strategy, division, arb_type, arb_set_id, outcome_bet, "
            f"       qty, entry_price, notional, entry_ts, resolved_ts, "
            f"       market_result, won, realized_pnl, roi_pct, "
            f"       implied_at_entry, llm_prob, divergence_pct, edge_cents, "
            f"       extra_json "
            f"FROM kalshi_round_trips "
            f"WHERE division IN ({kalshi_ph}) "
            f"ORDER BY resolved_ts DESC LIMIT ?",
            (*kalshi_slugs, limit),
        )
        for r in kalshi_rows:
            # Parse extra_json for the analysis fields. Older rows may not
            # have llm_reasoning / key_unknowns (resolver enrichment landed
            # 2026-05-11 ~05:30 UTC); we default cleanly.
            try:
                extra = json.loads(r.get("extra_json") or "{}")
            except (TypeError, ValueError):
                extra = {}
            key_unknowns = extra.get("key_unknowns")
            if not isinstance(key_unknowns, list):
                key_unknowns = []
            out.append(PMRoundTrip(
                order_id=str(r.get("order_id") or ""),
                venue="kalshi",
                division=str(r.get("division") or ""),
                strategy=str(r.get("strategy") or ""),
                market_title=str(r.get("event_title") or r.get("ticker") or ""),
                market_id=str(r.get("ticker") or ""),
                category=r.get("category"),
                outcome_bet=str(r.get("outcome_bet") or ""),
                qty=float(r.get("qty") or 0.0),
                entry_price=float(r.get("entry_price") or 0.0),
                notional=float(r.get("notional") or 0.0),
                entry_ts=str(r.get("entry_ts") or ""),
                resolved_ts=str(r.get("resolved_ts") or ""),
                market_result=str(r.get("market_result") or ""),
                won=int(r.get("won") or 0),
                realized_pnl=float(r.get("realized_pnl") or 0.0),
                roi_pct=float(r.get("roi_pct") or 0.0),
                implied_at_entry=(
                    float(r["implied_at_entry"]) if r.get("implied_at_entry") is not None
                    else None
                ),
                llm_prob=(
                    float(r["llm_prob"]) if r.get("llm_prob") is not None else None
                ),
                divergence_pct=(
                    float(r["divergence_pct"]) if r.get("divergence_pct") is not None
                    else None
                ),
                arb_type=r.get("arb_type"),
                # Whale-closed K3 rows: prefer exit rationale; otherwise
                # the regular rationale (or LLM-arbitrage's structural one).
                rationale=extra.get("rationale_exit") or extra.get("rationale"),
                llm_reasoning=extra.get("llm_reasoning"),
                key_unknowns=key_unknowns,
                llm_confidence=extra.get("llm_confidence"),
                subtitle=extra.get("subtitle"),
                whale_handle=extra.get("whale_handle"),
            ))

    out.sort(key=lambda rt: rt.resolved_ts, reverse=True)
    return out[:limit]


def _query_pm_equity_curve(
    db_url: str, division_slugs: list[str], days: int,
) -> list[PMEquityPoint]:
    """Equity-history points for the selected divisions over `days` of
    history. In All-mode (multiple selected slugs) we DON'T sum here —
    return raw per-division points and let the chart layer aggregate.
    """
    if not division_slugs:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: list[PMEquityPoint] = []

    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        poly_ph = ",".join("?" for _ in poly_slugs)
        poly_rows = _query(
            db_url,
            f"SELECT ts, division, equity, cash_usdc, positions_value "
            f"FROM polymarket_equity_history "
            f"WHERE ts >= ? AND division IN ({poly_ph}) "
            f"ORDER BY ts ASC",
            (cutoff, *poly_slugs),
        )
        for r in poly_rows:
            out.append(PMEquityPoint(
                ts=str(r.get("ts") or ""),
                division=str(r.get("division") or ""),
                equity=float(r.get("equity") or 0.0),
                cash=float(r.get("cash_usdc") or 0.0),
                positions_value=float(r.get("positions_value") or 0.0),
            ))

    kalshi_slugs = [s for s in division_slugs if s.startswith(_KALSHI_PREFIX)]
    if kalshi_slugs:
        kalshi_ph = ",".join("?" for _ in kalshi_slugs)
        kalshi_rows = _query(
            db_url,
            f"SELECT ts, division, equity, cash_usd, positions_value "
            f"FROM kalshi_equity_history "
            f"WHERE ts >= ? AND division IN ({kalshi_ph}) "
            f"ORDER BY ts ASC",
            (cutoff, *kalshi_slugs),
        )
        for r in kalshi_rows:
            out.append(PMEquityPoint(
                ts=str(r.get("ts") or ""),
                division=str(r.get("division") or ""),
                equity=float(r.get("equity") or 0.0),
                cash=float(r.get("cash_usd") or 0.0),
                positions_value=float(r.get("positions_value") or 0.0),
            ))

    out.sort(key=lambda p: p.ts)
    return out


def _query_pm_open_trades(
    db_url: str, division_slugs: list[str], limit: int = 200,
) -> list[PMOpenTrade]:
    """Pull would_have_placed audit rows that have no round-trip resolution
    yet, normalize across venues, sort by emit ts DESC, cap to `limit`.

    These are the live paper positions — the trades the dashboard's
    "OPEN" tab visualizes. Cross-venue UNION mirrors _query_pm_round_trips.
    """
    if not division_slugs:
        return []
    out: list[PMOpenTrade] = []
    now = datetime.now(timezone.utc)

    def _age_hours(ts: str) -> float:
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return max(0.0, (now - t).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            return 0.0

    # Polymarket open trades — actor IN (arbitrage, copy_trader); filter on
    # payload.division so a single-division dashboard view doesn't bleed
    # rows from the sibling polymarket division. Also exclude:
    #   - Audit rows linked as the entry leg of a paired round-trip
    #     (entry_order_id IS NOT NULL) — those are resolved via whale-exit
    #     pairing, not pending.
    #   - SELL-side audit rows — they're closing actions, not "open"
    #     positions; pairing surfaces them via the History tab instead.
    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        poly_ph = ",".join("?" for _ in poly_slugs)
        rows = _query(
            db_url,
            f"SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            f"FROM audit_event a "
            f"LEFT JOIN polymarket_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.actor IN ('polymarket_arbitrage', 'polymarket_copy_trader') "
            f"  AND a.kind = 'would_have_placed' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.division'), 'polymarket_arbitrage') IN ({poly_ph}) "
            f"  AND r.order_id IS NULL "
            f"  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            f"    SELECT entry_order_id FROM polymarket_round_trips "
            f"    WHERE entry_order_id IS NOT NULL"
            f"  ) "
            f"ORDER BY a.ts DESC LIMIT ?",
            (*poly_slugs, limit),
        )
        for r in rows:
            try:
                p = json.loads(r["payload_json"])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            actor = r["actor"] or ""
            div = str(p.get("division") or "polymarket_arbitrage")
            qty = float(p.get("qty") or 0.0)
            price = float(p.get("limit_price") or 0.0)
            ku = p.get("key_unknowns")
            if not isinstance(ku, list):
                ku = []
            out.append(PMOpenTrade(
                order_id=str(p.get("order_id") or ""),
                venue="polymarket",
                division=div,
                strategy=actor or "polymarket_arbitrage",
                whale_handle=(
                    str(p["whale_user_name"]) if p.get("whale_user_name") else None
                ),
                emit_ts=str(r["ts"] or ""),
                market_title=str(
                    p.get("market_question")
                    or p.get("market_title")
                    or p.get("market_slug")
                    or ""
                ),
                market_id=str(p.get("market_slug") or p.get("condition_id") or ""),
                category=p.get("category"),
                outcome_bet=str(p.get("outcome") or ""),
                qty=qty,
                entry_price=price,
                notional=qty * price,
                divergence_pct=(
                    float(p["divergence_pct"]) if p.get("divergence_pct") is not None
                    else None
                ),
                edge_cents=None,
                arb_type=None,
                resolves_at=p.get("resolves_at"),
                age_hours=_age_hours(r["ts"] or ""),
                rationale=p.get("rationale"),
                llm_reasoning=p.get("llm_reasoning"),
                key_unknowns=ku,
                llm_confidence=p.get("llm_confidence"),
                subtitle=None,        # polymarket markets have no subtitle field
                leg_date=None,        # polymarket has no temporal-arb concept
            ))

    # Kalshi open trades — actor IN 4 strategies (3 arb-family + copy_trader),
    # filter on payload.division so a single-division view doesn't bleed rows.
    # Same exclusions as Polymarket: drop SELLs (paired into round-trips by
    # resolver), drop entries already linked to a paired round-trip.
    kalshi_slugs = [s for s in division_slugs if s.startswith(_KALSHI_PREFIX)]
    if kalshi_slugs:
        kalshi_ph = ",".join("?" for _ in kalshi_slugs)
        rows = _query(
            db_url,
            f"SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            f"FROM audit_event a "
            f"LEFT JOIN kalshi_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.actor IN ('kalshi_tail_price_arb', 'kalshi_temporal_bucket_arb', 'kalshi_llm_arbitrage', 'kalshi_copy_trader') "
            f"  AND a.kind = 'would_have_placed' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            f"  AND json_extract(a.payload_json, '$.division') IN ({kalshi_ph}) "
            f"  AND r.order_id IS NULL "
            f"  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            f"    SELECT entry_order_id FROM kalshi_round_trips "
            f"    WHERE entry_order_id IS NOT NULL"
            f"  ) "
            f"ORDER BY a.ts DESC LIMIT ?",
            (*kalshi_slugs, limit),
        )
        for r in rows:
            try:
                p = json.loads(r["payload_json"])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            actor = r["actor"] or ""
            # Side: kalshi_llm uses `outcome`; structural strategies use
            # `leg` (yes/no for tail; yes_<ticker>/no_<ticker> for temporal/bucket).
            outcome = (p.get("outcome") or "").lower()
            if outcome not in ("yes", "no"):
                leg = (p.get("leg") or "").lower()
                outcome = "yes" if leg.startswith("yes") else "no" if leg.startswith("no") else ""
            qty = float(p.get("qty") or 0.0)
            price = float(p.get("limit_price") or 0.0)
            arb_type = p.get("kalshi_arb_type") or (
                "tail" if actor == "kalshi_tail_price_arb"
                else "llm_divergence" if actor == "kalshi_llm_arbitrage"
                else "copy_trade" if actor == "kalshi_copy_trader"
                else None
            )
            ku = p.get("key_unknowns")
            if not isinstance(ku, list):
                ku = []
            out.append(PMOpenTrade(
                order_id=str(p.get("order_id") or ""),
                venue="kalshi",
                division=str(p.get("division") or ""),
                strategy=actor,
                whale_handle=(
                    str(p["whale_handle"]) if p.get("whale_handle") else None
                ),
                side_detection_confidence=(
                    str(p["side_detection_confidence"])
                    if p.get("side_detection_confidence") else None
                ),
                emit_ts=str(r["ts"] or ""),
                market_title=str(p.get("event_title") or p.get("ticker") or ""),
                market_id=str(p.get("ticker") or ""),
                category=p.get("category"),
                outcome_bet=outcome,
                qty=qty,
                entry_price=price,
                notional=qty * price,
                divergence_pct=(
                    float(p["divergence_pct"]) if p.get("divergence_pct") is not None
                    else None
                ),
                edge_cents=(
                    float(p["edge_cents"]) if p.get("edge_cents") is not None
                    else None
                ),
                arb_type=arb_type,
                resolves_at=p.get("expires_at"),
                age_hours=_age_hours(r["ts"] or ""),
                rationale=p.get("rationale"),
                llm_reasoning=p.get("llm_reasoning"),
                key_unknowns=ku,
                llm_confidence=p.get("llm_confidence"),
                subtitle=p.get("subtitle"),
                leg_date=p.get("leg_date"),
            ))

    out.sort(key=lambda t: t.emit_ts, reverse=True)
    return out[:limit]


def _query_pm_pending_count(
    db_url: str, division_slugs: list[str],
) -> int:
    """Count would_have_placed audit rows without a corresponding
    round-trip resolution. Cross-venue."""
    if not division_slugs:
        return 0

    total = 0

    # Polymarket: would_have_placed rows from polymarket_arbitrage OR
    # polymarket_copy_trader actors, filtered by payload.division so the
    # count matches what the same-division Open tab renders. Mirrors the
    # exclusion clauses in _query_pm_open_trades — drop SELLs and drop
    # entries linked to a paired round-trip.
    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        poly_ph = ",".join("?" for _ in poly_slugs)
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n FROM audit_event a "
            f"LEFT JOIN polymarket_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.actor IN ('polymarket_arbitrage', 'polymarket_copy_trader') "
            f"  AND a.kind = 'would_have_placed' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.division'), 'polymarket_arbitrage') IN ({poly_ph}) "
            f"  AND r.order_id IS NULL "
            f"  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            f"    SELECT entry_order_id FROM polymarket_round_trips "
            f"    WHERE entry_order_id IS NOT NULL"
            f"  )",
            tuple(poly_slugs),
        )
        if rows:
            total += int(rows[0].get("n") or 0)

    # Kalshi: actor IN (4 kalshi strategies: 3 arb-family + copy_trader)
    # AND no kalshi_round_trips row. The audit-event payload's `division`
    # field tells us which division the row belongs to so we filter on it.
    # Same SELL + entry_order_id exclusions as Polymarket.
    kalshi_slugs = [s for s in division_slugs if s.startswith(_KALSHI_PREFIX)]
    if kalshi_slugs:
        kalshi_ph = ",".join("?" for _ in kalshi_slugs)
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n FROM audit_event a "
            f"LEFT JOIN kalshi_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.actor IN ('kalshi_tail_price_arb', 'kalshi_temporal_bucket_arb', 'kalshi_llm_arbitrage', 'kalshi_copy_trader') "
            f"  AND a.kind = 'would_have_placed' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            f"  AND json_extract(a.payload_json, '$.division') IN ({kalshi_ph}) "
            f"  AND r.order_id IS NULL "
            f"  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            f"    SELECT entry_order_id FROM kalshi_round_trips "
            f"    WHERE entry_order_id IS NOT NULL"
            f"  )",
            tuple(kalshi_slugs),
        )
        if rows:
            total += int(rows[0].get("n") or 0)

    return total


def _pm_equity_at(curve: list[PMEquityPoint], at_or_before: datetime) -> float | None:
    """Last equity point at or before the given UTC datetime, summed
    across divisions present in the curve. Returns None if no points."""
    if not curve:
        return None
    target = at_or_before.isoformat()
    # Sum the LATEST point per division that is <= target.
    by_div: dict[str | None, PMEquityPoint] = {}
    for p in curve:
        if p.ts <= target:
            by_div[p.division] = p   # later overwrites earlier (curve is ts-asc)
    if not by_div:
        return None
    return sum(p.equity for p in by_div.values())


def _pm_summary(
    round_trips: list[PMRoundTrip],
    equity_curve: list[PMEquityPoint],
    pending_count: int,
) -> PMSummary:
    """Compute the summary cards. Returns zeros/Nones cleanly when there's
    no data so the template doesn't have to guard."""
    n_wins = sum(1 for rt in round_trips if rt.won == 1)
    n_resolved = len(round_trips)
    n_voids = sum(1 for rt in round_trips if rt.market_result == "void")
    n_losses = n_resolved - n_wins - n_voids
    decisive = n_wins + n_losses
    win_rate = (100.0 * n_wins / decisive) if decisive > 0 else None
    total_pnl = sum(rt.realized_pnl for rt in round_trips)

    now = datetime.now(timezone.utc)
    current_equity = _pm_equity_at(equity_curve, now)
    yesterday_equity = _pm_equity_at(equity_curve, now - timedelta(days=1))
    todays_pnl: float | None = None
    todays_pnl_pct: float | None = None
    if current_equity is not None and yesterday_equity is not None and yesterday_equity > 0:
        todays_pnl = current_equity - yesterday_equity
        todays_pnl_pct = 100.0 * todays_pnl / yesterday_equity

    return PMSummary(
        current_equity=current_equity,
        todays_pnl=todays_pnl,
        todays_pnl_pct=todays_pnl_pct,
        n_resolved=n_resolved,
        n_pending=pending_count,
        n_wins=n_wins,
        n_losses=n_losses,
        n_voids=n_voids,
        win_rate_pct=win_rate,
        total_realized_pnl=total_pnl,
    )


async def build_prediction_market_view(
    deps,
    division: str | None,
    *,
    history_limit: int = 100,
    equity_curve_days: int = 30,
) -> PMDashboardView | None:
    """Build the dashboard view for /prediction-markets/ and
    /prediction-markets/{division}.

    `division=None` is the "All Prediction Markets" combined view — queries
    span every active prediction-market division, summary cards aggregate.

    Returns None ONLY if `division` is non-None but not a valid
    prediction-market slug — the route handler turns that into 404.

    Heavy DB work runs via asyncio.to_thread so the event loop isn't blocked.
    """
    all_pm = _pm_divisions_all()
    available = [
        PMDivisionOption(slug=d.slug, display_name=d.name, venue=_pm_venue(d.slug))
        for d in all_pm
    ]

    if division is not None:
        target = next((d for d in all_pm if d.slug == division), None)
        if target is None:
            return None
        target_slugs = [target.slug]
        selected_label = target.name
    else:
        target_slugs = [d.slug for d in all_pm]
        selected_label = "All Prediction Markets"

    db_url = deps.db_url

    round_trips, equity_curve, open_trades = await asyncio.gather(
        asyncio.to_thread(_query_pm_round_trips, db_url, target_slugs, history_limit),
        asyncio.to_thread(_query_pm_equity_curve, db_url, target_slugs, equity_curve_days),
        asyncio.to_thread(_query_pm_open_trades, db_url, target_slugs, 200),
    )

    # Pending count = len(open_trades). One source of truth — no separate
    # count query that could go out of sync with the list.
    summary = _pm_summary(round_trips, equity_curve, len(open_trades))

    return PMDashboardView(
        selected=division,
        selected_label=selected_label,
        available_divisions=available,
        summary=summary,
        equity_curve=equity_curve,
        round_trips=round_trips,
        open_trades=open_trades,
    )


# ── Helpers ───────────────────────────────────────────────────────────────

async def _none():
    return None


async def _safe_call(obj, method_name: str):
    try:
        return await getattr(obj, method_name)()
    except Exception as e:
        log.debug("safe_call %s.%s failed: %s", obj, method_name, e)
        return None


async def _fetch_prices_async(symbols: list[str]) -> dict[str, float]:
    """Wrap PMCCAgent's price fetcher (yfinance under the hood).

    Crypto symbols arrive in unified `{CODE}/USD` form (matching Coinbase's
    convention; emitted by RobinhoodBroker.snapshot for crypto positions).
    yfinance uses `{CODE}-USD` instead, so we translate the slash to a dash
    on the way in and reverse-map results back to the unified form on the
    way out — callers stay in unified land and don't need to know.
    """
    if not symbols:
        return {}
    # Build a parallel list of yfinance-compatible symbols + a reverse map.
    yf_syms: list[str] = []
    yf_to_orig: dict[str, str] = {}
    for s in symbols:
        yf_s = s.replace("/", "-") if "/" in s else s
        yf_syms.append(yf_s)
        yf_to_orig[yf_s] = s
    try:
        from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
        raw = await PMCCAgent._fetch_prices(yf_syms)
    except Exception as e:
        log.debug("price fetch failed: %s", e)
        return {}
    # Map keys back to the caller's input form.
    return {yf_to_orig.get(k, k): v for k, v in raw.items()}


def _group_pmcc_pairs(
    legs: list[OptionLeg], prices: dict[str, float]
) -> tuple[list[PMCCPair], list[OptionLeg]]:
    """Group option legs into pairs by underlying.

    Every underlying with at least one option becomes a pair entry. The
    DTE-based "qualify as PMCC" filter has been removed — short-DTE long
    calls still appear as pairs, and `pair.structure_type` classifies them
    ('pmcc' vs 'covered_call' vs 'uncovered_leap' etc.).

    For each underlying:
      - leap   = the long call with the highest DTE (longest-dated long)
      - short  = the short call with the lowest DTE (nearest expiry)
      - extras = everything else (puts, additional longs/shorts)

    Returns (pairs, other_options) where `other_options` is now always empty
    — kept in the return signature to avoid churn at call sites; will be
    removed once nothing reads it.
    """
    by_und: dict[str, list[OptionLeg]] = {}
    for leg in legs:
        by_und.setdefault(leg.underlying, []).append(leg)

    pairs: list[PMCCPair] = []

    for und, lst in by_und.items():
        long_calls = [l for l in lst if l.option_type == "call" and l.is_long]
        short_calls = [l for l in lst if l.option_type == "call" and l.is_short]
        leap = (
            max(long_calls, key=lambda l: l.dte or 0)
            if long_calls else None
        )
        short = (
            min(short_calls, key=lambda l: l.dte or 999)
            if short_calls else None
        )
        extras = [l for l in lst if l is not leap and l is not short]
        pairs.append(PMCCPair(
            underlying=und,
            underlying_price=prices.get(und),
            leap=leap,
            short_call=short,
            extras=extras,
        ))

    # Sort by priority (most urgent first), then alphabetically as tie-break
    pairs.sort(key=lambda p: (-p.priority_score, p.underlying))
    return pairs, []


def _query_division_activity(
    db_url: str, slug: str, strategy: str | None, limit: int
) -> list[dict]:
    """Recent audit + order events for a specific division.

    Filters by strategy name (from divisions.yaml) when present, else by
    payload symbol/division match. Falls back to recent corp-wide events
    if neither yields results.
    """
    rows = _query(
        db_url,
        """SELECT id, ts, actor, kind, payload_json
           FROM audit_event
           WHERE kind IN (
             'risk_approved','risk_rejected',
             'board_approved','board_rejected','auto_executed',
             'fill','filled','execution_error',
             'scan_order_result',
             -- Lord Otter / TradingView webhook strategy kinds:
             -- include both action ('would_have_placed') and visibility
             -- events ('webhook_received','alert_ignored') so the per-
             -- division rail shows real-time signal traffic, not just
             -- placed orders. The home-page `trade_flow` keeps a tighter
             -- filter so it doesn't get flooded.
             'webhook_received','alert_ignored','would_have_placed','agent_error',
             -- Phase 1.5b: surface webhook rejections (bad JSON, auth, etc.)
             -- so silent failures stop being silent.
             'webhook_rejected',
             -- Polymarket-arbitrage strategy kinds. polymarket_scan_cycle
             -- is intentionally OMITTED — fires every 30s and would flood
             -- the rail. The rail surfaces decisions (LLM-called +
             -- emit/skip + risk-rejected), not bookkeeping ticks.
             'polymarket_llm_probability_called',
             'polymarket_order_rejected_by_risk',
             -- Kalshi K2.x strategy kinds. Scan summaries fire every
             -- 5 min per strategy (2-4 rows / 5 min = manageable rail
             -- volume vs. polymarket's 30s cadence). Discovery-refreshed
             -- only fires every 10 min (cache_ttl). Order-rejected-by-risk
             -- only fires when an opportunity actually triggers.
             'kalshi_discovery_refreshed',
             'kalshi_tail_arb_scan',
             'kalshi_temporal_bucket_scan',
             'kalshi_market_evaluated',
             'kalshi_pair_evaluated',
             'kalshi_bucket_evaluated',
             'kalshi_order_rejected_by_risk',
             'kalshi_tb_order_rejected_by_risk',
             -- Phase K6.1: Kalshi LLM arbitrage strategy. Same per-market
             -- grain as polymarket. kalshi_llm_scan_cycle is bookkeeping
             -- (one row per cycle); kalshi_llm_probability_called is the
             -- rich per-market LLM result (same shape as
             -- polymarket_llm_probability_called).
             'kalshi_llm_scan_cycle',
             'kalshi_llm_probability_called',
             'kalshi_llm_order_rejected_by_risk'
           )
           ORDER BY id DESC LIMIT ?""",
        (limit * 5,),    # over-fetch then filter
    )
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r.get("payload_json") or "{}")
        except Exception:
            payload = {}
        # Match by strategy field (most reliable) or by division key
        matches = (
            (strategy and payload.get("strategy") == strategy)
            or payload.get("division") == slug
        )
        if not matches:
            continue
        evt: dict = {
            "id": r["id"],   # exposed so the template can build /audit/{id}/replay-research
            "ts": r["ts"],
            "ts_short": _humanize_ts(r["ts"]),
            "actor": r["actor"],
            "kind": r["kind"],
            "symbol": payload.get("symbol", ""),
            "side": payload.get("side", ""),
            "qty": payload.get("qty", ""),
            "signal": payload.get("signal", ""),
            "reason": (payload.get("reason") or "")[:140],
            "color": _color_for(r["kind"]),
            "polymarket": None,
            "kalshi": None,
            "kalshi_llm": None,
        }
        # Polymarket-specific enrichment so the activity tile + right
        # rail can render rich content without a second DB hit. Full
        # payload (with full LLM reasoning text) is fetched via the
        # /partials/polymarket-analysis/{id} endpoint when the user
        # clicks "show analysis"; the truncated preview lives here.
        # Kalshi K2.x enrichment — same pattern as polymarket. Different
        # event kinds have different rich fields so the template can render
        # contextually (scan summary vs. would_have_placed vs. risk reject).
        if r["actor"] in ("kalshi_tail_price_arb", "kalshi_temporal_bucket_arb"):
            kind = r["kind"]
            kdict: dict = {
                "actor": r["actor"],
                "kind": kind,
                # Common fields available on most kalshi events:
                "ticker": payload.get("ticker"),
                "event_ticker": payload.get("event_ticker"),
                "edge_cents": payload.get("edge_cents"),
                "edge_dollars": payload.get("edge_dollars"),
                "leg": payload.get("leg"),
                "kalshi_pair_id": payload.get("kalshi_pair_id"),
                "kalshi_arb_set_id": payload.get("kalshi_arb_set_id"),
                "kalshi_arb_type": payload.get("kalshi_arb_type"),
                "qty": payload.get("qty"),
                "limit_price": payload.get("limit_price"),
                "yes_ask": payload.get("yes_ask"),
                "no_ask": payload.get("no_ask"),
                "sum_asks": payload.get("sum_asks") or payload.get("sum_yes_asks"),
                "tier": payload.get("tier"),
                "risk_verdict": payload.get("risk_verdict"),
                "risk_reason": payload.get("risk_reason"),
                "max_dollar_risk": payload.get("max_dollar_risk"),
                "expires_at": payload.get("expires_at"),
                "leg_date": payload.get("leg_date"),
            }
            # Per-kind extra fields:
            if kind == "kalshi_discovery_refreshed":
                kdict["n_events_total"] = payload.get("n_events_total")
                kdict["n_markets_total"] = payload.get("n_markets_total")
                kdict["n_markets_filtered_collection"] = payload.get("n_markets_filtered_collection")
                kdict["events_by_type"] = payload.get("events_by_type") or {}
            elif kind == "kalshi_tail_arb_scan":
                kdict["n_markets_scanned"] = payload.get("n_markets_scanned")
                kdict["n_tail_candidates"] = payload.get("n_tail_candidates")
                kdict["n_opportunities_above_threshold"] = payload.get("n_opportunities_above_threshold")
                kdict["min_edge_cents"] = payload.get("min_edge_cents")
                kdict["yes_max_for_yes_tail"] = payload.get("yes_max_for_yes_tail")
                kdict["yes_min_for_no_tail"] = payload.get("yes_min_for_no_tail")
            elif kind == "kalshi_temporal_bucket_scan":
                kdict["n_temporal_events_scanned"] = payload.get("n_temporal_events_scanned")
                kdict["n_bucket_events_scanned"] = payload.get("n_bucket_events_scanned")
                kdict["n_temporal_opportunities"] = payload.get("n_temporal_opportunities")
                kdict["n_bucket_opportunities"] = payload.get("n_bucket_opportunities")
                kdict["n_emitted_after_cap"] = payload.get("n_emitted_after_cap")
                kdict["n_pairs_examined"] = payload.get("n_pairs_examined")
                kdict["n_buckets_examined"] = payload.get("n_buckets_examined")
                kdict["temporal_min_edge_cents"] = payload.get("temporal_min_edge_cents")
                kdict["bucket_min_edge_cents"] = payload.get("bucket_min_edge_cents")
            elif kind == "kalshi_market_evaluated":
                # Per-tail-candidate audit event — rich rail row
                kdict["event_title"] = payload.get("event_title")
                kdict["category"] = payload.get("category")
                kdict["subtitle"] = payload.get("subtitle")
                kdict["yes_ask"] = payload.get("yes_ask")
                kdict["no_ask"] = payload.get("no_ask")
                kdict["yes_bid"] = payload.get("yes_bid")
                kdict["no_bid"] = payload.get("no_bid")
                kdict["sum_asks"] = payload.get("sum_asks")
                kdict["edge_cents"] = payload.get("edge_cents")
                kdict["would_emit"] = payload.get("would_emit")
                kdict["min_edge_cents"] = payload.get("min_edge_cents")
                kdict["in_yes_tail"] = payload.get("in_yes_tail")
                kdict["in_no_tail"] = payload.get("in_no_tail")
                kdict["expires_at"] = payload.get("expires_at")
            elif kind == "kalshi_pair_evaluated":
                # Per-temporal-pair audit event
                kdict["event_title"] = payload.get("event_title")
                kdict["category"] = payload.get("category")
                kdict["early_ticker"] = payload.get("early_ticker")
                kdict["early_subtitle"] = payload.get("early_subtitle")
                kdict["early_date"] = payload.get("early_date")
                kdict["early_yes_ask"] = payload.get("early_yes_ask")
                kdict["late_ticker"] = payload.get("late_ticker")
                kdict["late_subtitle"] = payload.get("late_subtitle")
                kdict["late_date"] = payload.get("late_date")
                kdict["late_yes_ask"] = payload.get("late_yes_ask")
                kdict["edge_cents"] = payload.get("edge_cents")
                kdict["would_emit"] = payload.get("would_emit")
                kdict["min_edge_cents"] = payload.get("min_edge_cents")
            elif kind == "kalshi_bucket_evaluated":
                # Per-bucket-event audit event
                kdict["event_title"] = payload.get("event_title")
                kdict["category"] = payload.get("category")
                kdict["n_legs"] = payload.get("n_legs")
                kdict["sum_yes_asks"] = payload.get("sum_yes_asks")
                kdict["edge_cents"] = payload.get("edge_cents")
                kdict["would_emit"] = payload.get("would_emit")
                kdict["min_edge_cents"] = payload.get("min_edge_cents")
            evt["kalshi"] = kdict

        # Phase K6.1: Kalshi LLM arbitrage enrichment — same shape as
        # polymarket so the existing rich-rail UI can render it via a
        # parallel template branch. Field names mirror polymarket's
        # `polymarket: {...}` dict for consistency.
        if r["actor"] == "kalshi_llm_arbitrage":
            reasoning_text = payload.get("llm_reasoning") or ""
            evt["kalshi_llm"] = {
                "ticker": payload.get("ticker"),
                "event_ticker": payload.get("event_ticker"),
                "event_title": payload.get("event_title"),
                "subtitle": payload.get("subtitle"),
                "outcome": payload.get("outcome"),
                "category": payload.get("category"),
                "implied_prob": payload.get("implied_prob_at_entry") or payload.get("implied_prob_yes"),
                "llm_prob": payload.get("llm_prob_estimate") or payload.get("llm_prob_yes"),
                "llm_confidence": payload.get("llm_confidence"),
                "divergence_pct": payload.get("divergence_pct"),
                "min_divergence_pct": payload.get("min_divergence_pct"),
                "would_emit": payload.get("would_emit"),
                "qty": payload.get("qty"),
                "limit_price": payload.get("limit_price"),
                "risk_verdict": payload.get("risk_verdict"),
                "risk_reason": payload.get("risk_reason"),
                "reasoning_preview": (reasoning_text[:200] + "…") if len(reasoning_text) > 200 else reasoning_text,
                "key_unknowns": payload.get("key_unknowns") or [],
                "expires_at": payload.get("expires_at"),
            }

        if r["actor"] == "polymarket_arbitrage":
            reasoning_text = payload.get("llm_reasoning") or ""
            evt["polymarket"] = {
                "market_slug": payload.get("market_slug") or payload.get("slug"),
                "market_question": payload.get("market_question") or payload.get("question"),
                "outcome": payload.get("outcome"),
                "category": payload.get("category"),
                "series": payload.get("series"),
                "implied_prob": payload.get("implied_prob_at_entry") or payload.get("implied_prob_yes"),
                "llm_prob": payload.get("llm_prob_estimate") or payload.get("llm_prob_yes"),
                "llm_confidence": payload.get("llm_confidence"),
                "divergence_pct": payload.get("divergence_pct"),
                "min_divergence_pct": payload.get("min_divergence_pct"),
                "would_emit": payload.get("would_emit"),
                "qty": payload.get("qty"),
                "limit_price": payload.get("limit_price"),
                "risk_verdict": payload.get("risk_verdict"),
                "risk_reason": payload.get("risk_reason"),
                "reasoning_preview": (reasoning_text[:200] + "…") if len(reasoning_text) > 200 else reasoning_text,
                "key_unknowns": payload.get("key_unknowns") or [],
                "resolves_at": payload.get("resolves_at"),
                "condition_id": payload.get("condition_id"),
            }
        out.append(evt)
        if len(out) >= limit:
            break
    return out


def _query_account_equity_curve(
    db_url: str, slug: str, days: int
) -> list[dict]:
    """Per-account equity curve.

    NOTE: the current `account_state` schema is "current state only" —
    `account` is PRIMARY KEY so there's exactly one row per account that
    gets overwritten on every update. We can't derive a time series from it.

    Phase 2.1 will add an `account_state_history` table written to periodically
    (or on every snapshot) to enable real curves. For now we return [] and
    the division page's chart shows its "no history yet" empty state.

    TODO: add account_state_history table; write to it from PortfolioAgent.
    """
    return []


def _approx_todays_pnl(
    equity_curve: list[dict], current_equity: float | None
) -> tuple[float | None, float | None]:
    """Best-effort today's P&L from yesterday's close vs current equity."""
    if current_equity is None or not equity_curve:
        return None, None
    today_iso = datetime.now(timezone.utc).date().isoformat()
    prior = [p for p in equity_curve if p["date"] < today_iso]
    if not prior:
        return None, None
    yesterday = prior[-1]["equity"]
    if yesterday <= 0:
        return None, None
    pnl = current_equity - yesterday
    pct = pnl / yesterday
    return pnl, pct
