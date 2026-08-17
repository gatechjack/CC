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
from trading_corp.web.kalshi_crypto_vol_v2 import PMVolV2Block, query_pm_vol_v2_block
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
    # Unified tile/expert decision status, attached by build_division_view for
    # robinhood_pmcc: {state: fresh|stale|none, status_label, urgency, source,
    # age_h, stale_label, no_signal_label}. None until attached / non-PMCC.
    unified_status: dict | None = None
    # Equity shares held on this underlying (from stock_holdings), so the
    # classifier can tell a real shares-backed covered call from a LEAP-covered
    # PMCC. None = unknown (treated as no shares). Populated by _group_pmcc_pairs.
    underlying_shares: float | None = None

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

    def _shares_cover_short(self) -> bool:
        """True iff equity shares fully back the short call (>= 100 per short
        contract). Shares are the *covered-call* cover; a PMCC uses a long call
        instead (checked before this). None shares => not covered."""
        if self.short_call is None or self.underlying_shares is None:
            return False
        needed = 100.0 * abs(self.short_call.qty or 0)
        return needed > 0 and self.underlying_shares >= needed

    @property
    def structure_type(self) -> str:
        """Classify the structure by WHAT COVERS THE SHORT — never by the long
        leg's remaining DTE. A real LEAP that has aged below any day-count still
        covers its short and is still a PMCC (the 180-DTE discriminator was the
        old covered-call mislabel bug: an aged LEAP flipped to 'covered_call').

        Returns one of:
          'pmcc'           — long call (LEAP/diagonal) + short call: the long
                             call covers the short, at ANY remaining DTE.
          'covered_call'   — equity shares (>= 100 per short contract) + short
                             call, NO long-call cover: the shares cover.
          'uncovered_leap' — long call only, no short (any DTE).
          'short_only'     — short call with no cover (no long call, no shares)
                             = a naked short.
          'other'          — no primary call legs (extras/puts only, or empty).
        """
        if self.short_call:
            if self.leap:
                return "pmcc"                    # long call covers the short (any DTE)
            if self._shares_cover_short():
                return "covered_call"            # equity-covered short
            return "short_only"                  # naked short
        if self.leap:
            return "uncovered_leap"              # long call, no short (any DTE)
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
    # trade-plan PR 6 — PA validators (not scored) panel. Reads
    # `pa_validation_decision` audit. Shape on `build_bitunix_pa_view`.
    bitunix_pa: dict | None = None
    # trade-plan PR 6 — decision flow timeline (last 5 fires showing
    # score → PA → HTF → outcome). Shape on `build_bitunix_decision_flow_view`.
    bitunix_decision_flow: dict | None = None
    # Deferred-fire — live snapshot of the observer's in-memory PA-rejected
    # payload cache. Shape on `build_bitunix_pending_pa_view`. Always set
    # (even when nothing cached) so the template can show "no signal
    # pending" rather than hiding the panel.
    bitunix_pending_pa: dict | None = None
    # trade-plan PR 6 — v2 trade-plan decisions + SL lifecycle updates.
    # Shape on `build_bitunix_trade_plan_view`. Renders empty-state when
    # `trade_plan.enabled: false` (today's prod state) so the panel is
    # visible-but-empty pre-flip and starts populating post-flip.
    bitunix_trade_plan: dict | None = None
    # Gate (a) REST/exec resilience — 24h counts of rest_request_retried /
    # snapshot_stale_halt / stuck_order_cancelled / stuck_order_cancel_failed.
    # Only populated for `bitunix_futures` (the division whose bitunix.py +
    # data_exec.py REST layer emits these). Shape: gate_a_resilience_24h().
    # Relocated here from the retired home-page Stage-1 monitoring row.
    gate_a: dict | None = None
    # True when the division's broker is NOT a paper/sim broker — i.e. orders
    # placed via Approve will hit a real-money account. Default False so that
    # any division whose broker is unknown/None stays conservatively "paper".
    # Purely observability; no order-placement logic reads this field.
    is_live: bool = False


def _division_is_live(broker) -> bool:
    """Return True when *broker* is a live (non-paper) broker instance.

    Reads the ``paper`` attribute that every broker exposes:
      - ``paper=True``  → simulation / paper-execution broker → returns False
      - ``paper=False`` → live real-money broker              → returns True
      - broker is None or attribute missing → conservatively False

    Pure function; no I/O.  Extracted so tests can cover it without spinning
    up the full async build_division_view stack.
    """
    if broker is None:
        return False
    return not bool(getattr(broker, "paper", True))


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
    # HITL activity (registry-backed pending count + 24h board decisions +
    # autonomous-live invariant), merged into the Pending Approvals stat card
    # so the count matches /approvals (actionable) rather than the all-time
    # proposed_order.status='risk_approved' DB residue. Shape: hitl_activity_24h().
    hitl: dict | None = None


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
        asyncio.to_thread(
            hitl_activity_24h, db_url, pending_registry=deps.pending_registry,
        ),
        asyncio.to_thread(_query_recent_audit, db_url, 10),
        asyncio.to_thread(_query_equity_curve, db_url, 30),
        asyncio.to_thread(_safe_get_vix),
        asyncio.to_thread(_safe_regime, deps.trend_agent),
        _build_market_ribbon(),
        return_exceptions=True,
    )
    hitl, recent_audit, eq_curve, vix, regime, ribbon = (
        r if not isinstance(r, Exception) else None for r in db_results
    )
    hitl = hitl if isinstance(hitl, dict) else {}
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
        pending_approvals=int(hitl.get("pending", 0)),
        vix=vix if isinstance(vix, (int, float)) else None,
        regime=regime if isinstance(regime, str) else "unknown",
        buckets=buckets,
        investment_groups=investment_groups,
        health=health,
        equity_curve=eq_curve,
        market_ribbon=ribbon,
        btc_owned=0.0,           # stub — wire to live feed in Phase 1.5c+
        dry_run=bool(getattr(deps, "dry_run", False)),
        hitl=hitl,
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

    Polymarket metrics-epoch cutoff is resolved internally and applied
    to the polymarket roll-up + the pending-count subquery for the
    polymarket_copy_trading division. Kalshi cutoffs live in
    DASHBOARD_RT_CUTOFFS (no change here).
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

    # Resolve metrics-epoch once for this hydrate sweep — affects the
    # polymarket roll-up below + the pending count below it. Kalshi
    # tile cutoffs use DASHBOARD_RT_CUTOFFS (hardcoded), not agent_state.
    pm_epoch = _get_polymarket_metrics_epoch(db_url)

    # Polymarket round-trips — grouped by division (Fix 2026-05-14).
    # Previously this was a table-wide aggregate dumped onto the
    # `polymarket_arbitrage` tile, which (1) showed the wrong WR for
    # the arb tile by mixing in copy-trader rows and (2) left the
    # `polymarket_copy_trading` tile at zero. Mirrors the kalshi
    # roll-up shape immediately below.
    try:
        rows = _query(
            db_url,
            "SELECT division, "
            "       COUNT(*) AS n, "
            "       SUM(won) AS w, "
            "       SUM(realized_pnl) AS pnl "
            "FROM polymarket_round_trips "
            "WHERE 1=1"
            + _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="entry_ts",
                div_col="COALESCE(division, 'polymarket_arbitrage')",
            )
            + " GROUP BY division",
        )
        for r in rows:
            div = r.get("division") or ""
            if div not in stats:
                continue
            n = int(r.get("n") or 0)
            w = int(r.get("w") or 0)
            pnl = float(r.get("pnl") or 0.0)
            s = stats[div]
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
            "FROM kalshi_round_trips "
            "WHERE 1=1" + _kalshi_cutoff_clause("entry_ts") + " "
            "GROUP BY division",
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
    # could be batched if it becomes hot. Polymarket-side pending counts
    # carry the metrics-epoch cutoff; Kalshi-side pending counts ignore
    # it (Kalshi uses DASHBOARD_RT_CUTOFFS for round_trips only).
    for d in pm_divisions:
        try:
            stats[d.slug]["n_pending"] = _query_pm_pending_count(
                db_url, [d.slug], pm_epoch=pm_epoch,
            )
        except Exception as e:
            log.debug("pm_overview: pending count failed for %s: %s", d.slug, e)

    # Compute win rate per division, then attach.
    for d in pm_divisions:
        s = stats[d.slug]
        decisive = s["n_wins"] + s["n_losses"]
        s["win_rate_pct"] = (100.0 * s["n_wins"] / decisive) if decisive > 0 else None
        cutoff = DASHBOARD_RT_CUTOFFS.get(d.slug)
        s["cutoff_label"] = cutoff.split("T", 1)[0] if cutoff else None
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
# NOTE: the old `_query_open_orders` (3-status in-flight; its result was computed then
# discarded) and `_query_pending_approvals` (all-time status='risk_approved' — the DB-residue
# count) were removed 2026-07-08. The Overview stat card + Telegram /pending now read the
# in-process PendingApprovalRegistry (commit 7f641d8 + the PMCC lifecycle fix), so any query
# counting raw `risk_approved` is stale-residue-prone. See reports/2026-07-08_pmcc_*.md.

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


def _get_metrics_epoch(db_url: str, agent: str) -> str | None:
    """Read agent_state(<agent>, metrics_epoch), ISO-8601-validated.

    Mirrors `_get_polymarket_metrics_epoch` but keyed by the caller's
    agent/division slug so each division owns its own epoch. Returns the
    stored ISO string verbatim if it parses, else None ("no epoch set" →
    unbounded / all-time). Validation is mandatory: the value is bound into
    SQL downstream, so anything that escapes `datetime.fromisoformat`
    becomes injection surface (we still bind it as a parameter, not an
    f-string, but the typecheck is cheap defense-in-depth + keeps a
    garbage value from silently scoping the panel to nothing).

    Set at runtime with a single INSERT (no redeploy):
        agent_state(<division>, 'metrics_epoch', '<ISO-8601>')
    Revert to all-time by deleting that row.
    """
    try:
        rec = db.load_agent_state(agent, "metrics_epoch", db_url=db_url)
    except Exception as e:
        log.debug("metrics_epoch load failed for %s: %s", agent, e)
        return None
    if rec is None:
        return None
    val = rec[0]
    if not isinstance(val, str) or not val:
        return None
    try:
        datetime.fromisoformat(val)
    except (TypeError, ValueError):
        log.warning(
            "metrics_epoch %r for %s failed ISO-8601 parse — treating as unset",
            val, agent,
        )
        return None
    return val


def _ptr_window_totals(
    db_url: str, division: str, *, live: bool, epoch_iso: str | None,
) -> tuple[list[dict], dict]:
    """Win-rate windows (7d/30d/all) over `paper_trade_record` for ONE
    execution_mode slice.

      - `live=False`: PAPER/sim rows (`execution_mode != 'live'`), unbounded.
      - `live=True`:  real-fill LIVE rows (`execution_mode = 'live'`),
        additionally scoped to `result_ts >= epoch_iso` when an epoch is set
        (the forward clean-booking window; `None` = all-time).

    Splitting by execution_mode is the correctness fix: a blended paper+live
    win-rate is meaningless once a division trades live. The epoch is applied
    to the LIVE slice ONLY — paper has never been ledger-contaminated by the
    live booking bugs (D1 double-book / P2 mislabel / maker-taker), so it
    stays unbounded. Boundary is `result_ts` (scope by resolution → open rows
    naturally drop out of the resolved aggregate once an epoch is set).
    `epoch_iso` is bound as a SQL parameter (not interpolated)."""
    cutoffs = {
        "7d":  (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
        "30d": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "all": "1970-01-01T00:00:00+00:00",
    }
    # COALESCE guards any legacy NULL execution_mode (pre-migration rows →
    # treated as paper, never as live).
    mode_sql = (
        "COALESCE(execution_mode, 'paper') = 'live'" if live
        else "COALESCE(execution_mode, 'paper') != 'live'"
    )
    windows: list[dict] = []
    totals: dict = {}
    for label, cutoff in cutoffs.items():
        if live:
            # `(? IS NULL OR result_ts >= ?)` → epoch None binds a no-op;
            # epoch set excludes pre-epoch AND still-open (result_ts NULL) rows.
            rows = _query(
                db_url,
                f"""SELECT tier, result,
                          COUNT(*) AS n,
                          COALESCE(SUM(actual_pnl_dollars), 0) AS sim_pnl
                   FROM paper_trade_record
                   WHERE division = ? AND {mode_sql} AND ts >= ?
                     AND (? IS NULL OR result_ts >= ?)
                   GROUP BY tier, result
                   ORDER BY tier ASC""",
                (division, cutoff, epoch_iso, epoch_iso),
            )
        else:
            rows = _query(
                db_url,
                f"""SELECT tier, result,
                          COUNT(*) AS n,
                          COALESCE(SUM(actual_pnl_dollars), 0) AS sim_pnl
                   FROM paper_trade_record
                   WHERE division = ? AND {mode_sql} AND ts >= ?
                   GROUP BY tier, result
                   ORDER BY tier ASC""",
                (division, cutoff),
            )
        windows.append({"label": label, "rows": rows})

        wins = sum(r["n"] for r in rows if r["result"] == "win")
        losses = sum(r["n"] for r in rows if r["result"] == "loss")
        expired = sum(r["n"] for r in rows if r["result"] == "expired")
        open_n = sum(r["n"] for r in rows if r["result"] is None)
        pre_a = sum(r["n"] for r in rows if r["result"] == "pre_phase_a")
        decided = wins + losses
        total_n = sum(r["n"] for r in rows)
        sim_pnl = sum(r["sim_pnl"] or 0.0 for r in rows)
        totals[label] = {
            "n": total_n,
            "wins": wins,
            "losses": losses,
            "expired": expired,
            "open": open_n,
            "n_pre_phase_a": pre_a,
            "win_rate_pct": (100.0 * wins / decided) if decided else None,
            "sim_pnl": round(sim_pnl, 2),
        }
    return windows, totals


def paper_trade_summary(db_url: str, division: str) -> dict:
    """Per-division win-rate summary for the dashboard, SPLIT by execution_mode.

    Returns a dict shaped:
      {
        "division": "<slug>",
        # PAPER/sim slice (execution_mode != 'live'), unbounded — these keys
        # are backward-compatible with the existing template + every
        # paper-only division (which renders identically to before).
        "windows": [{"label": "7d"|"30d"|"all", "rows": [...]}, ...],
        "totals":  {"7d": {...}, "30d": {...}, "all": {...}},
        # LIVE slice (execution_mode = 'live'), scoped forward from the
        # division's metrics epoch (result_ts >= agent_state(<div>,
        # 'metrics_epoch'); absent = all-time).
        "live_windows": [...],
        "live_totals":  {...},
        "has_live":      bool,        # gates the live panel
        "metrics_epoch": "<ISO>"|None # drives the "since <date>" label
      }

    WHY THE SPLIT: pre-split this aggregated ALL rows into one win-rate,
    blending paper-sim trades with real live fills (a meaningless number
    once a division trades live — e.g. the bitunix panel was paper+live
    mashed). The epoch keeps the LIVE numbers on correctly-booked data
    once it's set at the post-D1 cutover; until then the live panel shows
    all-time live (flagged in the label as pre-fix-inclusive). Paper stays
    unbounded — it was never ledger-contaminated by the live booking bugs.

    `pre_phase_a` rows are excluded from win-rate math (no TP/SL on them);
    still counted under `n_pre_phase_a` for transparency.
    """
    epoch_iso = _get_metrics_epoch(db_url, division)
    paper_windows, paper_totals = _ptr_window_totals(
        db_url, division, live=False, epoch_iso=None,
    )
    live_windows, live_totals = _ptr_window_totals(
        db_url, division, live=True, epoch_iso=epoch_iso,
    )
    return {
        "division": division,
        "windows": paper_windows,
        "totals": paper_totals,
        "live_windows": live_windows,
        "live_totals": live_totals,
        "has_live": bool(live_totals.get("all", {}).get("n")),
        "metrics_epoch": epoch_iso,
    }


# ── Trade flow ────────────────────────────────────────────────────────────

def trade_flow(
    db_url: str, limit: int = 20, *, stage1_only: bool = False,
) -> list[dict]:
    """Recent trade-flow rows for the home rail.

    When `stage1_only=True`, the result is filtered to bitunix_futures
    paper-mode activity (Stage 1's home). The filter matches rows where
    actor='bitunix_futures' AND the payload's execution_mode is either
    'paper' or absent (paper rows like `would_have_placed` omit the field
    by convention; only the live path stamps execution_mode='live' onto
    `live_order_placed` / `live_order_rejected` payloads).

    The toggle is a URL query-param flip in the routes; defaults to off
    so the home rail behavior is byte-identical when the toggle is off.
    """
    if stage1_only:
        sql = (
            """SELECT id, ts, actor, kind, payload_json
               FROM audit_event
               WHERE kind IN (
                 'risk_approved','risk_rejected',
                 'board_approved','board_rejected','auto_executed',
                 'fill','execution_error',
                 'scan_order_result','scheduled_scan_done','scheduled_scan_error',
                 'would_have_placed','live_order_placed','live_order_rejected'
               )
                 AND actor = 'bitunix_futures'
                 AND (
                   json_extract(payload_json, '$.execution_mode') IS NULL
                   OR json_extract(payload_json, '$.execution_mode') = 'paper'
                 )
               ORDER BY id DESC LIMIT ?"""
        )
    else:
        sql = (
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
               ORDER BY id DESC LIMIT ?"""
        )
    rows = _query(db_url, sql, (limit,))
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
            # Human-readable title for the row header. Prefer the
            # prediction-market event title (Kalshi: event_title,
            # Polymarket: market_question, copy-trading: market_title) —
            # repeating "WOULD HAVE PLACED" on every row is not useful when
            # there are 6+ rows. Falls back to None so the template renders
            # the kind.
            "event_title": (
                payload.get("event_title")
                or payload.get("market_question")
                or payload.get("market_title")
                or None
            ),
            "payload_pretty": json.dumps(payload, indent=2, default=str, sort_keys=True),
        })
    return out


# ── Stage-1 monitoring tiles ──────────────────────────────────────────────

# Gate (a) REST resilience: the audit-kind taxonomy actually emitted by
# trading_corp/brokers/bitunix.py and trading_corp/agents/data_exec.py.
# Mapped from the user-facing request as:
#   rest_retry         → rest_request_retried (bitunix.py:815)
#   snapshot_stale_halt → unchanged           (data_exec.py:334)
#   stuck_order_timeout → stuck_order_cancelled (success path,
#                          bitunix.py:1154) + stuck_order_cancel_failed
#                          (failure path, bitunix.py:1161)
GATE_A_KIND_REST_RETRIED = "rest_request_retried"
GATE_A_KIND_SNAPSHOT_STALE = "snapshot_stale_halt"
GATE_A_KIND_STUCK_CANCELLED = "stuck_order_cancelled"
GATE_A_KIND_STUCK_CANCEL_FAILED = "stuck_order_cancel_failed"

GATE_A_KINDS = (
    GATE_A_KIND_REST_RETRIED,
    GATE_A_KIND_SNAPSHOT_STALE,
    GATE_A_KIND_STUCK_CANCELLED,
    GATE_A_KIND_STUCK_CANCEL_FAILED,
)


def _gate_a_severity(counts: dict[str, int]) -> str:
    """Return 'green' | 'yellow' | 'red' for the Gate (a) tile color.

    Rules:
      red:   any snapshot_stale_halt (system-protective halt fired) OR
             any stuck_order_cancel_failed (cancel actually failed —
             order may still be resting at venue) OR
             rest_request_retried > 10 (sustained API churn).
      yellow: any rest_request_retried (1–10) or
              any stuck_order_cancelled (transient stuck-order resolved).
      green: all zero.
    """
    stale = counts.get(GATE_A_KIND_SNAPSHOT_STALE, 0)
    cancel_failed = counts.get(GATE_A_KIND_STUCK_CANCEL_FAILED, 0)
    rest = counts.get(GATE_A_KIND_REST_RETRIED, 0)
    cancelled = counts.get(GATE_A_KIND_STUCK_CANCELLED, 0)
    if stale > 0 or cancel_failed > 0 or rest > 10:
        return "red"
    if rest > 0 or cancelled > 0:
        return "yellow"
    return "green"


def gate_a_resilience_24h(
    db_url: str, *, now: datetime | None = None,
) -> dict:
    """Count Gate (a) REST resilience events in the last 24 hours.

    Returns a dict with:
      - by_kind: {kind → count} for each of the four GATE_A_KINDS
      - total: int sum across kinds
      - severity: 'green' | 'yellow' | 'red' (see _gate_a_severity)
      - since_iso: ISO-8601 lower bound of the 24h window
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    since_iso = since.isoformat(timespec="seconds")

    # One query — group by kind to keep round-trips minimal.
    placeholders = ",".join("?" for _ in GATE_A_KINDS)
    rows = _query(
        db_url,
        f"""SELECT kind, COUNT(*) AS n
            FROM audit_event
            WHERE kind IN ({placeholders})
              AND ts >= ?
            GROUP BY kind""",
        (*GATE_A_KINDS, since_iso),
    )
    by_kind = {k: 0 for k in GATE_A_KINDS}
    for r in rows:
        by_kind[r["kind"]] = int(r["n"])
    total = sum(by_kind.values())
    return {
        "by_kind": by_kind,
        "total": total,
        "severity": _gate_a_severity(by_kind),
        "since_iso": since_iso,
    }


# HITL activity tile — Stage 1 demands a HITL gate on the first N=10 live
# bitunix orders. The tile shows: how many approvals are pending right now,
# how many board decisions fired in the last 24h, and an "autonomous live
# orders" count that MUST be zero during Stage 1 (paper-mode deployment).
# A non-zero autonomous count is a Stage-1 violation — a live order placed
# without operator approval — and is the load-bearing safety signal here.

def hitl_activity_24h(
    db_url: str,
    *,
    pending_registry: Any = None,
    now: datetime | None = None,
) -> dict:
    """Aggregate HITL state for the Stage-1 monitoring row.

    Returns:
      - pending: int — len(registry._pending), 0 if registry unwired
      - approved_24h, rejected_24h: int — board decisions in window
      - autonomous_live_24h: int — bitunix live_order_placed rows in the
        24h window where hitl_gate == 'monitor_mode' (HITL bypassed
        because the first N=10 live-order cap was reached). MUST be 0
        during Stage 1 (paper deployment); any non-zero value is the
        red signal.
      - severity: 'green' | 'red' — red iff autonomous_live_24h > 0
      - since_iso: ISO-8601 lower bound
    """
    now = now or datetime.now(timezone.utc)
    since_iso = (now - timedelta(hours=24)).isoformat(timespec="seconds")

    pending = 0
    if pending_registry is not None:
        try:
            pending = int(pending_registry.pending_count())
        except Exception as e:
            log.warning("hitl_activity: pending_count() raised: %s", e)

    rows = _query(
        db_url,
        """SELECT kind, COUNT(*) AS n
           FROM audit_event
           WHERE actor = 'board'
             AND kind IN ('board_approved','board_rejected')
             AND ts >= ?
           GROUP BY kind""",
        (since_iso,),
    )
    by_decision = {r["kind"]: int(r["n"]) for r in rows}
    approved_24h = by_decision.get("board_approved", 0)
    rejected_24h = by_decision.get("board_rejected", 0)

    # Autonomous live orders — live_order_placed rows in the 24h window
    # tagged with hitl_gate='monitor_mode'. The bitunix observer stamps
    # this on the live path AFTER the first N=10 HITL gate is exhausted
    # (bitunix_futures_observer.py:2578). During Stage 1 (paper) any
    # such row indicates the paper-mode invariant has been broken.
    auto_rows = _query(
        db_url,
        """SELECT COUNT(*) AS n
           FROM audit_event
           WHERE actor = 'bitunix_futures'
             AND kind = 'live_order_placed'
             AND ts >= ?
             AND json_extract(payload_json, '$.hitl_gate') = 'monitor_mode'""",
        (since_iso,),
    )
    autonomous_live_24h = int(auto_rows[0]["n"]) if auto_rows else 0

    severity = "red" if autonomous_live_24h > 0 else "green"
    return {
        "pending": pending,
        "approved_24h": approved_24h,
        "rejected_24h": rejected_24h,
        "autonomous_live_24h": autonomous_live_24h,
        "severity": severity,
        "since_iso": since_iso,
    }


# tasty_options activation tile — shows the broker session connectivity
# (read from data_exec.brokers, mirroring the footer's _broker_health
# de-dupe pattern) and surfaces the Fork #4 anomaly: the tasty signal
# scanner does not emit a per-cycle audit event yet, so scanner-tick rate
# is rendered as an explicit placeholder pointing at the P3 BACKLOG.

def tasty_activation_status(
    brokers_map: dict[str, Any] | None,
) -> dict:
    """Build the tasty_options tile context.

    Returns:
      - session: 'connected' | 'disconnected' | 'unwired'
      - broker_name: str — class name or 'name' attr of the broker, or '—'
      - scanner_tick_rate: None — see note (audit kind not currently
        emitted). The template renders the placeholder + BACKLOG pointer
        when None.
    """
    if not brokers_map:
        return {
            "session": "unwired",
            "broker_name": "—",
            "scanner_tick_rate": None,
        }
    # Match either the canonical slug or any *tasty* prefix
    tasty_broker = brokers_map.get("tasty_options")
    if tasty_broker is None:
        for slug, broker in brokers_map.items():
            if slug.startswith("tasty"):
                tasty_broker = broker
                break
    if tasty_broker is None:
        return {
            "session": "unwired",
            "broker_name": "—",
            "scanner_tick_rate": None,
        }

    connected = bool(getattr(tasty_broker, "_connected", False)) or bool(
        getattr(tasty_broker, "connected", False)
    )
    broker_name = getattr(tasty_broker, "name", type(tasty_broker).__name__)
    return {
        "session": "connected" if connected else "disconnected",
        "broker_name": broker_name,
        # Scanner-tick rate is intentionally None until the
        # _ic_orchestration.run_signal_scanner_loop adds a per-cycle
        # audit kind for the tasty division (filed P3 in BACKLOG).
        "scanner_tick_rate": None,
    }


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


_TF_TOKENS = ("3m", "5m", "15m", "30m", "1h", "4h", "1d")


def _infer_alert_tf(trigger_signal: str | None) -> str | None:
    """Best-effort TF parse from signal name (e.g. 'otter_3m_pump' → '3m').

    Returns None when no TF token is found. PR 6 dashboard-only — future
    enhancement could add `alert_tf` directly to the score-decided audit
    payload to avoid the heuristic.
    """
    if not trigger_signal:
        return None
    s = trigger_signal.lower()
    for tok in _TF_TOKENS:
        if f"_{tok}_" in s or s.endswith(f"_{tok}") or s.startswith(f"{tok}_"):
            return tok
    return None


def _load_latest_sl_lifecycle_states(conn: Any) -> dict[str, str]:
    """Return {order_id: lifecycle_state} from the most recent
    `position_sl_update` audit row per order_id. Empty in paper mode
    today (reconciler is dormant — see trading_corp_bitunix_strategy_gaps
    memory). Phase 4 will populate as broker fills come in.
    """
    out: dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT ts, payload_json FROM audit_event "
            "WHERE kind = 'position_sl_update' "
            "ORDER BY ts DESC LIMIT 200"
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        try:
            p = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except Exception:
            continue
        oid = p.get("order_id")
        if oid and oid not in out:
            out[oid] = p.get("lifecycle_state") or ""
    return out


def _bitunix_fee_config(deps: Any) -> Any:
    """Fetch the live FeeConfig off the BitUnix observer for fee-floor
    reconstruction. Returns None when observer or fee_config is unwired.
    """
    observer = getattr(deps, "bitunix_observer", None)
    return getattr(observer, "fee_config", None) if observer else None


def build_bitunix_trade_plan_view(db_url: str, deps: Any) -> dict | None:
    """trade-plan PR 6 — surface trade-plan v2 decisions + SL lifecycle.

    Reads two audit kinds:
      - `trade_plan_decision`: emitted by observer's `_log_trade_plan_decision`
        whenever the v2 dispatch path runs (even when `trade_plan.enabled:
        false` — the observer still computes the plan, just doesn't use
        it for placement). Shows what v2 WOULD do.
      - `position_sl_update`: emitted by the position reconciler + the
        paper-mode v2 replay when SL advances per the Option C lifecycle.

    Returns None when the observer isn't wired (test envs); otherwise
    a dict that's always renderable — empty lists when no audits exist
    yet. Operators read this to validate v2 behavior before flipping
    `trade_plan.enabled: true`, and to monitor it after.

    Shape:
      {
        enabled: bool,                  # trade_plan.enabled in observer config
        fee_config: {...} | None,       # observer.fee_config introspection
        decisions: [{ts_et, trigger_signal, score_side, score_tier,
                     should_trade, skip_reason, entry, stop_loss,
                     tp1, tp2, tp3, sl_method, tp2_method,
                     risk_per_unit, tp1_frac, tp2_frac, tp3_frac}, ...],
        sl_updates: [{ts_et, order_id, symbol, side, lifecycle_state,
                      current_sl, new_sl, reason, filled_legs, source}, ...],
        counts_24h: {decisions_total, should_trade_true, skipped,
                     sl_updates_total},
      }
    """
    observer = getattr(deps, "bitunix_observer", None)
    if observer is None:
        return None
    trade_plan_cfg = getattr(observer, "trade_plan_config", None)
    fee_cfg = getattr(observer, "fee_config", None)
    enabled = False
    if trade_plan_cfg is not None:
        # Both StrategyConfig instances + dict-style configs supported.
        enabled_attr = getattr(trade_plan_cfg, "enabled", None)
        if isinstance(trade_plan_cfg, dict):
            enabled = bool(trade_plan_cfg.get("enabled", False))
        elif enabled_attr is not None:
            enabled = bool(enabled_attr)
        else:
            # StrategyConfig doesn't carry `enabled` itself; the activation
            # flag lives at the YAML `trade_plan.enabled` level. If
            # `trade_plan_config` is set at all, treat the path as live —
            # main.py only wires it when enabled=true.
            enabled = True

    fee_summary: dict | None = None
    if fee_cfg is not None:
        try:
            fee_summary = {
                "taker_pct": float(getattr(fee_cfg, "taker_fee_pct", 0.0)),
                "maker_pct": float(getattr(fee_cfg, "maker_fee_pct", 0.0)),
                "slippage_pct": float(getattr(fee_cfg, "slippage_pct", 0.0)),
                "entry_is_taker": bool(getattr(fee_cfg, "entry_is_taker", True)),
                "tp_is_maker": bool(getattr(fee_cfg, "tp_is_maker", False)),
            }
        except Exception as e:
            log.warning("bitunix trade_plan fee_config introspection failed: %s", e)

    decisions: list[dict] = []
    sl_updates: list[dict] = []
    counts_24h = {
        "decisions_total": 0,
        "should_trade_true": 0,
        "skipped": 0,
        "sl_updates_total": 0,
    }
    reconciler: dict = {
        "state": "never_run",
        "last_run_ts": None,
        "last_run_ts_et": None,
        "hours_since": None,
        "n_matches": 0,
        "n_total": 0,
        "n_mismatches": 0,
        "mismatches": [],
    }
    try:
        cutoff_24h = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()
        cutoff_26h = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
        with db.connect(db_url) as conn:
            # Recent decisions list (last 10)
            dec_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='trade_plan_decision' "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for r in dec_rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    continue
                decisions.append({
                    "ts_et": format_et_short(r["ts"]),
                    "trigger_signal": p.get("trigger_signal"),
                    "score_side": p.get("score_side"),
                    "score_tier": p.get("score_tier"),
                    "should_trade": bool(p.get("should_trade")),
                    "skip_reason": p.get("skip_reason"),
                    "entry": p.get("entry"),
                    "stop_loss": p.get("stop_loss"),
                    "tp1": p.get("tp1"),
                    "tp2": p.get("tp2"),
                    "tp3": p.get("tp3"),
                    "sl_method": p.get("sl_method"),
                    "tp2_method": p.get("tp2_method"),
                    "risk_per_unit": p.get("risk_per_unit"),
                    "tp1_frac": p.get("tp1_qty_fraction"),
                    "tp2_frac": p.get("tp2_qty_fraction"),
                    "tp3_frac": p.get("tp3_qty_fraction"),
                })
            # 24h decision counts
            dec_count_rows = conn.execute(
                "SELECT json_extract(payload_json, '$.should_trade') AS st, "
                "COUNT(*) AS n FROM audit_event "
                "WHERE kind='trade_plan_decision' AND ts >= ? "
                "GROUP BY st",
                (cutoff_24h,),
            ).fetchall()
            for r in dec_count_rows:
                n = int(r["n"])
                counts_24h["decisions_total"] += n
                if r["st"] in (1, "1", "true", True):
                    counts_24h["should_trade_true"] += n
                else:
                    counts_24h["skipped"] += n
            # Recent SL lifecycle updates (last 10)
            sl_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='position_sl_update' "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for r in sl_rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    continue
                sl_updates.append({
                    "ts_et": format_et_short(r["ts"]),
                    "order_id": p.get("order_id"),
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "lifecycle_state": p.get("lifecycle_state"),
                    "current_sl": p.get("current_sl"),
                    "new_sl": p.get("new_sl"),
                    "reason": p.get("reason"),
                    "filled_legs": p.get("filled_legs") or [],
                    "source": p.get("source") or "reconciler",
                })
            # 24h SL update count
            sl_count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_event "
                "WHERE kind='position_sl_update' AND ts >= ?",
                (cutoff_24h,),
            ).fetchone()
            if sl_count_row:
                counts_24h["sl_updates_total"] = int(sl_count_row["n"])

            # Reconciler state tile
            rec_row = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='audit_reality_run' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if rec_row:
                rec_ts = rec_row["ts"]
                try:
                    rec_payload = json.loads(rec_row["payload_json"]) if rec_row["payload_json"] else {}
                except Exception:
                    rec_payload = {}
                rec_status = rec_payload.get("status", "no_trades")
                n_total = int(rec_payload.get("n_total", 0))
                n_matches = int(rec_payload.get("n_matches", 0))
                n_mismatches = int(rec_payload.get("n_mismatches", 0))
                mismatches_raw = rec_payload.get("mismatches", [])
                mismatches_display = [
                    {
                        "order_id": m.get("order_id", ""),
                        "ts_et": format_et_short(m.get("ts")) if m.get("ts") else None,
                        "discrepancy": m.get("discrepancy"),
                    }
                    for m in mismatches_raw
                ]
                # Determine hours_since
                try:
                    rec_dt = datetime.fromisoformat(rec_ts.replace("Z", "+00:00"))
                    if rec_dt.tzinfo is None:
                        rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                    hours_since = (datetime.now(timezone.utc) - rec_dt).total_seconds() / 3600.0
                except Exception:
                    hours_since = None
                # State precedence: never_run → mismatch → stale → no_trades → match
                is_stale = rec_ts < cutoff_26h
                if rec_status == "mismatch":
                    state = "mismatch"
                elif is_stale:
                    state = "stale"
                elif rec_status == "no_trades":
                    state = "no_trades"
                else:
                    state = "match"
                reconciler = {
                    "state": state,
                    "last_run_ts": rec_ts,
                    "last_run_ts_et": format_et_short(rec_ts),
                    "hours_since": hours_since,
                    "n_matches": n_matches,
                    "n_total": n_total,
                    "n_mismatches": n_mismatches,
                    "mismatches": mismatches_display,
                }
    except Exception as e:
        log.warning("bitunix trade_plan view query failed: %s", e)

    return {
        "enabled": enabled,
        "fee_config": fee_summary,
        "decisions": decisions,
        "sl_updates": sl_updates,
        "counts_24h": counts_24h,
        "reconciler": reconciler,
    }


def build_bitunix_pending_pa_view(deps: Any) -> dict | None:
    """Deferred-fire dashboard: live snapshot of the observer's in-memory
    PA-rejected payload cache. Mirrors the cache lifecycle in
    `bitunix_futures_observer.py` — populated when PA rejects a high-score
    fire in enforce mode, cleared on score SKIP / opposite-side win /
    PA pass / successful fire.

    Returns None when the observer isn't wired (test envs); otherwise a
    dict that's always renderable — the template shows "no signal
    pending" when `cached=False`. Operators watching the dashboard during
    a hot trading window can read this to know "we're currently watching
    a sell-side signal that's been waiting 4 min for vwap+structure to
    align."

    Shape:
      {
        cached: bool,
        side: 'buy' | 'sell' | None,
        signal: str | None,                   # trigger_signal from cached payload
        cached_at: iso | None,
        cached_at_et: short str | None,
        bars_waited: int,                     # 0 when not cached
        seconds_waited: int,
        last_failed: [str, ...],              # validators failing as of most recent pa_validation_decision
        last_pa_decision_reason: str | None,
      }
    """
    observer = getattr(deps, "bitunix_observer", None)
    if observer is None:
        return None
    payload = getattr(observer, "_pending_pa_payload", None)
    side = getattr(observer, "_pending_pa_side", None)
    cached_at = getattr(observer, "_pending_pa_cached_at_ts", None)
    if payload is None or cached_at is None:
        return {
            "cached": False,
            "side": None,
            "signal": None,
            "cached_at": None,
            "cached_at_et": None,
            "bars_waited": 0,
            "seconds_waited": 0,
            "last_failed": [],
            "last_pa_decision_reason": None,
        }
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    delta_s = (now - cached_at).total_seconds()
    cached_signal = (payload.get("signal") or "").strip().lower() or None

    # Best-effort enrichment: most-recent pa_validation_decision REJECT
    # for this same trigger_signal. Tells the operator WHICH validators
    # are blocking right now. Bounded query — 50 rows is plenty.
    last_failed: list[str] = []
    last_reason: str | None = None
    try:
        db_url = getattr(deps, "db_url", None)
        if db_url and cached_signal:
            with db.connect(db_url) as conn:
                rows = conn.execute(
                    "SELECT payload_json FROM audit_event "
                    "WHERE kind = 'pa_validation_decision' "
                    "ORDER BY id DESC LIMIT 50"
                ).fetchall()
            for r in rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    continue
                if (p.get("trigger_signal") or "").lower() != cached_signal:
                    continue
                if (p.get("decision") or "").upper() != "REJECT":
                    continue
                last_failed = list(p.get("failed") or [])
                last_reason = p.get("reason")
                break
    except Exception as e:
        log.warning("bitunix pending PA enrichment query failed: %s", e)

    return {
        "cached": True,
        "side": side,
        "signal": cached_signal,
        "cached_at": cached_at.isoformat(),
        "cached_at_et": format_et_short(cached_at.isoformat()),
        "bars_waited": int(delta_s // 180),
        "seconds_waited": int(delta_s),
        "last_failed": last_failed,
        "last_pa_decision_reason": last_reason,
    }


def build_bitunix_pa_view(db_url: str, deps: Any) -> dict | None:
    """trade-plan PR 6 — PA validators (not scored) panel.

    Reads recent `pa_validation_decision` audit rows. Returns the latest
    decision plus the last N for trend review. Returns None if the
    observer / PA config isn't wired.

    Shape:
      {
        enabled: bool,            # PA config present
        mode: 'shadow'|'enforce', # latest decision's mode
        latest: {ts, decision, passed, failed, rush_fall_triggered, reason,
                 mode, score_side, score_tier, trigger_signal} | None,
        recent: [...latest entries...],  # up to 10
        counts: {pass: int, reject: int, rush_fall: int},  # over recent
        redeem_counts: {redeemed_24h: int, expired_score_decay_24h: int,
                        expired_opposite_side_24h: int},  # deferred-fire
        recent_redeems: [{ts_et, signal, bars_waited, side, order_id}, ...up to 5],
        recent_expired: [{ts_et, signal, reason, bars_waited, side}, ...up to 5],
      }
    """
    observer = getattr(deps, "bitunix_observer", None)
    pa_cfg = getattr(observer, "pa_config", None) if observer else None
    if pa_cfg is None:
        return None

    recent: list[dict] = []
    counts = {"pass": 0, "reject": 0, "rush_fall": 0}
    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind = 'pa_validation_decision' "
                "ORDER BY ts DESC LIMIT 10"
            ).fetchall()
            for r in rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    p = {}
                decision = (p.get("decision") or "").upper()
                entry = {
                    "ts": r["ts"],
                    "ts_et": format_et_short(r["ts"]),
                    "decision": decision,
                    "passed": p.get("passed") or [],
                    "failed": p.get("failed") or [],
                    "rush_fall_triggered": bool(p.get("rush_fall_triggered")),
                    "reason": p.get("reason"),
                    "mode": p.get("mode"),
                    "score_side": p.get("score_side"),
                    "score_tier": p.get("score_tier"),
                    "trigger_signal": p.get("trigger_signal"),
                }
                recent.append(entry)
                if decision == "PASS":
                    counts["pass"] += 1
                elif decision == "REJECT":
                    counts["reject"] += 1
                if entry["rush_fall_triggered"]:
                    counts["rush_fall"] += 1
    except Exception as e:
        log.warning("bitunix PA panel query failed: %s", e)

    # Deferred-fire aggregates: 24h window counts + recent 5 of each
    # audit kind. Bounded queries — these are summary tiles, not the
    # detail timeline.
    redeem_counts = {
        "redeemed_24h": 0,
        "expired_score_decay_24h": 0,
        "expired_opposite_side_24h": 0,
    }
    recent_redeems: list[dict] = []
    recent_expired: list[dict] = []
    try:
        cutoff_24h = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()
        with db.connect(db_url) as conn:
            redeem_count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_event "
                "WHERE kind='pa_validation_redeem' AND ts >= ?",
                (cutoff_24h,),
            ).fetchone()
            if redeem_count_row:
                redeem_counts["redeemed_24h"] = int(redeem_count_row["n"])
            exp_rows = conn.execute(
                "SELECT json_extract(payload_json, '$.reason') AS reason, "
                "COUNT(*) AS n FROM audit_event "
                "WHERE kind='pa_validation_expired' AND ts >= ? "
                "GROUP BY reason",
                (cutoff_24h,),
            ).fetchall()
            for r in exp_rows:
                reason = r["reason"] or "unknown"
                if reason == "score_decay":
                    redeem_counts["expired_score_decay_24h"] = int(r["n"])
                elif reason == "opposite_side":
                    redeem_counts["expired_opposite_side_24h"] = int(r["n"])
            redeem_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='pa_validation_redeem' "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
            for r in redeem_rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    continue
                recent_redeems.append({
                    "ts_et": format_et_short(r["ts"]),
                    "signal": p.get("trigger_signal"),
                    "bars_waited": p.get("bars_waited"),
                    "seconds_waited": p.get("seconds_waited"),
                    "side": p.get("final_side"),
                    "tier": p.get("final_tier"),
                    "order_id": p.get("order_id"),
                })
            expired_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind='pa_validation_expired' "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
            for r in expired_rows:
                try:
                    p = json.loads(r["payload_json"]) if r["payload_json"] else {}
                except Exception:
                    continue
                recent_expired.append({
                    "ts_et": format_et_short(r["ts"]),
                    "signal": p.get("trigger_signal"),
                    "reason": p.get("reason"),
                    "bars_waited": p.get("bars_waited"),
                    "seconds_waited": p.get("seconds_waited"),
                    "side": p.get("cached_side"),
                })
    except Exception as e:
        log.warning("bitunix PA redeem aggregate query failed: %s", e)

    return {
        "enabled": bool(getattr(pa_cfg, "enabled", False)),
        "mode": (recent[0]["mode"] if recent else None),
        "latest": recent[0] if recent else None,
        "recent": recent,
        "counts": counts,
        "redeem_counts": redeem_counts,
        "recent_redeems": recent_redeems,
        "recent_expired": recent_expired,
    }


def build_bitunix_decision_flow_view(db_url: str, deps: Any) -> dict | None:
    """trade-plan PR 6 — score → PA → HTF → outcome timeline (last 5).

    Pulls the last 5 score-decided audits and joins each to the closest
    `pa_validation_decision` and `htf_gate_decision` audit by trigger
    timestamp (within ±60s). Outcome comes from the score-decided row's
    `outcome` field (which captures the final disposition: placed,
    skipped_*, etc.).

    Returns None if the observer isn't wired. Empty `flows` list is a
    valid return when the observer is wired but no fires have occurred
    yet — the panel renders an empty state.

    Shape:
      {
        flows: [
          {ts_et, trigger_signal, trigger_side, alert_tf,
           score: {tier, side, net},
           pa: {decision, failed, rush_fall, mode} | None,
           htf: {regime, size_multiplier, permission_reason, mode} | None,
           outcome: str},
          ...up to 5
        ]
      }

    `trigger_side` is the intrinsic side of the trigger signal as
    declared in `bitunix_futures.scoring.factors.<name>.side` ("buy" /
    "sell"), or None for unknown / non-factor signals. Used by the
    template to color-code the trigger column independent of the
    aggregate order side.
    """
    observer = getattr(deps, "bitunix_observer", None)
    if observer is None:
        return None

    # PR 6 followup — surface each trigger signal's intrinsic side so the
    # panel can color-code buy-named vs sell-named signals independent of
    # the AGGREGATE score's resulting order side. Without this, a buy-named
    # trigger (e.g. mc_a_longema) sitting next to a SELL outcome reads like
    # a bug; with it, the disconnect is explanatory ("buy signal was
    # overruled by net-bearish confluence"). Lookup is best-effort —
    # unknown signals (guards, pa factors, future TV signals) return None.
    scoring_cfg = getattr(observer, "scoring_config", None)
    factors_map = getattr(scoring_cfg, "factors", {}) if scoring_cfg else {}

    def _intrinsic_side(signal_name: str | None) -> str | None:
        if not signal_name:
            return None
        f = factors_map.get(signal_name.lower())
        if f is None:
            # Match the scorer's strip-suffix fallback in _resolve_factor
            from trading_corp.agents.strategies.bitunix_confluence import (
                _strip_directional_suffix,
            )
            f = factors_map.get(_strip_directional_suffix(signal_name))
        return getattr(f, "side", None) if f is not None else None

    flows: list[dict] = []
    try:
        with db.connect(db_url) as conn:
            score_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind = 'bitunix_score_decided' "
                "ORDER BY ts DESC LIMIT 5"
            ).fetchall()
            if not score_rows:
                return {"flows": []}
            # Cache the last 100 PA + HTF rows once; nearest-by-ts join
            # in Python avoids per-fire SQL.
            pa_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind = 'pa_validation_decision' "
                "ORDER BY ts DESC LIMIT 100"
            ).fetchall()
            htf_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind = 'htf_gate_decision' "
                "ORDER BY ts DESC LIMIT 100"
            ).fetchall()
            # Deferred-fire: pull recent redeem audits so each fire row
            # can be tagged "redeemed (waited N bars)" if it came from
            # the bar-tick re-eval path. Same nearest-by-signal join
            # pattern as PA/HTF below.
            redeem_rows = conn.execute(
                "SELECT ts, payload_json FROM audit_event "
                "WHERE kind = 'pa_validation_redeem' "
                "ORDER BY ts DESC LIMIT 100"
            ).fetchall()
    except Exception as e:
        log.warning("bitunix decision flow query failed: %s", e)
        return {"flows": []}

    def _nearest_by_signal(
        rows: list[Any], target_ts: str, target_signal: str | None,
    ) -> dict | None:
        """Find the audit row with the same trigger_signal whose ts is
        closest to `target_ts` within 60s. None if no match in window."""
        target_dt = _parse_audit_ts(target_ts)
        if target_dt is None:
            return None
        best: tuple[float, dict] | None = None
        for r in rows:
            try:
                p = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except Exception:
                continue
            if target_signal and p.get("trigger_signal") != target_signal:
                continue
            r_dt = _parse_audit_ts(r["ts"])
            if r_dt is None:
                continue
            delta = abs((r_dt - target_dt).total_seconds())
            if delta > 60:
                continue
            if best is None or delta < best[0]:
                best = (delta, p)
        return best[1] if best else None

    for r in score_rows:
        try:
            sp = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except Exception:
            continue
        trigger = sp.get("trigger_signal")
        pa_p = _nearest_by_signal(pa_rows, r["ts"], trigger)
        htf_p = _nearest_by_signal(htf_rows, r["ts"], trigger)
        redeem_p = _nearest_by_signal(redeem_rows, r["ts"], trigger)
        # Score-decided's trigger_source field tells us if this fire came
        # from a redeem tick. The redeem audit row (when present) carries
        # the bars/seconds-waited metadata.
        is_redeem_source = (sp.get("trigger_source") == "bar_tick_redeem")
        flows.append({
            "ts": r["ts"],
            "ts_et": format_et_short(r["ts"]),
            "trigger_signal": trigger,
            "trigger_side": _intrinsic_side(trigger),
            "alert_tf": _infer_alert_tf(trigger),
            "score": {
                "tier": sp.get("tier"),
                "side": sp.get("side"),
                "net": sp.get("net_score"),
            },
            "pa": ({
                "decision": (pa_p.get("decision") or "").upper(),
                "failed": pa_p.get("failed") or [],
                "rush_fall": bool(pa_p.get("rush_fall_triggered")),
                "mode": pa_p.get("mode"),
            } if pa_p else None),
            "htf": ({
                "regime": htf_p.get("regime"),
                "size_multiplier": htf_p.get("size_multiplier"),
                "permission_reason": htf_p.get("permission_reason"),
                "mode": htf_p.get("mode"),
            } if htf_p else None),
            "outcome": sp.get("outcome"),
            "redeemed": is_redeem_source,
            "redeem": ({
                "bars_waited": redeem_p.get("bars_waited"),
                "seconds_waited": redeem_p.get("seconds_waited"),
            } if redeem_p else None),
        })

    return {"flows": flows}


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
                trigger_signal = p.get("trigger_signal")
                entry = {
                    "ts": row_ts,
                    "ts_et": format_et_short(row_ts),
                    "signal": trigger_signal,
                    "alert_tf": _infer_alert_tf(trigger_signal),
                    "tier": p.get("tier"),
                    "side": p.get("side"),
                    "net": p.get("net_score"),
                    "fb": p.get("final_buy_score"),
                    "fs": p.get("final_sell_score"),
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

            # PR 6 — recent fires sourced from paper_trade_record (NOT the
            # legacy `would_have_placed` audit) so we can surface v2
            # trade-plan extras: tp1/tp2/tp3, sl_method, tp2_method,
            # htf_size_multiplier, funding_rate_at_decision.
            #
            # Tradeoff: pre-PR-5 audit-only fires (where no paper_trade_record
            # row was written) NO LONGER appear in this panel. Acceptable
            # today since BitUnix is paper-only and every fire writes a
            # paper_trade_record row. If a non-paper division ever reuses
            # this view shape, revisit — they may need a UNION fallback.
            fire_rows = conn.execute(
                "SELECT order_id, ts, side, qty, entry_reference_price, "
                "stop_price, tp_price, tier, source_signal, result, "
                "actual_r_multiple, extra_json "
                "FROM paper_trade_record "
                "WHERE division = 'bitunix_futures' "
                "ORDER BY ts DESC LIMIT 10"
            ).fetchall()
            sl_lifecycle_by_order = _load_latest_sl_lifecycle_states(conn)
            fee_cfg = _bitunix_fee_config(deps)
            for r in fire_rows:
                try:
                    extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
                except Exception:
                    extra = {}
                entry_px = r["entry_reference_price"]
                fee_floor = (
                    2.0 * fee_cfg.round_trip_cost_pct() * float(entry_px)
                    if fee_cfg is not None and entry_px
                    else None
                )
                result_native = r["result"]
                actual_r_multiple_native = r["actual_r_multiple"]
                audit_corrected = bool(extra.get("audit_corrected"))
                corrected_result = extra.get("corrected_result")
                corrected_r_multiple = extra.get("corrected_r_multiple")
                display_result = corrected_result if (audit_corrected and corrected_result is not None) else result_native
                display_r = corrected_r_multiple if (audit_corrected and corrected_r_multiple is not None) else actual_r_multiple_native
                correction_tooltip = None
                if audit_corrected:
                    try:
                        correction_tooltip = (
                            f"Native: {result_native}/{float(actual_r_multiple_native):+.3f}R"
                            f" · Corrected: {corrected_result}/{float(corrected_r_multiple):+.3f}R"
                        )
                    except (TypeError, ValueError):
                        correction_tooltip = f"Native: {result_native} · Corrected: {corrected_result}"
                recent_fires.append({
                    "order_id": r["order_id"],
                    "ts": r["ts"],
                    "ts_et": format_et_short(r["ts"]),
                    "tier": r["tier"],
                    "side": r["side"],
                    "qty": r["qty"],
                    "entry": entry_px,
                    "stop": r["stop_price"],
                    "tp": r["tp_price"],
                    "tp1": extra.get("tp1_price"),
                    "tp2": extra.get("tp2_price"),
                    "tp3": extra.get("tp3_price"),
                    "sl_method": extra.get("sl_method"),
                    "tp2_method": extra.get("tp2_method"),
                    "htf_size_multiplier": extra.get("htf_size_multiplier"),
                    "funding_rate_at_decision": extra.get("funding_rate_at_decision"),
                    "fee_floor_dollars": fee_floor,
                    "sl_lifecycle_state": sl_lifecycle_by_order.get(r["order_id"]),
                    "result": result_native,
                    "result_native": result_native,
                    "actual_r_multiple_native": actual_r_multiple_native,
                    "audit_corrected": audit_corrected,
                    "corrected_result": corrected_result,
                    "corrected_r_multiple": corrected_r_multiple,
                    "display_result": display_result,
                    "display_r": display_r,
                    "correction_tooltip": correction_tooltip,
                    "net_score": extra.get("net_score"),
                    "trigger_signal": r["source_signal"],
                    "alert_tf": _infer_alert_tf(r["source_signal"]),
                    "tp_plan_version": extra.get("tp_plan_version"),
                })

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


def _build_pmcc_tile_status(symbol: str, *, db_url, now, cfg: dict, agent=None, slug=None) -> dict:
    """Resolve the unified tile/expert decision status for one PMCC underlying.

    Loads the single per-asset decision record (the SAME one the Expert panel
    reads, so they can't disagree) and classifies it fresh|stale|none. The label
    is the EFFECTIVE post-gate status (via `_pmcc_status.effective_status`) — the raw
    judgment only when actually actionable; else EARNINGS WINDOW / CAN'T PRICE — so the
    tile can never show a stale action the downstream gates already suppressed. When
    `agent`/`slug` are passed it reads the cheap earnings gate + the live pricing cache;
    without them it degrades to the raw label. Never raises (a status read must not break
    the page).
    """
    from trading_corp.agents.divisions import _pmcc_status
    cfg = cfg or {}
    staleness_h = float(cfg.get("staleness_hours", _pmcc_status.DEFAULT_STALENESS_HOURS))
    rec = _pmcc_status.load_decision(symbol, db_url=db_url)
    state = _pmcc_status.classify_freshness(rec, now, staleness_h)
    out = {
        "state": state,
        "stale_label": cfg.get("stale_label", "stale"),
        "no_signal_label": cfg.get("no_signal_label", "awaiting scan"),
        "status_label": None,
        "urgency": "routine",
        "source": None,
        "age_h": None,
        "suppressed": False,
        "reason": None,
        "actionable": False,
    }
    if state in ("fresh", "stale") and isinstance(rec, dict):
        _raw = (rec.get("status") or "").lower()
        # Gather the SAME effective-status inputs the Expert panel uses — only for a
        # placeable action (hold/watch have nothing to gate): the cheap earnings gate
        # (24h-cached) + the live pricing buildability already in the pmcc_pricing cache.
        _earn_state, _earn_reason = None, None
        _buildable, _price_reason, _mkt_closed = None, None, False
        if _raw in _pmcc_status.PLACEABLE_ACTIONS:
            if agent is not None:
                try:
                    _earn_state, _earn_reason = agent._earnings_gate_state(symbol)
                except Exception:      # noqa: BLE001 — a status read must not break the page
                    _earn_state, _earn_reason = None, None
            try:
                from trading_corp.web import pmcc_pricing
                _pr = pmcc_pricing.cached(slug, symbol) if slug else None
                _mkt_closed = (bool(_pr.market_closed) if _pr is not None
                               else not pmcc_pricing.market_regular_open())
                # Off-hours the cached `buildable` is stale — treat as unknown so a stale
                # 'buildable' never reads as actionable once the session is closed.
                _buildable = None if _mkt_closed else (_pr.buildable if _pr is not None else None)
                _price_reason = None if _mkt_closed else (_pr.estimate_reason if _pr is not None else None)
            except Exception:      # noqa: BLE001 — pricing read must not break the page
                _buildable, _price_reason, _mkt_closed = None, None, False
        eff = _pmcc_status.effective_status(
            _raw, earnings_state=_earn_state, earnings_reason=_earn_reason,
            buildable=_buildable, price_reason=_price_reason, market_closed=_mkt_closed,
        )
        out["status_label"] = eff["label"]
        out["suppressed"] = eff["suppressed"]
        out["reason"] = eff["reason"]
        out["actionable"] = eff["actionable"]
        out["urgency"] = rec.get("urgency") or "routine"
        out["source"] = rec.get("source")
        out["age_h"] = _pmcc_status.age_hours(rec, now)
    return out


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
    # as the LEAP and the nearest-DTE short call as the short leg. Pass equity
    # share counts so the classifier can tell a shares-backed covered call from
    # a LEAP-covered PMCC (structure_type by cover, not by LEAP DTE).
    _shares_by_sym = {(h.symbol or "").upper(): h.qty for h in stock_holdings}
    pmcc_pairs, other_options = _group_pmcc_pairs(legs, prices, _shares_by_sym)

    # Attach the unified tile/expert decision status to each PMCC pair
    # (robinhood_pmcc only) — one timestamped verdict per asset, shared with the
    # Expert panel, classified fresh|stale|none. Best-effort; a status-read
    # failure leaves the tile at NO SIGNAL rather than breaking the page.
    if slug == "robinhood_pmcc" and pmcc_pairs:
        _ts_cfg = {}
        _agent = getattr(deps, "pmcc_agent", None)
        if _agent is not None:
            _ts_cfg = (getattr(_agent, "_cfg", {}) or {}).get("tile_status", {}) or {}
        _ts_now = datetime.now(timezone.utc)
        # P1 (2026-07-31): live pricing for ALL tiles — RH-only, NO LLM. Serve the
        # display cache (< TTL) else price (staggered, market-hours-gated inside
        # refresh_division → no pull pre/after-hours). Never blocks the page on a
        # pricing failure; a failure leaves _p.pricing = None (tile shows nothing new).
        # Runs BEFORE the unified status below so the effective-status gate can read the
        # CURRENT-load buildability from the just-refreshed pricing cache.
        for _p in pmcc_pairs:
            _p.pricing = None
        if _agent is not None and broker is not None:
            try:
                from trading_corp.web import pmcc_pricing
                await pmcc_pricing.refresh_division(
                    _agent, broker, slug,
                    [_p.underlying for _p in pmcc_pairs], deps.db_url,
                )
                for _p in pmcc_pairs:
                    _p.pricing = pmcc_pricing.tile_pricing_view(
                        pmcc_pricing.cached(slug, _p.underlying))
            except Exception as e:      # noqa: BLE001 — pricing must never break the page
                log.warning("pmcc tile pricing failed for %s: %s", slug, e)
        # Unified EFFECTIVE status (post-gate) — reads the just-refreshed pricing cache +
        # the cheap earnings gate so the tile can never disagree with the Expert panel.
        for _p in pmcc_pairs:
            _p.unified_status = _build_pmcc_tile_status(
                _p.underlying, db_url=deps.db_url, now=_ts_now, cfg=_ts_cfg,
                agent=_agent, slug=slug,
            )

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
    bitunix_pa_view: dict | None = None
    bitunix_decision_flow_view: dict | None = None
    bitunix_pending_pa_view: dict | None = None
    bitunix_trade_plan_view: dict | None = None
    gate_a_view: dict | None = None
    if slug == "bitunix_futures":
        try:
            bitunix_score_view = build_bitunix_score_view(deps.db_url, deps)
        except Exception as e:
            log.warning("bitunix score view for %s failed: %s", slug, e)
        try:
            bitunix_htf_view = build_bitunix_htf_view(deps)
        except Exception as e:
            log.warning("bitunix HTF view for %s failed: %s", slug, e)
        try:
            bitunix_pa_view = build_bitunix_pa_view(deps.db_url, deps)
        except Exception as e:
            log.warning("bitunix PA view for %s failed: %s", slug, e)
        try:
            bitunix_decision_flow_view = build_bitunix_decision_flow_view(deps.db_url, deps)
        except Exception as e:
            log.warning("bitunix decision flow view for %s failed: %s", slug, e)
        try:
            bitunix_pending_pa_view = build_bitunix_pending_pa_view(deps)
        except Exception as e:
            log.warning("bitunix pending PA view for %s failed: %s", slug, e)
        try:
            bitunix_trade_plan_view = build_bitunix_trade_plan_view(deps.db_url, deps)
        except Exception as e:
            log.warning("bitunix trade_plan view for %s failed: %s", slug, e)
        try:
            gate_a_view = gate_a_resilience_24h(deps.db_url)
        except Exception as e:
            log.warning("gate_a view for %s failed: %s", slug, e)

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
        bitunix_pa=bitunix_pa_view,
        bitunix_decision_flow=bitunix_decision_flow_view,
        bitunix_pending_pa=bitunix_pending_pa_view,
        bitunix_trade_plan=bitunix_trade_plan_view,
        gate_a=gate_a_view,
        is_live=_division_is_live(broker),
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
    # Set when the selected (single) division is in DASHBOARD_RT_CUTOFFS.
    # cutoff_ts: full ISO; cutoff_label: YYYY-MM-DD for the tile badge.
    cutoff_ts: str | None = None
    cutoff_label: str | None = None
    # Option-B (distinct-market) aggregates for Kalshi divisions only.
    # One row per distinct ticker (canonical = earliest entry_ts emission).
    # None for non-kalshi divisions (or the All view mixing venues).
    # n_resolved_markets / n_wins_markets / total_realized_markets_pnl /
    # win_rate_markets_pct are additive siblings of the per-emission fields;
    # the existing per-emission (Option-A) fields are UNCHANGED.
    # TODO(display): wire these into the prediction-markets tile template.
    n_resolved_markets: int | None = None
    n_wins_markets: int | None = None
    n_voids_markets: int | None = None
    total_realized_markets_pnl: float | None = None
    win_rate_markets_pct: float | None = None


@dataclass
class PMDivisionOption:
    """Dropdown entry."""
    slug: str
    display_name: str
    venue: str                       # 'polymarket' | 'kalshi'


@dataclass
class PMWhaleRow:
    """One row of the cross-venue Whales tab.

    Aggregates per-whale stats across BOTH our resolved round-trips and
    currently-open paper positions for the copy-trading divisions
    (polymarket_copy_trading + kalshi_copy_trading).
    """
    handle: str                  # whale handle/username (Pedrobeliever47, smedtoshi, ...)
    venue: str                   # 'polymarket' | 'kalshi'
    division: str                # 'polymarket_copy_trading' | 'kalshi_copy_trading'
    n_resolved: int              # our copies that resolved
    n_wins: int
    n_losses: int
    win_rate_pct: float | None   # None pre-first-resolve
    total_realized_pnl: float
    n_open: int                  # OUR copies still open (whale still holds)
    last_entry_ts: str | None    # ISO; most-recent entry we placed for this whale
    # Identifier the dashboard endpoints use to address this whale.
    # Kalshi: same as `handle` (nickname). Polymarket: proxy_wallet
    # (because the Polymarket strategy keys selected_whales/pinned_whales
    # by wallet, not by user_name). Empty string until the query layer
    # populates it from the per-venue selected_whales slot.
    actor_id: str = ""
    # True when the whale was manually promoted via the dashboard and is
    # in `agent_state(<actor>, pinned_whales)`. Template renders a 📌
    # badge so the user can tell algorithm-picked vs manually-managed
    # entries apart.
    is_pinned: bool = False
    # Per-whale copy-quality intel from our own audit_event + kalshi_round_trips.
    # Zero/None defaults. Kalshi rows only; Polymarket rows stay at defaults.
    intel_copies: int = 0                        # would_have_placed entry count
    intel_detections: int = 0                    # copies + no_side + sports skips
    intel_no_side: int = 0                       # kalshi_copy_entry_skipped_no_side count
    intel_sports: int = 0                        # kalshi_copy_entry_skipped_sports count
    intel_copyability_pct: float | None = None   # 100 * copies / detections
    intel_net_pnl: float | None = None           # fee+slip adjusted PnL (vs gross total_realized_pnl)
    intel_days_since_last_copy: float | None = None  # days since last entry copy
    intel_crypto_pct: float | None = None        # % of our resolved copies in crypto tickers


@dataclass
class KalshiWatchOnlyRow:
    """One row of the K3 Watch List panel — whales we observe but do NOT copy.

    Source: `agent_state(kalshi_copy_trader, watch_only_stats)`, refreshed
    daily by `refresh_kalshi_watchlist_stats.py`. Stats reflect the whale's
    OWN Kalshi performance (from Apify closed_positions), NOT our copied
    trades. Promote a row via the (future) `[Promote]` flow to move the
    handle onto the active `selected_whales` roster.
    """
    handle: str
    tier: int | None                 # 1 = public-name traders, 2 = curators
    source_x_handle: str | None      # provenance — the X.com handle we sourced from
    notes: str | None
    resolved_count: int              # whale's resolved positions visible via Apify
    wins: int
    losses: int
    win_rate_pct: float | None       # None when resolved_count == 0
    total_pnl: float                 # whale's realized PnL (Apify units — dollars)
    avg_pnl_per_contract: float
    top_category: str | None         # first entry of top_categories tuple
    n_open: int                      # whale's currently-open positions
    lifetime_markets_traded: int
    last_refresh_iso: str | None
    # Per-whale copy-quality intel from our own audit_event + kalshi_round_trips.
    # Zero/None defaults so old code paths that don't call _query_kalshi_whale_intel
    # still render cleanly. Populated by _query_kalshi_watch_only_rows.
    intel_copies: int = 0                        # would_have_placed entry count
    intel_detections: int = 0                    # copies + no_side + sports skips
    intel_no_side: int = 0                       # kalshi_copy_entry_skipped_no_side count
    intel_sports: int = 0                        # kalshi_copy_entry_skipped_sports count
    intel_copyability_pct: float | None = None   # 100 * copies / detections
    intel_net_pnl: float | None = None           # fee+slip adjusted PnL on our resolved copies
    intel_n_resolved: int = 0                    # our resolved round-trips for this whale
    intel_hit_rate_pct: float | None = None      # win rate on our resolved copies
    intel_days_since_last_copy: float | None = None  # days since last entry copy
    intel_crypto_pct: float | None = None        # % of our resolved copies in crypto tickers


@dataclass
class PolymarketWatchOnlyRow:
    """One row of the Polymarket Watch List panel — whales we observe but do NOT copy.

    Source: `agent_state(polymarket_copy_trader, watch_only_whales)`, written
    by the top-50 sweep. Stats reflect each whale's own Polymarket performance
    from the Gamma/leaderboard API. win_rate_pct is converted from the
    0..1 source value to 0..100 for template parity with KalshiWatchOnlyRow.
    """
    rank: int
    user_name: str
    proxy_wallet: str
    x_username: str | None
    verified_badge: bool
    total_resolved_positions: int
    wins: int
    losses: int
    win_rate_pct: float | None        # None when total_resolved_positions == 0; else 0..100
    realized_pnl_usdc: float
    lifetime_pnl_from_leaderboard: float
    lifetime_vol_from_leaderboard: float
    best_category: str
    included_iso: str | None
    # Windowed-scoring fields (added 2026-05-23). Optional/defaulted for
    # back-compat — agent_state entries written before the windowed seed
    # shipped won't carry them. Templates should treat 0/None as "absent".
    window_size_n: int = 0
    window_days_span: float = 0.0
    last_trade_iso: str | None = None
    provisional: bool = False
    # Entry-price edge proxies. `avg_entry_price` is the mean BUY price
    # across the window; `share_below_70` is the fraction of windowed BUYs
    # entered at <$0.70. Low avg + high share-below-70 = sharp/contrarian
    # whale; high avg + near-zero share-below-70 = capital-driven favorite-
    # farmer. Visible columns; NOT a filter gate.
    avg_entry_price: float = 0.0
    share_below_70: float = 0.0


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
    whales: list[PMWhaleRow]         # populated for copy_trading divisions; empty otherwise
    kalshi_watch_only: list[KalshiWatchOnlyRow]   # K3 watch-list panel; empty unless kalshi_copy_trading is selected
    polymarket_watch_only: list[PolymarketWatchOnlyRow]  # Polymarket watch-list panel; empty unless polymarket_copy_trading is selected
    # Current Polymarket Watch List sort state (server-side sort, persisted
    # via `?pm_watch_sort=&pm_watch_desc=` query params). The template reads
    # these to render the active-column arrow + compute the toggle URL.
    pm_watch_sort: str | None = None  # whitelisted user-facing key (e.g. "avg_entry_price")
    pm_watch_desc: bool = True
    # Kalshi Watch List sort state (server-side sort, same pattern as polymarket).
    kalshi_watch_sort: str | None = None  # whitelisted user-facing key
    kalshi_watch_desc: bool = True
    # Kalshi Watch List active filter toggles (URL query params; default off).
    kalshi_hide_uncopyable: bool = False  # hide rows where copyability_pct < 5%
    kalshi_hide_net_neg: bool = False     # hide rows where net_pnl < 0 at n_resolved >= 30
    # Kalshi Selected Whales panel sort + filter state (independent of Watch List).
    kalshi_selected_sort: str | None = None  # whitelisted intel key (e.g. "net_pnl")
    kalshi_selected_desc: bool = True
    kalshi_sel_hide_uncopyable: bool = False
    kalshi_sel_hide_net_neg: bool = False
    # Polymarket metrics-epoch (None when unset). Read from
    # agent_state(polymarket_copy_trader, metrics_epoch). Surfaced on the
    # view so the template can render a "metrics since {epoch}" badge.
    # The filter itself is applied inside each PM query helper; this field
    # is just the UI signal that an epoch is active.
    pm_metrics_epoch: str | None = None
    # Set only when the selected division is 'kalshi_crypto'. None
    # on every other division and on the All Prediction Markets view.
    # Owns the vol-v2 paper-validation cards; see
    # `web/kalshi_crypto_vol_v2.py` for the module.
    vol_v2_block: "PMVolV2Block | None" = None
    # Paper/Live/All stats toggle state — kalshi_copy_trading only. `wr_mode`
    # is the active slice ('live' default; 'paper'|'all'); `wr_live_epoch` is
    # the go-live ISO boundary used for the "since <date>" label. On every
    # other division the template doesn't render the toggle, so these are
    # inert UI signals.
    wr_mode: str = "live"
    wr_live_epoch: str = ""


_POLYMARKET_PREFIX = "polymarket_"
_KALSHI_PREFIX = "kalshi_"
# Poly->Kalshi copy divisions (live) — slug 'poly_kalshi_*'. Disjoint from the two
# prefixes above ('poly_kalshi_mlb' starts with neither 'polymarket_' nor 'kalshi_'),
# so these rows are bucketed by their OWN branch (different kind/actor/field names).
_POLY_KALSHI_PREFIX = "poly_kalshi_"

# Per-division entry_ts cutoffs. Round-trips with `entry_ts < cutoff` are
# excluded from dashboard aggregates (tile counts, win rate, history list)
# because their logic predates a deployed fix and including them would
# misrepresent current-logic performance. Pre-cutoff rows are NOT deleted
# — they remain in `kalshi_round_trips` for σ-scaling work and forensic
# diffs. Rollback = empty this dict.
#
# Add a new entry here when you deploy a strategy fix big enough that
# pre-fix PnL would mislead the dashboard. Don't filter speculatively;
# only filter when bet-side or sizing math changed.
DASHBOARD_RT_CUTOFFS: dict[str, str] = {
    # Advanced 2026-05-20 from the 2026-05-16 bucket-guard fix to each
    # strategy's own logic-change date. Pre-cutoff rows remain queryable
    # in `kalshi_round_trips` (forensic + σ-scaling work); they're only
    # filtered out of dashboard aggregates.
    "kalshi_weather": "2026-05-26T01:08:00+00:00",  # bias-offset v1 deploy (22 cells, magnitude-filtered ≥1.0°F, 9 spring fully-validated + 13 non-spring nbm-only watch-items). Advanced from 2026-05-22T16:25 (P3 xref deploy). First attempt 2026-05-26 00:24 UTC crash-looped on missing residual_logic dependency (rolled back 00:44); successful re-deploy at 2026-05-26 01:10:33 UTC after inlining derive_season. cutoff pre-picked at 01:08 (2.5 min before actual restart) — minor sliver of 01:08-01:10 pre-bias rows passes the dashboard filter. See deploy_log.md 2026-05-26 01:10 UTC.
    "kalshi_crypto":  "2026-05-20T05:52:09+00:00",  # vol-v2 + max_divergence_pct live — see deploy_log.md 2026-05-20 05:52 UTC (matches KALSHI_CRYPTO_VOL_V2_CUTOFF in web/kalshi_crypto_vol_v2.py)
    "kalshi_llm_arbitrage": "2026-07-07T16:40:00+00:00",  # discovery narrowed to [Economics, Elections] + resolver leg_date fix (deploy_log 2026-07-07 16:40 UTC; commits b5eb93f/d1f5ea6). Scopes dashboard round-trip metrics to post-change entries; the OPEN tab honors the same cutoff via _query_pm_open_trades (_llm_cut).
}


def _get_kalshi_division_epoch(db_url: str, slug: str) -> str | None:
    """CP5: runtime, reversible per-division dashboard cutoff for a kalshi division,
    read from agent_state[<slug>/metrics_epoch] (ISO-validated by `_get_metrics_epoch`).

    Mirror of `_get_polymarket_metrics_epoch`, keyed on the division slug itself
    (which IS the audit actor for these divisions, e.g. 'poly_kalshi_mlb'). Returns
    None when unset -> the hardcoded `DASHBOARD_RT_CUTOFFS` fallback applies. Set at
    runtime with agent_state(<slug>,'metrics_epoch','<ISO>'); revert to all-time by
    deleting that row (no redeploy)."""
    return _get_metrics_epoch(db_url, slug)


def _kalshi_cutoff_clause(
    ts_col: str, *, division_slugs: list[str] | None = None, db_url: str | None = None,
) -> str:
    """SQL fragment that excludes pre-cutoff `kalshi_round_trips` rows. Returns a
    string starting with a leading space, suitable to concatenate after any existing
    WHERE clause; empty string when there are no cutoffs (rollback path).

    Per-division cutoff resolution (CP5): the effective cutoff for a division is an
    `agent_state[<slug>/metrics_epoch]` override (runtime, reversible) when set, ELSE
    the hardcoded `DASHBOARD_RT_CUTOFFS` entry. agent_state overrides are resolved
    ONLY for the passed `division_slugs` (needs `db_url`); called with neither (the
    default), the behavior is EXACTLY the pre-CP5 hardcoded dict — so back-compat
    callers and the cross-division overview are unchanged.

    Injection-safe: agent_state epochs are ISO-validated (`_get_kalshi_division_epoch`
    -> `_get_metrics_epoch`) and hardcoded cutoffs are literals, so both inline safely
    — same pattern as `_polymarket_cutoff_clause`.
    """
    cutoffs = dict(DASHBOARD_RT_CUTOFFS)
    if division_slugs and db_url:
        for slug in division_slugs:
            epoch = _get_kalshi_division_epoch(db_url, slug)
            if epoch:                 # agent_state override wins over the hardcoded entry
                cutoffs[slug] = epoch
    parts = [
        f" AND NOT (division='{div}' AND {ts_col} < '{cutoff}')"
        for div, cutoff in cutoffs.items()
    ]
    return "".join(parts)


def _kalshi_division_epoch_clause(
    division_slugs: list[str], db_url: str, *, ts_col: str, div_col: str,
) -> str:
    """CP5 (symmetric): the audit-event-path counterpart of `_kalshi_cutoff_clause`,
    for the OPEN tab + pending badge. Per passed slug, emits an
    `AND NOT (<div_col>='<slug>' AND <ts_col> < '<cutoff>')` term where the cutoff is
    the agent_state[<slug>/metrics_epoch] override (precedence) ELSE the hardcoded
    `DASHBOARD_RT_CUTOFFS` entry — so a set epoch hides pre-epoch OPEN rows exactly as
    it hides resolved rows.

    SCOPED to the passed `division_slugs` (does NOT touch the 6-actor arb audit path /
    the inline `_llm_cut`), and takes a parameterizable `div_col` because the audit
    path filters on `json_extract(a.payload_json,'$.division')`, not a bare column —
    same shape as `_polymarket_cutoff_clause`'s div_col. '' (no-op) when no slug has a
    cutoff. Injection-safe: agent_state epochs ISO-validated, hardcoded values literal.
    """
    parts = []
    for slug in division_slugs:
        cutoff = _get_kalshi_division_epoch(db_url, slug) or DASHBOARD_RT_CUTOFFS.get(slug)
        if cutoff:
            parts.append(f" AND NOT ({div_col}='{slug}' AND {ts_col} < '{cutoff}')")
    return "".join(parts)


# ── Kalshi copy-trading Paper/Live/All go-live epoch ────────────────────
#
# kalshi_round_trips has NO paper/live discriminator column — the split is
# purely temporal. Copies with `entry_ts >= epoch` are LIVE (real fills
# since go-live); earlier copies are historical PAPER copies. The dashboard's
# Paper|Live|All toggle scopes the summary / history / open lists to one
# slice (default LIVE). The epoch defaults to the constant below so the
# toggle works out-of-the-box, but an operator can override it at runtime
# (no redeploy) via agent_state(kalshi_copy_trader, metrics_epoch) — the
# same ISO-validated slot `_get_metrics_epoch` reads; clearing that row
# falls back to the constant.
KALSHI_COPY_LIVE_EPOCH = "2026-07-01T14:08:58+00:00"


def _get_kalshi_copy_live_epoch(db_url: str) -> str:
    """Go-live epoch for the kalshi_copy_trading Paper/Live split.

    Returns the operator override in agent_state(kalshi_copy_trader,
    metrics_epoch) when set (ISO-validated by `_get_metrics_epoch`), else the
    hardcoded `KALSHI_COPY_LIVE_EPOCH` constant. Always returns a usable ISO
    string (never None) so the toggle has a stable boundary out-of-the-box.
    """
    return _get_metrics_epoch(db_url, "kalshi_copy_trader") or KALSHI_COPY_LIVE_EPOCH


def _kalshi_copy_mode_clause(
    mode: str, epoch_iso: str, ts_col: str = "entry_ts",
) -> str:
    """SQL fragment scoping kalshi_copy_trading rows to a Paper/Live/All
    slice by timestamp. Returns a string starting with a leading space so it
    concatenates after an existing WHERE clause; '' for the 'all' slice (or
    when no epoch is available — the no-op / reversibility path).

      - 'live':  AND {ts_col} >= '{epoch}'   (copies since go-live)
      - 'paper': AND {ts_col} <  '{epoch}'   (historical paper copies)
      - else:    ''                          (no scoping)

    `mode` is whitelisted upstream to {'all','paper','live'} and `epoch_iso`
    is a validated ISO constant (never user input), so the inline literal is
    injection-safe — same pattern as `_kalshi_cutoff_clause` /
    `_polymarket_cutoff_clause`.
    """
    if not epoch_iso:
        return ""
    if mode == "live":
        return f" AND {ts_col} >= '{epoch_iso}'"
    if mode == "paper":
        return f" AND {ts_col} < '{epoch_iso}'"
    return ""


# ── Polymarket per-division metrics-epoch reset ─────────────────────────
#
# Where the Kalshi pattern above uses a hardcoded Python dict (requires
# redeploy to roll forward/back), the polymarket_copy_trading epoch lives
# in `agent_state(polymarket_copy_trader, metrics_epoch)`. Operator sets
# the ISO timestamp at runtime; clearing the slot (DELETE FROM agent_state
# WHERE agent='polymarket_copy_trader' AND key='metrics_epoch') fully
# restores the prior dashboard view. Pre-epoch rows are never deleted —
# they stay in polymarket_round_trips / audit_event / polymarket_equity_
# history for forensics; only dashboard aggregates filter them out.
#
# Coverage extends beyond round_trips: the curve filters on
# polymarket_equity_history.ts, and open/pending counts filter on
# audit_event.ts. polymarket_resolver.py:161+:306 sets
# polymarket_round_trips.entry_ts == the original BUY audit_event.ts
# byte-equal, so the dual filter (a.ts pre-resolution, entry_ts
# post-resolution) is filtering the same physical timestamp from two
# angles — no semantic gap.


def _get_polymarket_metrics_epoch(db_url: str) -> str | None:
    """Read agent_state(polymarket_copy_trader, metrics_epoch), validated.

    Returns the ISO-8601 timestamp string verbatim if it parses; otherwise
    None (the no-op path — equivalent to "no epoch set"). The validation
    is mandatory regardless of how trusted the write path is: the returned
    value is f-string-interpolated into SQL by `_polymarket_cutoff_clause`,
    so anything that escapes the parse becomes injection surface.

    We do NOT return the parsed datetime; we return the original string so
    it round-trips into SQL literally as the operator stored it. The parse
    is purely a typecheck.
    """
    try:
        rec = db.load_agent_state(
            "polymarket_copy_trader", "metrics_epoch", db_url=db_url,
        )
    except Exception as e:
        log.debug("metrics_epoch load failed: %s", e)
        return None
    if rec is None:
        return None
    val = rec[0]
    if not isinstance(val, str) or not val:
        return None
    try:
        datetime.fromisoformat(val)
    except (TypeError, ValueError):
        log.warning(
            "metrics_epoch value %r failed ISO-8601 parse — treating as unset",
            val,
        )
        return None
    return val


def _polymarket_cutoff_clause(
    epoch_iso: str | None,
    *,
    ts_col: str = "entry_ts",
    div_col: str = "division",
    div_value: str = "polymarket_copy_trading",
) -> str:
    """SQL fragment excluding pre-epoch rows for the specified polymarket
    division. Returns '' (no-op) when `epoch_iso` is None — the
    reversibility path. Mirrors `_kalshi_cutoff_clause` shape.

    `epoch_iso` is assumed pre-validated by `_get_polymarket_metrics_epoch`
    (datetime.fromisoformat round-trip). Passing an unvalidated value here
    is an injection bug; the helper above is the only intended caller.

    Per-call-site column parameterization because the three relevant
    tables use different column names:
      - polymarket_round_trips:     ts_col='entry_ts',
                                    div_col="COALESCE(division,'polymarket_arbitrage')"
      - polymarket_equity_history:  filter applied via Python max(window, epoch)
                                    BEFORE the SQL, not via this clause
      - audit_event:                ts_col='a.ts',
                                    div_col="COALESCE(json_extract(...payload_json,'$.division'),
                                                      'polymarket_arbitrage')"
    """
    if not epoch_iso:
        return ""
    return f" AND NOT ({div_col}='{div_value}' AND {ts_col} < '{epoch_iso}')"


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
    *,
    pm_epoch: str | None = None,
    kalshi_copy_mode: str = "all",
    kalshi_copy_epoch: str | None = None,
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
            f"WHERE COALESCE(division, 'polymarket_arbitrage') IN ({poly_ph})"
            + _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="entry_ts",
                div_col="COALESCE(division, 'polymarket_arbitrage')",
            )
            + " ORDER BY resolved_ts DESC LIMIT ?",
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
    # CP4: poly_kalshi_mlb round-trips also live here (composed by the resolver),
    # so include the poly_kalshi_ prefix — cutoff/copy-mode clauses are no-ops for it.
    kalshi_slugs = [s for s in division_slugs
                    if s.startswith(_KALSHI_PREFIX) or s.startswith(_POLY_KALSHI_PREFIX)]
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
            f"WHERE division IN ({kalshi_ph})"
            + _kalshi_cutoff_clause("entry_ts", division_slugs=kalshi_slugs, db_url=db_url)
            + _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "entry_ts")
            + " "
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
                market_title=str(r.get("event_title") or _pk_mlb_display(str(r.get("ticker") or ""))[0]),
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
    *,
    pm_epoch: str | None = None,
) -> list[PMEquityPoint]:
    """Equity-history points for the selected divisions over `days` of
    history. In All-mode (multiple selected slugs) we DON'T sum here —
    return raw per-division points and let the chart layer aggregate.

    When `pm_epoch` is set AND `polymarket_copy_trading` is in scope,
    the polymarket side's effective cutoff is `max(now - days, epoch)`
    so the rendered curve's X-axis (auto-fit via Lightweight Charts'
    timeScale().fitContent()) anchors at the epoch — not the days-back
    window — when the epoch is more recent. Filter and anchor are ONE
    change because of fitContent's auto-range behavior.
    """
    if not division_slugs:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: list[PMEquityPoint] = []

    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        # Effective cutoff for the polymarket side = max(window, epoch).
        # Kalshi side keeps the window-only cutoff — kalshi cutoffs live
        # in DASHBOARD_RT_CUTOFFS (round_trips only), not in equity-history
        # filtering. Both behaviors intentionally distinct.
        poly_cutoff = max(cutoff, pm_epoch) if pm_epoch else cutoff
        poly_ph = ",".join("?" for _ in poly_slugs)
        poly_rows = _query(
            db_url,
            f"SELECT ts, division, equity, cash_usdc, positions_value "
            f"FROM polymarket_equity_history "
            f"WHERE ts >= ? AND division IN ({poly_ph}) "
            f"ORDER BY ts ASC",
            (poly_cutoff, *poly_slugs),
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
    *,
    pm_epoch: str | None = None,
    kalshi_copy_mode: str = "all",
    kalshi_copy_epoch: str | None = None,
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
            + _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="a.ts",
                div_col=(
                    "COALESCE(json_extract(a.payload_json, '$.division'),"
                    "'polymarket_arbitrage')"
                ),
            )
            + f"  AND r.order_id IS NULL "
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
        # Scope the OPEN tab to a kalshi division's dashboard cutoff (entry
        # epoch), mirroring _kalshi_cutoff_clause but against the audit
        # payload's division + a.ts. Empty => the clause self-disables.
        _llm_cut = DASHBOARD_RT_CUTOFFS.get("kalshi_llm_arbitrage", "")
        rows = _query(
            db_url,
            f"SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            f"FROM audit_event a "
            f"LEFT JOIN kalshi_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.actor IN ('kalshi_tail_price_arb', 'kalshi_temporal_bucket_arb', 'kalshi_llm_arbitrage', 'kalshi_copy_trader', 'kalshi_weather_arb', 'kalshi_crypto_arb') "
            f"  AND a.kind = 'would_have_placed' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            f"  AND json_extract(a.payload_json, '$.division') IN ({kalshi_ph}) "
            + _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "a.ts")
            + f"  AND NOT (json_extract(a.payload_json, '$.division') = 'kalshi_llm_arbitrage' AND a.ts < '{_llm_cut}') "
            + f"  AND r.order_id IS NULL "
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
                else "weather_forecast" if actor == "kalshi_weather_arb"
                else "crypto_spot" if actor == "kalshi_crypto_arb"
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

    # Poly->Kalshi copy (live) open trades — kind='poly_kalshi_order',
    # actor/division = 'poly_kalshi_mlb'. This division's journal differs from the
    # arb actors: fields are `count`/`price` (+ Flag-1 `fill_count`/`fill_price`),
    # NOT qty/limit_price, and `side` is the V2 leg (bid/ask), so an OPEN row is
    # keyed off `action='entry'`. Additive branch — the arb query above is left
    # byte-identical. An entry drops off OPEN once CP4's resolver composes its
    # kalshi_round_trips row (the LEFT JOIN on order_id -> `r.order_id IS NULL`
    # gate); a placed row with no order_id yet stays open, which is correct
    # pre-resolution.
    pk_slugs = [s for s in division_slugs if s.startswith(_POLY_KALSHI_PREFIX)]
    if pk_slugs:
        pk_ph = ",".join("?" for _ in pk_slugs)
        rows = _query(
            db_url,
            f"SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            f"FROM audit_event a "
            f"LEFT JOIN kalshi_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.kind = 'poly_kalshi_order' "
            f"  AND json_extract(a.payload_json, '$.status') IN ('placed', 'DRY_RUN_would_place') "
            f"  AND COALESCE(json_extract(a.payload_json, '$.action'), 'entry') = 'entry' "
            f"  AND json_extract(a.payload_json, '$.division') IN ({pk_ph}) "
            + _kalshi_division_epoch_clause(
                pk_slugs, db_url, ts_col="a.ts",
                div_col="json_extract(a.payload_json, '$.division')")
            + f"  AND r.order_id IS NULL "
            f"ORDER BY a.ts DESC LIMIT ?",
            (*pk_slugs, limit),
        )
        for r in rows:
            try:
                p = json.loads(r["payload_json"])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            # Prefer the REAL fill (Flag 1) when present; fall back to the
            # requested count / limit price for a not-yet-/never-filled row.
            qty = float(
                p["fill_count"] if p.get("fill_count") is not None
                else (p.get("count") or 0)
            )
            price = float(
                p["fill_price"] if p.get("fill_price") is not None
                else (p.get("price") or 0)
            )
            whale = p.get("whale")
            out.append(PMOpenTrade(
                order_id=str(p.get("order_id") or ""),
                venue="kalshi",
                division=str(p.get("division") or ""),
                strategy=str(r["actor"] or "poly_kalshi_mlb"),
                whale_handle=str(whale) if whale else None,
                emit_ts=str(r["ts"] or ""),
                market_title=_pk_mlb_display(str(p.get("ticker") or ""))[0],
                market_id=str(p.get("ticker") or ""),
                category=None,
                outcome_bet=str(p.get("outcome") or ""),
                qty=qty,
                entry_price=price,
                notional=qty * price,
                divergence_pct=None,
                edge_cents=None,
                arb_type="poly_kalshi_copy",
                resolves_at=None,
                age_hours=_age_hours(r["ts"] or ""),
                rationale=(f"copy @{whale}" if whale else None),
                llm_reasoning=None,
                key_unknowns=[],
                llm_confidence=None,
                subtitle=None,
                leg_date=None,
            ))

    out.sort(key=lambda t: t.emit_ts, reverse=True)
    return out[:limit]


def _query_pm_pending_count(
    db_url: str, division_slugs: list[str],
    *,
    pm_epoch: str | None = None,
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
            + _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="a.ts",
                div_col=(
                    "COALESCE(json_extract(a.payload_json, '$.division'),"
                    "'polymarket_arbitrage')"
                ),
            )
            + f"  AND r.order_id IS NULL "
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
        # Match _query_pm_open_trades: scope the pending COUNT to a kalshi
        # division cutoff (entry epoch) so the OPEN badge equals the OPEN list.
        _llm_cut = DASHBOARD_RT_CUTOFFS.get("kalshi_llm_arbitrage", "")
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n FROM audit_event a "
            f"LEFT JOIN kalshi_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.actor IN ('kalshi_tail_price_arb', 'kalshi_temporal_bucket_arb', 'kalshi_llm_arbitrage', 'kalshi_copy_trader', 'kalshi_weather_arb', 'kalshi_crypto_arb') "
            f"  AND a.kind = 'would_have_placed' "
            f"  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            f"  AND json_extract(a.payload_json, '$.division') IN ({kalshi_ph}) "
            f"  AND NOT (json_extract(a.payload_json, '$.division') = 'kalshi_llm_arbitrage' AND a.ts < '{_llm_cut}') "
            f"  AND r.order_id IS NULL "
            f"  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            f"    SELECT entry_order_id FROM kalshi_round_trips "
            f"    WHERE entry_order_id IS NOT NULL"
            f"  )",
            tuple(kalshi_slugs),
        )
        if rows:
            total += int(rows[0].get("n") or 0)

    # Poly->Kalshi copy (live): the OPEN badge must equal the OPEN list, so this
    # COUNT uses the SAME WHERE as _query_pm_open_trades' poly_kalshi branch —
    # kind='poly_kalshi_order' (single writer poly_kalshi_executor.py:368, so
    # actor==division; the division predicate fully scopes it), placed/would-place
    # ENTRY rows not yet resolved (order_id LEFT-JOIN -> r.order_id IS NULL). The
    # arb COUNT above is untouched.
    pk_slugs = [s for s in division_slugs if s.startswith(_POLY_KALSHI_PREFIX)]
    if pk_slugs:
        pk_ph = ",".join("?" for _ in pk_slugs)
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n FROM audit_event a "
            f"LEFT JOIN kalshi_round_trips r "
            f"  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            f"WHERE a.kind = 'poly_kalshi_order' "
            f"  AND json_extract(a.payload_json, '$.status') IN ('placed', 'DRY_RUN_would_place') "
            f"  AND COALESCE(json_extract(a.payload_json, '$.action'), 'entry') = 'entry' "
            f"  AND json_extract(a.payload_json, '$.division') IN ({pk_ph}) "
            + _kalshi_division_epoch_clause(
                pk_slugs, db_url, ts_col="a.ts",
                div_col="json_extract(a.payload_json, '$.division')")
            + f"  AND r.order_id IS NULL",
            tuple(pk_slugs),
        )
        if rows:
            total += int(rows[0].get("n") or 0)

    return total


def _query_pm_resolved_stats(
    db_url: str, division_slugs: list[str],
    *,
    pm_epoch: str | None = None,
    kalshi_copy_mode: str = "all",
    kalshi_copy_epoch: str | None = None,
) -> dict:
    """Aggregate stats over ALL resolved round-trips (no LIMIT), cross-venue.

    Returns {n_resolved, n_wins, n_voids, total_realized_pnl}. Used by the
    dashboard summary tiles so they reflect true totals rather than the
    capped list size — `_query_pm_round_trips` truncates at history_limit,
    so `len(round_trips)` was silently capped at 100 (or whichever limit).
    """
    out = {"n_resolved": 0, "n_wins": 0, "n_voids": 0, "total_realized_pnl": 0.0}
    if not division_slugs:
        return out

    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        poly_ph = ",".join("?" for _ in poly_slugs)
        # polymarket_round_trips has no `market_result` column — voids
        # would surface via extra_json if at all (rare on polymarket).
        # Approximate void as realized_pnl=0 AND won=0, which matches the
        # PMRoundTrip-derivation rule used in `_query_pm_round_trips`.
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n_resolved, "
            f"       COALESCE(SUM(won), 0) AS n_wins, "
            f"       COALESCE(SUM(CASE WHEN won = 0 AND realized_pnl = 0.0 THEN 1 ELSE 0 END), 0) AS n_voids, "
            f"       COALESCE(SUM(realized_pnl), 0.0) AS total_pnl "
            f"FROM polymarket_round_trips "
            f"WHERE COALESCE(division, 'polymarket_arbitrage') IN ({poly_ph})"
            + _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="entry_ts",
                div_col="COALESCE(division, 'polymarket_arbitrage')",
            ),
            tuple(poly_slugs),
        )
        if rows:
            out["n_resolved"] += int(rows[0].get("n_resolved") or 0)
            out["n_wins"] += int(rows[0].get("n_wins") or 0)
            out["n_voids"] += int(rows[0].get("n_voids") or 0)
            out["total_realized_pnl"] += float(rows[0].get("total_pnl") or 0.0)

    # CP4: poly_kalshi_mlb round-trips live in kalshi_round_trips too.
    kalshi_slugs = [s for s in division_slugs
                    if s.startswith(_KALSHI_PREFIX) or s.startswith(_POLY_KALSHI_PREFIX)]
    if kalshi_slugs:
        kalshi_ph = ",".join("?" for _ in kalshi_slugs)
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n_resolved, "
            f"       COALESCE(SUM(won), 0) AS n_wins, "
            f"       COALESCE(SUM(CASE WHEN COALESCE(market_result,'') = 'void' THEN 1 ELSE 0 END), 0) AS n_voids, "
            f"       COALESCE(SUM(realized_pnl), 0.0) AS total_pnl "
            f"FROM kalshi_round_trips "
            f"WHERE division IN ({kalshi_ph})"
            + _kalshi_cutoff_clause("entry_ts", division_slugs=kalshi_slugs, db_url=db_url)
            + _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "entry_ts"),
            tuple(kalshi_slugs),
        )
        if rows:
            out["n_resolved"] += int(rows[0].get("n_resolved") or 0)
            out["n_wins"] += int(rows[0].get("n_wins") or 0)
            out["n_voids"] += int(rows[0].get("n_voids") or 0)
            out["total_realized_pnl"] += float(rows[0].get("total_pnl") or 0.0)

    return out


def _query_kalshi_distinct_market_stats(
    db_url: str, division_slugs: list[str],
    *,
    kalshi_copy_mode: str = "all",
    kalshi_copy_epoch: str | None = None,
) -> dict:
    """Option-B distinct-market headline aggregation for Kalshi divisions.

    Option A (per-emission) is the raw `kalshi_round_trips` table — every
    re-emission of the same market signal creates a separate row.
    Option B (per-market) is this read-layer aggregation: one canonical row
    per distinct `ticker`, chosen as the EARLIEST entry_ts emission (tie-
    broken by rowid/id).  Canonical = earliest because it models the
    idealized case where the division entered once on first signal fire and
    held to resolution.

    Dedup key is the full market `ticker` (NOT `event_ticker`, which
    over-collapses distinct strikes — e.g. Treasury T10 / T8 share one
    event_ticker but are separate markets with independent results).

    Only handles kalshi slugs (division_slugs filtered by `_KALSHI_PREFIX`);
    returns the zero dict if none are present — does NOT touch polymarket
    data.  Applies `_kalshi_cutoff_clause` and `_kalshi_copy_mode_clause`
    identically to `_query_pm_resolved_stats`'s kalshi branch.

    Returns {n_resolved, n_wins, n_voids, total_realized_pnl} where counts
    are over DISTINCT markets, not emissions — n_resolved will be lower than
    or equal to the per-emission Option-A count.
    """
    out: dict = {"n_resolved": 0, "n_wins": 0, "n_voids": 0, "total_realized_pnl": 0.0}
    # CP4: poly_kalshi_mlb round-trips live in kalshi_round_trips too.
    kalshi_slugs = [s for s in division_slugs
                    if s.startswith(_KALSHI_PREFIX) or s.startswith(_POLY_KALSHI_PREFIX)]
    if not kalshi_slugs:
        return out

    kalshi_ph = ",".join("?" for _ in kalshi_slugs)
    # Window-function CTE: rank emissions per ticker by entry_ts ASC (earliest
    # first), tie-break by id ASC (rowid proxy for insertion order).  The
    # outer SELECT filters to rn=1 so each ticker contributes exactly one row.
    sql = (
        f"WITH ranked AS ("
        f"  SELECT ticker, won, market_result, realized_pnl, "
        f"         ROW_NUMBER() OVER ("
        f"           PARTITION BY ticker ORDER BY entry_ts ASC, id ASC"
        f"         ) AS rn "
        f"  FROM kalshi_round_trips "
        f"  WHERE division IN ({kalshi_ph})"
        + _kalshi_cutoff_clause("entry_ts", division_slugs=kalshi_slugs, db_url=db_url)
        + _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "entry_ts")
        + f") "
        f"SELECT COUNT(*) AS n_resolved, "
        f"       COALESCE(SUM(won), 0) AS n_wins, "
        f"       COALESCE(SUM(CASE WHEN COALESCE(market_result,'') = 'void' "
        f"                         THEN 1 ELSE 0 END), 0) AS n_voids, "
        f"       COALESCE(SUM(realized_pnl), 0.0) AS total_pnl "
        f"FROM ranked WHERE rn = 1"
    )
    rows = _query(db_url, sql, tuple(kalshi_slugs))
    if rows:
        out["n_resolved"] = int(rows[0].get("n_resolved") or 0)
        out["n_wins"] = int(rows[0].get("n_wins") or 0)
        out["n_voids"] = int(rows[0].get("n_voids") or 0)
        out["total_realized_pnl"] = float(rows[0].get("total_pnl") or 0.0)
    return out


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


def _query_pm_whales(
    db_url: str, target_slugs: list[str],
    *,
    pm_epoch: str | None = None,
    kalshi_copy_mode: str = "all",
    kalshi_copy_epoch: str | None = None,
    selected_sort: str | None = None,
    selected_desc: bool = True,
    hide_uncopyable: bool = False,
    hide_net_neg: bool = False,
) -> list[PMWhaleRow]:
    """Per-whale aggregates for the Whales tab.

    Returns one row per (whale_handle, division). The panel shows whales we
    are CURRENTLY copy-trading — membership comes from
    `agent_state(<actor>, selected_whales)`. A whale demoted via the
    dashboard disappears from the panel even if they have historical
    round_trips or open paper positions; their history stays accessible
    via the Trades / History tab.

    Aggregates for current members pull from:
      - kalshi_round_trips.extra_json.whale_handle  (K3 schema)
      - polymarket_round_trips.extra_json.whale_user_name (PCT schema)
    plus open-trade counts from audit_event for would_have_placed BUY
    rows that don't yet have an entry_order_id linkage. Members with no
    historical activity render as zero-stat placeholder rows so freshly
    promoted whales are visible immediately.
    """
    out: list[PMWhaleRow] = []
    if not target_slugs:
        return out

    # Load current selected-whale rosters first — these gate the panel.
    kalshi_selected: set[str] = set()
    if "kalshi_copy_trading" in target_slugs:
        try:
            rec = db.load_agent_state(
                "kalshi_copy_trader", "selected_whales", db_url=db_url,
            )
            if rec is not None and isinstance(rec[0], list):
                kalshi_selected = {str(h) for h in rec[0] if h}
        except Exception as e:
            log.debug("_query_pm_whales kalshi selected load failed: %s", e)

    pm_selected_user_names: set[str] = set()
    if "polymarket_copy_trading" in target_slugs:
        try:
            rec = db.load_agent_state(
                "polymarket_copy_trader", "selected_whales", db_url=db_url,
            )
            if rec is not None and isinstance(rec[0], list):
                for s in rec[0]:
                    if isinstance(s, dict):
                        name = str(s.get("user_name") or "")
                        if name:
                            pm_selected_user_names.add(name)
        except Exception as e:
            log.debug("_query_pm_whales polymarket selected load failed: %s", e)

    # Kalshi K3
    if "kalshi_copy_trading" in target_slugs:
        try:
            # S2 fix (c) 2026-07-26: epoch-scope the per-whale panel to match the
            # tile (_query_pm_resolved_stats). Was full-history; now honors the
            # Paper/Live/All slice via _kalshi_copy_mode_clause on entry_ts.
            rows = _query(
              db_url,
              "SELECT json_extract(extra_json, '$.whale_handle') AS handle, "
              "COUNT(*) AS n, "
              "SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS w, "
              "SUM(CASE WHEN won=0 THEN 1 ELSE 0 END) AS l, "
              "SUM(realized_pnl) AS pnl, MAX(entry_ts) AS last_ts "
              "FROM kalshi_round_trips "
              "WHERE division='kalshi_copy_trading' AND won IS NOT NULL"
              + _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "entry_ts")
              + " GROUP BY handle"
            )
            # S2 fix (c): scope opens to the same epoch as the panel (on audit ts).
            opens = _query(
              db_url,
              "SELECT json_extract(payload_json,'$.whale_handle') AS handle, "
              "COUNT(*) AS n "
              "FROM audit_event "
              "WHERE actor='kalshi_copy_trader' "
              "AND kind='would_have_placed' "
              "AND json_extract(payload_json,'$.side')='buy' "
              "AND json_extract(payload_json,'$.order_id') NOT IN "
              "(SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL)"
              + _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "ts")
              + " GROUP BY handle"
            )
            opens_map = {r.get("handle"): int(r.get("n") or 0) for r in opens}
            for r in rows:
                handle = r.get("handle") or "(unknown)"
                if handle not in kalshi_selected:
                    continue
                n = int(r.get("n") or 0)
                w = int(r.get("w") or 0)
                ll = int(r.get("l") or 0)
                decisive = w + ll
                wr = (100.0 * w / decisive) if decisive > 0 else None
                out.append(PMWhaleRow(
                    handle=handle, venue="kalshi",
                    division="kalshi_copy_trading",
                    n_resolved=n, n_wins=w, n_losses=ll,
                    win_rate_pct=wr,
                    total_realized_pnl=float(r.get("pnl") or 0.0),
                    n_open=opens_map.get(handle, 0),
                    last_entry_ts=r.get("last_ts"),
                ))
        except Exception as e:
            log.debug("_query_pm_whales kalshi failed: %s", e)

    # Polymarket Copy Trader
    if "polymarket_copy_trading" in target_slugs:
        try:
            # Aggregate over polymarket_round_trips. Epoch cutoff appended
            # to WHERE. Division is hardcoded to polymarket_copy_trading so
            # we pass division as a literal — clause renders identically
            # via the COALESCE'd div_col we use elsewhere.
            rt_clause = _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="entry_ts",
                div_col="division",
            )
            rows = _query(db_url, f"""
              SELECT json_extract(extra_json, '$.whale_user_name') AS handle,
                     COUNT(*) AS n,
                     SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS w,
                     SUM(CASE WHEN won=0 THEN 1 ELSE 0 END) AS l,
                     SUM(realized_pnl) AS pnl,
                     MAX(entry_ts) AS last_ts
              FROM polymarket_round_trips
              WHERE division='polymarket_copy_trading' AND won IS NOT NULL
                {rt_clause}
              GROUP BY handle
            """)
            # Opens subquery: audit_event with a.ts cutoff. Aliased as
            # `a` so the cutoff clause's a.ts column reference resolves.
            opens_clause = _polymarket_cutoff_clause(
                pm_epoch,
                ts_col="a.ts",
                div_col=(
                    "COALESCE(json_extract(a.payload_json,'$.division'),"
                    "'polymarket_arbitrage')"
                ),
            )
            opens = _query(db_url, f"""
              SELECT json_extract(a.payload_json,'$.whale_user_name') AS handle,
                     COUNT(*) AS n
              FROM audit_event a
              WHERE a.kind='would_have_placed'
                AND json_extract(a.payload_json,'$.division')='polymarket_copy_trading'
                AND json_extract(a.payload_json,'$.side')='buy'
                AND json_extract(a.payload_json,'$.order_id') NOT IN
                  (SELECT entry_order_id FROM polymarket_round_trips WHERE entry_order_id IS NOT NULL)
                {opens_clause}
              GROUP BY handle
            """)
            opens_map = {r.get("handle"): int(r.get("n") or 0) for r in opens}
            for r in rows:
                handle = r.get("handle") or "(unknown)"
                if handle not in pm_selected_user_names:
                    continue
                n = int(r.get("n") or 0)
                w = int(r.get("w") or 0)
                ll = int(r.get("l") or 0)
                decisive = w + ll
                wr = (100.0 * w / decisive) if decisive > 0 else None
                out.append(PMWhaleRow(
                    handle=handle, venue="polymarket",
                    division="polymarket_copy_trading",
                    n_resolved=n, n_wins=w, n_losses=ll,
                    win_rate_pct=wr,
                    total_realized_pnl=float(r.get("pnl") or 0.0),
                    n_open=opens_map.get(handle, 0),
                    last_entry_ts=r.get("last_ts"),
                ))
            # Surface currently-selected whales with OPEN positions but ZERO
            # resolved (so silent whales don't disappear from the UI). Filter
            # by selected_whales membership so a demoted whale's lingering
            # unpaired BUYs don't keep their row alive.
            for handle, n_open in opens_map.items():
                if not handle or handle not in pm_selected_user_names:
                    continue
                if any(w.handle == handle and w.venue == "polymarket" for w in out):
                    continue
                out.append(PMWhaleRow(
                    handle=handle, venue="polymarket",
                    division="polymarket_copy_trading",
                    n_resolved=0, n_wins=0, n_losses=0,
                    win_rate_pct=None,
                    total_realized_pnl=0.0,
                    n_open=n_open,
                    last_entry_ts=None,
                ))
        except Exception as e:
            log.debug("_query_pm_whales polymarket failed: %s", e)

    # Surface freshly-promoted whales who have no round_trip activity yet.
    # The query above only emits rows for whales with resolved round_trips
    # or open paper positions, so a whale promoted via dashboard-button
    # won't appear until the next copy-trade poll fires. Walk selected_whales
    # and append zero-stat placeholders for anyone not already in `out`.
    if "kalshi_copy_trading" in target_slugs:
        try:
            rec = db.load_agent_state(
                "kalshi_copy_trader", "selected_whales", db_url=db_url,
            )
            if rec is not None and isinstance(rec[0], list):
                existing = {w.handle for w in out if w.venue == "kalshi"}
                for h in rec[0]:
                    handle = str(h) if h else ""
                    if not handle or handle in existing:
                        continue
                    out.append(PMWhaleRow(
                        handle=handle, venue="kalshi",
                        division="kalshi_copy_trading",
                        n_resolved=0, n_wins=0, n_losses=0,
                        win_rate_pct=None,
                        total_realized_pnl=0.0,
                        n_open=0,
                        last_entry_ts=None,
                    ))
        except Exception as e:
            log.debug("_query_pm_whales kalshi selected-placeholder failed: %s", e)

    # Merge per-whale copy-quality intel for Kalshi selected-whale rows.
    # Placed AFTER the placeholder block so that freshly-promoted whales
    # (no round_trips yet) still get their audit_event intel populated.
    if "kalshi_copy_trading" in target_slugs:
        kalshi_handles = [w.handle for w in out if w.venue == "kalshi"]
        if kalshi_handles:
            try:
                intel = _query_kalshi_whale_intel(
                    db_url, kalshi_handles,
                    kalshi_copy_mode=kalshi_copy_mode, kalshi_copy_epoch=kalshi_copy_epoch,
                )
                for w in out:
                    if w.venue != "kalshi":
                        continue
                    d = intel.get(w.handle, {})
                    w.intel_copies = d.get("copies", 0)
                    w.intel_detections = d.get("detections", 0)
                    w.intel_no_side = d.get("no_side", 0)
                    w.intel_sports = d.get("sports", 0)
                    w.intel_copyability_pct = d.get("copyability_pct")
                    w.intel_net_pnl = d.get("net_pnl")
                    w.intel_days_since_last_copy = d.get("days_since_last_copy")
                    w.intel_crypto_pct = d.get("crypto_pct")
            except Exception as e:
                log.warning("_query_pm_whales: intel merge failed: %s", e)

    if "polymarket_copy_trading" in target_slugs:
        try:
            rec = db.load_agent_state(
                "polymarket_copy_trader", "selected_whales", db_url=db_url,
            )
            if rec is not None and isinstance(rec[0], list):
                existing = {w.handle for w in out if w.venue == "polymarket"}
                for s in rec[0]:
                    if not isinstance(s, dict):
                        continue
                    user_name = str(s.get("user_name") or "")
                    if not user_name or user_name in existing:
                        continue
                    out.append(PMWhaleRow(
                        handle=user_name, venue="polymarket",
                        division="polymarket_copy_trading",
                        n_resolved=0, n_wins=0, n_losses=0,
                        win_rate_pct=None,
                        total_realized_pnl=0.0,
                        n_open=0,
                        last_entry_ts=None,
                    ))
        except Exception as e:
            log.debug("_query_pm_whales polymarket selected-placeholder failed: %s", e)

    # Decorate rows with actor_id (the identifier the demote endpoint
    # consumes) and is_pinned (whether the whale was manually promoted
    # via the dashboard; pinned whales survive refresh_*_whales.py runs).
    kalshi_pinned: set[str] = set()
    pm_pinned_user_names: set[str] = set()
    pm_user_name_to_wallet: dict[str, str] = {}
    try:
        rec = db.load_agent_state(
            "kalshi_copy_trader", "pinned_whales", db_url=db_url,
        )
        if rec is not None and isinstance(rec[0], list):
            kalshi_pinned = {str(h) for h in rec[0] if h}
    except Exception as e:
        log.debug("_query_pm_whales kalshi pinned load failed: %s", e)
    try:
        rec = db.load_agent_state(
            "polymarket_copy_trader", "pinned_whales", db_url=db_url,
        )
        if rec is not None and isinstance(rec[0], list):
            for r in rec[0]:
                if isinstance(r, dict):
                    user_name = str(r.get("user_name") or "")
                    wallet = str(r.get("wallet") or r.get("proxy_wallet") or "")
                    if user_name:
                        pm_pinned_user_names.add(user_name)
                        if wallet:
                            pm_user_name_to_wallet[user_name] = wallet
    except Exception as e:
        log.debug("_query_pm_whales polymarket pinned load failed: %s", e)
    try:
        rec = db.load_agent_state(
            "polymarket_copy_trader", "selected_whales", db_url=db_url,
        )
        if rec is not None and isinstance(rec[0], list):
            for r in rec[0]:
                if isinstance(r, dict):
                    user_name = str(r.get("user_name") or "")
                    wallet = str(r.get("wallet") or r.get("proxy_wallet") or "")
                    if user_name and wallet:
                        pm_user_name_to_wallet.setdefault(user_name, wallet)
    except Exception as e:
        log.debug("_query_pm_whales polymarket selected load failed: %s", e)

    for w in out:
        if w.venue == "kalshi":
            w.actor_id = w.handle
            w.is_pinned = w.handle in kalshi_pinned
        elif w.venue == "polymarket":
            w.actor_id = pm_user_name_to_wallet.get(w.handle, "")
            w.is_pinned = w.handle in pm_pinned_user_names

    # Sort: highest realized PnL first; silent whales (n_resolved=0) at end.
    out.sort(key=lambda w: (w.n_resolved == 0, -w.total_realized_pnl))

    # Apply sort + quality filters to the Kalshi Selected subset only,
    # mirroring the equivalent logic in _query_kalshi_watch_only_rows.
    if "kalshi_copy_trading" in target_slugs:
        k = [w for w in out if w.venue == "kalshi"]
        other = [w for w in out if w.venue != "kalshi"]
        # "Hide uncopyable": kalshi rows where detections>0 but copyability<5%.
        if hide_uncopyable:
            k = [
                w for w in k
                if not (
                    w.intel_detections > 0
                    and (
                        w.intel_copyability_pct is None
                        or w.intel_copyability_pct < 5.0
                    )
                )
            ]
        # "Hide net-negative": kalshi rows with credible sample (n>=30) and
        # fee+slip adjusted PnL below zero.
        if hide_net_neg:
            k = [
                w for w in k
                if not (
                    w.n_resolved >= 30
                    and (w.intel_net_pnl or 0.0) < 0.0
                )
            ]
        # Sort by the requested intel key, None-trailing.
        attr = _KALSHI_SELECTED_SORT_KEYS.get((selected_sort or "").lower())
        if attr is not None:
            with_val = [w for w in k if getattr(w, attr, None) is not None]
            without_val = [w for w in k if getattr(w, attr, None) is None]
            with_val.sort(key=lambda w: getattr(w, attr), reverse=selected_desc)
            k = with_val + without_val
        out = k + other

    return out


def _query_kalshi_watch_only_rows(
    db_url: str, target_slugs: list[str],
    *,
    sort_key: str | None = None,
    sort_desc: bool = True,
    hide_uncopyable: bool = False,
    hide_net_neg: bool = False,
) -> list[KalshiWatchOnlyRow]:
    """Render the K3 Watch List panel from `agent_state(watch_only_whales)`.

    `watch_only_whales` (a list[dict]) is the membership of the observation
    pool — refresh_kalshi_whales.py writes it. `watch_only_stats` (a dict)
    is an enrichment slot maintained by refresh_kalshi_watchlist_stats.py.

    Entries whose handle is currently in `selected_whales` are filtered out
    — a promoted whale belongs on the Selected Whales panel, not the watch
    list. When that whale is later demoted, removing them from
    `selected_whales` causes them to reappear here with their original
    enriched stats intact (no API refetch needed).

    Only populated when kalshi_copy_trading is in scope. Empty list
    otherwise.

    Sort: tier ascending (Tier 1 first), then total_pnl descending.
    """
    if "kalshi_copy_trading" not in target_slugs:
        return []

    whales_rec = db.load_agent_state(
        "kalshi_copy_trader", "watch_only_whales", db_url=db_url,
    )
    if whales_rec is None or not isinstance(whales_rec[0], list):
        return []
    whales_list = whales_rec[0]

    stats_rec = db.load_agent_state(
        "kalshi_copy_trader", "watch_only_stats", db_url=db_url,
    )
    stats_by_handle: dict[str, dict] = {}
    if stats_rec is not None and isinstance(stats_rec[0], dict):
        stats_by_handle = stats_rec[0]

    # Exclude handles currently being copy-traded.
    selected_set: set[str] = set()
    try:
        sel_rec = db.load_agent_state(
            "kalshi_copy_trader", "selected_whales", db_url=db_url,
        )
        if sel_rec is not None and isinstance(sel_rec[0], list):
            selected_set = {str(h) for h in sel_rec[0] if h}
    except Exception as e:
        log.debug("_query_kalshi_watch_only_rows selected load failed: %s", e)

    out: list[KalshiWatchOnlyRow] = []
    for w in whales_list:
        if not isinstance(w, dict):
            continue
        handle = str(w.get("handle") or "")
        if not handle or handle in selected_set:
            continue
        s = stats_by_handle.get(handle) or {}
        if not isinstance(s, dict):
            s = {}
        decisive = int(s.get("wins") or 0) + int(s.get("losses") or 0)
        wr_pct: float | None = None
        if decisive > 0:
            wr_pct = 100.0 * int(s.get("wins") or 0) / decisive
        cats = s.get("top_categories") or []
        top_cat = cats[0] if cats else None
        # Prefer fresh stats fields; fall back to whatever the
        # watch_only_whales list entry recorded (tier/notes/source flagged
        # by promote/demote or by the refresh script).
        tier_raw = s.get("tier") if s.get("tier") is not None else w.get("tier")
        out.append(KalshiWatchOnlyRow(
            handle=handle,
            tier=int(tier_raw) if tier_raw is not None else None,
            source_x_handle=s.get("source_x_handle") or w.get("source_x_handle"),
            notes=s.get("notes") or w.get("notes"),
            resolved_count=int(s.get("resolved_count") or 0),
            wins=int(s.get("wins") or 0),
            losses=int(s.get("losses") or 0),
            win_rate_pct=wr_pct,
            total_pnl=float(s.get("total_pnl") or 0.0),
            avg_pnl_per_contract=float(s.get("avg_pnl_per_contract") or 0.0),
            top_category=top_cat,
            n_open=int(s.get("n_open") or 0),
            lifetime_markets_traded=int(s.get("lifetime_markets_traded") or 0),
            last_refresh_iso=s.get("last_refresh_iso") or w.get("included_iso"),
        ))
    # Merge per-whale copy-quality intel BEFORE sorting so intel-keyed sorts work.
    if out:
        try:
            intel = _query_kalshi_whale_intel(db_url, [w.handle for w in out])
            for w in out:
                d = intel.get(w.handle, {})
                w.intel_copies = d.get("copies", 0)
                w.intel_detections = d.get("detections", 0)
                w.intel_no_side = d.get("no_side", 0)
                w.intel_sports = d.get("sports", 0)
                w.intel_copyability_pct = d.get("copyability_pct")
                w.intel_net_pnl = d.get("net_pnl")
                w.intel_n_resolved = d.get("n_resolved", 0)
                w.intel_hit_rate_pct = d.get("hit_rate_pct")
                w.intel_days_since_last_copy = d.get("days_since_last_copy")
                w.intel_crypto_pct = d.get("crypto_pct")
        except Exception as e:
            log.warning("_query_kalshi_watch_only_rows: intel merge failed: %s", e)

    # Sort by the requested key (or default to tier asc + total_pnl desc,
    # which matches the seed's tier-grouped-by-PnL ordering).
    attr = _KALSHI_WATCH_SORT_KEYS.get((sort_key or "").lower(), None)
    if attr is None:
        out.sort(key=lambda w: (w.tier or 99, -w.total_pnl))
    else:
        # Split None/non-None and concat None-trailing AFTER sorting non-Nones.
        with_val = [w for w in out if getattr(w, attr, None) is not None]
        without_val = [w for w in out if getattr(w, attr, None) is None]
        with_val.sort(key=lambda w: getattr(w, attr), reverse=sort_desc)
        out = with_val + without_val

    # Apply quality filters (URL toggle params; both default OFF).
    # "Hide uncopyable": remove whales where copyability < 5% (the
    # lengthy.starfish problem — many no_side skips, almost no real copies).
    if hide_uncopyable:
        out = [
            w for w in out
            if not (
                w.intel_detections > 0
                and (
                    w.intel_copyability_pct is None
                    or w.intel_copyability_pct < 5.0
                )
            )
        ]
    # "Hide net-negative": remove whales where fee+slip adjusted PnL is negative
    # at a credible sample (n_resolved >= 30 — the strategy's own auto-pause floor).
    if hide_net_neg:
        out = [
            w for w in out
            if not (
                w.intel_n_resolved >= 30
                and (w.intel_net_pnl or 0.0) < 0.0
            )
        ]

    return out


# Whitelist of sort keys the Kalshi Watch List panel will honor from the
# URL query param `?kalshi_watch_sort=`. The string the user provides is
# mapped to the KalshiWatchOnlyRow attribute the sort runs on. Anything
# not in this dict falls back to the default sort (tier asc + total_pnl desc).
# String keys are case-insensitive on lookup.
_KALSHI_WATCH_SORT_KEYS: dict[str, str] = {
    "handle": "handle",
    "tier": "tier",
    "resolved": "resolved_count",
    "resolved_count": "resolved_count",
    "wr": "win_rate_pct",
    "win_rate_pct": "win_rate_pct",
    "pnl": "total_pnl",
    "total_pnl": "total_pnl",
    "pnl_contract": "avg_pnl_per_contract",
    "avg_pnl_per_contract": "avg_pnl_per_contract",
    "open": "n_open",
    "n_open": "n_open",
    "top_category": "top_category",
    "last_refresh": "last_refresh_iso",
    "last_refresh_iso": "last_refresh_iso",
    # Intel sort keys (per-whale copy-quality metrics)
    "copies": "intel_copies",
    "intel_copies": "intel_copies",
    "detections": "intel_detections",
    "intel_detections": "intel_detections",
    "no_side": "intel_no_side",
    "intel_no_side": "intel_no_side",
    "sports": "intel_sports",
    "intel_sports": "intel_sports",
    "copyability": "intel_copyability_pct",
    "copyability_pct": "intel_copyability_pct",
    "intel_copyability_pct": "intel_copyability_pct",
    "net_pnl": "intel_net_pnl",
    "copy_net_pnl": "intel_net_pnl",
    "intel_net_pnl": "intel_net_pnl",
    "intel_n_resolved": "intel_n_resolved",
    "hit_rate": "intel_hit_rate_pct",
    "intel_hit_rate_pct": "intel_hit_rate_pct",
    "days_since": "intel_days_since_last_copy",
    "intel_days_since": "intel_days_since_last_copy",
    "intel_days_since_last_copy": "intel_days_since_last_copy",
    "crypto_pct": "intel_crypto_pct",
    "intel_crypto_pct": "intel_crypto_pct",
}

_KALSHI_WATCH_DEFAULT_SORT_KEY = None  # default = tier asc + total_pnl desc

# Sort keys for the Selected Whales panel (PMWhaleRow). Only the 8 intel
# columns + handle are sortable here — the base whale metrics (tier,
# resolved_count, win_rate_pct, etc.) don't exist on PMWhaleRow.
_KALSHI_SELECTED_SORT_KEYS: dict[str, str] = {
    "handle": "handle",
    "copies": "intel_copies",
    "intel_copies": "intel_copies",
    "detections": "intel_detections",
    "intel_detections": "intel_detections",
    "no_side": "intel_no_side",
    "intel_no_side": "intel_no_side",
    "sports": "intel_sports",
    "intel_sports": "intel_sports",
    "copyability": "intel_copyability_pct",
    "copyability_pct": "intel_copyability_pct",
    "intel_copyability_pct": "intel_copyability_pct",
    "net_pnl": "intel_net_pnl",
    "copy_net_pnl": "intel_net_pnl",
    "intel_net_pnl": "intel_net_pnl",
    "days_since": "intel_days_since_last_copy",
    "intel_days_since": "intel_days_since_last_copy",
    "intel_days_since_last_copy": "intel_days_since_last_copy",
    "crypto_pct": "intel_crypto_pct",
    "intel_crypto_pct": "intel_crypto_pct",
}


def _query_kalshi_whale_intel(
    db_url: str,
    whale_handles: list[str],
    division: str = "kalshi_copy_trading",
    *,
    kalshi_copy_mode: str = "all",
    kalshi_copy_epoch: str | None = None,
) -> dict[str, dict]:
    """Read-only per-whale copy-quality intel from audit_event + kalshi_round_trips.

    Returns a dict keyed by whale handle:
      {copies, detections, no_side, sports, copyability_pct,
       net_pnl, n_resolved, hit_rate_pct, days_since_last_copy, crypto_pct}

    Cost model mirrors kanalysis.py (2026-06-21):
      fee = ceil(0.07 * C * P * (1-P)) per traded side (entry always; exit only
      when pre-resolution, i.e. 0 < exit_price < 1).
      slippage = $0.01/contract per traded side (entry always; exit same gate).
    Crypto tickers: KXBTC/KXETH/KXSOL/KXDOGE/KXXRP/KXBNB/KXHYPE prefixes.

    READ-ONLY — SELECT only, no writes to any table.
    """
    import math

    if not whale_handles:
        return {}

    result: dict[str, dict] = {
        h: {
            "copies": 0,
            "no_side": 0,
            "sports": 0,
            "detections": 0,
            "copyability_pct": None,
            "net_pnl": 0.0,
            "n_resolved": 0,
            "hit_rate_pct": None,
            "days_since_last_copy": None,
            "crypto_pct": None,
        }
        for h in whale_handles
    }

    now = datetime.now(timezone.utc)
    _SLIP = 0.01  # $/contract adverse per traded side
    _CRYPTO_PREFIXES = ("KXBTC", "KXETH", "KXSOL", "KXDOGE", "KXXRP", "KXBNB", "KXHYPE")

    def _fee(c: float, p: float | None) -> float:
        """Kalshi general-trading fee: ceil(0.07*C*P*(1-P)), per side."""
        if p is None or c <= 0:
            return 0.0
        p = max(0.0, min(1.0, float(p)))
        return math.ceil(0.07 * c * p * (1.0 - p) * 100.0) / 100.0

    # P1 2026-07-27: epoch-scope the intel columns to match fix (c)'s base columns
    # on the Selected panel (Paper/Live/All slice, same as the tile). Default 'all'
    # = no scoping (Watch bench caller keeps all-time). audit_event scoped on `ts`,
    # kalshi_round_trips on `entry_ts`. Keeps copies/no_side/sports (and thus
    # copyability) + net_pnl consistently scoped so the row reads one scope.
    _ts_clause = _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "ts")
    _entry_clause = _kalshi_copy_mode_clause(kalshi_copy_mode, kalshi_copy_epoch, "entry_ts")

    try:
        with db.connect(db_url) as conn:
            # 1. Entry copies per whale handle. S2 fix (a) 2026-07-26: count BOTH
            #    paper would_have_placed AND live kalshi_copy_placed_live (the live
            #    kind froze the paper-only numerator at go-live). no_fill excluded
            #    (liquidity, not copyability). side='buy' matches both kinds.
            rows = conn.execute(
                "SELECT json_extract(payload_json,'$.whale_handle') AS h, "
                "       COUNT(*) AS n, MAX(ts) AS last_ts "
                "FROM audit_event "
                "WHERE actor='kalshi_copy_trader' "
                "  AND kind IN ('would_have_placed','kalshi_copy_placed_live') "
                "  AND json_extract(payload_json,'$.side')='buy' "
                "  AND json_extract(payload_json,'$.whale_handle') IS NOT NULL"
                + _ts_clause
                + " GROUP BY h"
            ).fetchall()
            for r in rows:
                h = r[0]
                if h and h in result:
                    result[h]["copies"] = int(r[1])
                    try:
                        last_ts = r[2]
                        if last_ts:
                            dt = datetime.fromisoformat(
                                last_ts.replace("Z", "+00:00")
                            )
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            result[h]["days_since_last_copy"] = (
                                now - dt
                            ).total_seconds() / 86400.0
                    except Exception:
                        pass

            # 2. no_side skips per whale (payload uses whale_handle or whale).
            rows = conn.execute(
                "SELECT COALESCE(json_extract(payload_json,'$.whale_handle'),"
                "                json_extract(payload_json,'$.whale')) AS h, "
                "       COUNT(*) AS n "
                "FROM audit_event "
                "WHERE actor='kalshi_copy_trader' "
                "  AND kind='kalshi_copy_entry_skipped_no_side'"
                + _ts_clause
                + " GROUP BY h"
            ).fetchall()
            for r in rows:
                h = r[0]
                if h and h in result:
                    result[h]["no_side"] = int(r[1])

            # 3. Sports skips per whale (payload uses whale or whale_handle).
            rows = conn.execute(
                "SELECT COALESCE(json_extract(payload_json,'$.whale'),"
                "                json_extract(payload_json,'$.whale_handle')) AS h, "
                "       COUNT(*) AS n "
                "FROM audit_event "
                "WHERE actor='kalshi_copy_trader' "
                "  AND kind='kalshi_copy_entry_skipped_sports'"
                + _ts_clause
                + " GROUP BY h"
            ).fetchall()
            for r in rows:
                h = r[0]
                if h and h in result:
                    result[h]["sports"] = int(r[1])

            # 4. Net-of-fee PnL from resolved round-trips per whale.
            rows = conn.execute(
                "SELECT json_extract(extra_json,'$.whale_handle') AS h, "
                "       ticker, qty, entry_price, realized_pnl, won, "
                "       json_extract(extra_json,'$.exit_price') AS exit_price "
                "FROM kalshi_round_trips "
                "WHERE division=? "
                "  AND json_extract(extra_json,'$.whale_handle') IS NOT NULL"
                + _entry_clause,
                (division,),
            ).fetchall()
            # Accumulate per handle.
            accum: dict[str, dict] = {}
            for r in rows:
                h = r[0]
                if not h or h not in result:
                    continue
                c = float(r[2] or 0)
                ep = float(r[3] or 0)
                xp_raw = r[6]
                xp = float(xp_raw) if xp_raw is not None else None
                settled = xp is None or xp <= 0.0 or xp >= 1.0
                ef = _fee(c, ep)
                xf = 0.0 if settled else _fee(c, xp)
                # Slippage: entry always; exit only on pre-resolution exits.
                sl = _SLIP * c * (1 if settled else 2)
                net = float(r[4] or 0) - ef - xf - sl
                won = int(r[5] or 0)
                ticker = str(r[1] or "").upper()
                is_crypto = any(ticker.startswith(p) for p in _CRYPTO_PREFIXES)
                acc = accum.setdefault(
                    h, {"n": 0, "wins": 0, "net": 0.0, "crypto": 0}
                )
                acc["n"] += 1
                acc["wins"] += won
                acc["net"] += net
                if is_crypto:
                    acc["crypto"] += 1

            for h, acc in accum.items():
                if h not in result:
                    continue
                n = acc["n"]
                result[h]["n_resolved"] = n
                result[h]["net_pnl"] = round(acc["net"], 2)
                if n > 0:
                    result[h]["hit_rate_pct"] = round(
                        100.0 * acc["wins"] / n, 1
                    )
                    result[h]["crypto_pct"] = round(
                        100.0 * acc["crypto"] / n, 1
                    )

    except Exception as e:
        log.warning("_query_kalshi_whale_intel failed: %s", e)

    # Compute derived detection + copyability fields.
    for d in result.values():
        det = d["copies"] + d["no_side"] + d["sports"]
        d["detections"] = det
        d["copyability_pct"] = (
            round(100.0 * d["copies"] / det, 1) if det > 0 else None
        )

    return result


# Whitelist of sort keys the Polymarket Watch List panel will honor from
# the URL query param `?pm_watch_sort=`. The string the user provides is
# mapped to the PolymarketWatchOnlyRow attribute the sort runs on. Anything
# not in this dict falls back to the default sort (realized_pnl_usdc desc).
# String keys are case-insensitive on lookup.
_PM_WATCH_SORT_KEYS: dict[str, str] = {
    "rank": "rank",
    "user_name": "user_name",
    "best_category": "best_category",
    "n": "window_size_n",
    "window_size_n": "window_size_n",
    "span": "window_days_span",
    "window_days_span": "window_days_span",
    "last": "last_trade_iso",
    "last_trade_iso": "last_trade_iso",
    "wr": "win_rate_pct",
    "win_rate_pct": "win_rate_pct",
    "avg_entry_price": "avg_entry_price",
    "avg": "avg_entry_price",
    "avgpx": "avg_entry_price",
    "share_below_70": "share_below_70",
    "below_70": "share_below_70",
    "realized_pnl_usdc": "realized_pnl_usdc",
    "pnl": "realized_pnl_usdc",
    "lifetime_pnl_from_leaderboard": "lifetime_pnl_from_leaderboard",
    "lifetime_pnl": "lifetime_pnl_from_leaderboard",
    "lifetime_vol_from_leaderboard": "lifetime_vol_from_leaderboard",
    "lifetime_vol": "lifetime_vol_from_leaderboard",
}

_PM_WATCH_DEFAULT_SORT_KEY = "realized_pnl_usdc"
_PM_WATCH_DEFAULT_SORT_DESC = True


def _query_polymarket_watch_only_rows(
    db_url: str, target_slugs: list[str],
    *,
    sort_key: str | None = None,
    sort_desc: bool = True,
) -> list[PolymarketWatchOnlyRow]:
    """Render the Polymarket Watch List panel from `agent_state(watch_only_whales)`.

    Entries whose proxy_wallet is currently in `selected_whales` are filtered
    out — a promoted whale belongs on the Selected Whales panel. Demoting
    them later (removal from selected_whales) causes them to reappear here
    with their original leaderboard PnL / win-rate / etc. intact, since the
    watch_only_whales entry is never mutated by promote/demote.

    Only populated when polymarket_copy_trading is in scope. Empty list
    otherwise.

    Sort: rank ascending (pre-sorted by realized PnL descending by the sweep).
    win_rate is stored as 0..1 in agent_state and converted to 0..100 here for
    template parity with KalshiWatchOnlyRow.
    """
    if "polymarket_copy_trading" not in target_slugs:
        return []
    loaded = db.load_agent_state(
        "polymarket_copy_trader", "watch_only_whales", db_url=db_url,
    )
    if loaded is None:
        return []
    whales_list, _updated = loaded
    if not isinstance(whales_list, list):
        return []

    # Wallets currently being copy-traded — hide from watch list.
    selected_wallets: set[str] = set()
    try:
        sel_rec = db.load_agent_state(
            "polymarket_copy_trader", "selected_whales", db_url=db_url,
        )
        if sel_rec is not None and isinstance(sel_rec[0], list):
            for s in sel_rec[0]:
                if isinstance(s, dict):
                    wallet = str(s.get("wallet") or s.get("proxy_wallet") or "").lower()
                    if wallet:
                        selected_wallets.add(wallet)
    except Exception as e:
        log.debug("_query_polymarket_watch_only_rows selected load failed: %s", e)

    out: list[PolymarketWatchOnlyRow] = []
    for w in whales_list:
        if not isinstance(w, dict):
            continue
        proxy_wallet = str(w.get("proxy_wallet") or "").lower()
        if proxy_wallet and proxy_wallet in selected_wallets:
            continue
        total_resolved = int(w.get("total_resolved_positions") or 0)
        raw_wr = w.get("win_rate")
        wr_pct: float | None = None
        if raw_wr is not None and total_resolved > 0:
            wr_pct = float(raw_wr) * 100.0
        out.append(PolymarketWatchOnlyRow(
            rank=int(w.get("rank") or 0),
            user_name=str(w.get("user_name") or ""),
            proxy_wallet=str(w.get("proxy_wallet") or ""),
            x_username=w.get("x_username") or None,
            verified_badge=bool(w.get("verified_badge", False)),
            total_resolved_positions=total_resolved,
            wins=int(w.get("wins") or 0),
            losses=int(w.get("losses") or 0),
            win_rate_pct=wr_pct,
            realized_pnl_usdc=float(w.get("realized_pnl_usdc") or 0.0),
            lifetime_pnl_from_leaderboard=float(w.get("lifetime_pnl_from_leaderboard") or 0.0),
            lifetime_vol_from_leaderboard=float(w.get("lifetime_vol_from_leaderboard") or 0.0),
            best_category=str(w.get("best_category") or ""),
            included_iso=w.get("included_iso") or None,
            window_size_n=int(w.get("window_size_n") or 0),
            window_days_span=float(w.get("window_days_span") or 0.0),
            last_trade_iso=w.get("last_trade_iso") or None,
            provisional=bool(w.get("provisional", False)),
            avg_entry_price=float(w.get("avg_entry_price") or 0.0),
            share_below_70=float(w.get("share_below_70") or 0.0),
        ))

    # Sort by the requested key (or default to rank ascending, which mirrors
    # the agent_state pre-sorted-by-PnL ordering). The attribute name is
    # whitelisted; falls back to default if the user passed garbage.
    attr = _PM_WATCH_SORT_KEYS.get((sort_key or "").lower(), None)
    if attr is None:
        # Default = realized_pnl_usdc desc, which is how the seed writes them.
        # Honor the seed's rank field directly for the default path.
        out.sort(key=lambda w: w.rank)
    else:
        # Split None/non-None and concat None-trailing AFTER sorting non-Nones.
        # If we used a tuple-based composite key with reverse=True, the None
        # bucket would flip to the top — undesirable for old-schema rows
        # missing windowed fields.
        with_val = [w for w in out if getattr(w, attr, None) is not None]
        without_val = [w for w in out if getattr(w, attr, None) is None]
        with_val.sort(key=lambda w: getattr(w, attr), reverse=sort_desc)
        out = with_val + without_val
    return out


def _pm_summary(
    round_trips: list[PMRoundTrip],
    equity_curve: list[PMEquityPoint],
    pending_count: int,
    resolved_stats: dict | None = None,
) -> PMSummary:
    """Compute the summary cards. Returns zeros/Nones cleanly when there's
    no data so the template doesn't have to guard.

    `resolved_stats` is the output of `_query_pm_resolved_stats` (true
    aggregates over ALL resolved round-trips, no LIMIT). When provided,
    n_resolved / n_wins / n_voids / total_realized_pnl come from there.
    When None (legacy callers), fall back to counting from the round_trips
    list — but be aware the list is truncated by `history_limit` and
    callers that care about correct tile counts MUST pass resolved_stats.
    """
    if resolved_stats is not None:
        n_resolved = int(resolved_stats.get("n_resolved", 0))
        n_wins = int(resolved_stats.get("n_wins", 0))
        n_voids = int(resolved_stats.get("n_voids", 0))
        total_pnl = float(resolved_stats.get("total_realized_pnl", 0.0))
    else:
        n_wins = sum(1 for rt in round_trips if rt.won == 1)
        n_resolved = len(round_trips)
        n_voids = sum(1 for rt in round_trips if rt.market_result == "void")
        total_pnl = sum(rt.realized_pnl for rt in round_trips)
    n_losses = n_resolved - n_wins - n_voids
    decisive = n_wins + n_losses
    win_rate = (100.0 * n_wins / decisive) if decisive > 0 else None

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


# ── Poly->Kalshi live section (Phase 2b CP3): broker-free live view ──────────
# Reads ONLY audit_event + poly_kalshi_mark_live/_history + kalshi_round_trips
# (SELECTs). NEVER calls the Kalshi client — marks come from the volatile tables the
# ~60s mark poller (CP2) writes; the "why" from the CP1 trigger fields on the row.
_POLY_KALSHI_MARK_STALE_AFTER_SEC = 180.0     # marks tick ~60s -> stale after ~3 min
_POLY_KALSHI_COPY_MOMENT_LIMIT = 25
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"   # unicode block sparkline


def _sparkline_text(series: list[float]) -> str:
    """A tiny unicode-block sparkline of a yes-mid series (a functional preview; the
    fancy chart binds to the raw `sparkline` series later). '' for <2 points."""
    if not series or len(series) < 2:
        return ""
    lo, hi = min(series), max(series)
    rng = (hi - lo) or 1e-9
    return "".join(_SPARK_BLOCKS[min(7, int((v - lo) / rng * 7))] for v in series)


def _pk_mlb_display(ticker: str) -> tuple[str, str | None]:
    """Broker-free readable label for a KXMLBGAME ticker via the shared parser:
    ("{yes_team} vs {other_team}", bet_team=yes_team). The Kalshi YES side is the team the
    always-YES copy leg is long, so it leads. Non-MLB / unparseable ticker (all-star,
    TIE/DRAW, non-KXMLBGAME) -> (raw ticker, None) so nothing ever renders blank."""
    try:
        from trading_corp.data.mlb_poly_kalshi_match import parse_kalshi_mlb_ticker
        pk = parse_kalshi_mlb_ticker(ticker or "")
    except Exception:  # noqa: BLE001 — a display helper must never break a read view
        pk = None
    if pk is None:
        return (ticker or ""), None
    return f"{pk.yes_name} vs {pk.other_name}", pk.yes_name


@dataclass
class PolyKalshiLivePosition:
    order_id: str
    ticker: str
    market_title: str      # readable "{yes} vs {other}" (parsed); raw ticker fallback
    bet_team: str | None   # team the always-YES leg is long (parsed); None if unparseable
    outcome: str
    entry_ts: str
    fill_price: float
    contracts: float
    cost_basis: float
    whale: str | None
    # CP1 trigger — None on pre-CP1 rows (render gracefully)
    poly_slug: str | None
    poly_outcome: str | None
    poly_side: str | None
    poly_market_type: str | None
    # CP2 marks — None until the poller has marked (render "marking...", never fabricate)
    yes_mid: float | None
    unrealized: float | None
    unrealized_pct: float | None
    mark_ts: str | None
    stale: bool
    sparkline: list[float]
    sparkline_text: str


@dataclass
class PolyKalshiCopyMoment:
    order_id: str
    ts: str
    ticker: str
    market_title: str      # readable "{yes} vs {other}" (parsed); raw ticker fallback
    bet_team: str | None
    whale: str | None
    outcome: str
    count: float
    fill_price: float | None
    poly_slug: str | None
    poly_outcome: str | None


@dataclass
class PolyKalshiLiveView:
    open_positions: list[PolyKalshiLivePosition]
    copy_moments: list[PolyKalshiCopyMoment]
    total_unrealized: float | None
    n_open: int
    latest_order_id: str | None       # for client-side new-row (sound/flash) detection
    latest_ts: str | None


def build_poly_kalshi_live_view(db_url: str) -> PolyKalshiLiveView:
    """Broker-free live view for the poly_kalshi_mlb dashboard section. SELECT-only over
    audit_event + poly_kalshi_mark_live + poly_kalshi_mark_history + kalshi_round_trips.
    NEVER calls the Kalshi client (marks are pre-computed by the CP2 poller)."""
    now = datetime.now(timezone.utc)
    # OPEN positions — CP3(phase1) gate: placed ENTRY rows with an order_id, not resolved.
    open_rows = _query(
        db_url,
        "SELECT a.ts AS ts, a.payload_json FROM audit_event a "
        "LEFT JOIN kalshi_round_trips r "
        "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
        "WHERE a.actor = 'poly_kalshi_mlb' AND a.kind = 'poly_kalshi_order' "
        "  AND json_extract(a.payload_json, '$.status') = 'placed' "
        "  AND COALESCE(json_extract(a.payload_json, '$.action'), 'entry') = 'entry' "
        "  AND COALESCE(json_extract(a.payload_json, '$.order_id'), '') != '' "
        "  AND r.order_id IS NULL "
        "ORDER BY a.ts DESC",
    )
    marks = {m["order_id"]: m for m in _query(
        db_url, "SELECT order_id, yes_mid, unrealized, unrealized_pct, mark_ts "
        "FROM poly_kalshi_mark_live")}
    hist: dict[str, list[float]] = {}
    for h in _query(db_url, "SELECT order_id, yes_mid FROM poly_kalshi_mark_history ORDER BY id ASC"):
        hist.setdefault(str(h["order_id"]), []).append(float(h["yes_mid"]))

    positions: list[PolyKalshiLivePosition] = []
    total_unrealized: float | None = None
    for row in open_rows:
        try:
            p = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        oid = str(p.get("order_id") or "")
        fp = float(p.get("fill_price") or 0.0)
        fc = float(p.get("fill_count") or 0.0)
        mk = marks.get(oid)
        yes_mid = unrealized = unrealized_pct = mark_ts = None
        stale = True
        if mk and mk["yes_mid"] is not None:
            yes_mid = float(mk["yes_mid"])
            unrealized = None if mk["unrealized"] is None else float(mk["unrealized"])
            unrealized_pct = None if mk["unrealized_pct"] is None else float(mk["unrealized_pct"])
            mark_ts = mk["mark_ts"]
            try:
                ts_dt = datetime.fromisoformat(str(mark_ts))
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                stale = (now - ts_dt).total_seconds() > _POLY_KALSHI_MARK_STALE_AFTER_SEC
            except (TypeError, ValueError):
                stale = True
        series = hist.get(oid, [])
        _tkr = str(p.get("ticker") or "")
        _mt, _bt = _pk_mlb_display(_tkr)
        positions.append(PolyKalshiLivePosition(
            order_id=oid, ticker=_tkr,
            market_title=_mt, bet_team=_bt, outcome=str(p.get("outcome") or "yes"),
            entry_ts=str(row["ts"] or ""), fill_price=fp, contracts=fc, cost_basis=fp * fc,
            whale=(str(p["whale"]) if p.get("whale") else None),
            poly_slug=p.get("poly_slug"), poly_outcome=p.get("poly_outcome"),
            poly_side=p.get("poly_side"), poly_market_type=p.get("poly_market_type"),
            yes_mid=yes_mid, unrealized=unrealized, unrealized_pct=unrealized_pct,
            mark_ts=mark_ts, stale=stale, sparkline=series,
            sparkline_text=_sparkline_text(series),
        ))
        if unrealized is not None:
            total_unrealized = (total_unrealized or 0.0) + unrealized

    # COPY-MOMENT feed — recent placements, newest first, bounded.
    moments: list[PolyKalshiCopyMoment] = []
    for row in _query(
        db_url,
        "SELECT a.ts AS ts, a.payload_json FROM audit_event a "
        "WHERE a.actor = 'poly_kalshi_mlb' AND a.kind = 'poly_kalshi_order' "
        "  AND json_extract(a.payload_json, '$.status') = 'placed' "
        "  AND COALESCE(json_extract(a.payload_json, '$.order_id'), '') != '' "
        "ORDER BY a.ts DESC LIMIT ?",
        (_POLY_KALSHI_COPY_MOMENT_LIMIT,),
    ):
        try:
            p = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        fpx = p.get("fill_price")
        _mtkr = str(p.get("ticker") or "")
        _mmt, _mbt = _pk_mlb_display(_mtkr)
        moments.append(PolyKalshiCopyMoment(
            order_id=str(p.get("order_id") or ""), ts=str(row["ts"] or ""),
            ticker=_mtkr, market_title=_mmt, bet_team=_mbt,
            whale=(str(p["whale"]) if p.get("whale") else None),
            outcome=str(p.get("outcome") or "yes"),
            count=float(p.get("fill_count") or p.get("count") or 0.0),
            fill_price=(None if fpx is None else float(fpx)),
            poly_slug=p.get("poly_slug"), poly_outcome=p.get("poly_outcome"),
        ))
    latest = moments[0] if moments else None
    return PolyKalshiLiveView(
        open_positions=positions, copy_moments=moments, total_unrealized=total_unrealized,
        n_open=len(positions),
        latest_order_id=(latest.order_id if latest else None),
        latest_ts=(latest.ts if latest else None),
    )


async def build_prediction_market_view(
    deps,
    division: str | None,
    *,
    history_limit: int = 100,
    equity_curve_days: int = 30,
    pm_watch_sort: str | None = None,
    pm_watch_desc: bool = True,
    kalshi_watch_sort: str | None = None,
    kalshi_watch_desc: bool = True,
    kalshi_hide_uncopyable: bool = False,
    kalshi_hide_net_neg: bool = False,
    kalshi_selected_sort: str | None = None,
    kalshi_selected_desc: bool = True,
    kalshi_sel_hide_uncopyable: bool = False,
    kalshi_sel_hide_net_neg: bool = False,
    wr_mode: str = "live",
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

    # Resolve the polymarket_copy_trading metrics epoch once for this build.
    # All 6 PM query helpers below accept it as a kwarg and apply the
    # cutoff to their polymarket side; passing None == no-op (the
    # reversibility path). Kalshi-side queries ignore it; their cutoffs
    # live in DASHBOARD_RT_CUTOFFS.
    pm_epoch = await asyncio.to_thread(_get_polymarket_metrics_epoch, db_url)

    # Kalshi copy-trading Paper/Live/All scoping. Resolve the go-live epoch
    # once; only the kalshi_copy_trading single-division view is scoped by
    # `wr_mode` — every other division (and the All view) forces mode='all'
    # so their stats stay byte-identical regardless of the query param.
    kalshi_copy_epoch = await asyncio.to_thread(_get_kalshi_copy_live_epoch, db_url)
    kalshi_copy_mode = wr_mode if division == "kalshi_copy_trading" else "all"

    round_trips, equity_curve, open_trades, whales, kalshi_watch_only, polymarket_watch_only, pending_count, resolved_stats = await asyncio.gather(
        asyncio.to_thread(_query_pm_round_trips, db_url, target_slugs, history_limit, pm_epoch=pm_epoch, kalshi_copy_mode=kalshi_copy_mode, kalshi_copy_epoch=kalshi_copy_epoch),
        asyncio.to_thread(_query_pm_equity_curve, db_url, target_slugs, equity_curve_days, pm_epoch=pm_epoch),
        asyncio.to_thread(_query_pm_open_trades, db_url, target_slugs, 200, pm_epoch=pm_epoch, kalshi_copy_mode=kalshi_copy_mode, kalshi_copy_epoch=kalshi_copy_epoch),
        asyncio.to_thread(
            _query_pm_whales, db_url, target_slugs, pm_epoch=pm_epoch,
            kalshi_copy_mode=kalshi_copy_mode, kalshi_copy_epoch=kalshi_copy_epoch,
            selected_sort=kalshi_selected_sort, selected_desc=kalshi_selected_desc,
            hide_uncopyable=kalshi_sel_hide_uncopyable, hide_net_neg=kalshi_sel_hide_net_neg,
        ),
        asyncio.to_thread(
            _query_kalshi_watch_only_rows, db_url, target_slugs,
            sort_key=kalshi_watch_sort, sort_desc=kalshi_watch_desc,
            hide_uncopyable=kalshi_hide_uncopyable,
            hide_net_neg=kalshi_hide_net_neg,
        ),
        asyncio.to_thread(
            _query_polymarket_watch_only_rows, db_url, target_slugs,
            sort_key=pm_watch_sort, sort_desc=pm_watch_desc,
        ),
        asyncio.to_thread(_query_pm_pending_count, db_url, target_slugs, pm_epoch=pm_epoch),
        asyncio.to_thread(_query_pm_resolved_stats, db_url, target_slugs, pm_epoch=pm_epoch, kalshi_copy_mode=kalshi_copy_mode, kalshi_copy_epoch=kalshi_copy_epoch),
    )

    # Tiles must show TRUE totals, not list lengths. open_trades/round_trips
    # are LIMIT-capped (200/history_limit); deriving n_pending/n_resolved
    # from `len()` silently truncated the tiles at the limit values.
    summary = _pm_summary(round_trips, equity_curve, pending_count, resolved_stats)
    # Only attach cutoff badge for single-division pages. The combined
    # "All Prediction Markets" aggregate has no honest "since" date.
    if division is not None and division in DASHBOARD_RT_CUTOFFS:
        summary.cutoff_ts = DASHBOARD_RT_CUTOFFS[division]
        summary.cutoff_label = summary.cutoff_ts.split("T", 1)[0]

    # Option-B (distinct-market) aggregates — Kalshi single-division only.
    # Scoped to single-division pages so the metric is unambiguous; the All
    # view mixes venues and the per-market dedup only makes sense per-division.
    # Non-kalshi (polymarket) single-division pages leave the fields as None.
    if division is not None and division.startswith(_KALSHI_PREFIX):
        dm = await asyncio.to_thread(
            _query_kalshi_distinct_market_stats,
            db_url, [division],
            kalshi_copy_mode=kalshi_copy_mode,
            kalshi_copy_epoch=kalshi_copy_epoch,
        )
        n_res_m = dm["n_resolved"]
        n_win_m = dm["n_wins"]
        n_voi_m = dm["n_voids"]
        n_loss_m = n_res_m - n_win_m - n_voi_m
        decisive_m = n_win_m + n_loss_m
        summary.n_resolved_markets = n_res_m
        summary.n_wins_markets = n_win_m
        summary.n_voids_markets = n_voi_m
        summary.total_realized_markets_pnl = dm["total_realized_pnl"]
        summary.win_rate_markets_pct = (
            100.0 * n_win_m / decisive_m if decisive_m > 0 else None
        )

    vol_v2_block = None
    if division == "kalshi_crypto":
        vol_v2_block = await asyncio.to_thread(query_pm_vol_v2_block, db_url)

    return PMDashboardView(
        selected=division,
        selected_label=selected_label,
        available_divisions=available,
        summary=summary,
        equity_curve=equity_curve,
        round_trips=round_trips,
        open_trades=open_trades,
        whales=whales,
        kalshi_watch_only=kalshi_watch_only,
        polymarket_watch_only=polymarket_watch_only,
        pm_watch_sort=(pm_watch_sort or "").lower() or None,
        pm_watch_desc=pm_watch_desc,
        kalshi_watch_sort=(kalshi_watch_sort or "").lower() or None,
        kalshi_watch_desc=kalshi_watch_desc,
        kalshi_hide_uncopyable=kalshi_hide_uncopyable,
        kalshi_hide_net_neg=kalshi_hide_net_neg,
        kalshi_selected_sort=(kalshi_selected_sort or "").lower() or None,
        kalshi_selected_desc=kalshi_selected_desc,
        kalshi_sel_hide_uncopyable=kalshi_sel_hide_uncopyable,
        kalshi_sel_hide_net_neg=kalshi_sel_hide_net_neg,
        pm_metrics_epoch=pm_epoch,
        vol_v2_block=vol_v2_block,
        wr_mode=wr_mode,
        wr_live_epoch=kalshi_copy_epoch,
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
    legs: list[OptionLeg], prices: dict[str, float],
    shares: dict[str, float] | None = None,
) -> tuple[list[PMCCPair], list[OptionLeg]]:
    """Group option legs into pairs by underlying.

    Every underlying with at least one option becomes a pair entry. The
    DTE-based "qualify as PMCC" filter has been removed — short-DTE long
    calls still appear as pairs, and `pair.structure_type` classifies them
    ('pmcc' vs 'covered_call' vs 'uncovered_leap' etc.) by WHAT COVERS THE
    SHORT, not by the long leg's remaining DTE.

    For each underlying:
      - leap   = the long call with the highest DTE (longest-dated long)
      - short  = the short call with the lowest DTE (nearest expiry)
      - extras = everything else (puts, additional longs/shorts)
      - underlying_shares = equity share qty (from `shares`), so a genuine
        shares-backed covered call classifies as 'covered_call' while every
        LEAP-covered position is 'pmcc'.

    Returns (pairs, other_options) where `other_options` is now always empty
    — kept in the return signature to avoid churn at call sites; will be
    removed once nothing reads it.
    """
    shares = shares or {}
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
            underlying_shares=shares.get(und),
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
