"""Robinhood PMCC Division — Phase 3 implementation with LLM expert analysis.

Strategy:
  - Buy deep ITM LEAP calls (delta >= 0.80, DTE >= 365) as the long leg.
  - Sell weekly OTM calls (delta ~0.30) against each LEAP for income.
  - Roll the short call at 21 DTE OR 50% profit capture.
  - LLM expert analyzes each position and can surface additional actions
    (LEAP delta drift, assignment risk, early rolls, regime-adjusted strikes).

Universe (config/strategies.yaml):
  universe_source: positions  — reads live Robinhood account at scan time.
    Existing PMCC legs are detected and managed (roll / cover uncovered LEAPs).
    New setups are only proposed on names the Board already owns as stock.
  universe_source: watchlist  — uses the static list in config (paper testing).

All sizing and roll thresholds come from config/risk.yaml (pmcc section).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

import yaml

from trading_corp.brokers.base import Broker
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.time import iso, now_utc

# Type-only imports — research firm wiring is optional (Phase 1a-2).
# Kept under TYPE_CHECKING-style guard to avoid forcing a research-firm
# import on every PMCC code path.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from trading_corp.agents.logger import LoggerAgent
    from trading_corp.agents.research.engagement import ResearchFirmDeps

log = logging.getLogger(__name__)

# Minimum DTE considered a "LEAP" for detection purposes (existing positions).
_LEAP_DTE_FLOOR = 180
# Weekly target DTE range: 7–21 days.
_WEEKLY_MIN_DTE = 7
_WEEKLY_MAX_DTE = 21
# B7 fallback ceiling (2026-07-21): the sparse-chain fallback may only accept an
# expiry that is a plausible weekly by DTE. Legit short-roll DTEs top ~45
# (strategies.yaml short_leg.dte_extended); LEAPs are 365+. 60 gives margin above
# the roll ceiling while excluding LEAP-DTE contracts from being taken as a
# "weekly" (the future[0] LEAP-as-weekly pathology closed by B7 — a latent
# hazard that never realized in the 157-row history, all short-opens <= 59 DTE).
_WEEKLY_FALLBACK_MAX_DTE = 60

# Phase-2 override contract: the LLM's structured escape hatch (kind + reason)
# authorizing a deterministic gate to permit what it would otherwise block.
_OVERRIDE_KINDS = ("hold_override", "net_debit_justified", "earnings_override")

# Urgency display emoji
_URGENCY_EMOJI = {"routine": "🟢", "elevated": "🟡", "urgent": "🔴"}

# LLM system prompt — baked in so it can be prompt-cached
_PMCC_EXPERT_SYSTEM = """\
You are a world-class options trader managing the "Aggressive Weekly Income on
High-Beta / Crypto-Levered Names" PMCC book. The thesis:
- LEAP captures explosive upside on volatile underlyings.
- Weekly short calls milk high IV for 1.5-3% weekly portfolio yield.
- Acceptable underlyings: crypto miners (MARA/RIOT/CIFR/IREN/BULL),
  crypto proxies (MSTR/HOOD/BLSH), high-beta momentum (ASTS/RKLB/SMR),
  high-IV specialty.

You have deep expertise in:
- Options Greeks (delta, theta, gamma, vega) and their evolution
- IV rank/percentile and premium-selling environments
- Rolling mechanics: standard up-and-out, OTM, defensive
- Assignment risk on cash-constrained accounts
- Mean-reversion timing on high-IV names

The user message gives you the specific rules that apply to this position.
Apply them exactly.

Respond ONLY with valid JSON. No markdown fences, no preamble, no explanation outside the JSON object.
"""

# Concise rule blocks injected into the user prompt
_STANDARD_RULES = """\
## RULES: STANDARD PMCC — {symbol}
1. ROLL TRIGGER: 2 DTE OR 50%+ profit captured (whichever first).
2. SHORT LEG: 7-DTE target. Delta range by regime:
   - Aggressive (uptrend): 0.30-0.45
   - Balanced (default):    0.20-0.30
   - Defensive (chop/down): 0.10-0.20
3. BREACH POLICY (combined PMCC P/L matters, not just short leg):
   - Minor (0-3% above strike): roll up-and-out 7 DTE, delta 0.25-0.35.
   - Major (3-8% above strike): roll up-and-out 21 DTE, delta 0.30-0.40, MUST credit.
   - Runaway (8%+ above strike): consider closing short and holding LEAP naked
     ONLY IF LEAP DTE > 270 AND thesis intact AND VIX < 30.
     Otherwise close full PMCC.
   COOLDOWN (back-to-back roll-up guard): When ROLL HISTORY shows a
   recent roll-up (positive strike_change >= $1 within `cooldown_days`,
   default 7) AND the current short_leg_dte > 2 AND extrinsic >
   `extrinsic_floor` ($0.50/sh default) — choose `hold` directly.
   Back-to-back roll-ups in one weekly cycle compound bid-ask cost
   and lock in the prior roll's debit.
   Override `hold` and choose roll_short ONLY if the breach has
   ACCELERATED past the prior roll's projected range — concretely:
   spot now > prior_short_strike_after + |prior_strike_change|.
   BACKSTOP: deterministic Python guard
   (`_recent_halfway_roll_cooldown`) downgrades roll_short → hold
   when the cooldown conditions hold, regardless of LLM judgment.
   HONOR the cooldown directly when ROLL HISTORY shows a recent
   roll-up; let the backstop catch only the acceleration-override
   edge cases.
   STRIKE TARGETING: when prescribing a specific strike in your
   rationale (e.g. "roll above $200 resistance" or a rule-cited
   level), ALSO populate `target_strike` in the JSON response with
   that value. Without `target_strike` the picker uses `target_delta`
   ranking, which can land well away from your cited strike on
   high-IV names. Leave `target_strike` null for normal cycle rolls
   where the delta target IS the strike-selection criterion.
4. TERMINAL-DTE OVERRIDE (CRITICAL — applies before breach rules above):
   When the short has ≤2 DTE AND the underlying is within ±1.5% of the strike
   (the "ATM zone"), DEFAULT TO HOLD. The mark is almost entirely extrinsic
   premium that decays to zero by expiration. Rolling at this stage locks in
   theta you would otherwise collect for free. Compare:
     - ROLL NOW: pay current short close cost; receive new short credit
     - WAIT TO EXPIRY: short likely closes at $0 (intrinsic only)
   Roll-vs-wait breakeven occurs when the underlying at expiration would be
   above (strike + close_cost_now/100/qty). If current spot is well below
   that breakeven, prefer HOLD.
   Override HOLD and roll/close ONLY if any are true:
     (a) Underlying is more than 1.5% above strike (genuine breach)
     (b) Account cannot tolerate overnight assignment risk
     (c) IV pricing suggests a >1σ implied move before expiration
   When in doubt with ≤2 DTE ATM short: HOLD and re-evaluate at next session.
   NOTE: deterministic Python guard (`_terminal_dte_time_release`)
   overrides this rule for 0-DTE positions regardless of LLM judgment.
   Two release paths, both calendar- and config-aware:
     - **Time gate.** Anchored to the actual session close from
       NYSE calendar. release = close - release_offset_min (default
       60m); hard_deadline = close - hard_deadline_offset_min (30m).
       On 16:00 close that's 15:00/15:30 ET; on 13:00 half-days
       12:00/12:30 ET. Inside the release window: forces roll_short.
       Past hard_deadline: forces close_short with urgency='urgent'.
     - **Cycle-continuity.** If short_leg_mark <=
       cycle_continuity_extrinsic_threshold ($/share, default $0.15)
       AND short_leg_dte == 0, force roll_short regardless of time.
   LLM should narrate the override when it fires; the override
   warning is appended to analysis.warnings.
   DEEP-OTM NEAR-WORTHLESS EXCEPTION (distinct from the ATM-zone HOLD
   above): when the short is worth only a few cents AND sits well outside
   the underlying's typical overnight move — clearly, safely
   out-of-the-money, not merely cheap because it is a near-expiry
   at-the-money contract — do NOT default to HOLD. Prefer rolling it early
   (buy-to-close the near-worthless short, sell a fresh short) to capture
   next-cycle premium rather than waiting for the last day. A cheap mark
   alone does NOT qualify: an at-the-money short near expiry can also mark a
   few cents while still carrying real overnight assignment risk. Only the
   clearly deep-OTM case qualifies.
   NOTE: a deterministic Python guard (`_deep_otm_early_release`, invoked by
   `_terminal_dte_time_release`) enforces the exact "deeply out-of-the-money"
   distance and "near-worthless" mark from config, releases this roll on the
   penultimate day for eligible names, and EXCLUDES the highest-overnight-
   volatility names (which keep 0-DTE-only behavior). Narrate the exception
   when it applies; the release warning is appended to analysis.warnings.
5. HARD RULES:
   - Never roll for debit > 8% of LEAP value.
   - If LEAP delta > 0.95, treat as deep ITM equity — close or roll deep.
   - No new short premium within 7 DTE of earnings.
   - On breach, evaluate combined PMCC P/L — never act on short leg alone.
   NOTE: deterministic Python guard
   (`_promote_to_roll_leap_if_hard_rule`) promotes roll_short →
   roll_leap when LEAP delta >= 0.95 OR long_leg_dte < 120,
   regardless of LLM judgment. The promotion ensures the
   recommendation card includes the LEAP roll legs (close short +
   close LEAP + open new LEAP + open new short on the new LEAP),
   not just the short roll, so a distracted approval doesn't leave
   a fresh short on a dying LEAP.
6. LEAP MANAGEMENT:
   - Roll out at 120 DTE.
   - Roll down if LEAP delta < 0.40.
   - Acceptable LEAP delta range: 0.55-0.80.
"""


# ---------------------------------------------------------------------------
# Protocol for option-capable brokers (duck-typed; RobinhoodBroker satisfies it)
# ---------------------------------------------------------------------------

@runtime_checkable
class OptionBroker(Protocol):
    async def get_option_positions_detail(self) -> list[dict]: ...
    async def get_expiration_dates(self, symbol: str) -> list[str]: ...
    async def get_calls_for_expiry(self, symbol: str, expiry: str) -> list[dict]: ...


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------

@dataclass
class PMCCPosition:
    """One PMCC pair currently held in the account."""
    symbol: str             # underlying, e.g. "AAPL"

    # Long leg (LEAP)
    long_leg_expiry: str    # "2027-01-15"
    long_leg_strike: float  # 150.0
    long_leg_delta: float
    long_leg_dte: int
    long_leg_qty: float     # contracts (positive)
    long_leg_avg_price: float
    long_leg_symbol: str    # display: "AAPL 2027-01-15 C 150.00"
    # Phase 2: LEAP mark for richer Telegram approval messages (used by
    # `_build_position_context`). Per-share. Optional because the
    # construction sites that build PMCCPosition may have it as None
    # if the broker chain query didn't return mark_price.
    long_leg_mark: float | None = None

    # Short leg (weekly call) — None when LEAP is uncovered
    short_leg_expiry: str | None = None
    short_leg_strike: float | None = None
    short_leg_dte: int | None = None
    short_leg_pnl_pct: float | None = None  # fraction of premium captured (0–1+)
    short_leg_qty: float | None = None      # contracts (negative for short)
    short_leg_mark: float | None = None
    short_leg_avg_price: float | None = None
    short_leg_symbol: str | None = None     # display


@dataclass
class TradeLegDetail:
    """One leg of a recommended trade, with everything the UI needs to show
    'what will happen if you click Execute'."""
    action_label: str          # human-readable: "Buy to close" / "Sell to open" / etc.
    side: str                  # "buy" | "sell"
    position_effect: str       # "open" | "close"
    underlying: str
    expiry: str
    strike: float
    option_type: str           # "call" | "put"
    qty: int                   # positive integer (qty of contracts)
    dte: int | None
    delta: float | None

    # Pricing
    mark_per_share: float | None
    bid: float | None
    ask: float | None

    # Computed cost (signed dollars: positive = debit, negative = credit)
    estimated_dollars: float

    @property
    def spread_pct(self) -> float | None:
        """(ask - bid) / mark — for spread-quality classification."""
        if self.bid is None or self.ask is None or self.mark_per_share is None:
            return None
        if self.mark_per_share <= 0:
            return None
        return (self.ask - self.bid) / self.mark_per_share

    @property
    def spread_quality(self) -> str:
        """tight | medium | wide | unknown."""
        sp = self.spread_pct
        if sp is None:
            return "unknown"
        if sp < 0.05: return "tight"
        if sp < 0.15: return "medium"
        return "wide"


@dataclass
class WaitScenario:
    """One row of the 'wait until expiration' alternative analysis."""
    label: str                # e.g. "−2%", "unchanged", "+3%"
    scen_spot: float          # underlying price in this scenario
    close_cost: float         # dollars to close the existing short at this scenario
    savings_vs_now: float     # signed; positive = waiting saves vs rolling now


@dataclass
class WaitAlternative:
    """The 'what if we just waited?' analysis — relevant near-expiration."""
    breakeven_spot: float     # stock price at expiry where wait == roll cost-wise
    breakeven_pct: float      # signed % from current spot
    max_savings: float        # if short expires worthless, this much vs roll-now
    scenarios: list[WaitScenario]
    summary: str              # one-line plain-English takeaway


@dataclass
class TradeRecommendation:
    """A concrete, dollar-priced trade plan derived from a PMCCAnalysis."""
    action: str                          # the analysis's recommended action verb
    legs: list[TradeLegDetail]
    net_cost_dollars: float              # signed; positive = net debit, negative = net credit
    cost_confidence: str                 # "high" | "medium" | "low" — combined spread quality
    benefits: list[str]                  # bullet points describing what this trade buys you
    wait_alternative: WaitAlternative | None = None


@dataclass
class PMCCAnalysis:
    """LLM expert analysis result for one PMCC position."""
    symbol: str
    # hold | roll_short | roll_short_early | roll_leap | close_short |
    # open_short | watch   (close_all REMOVED — division is short-side only)
    action: str
    confidence: float           # 0.0–1.0
    urgency: str                # routine | elevated | urgent
    summary: str
    rationale: str
    warnings: list[str] = field(default_factory=list)
    target_delta: float | None = None   # LLM-suggested short call delta
    target_dte: int | None = None       # LLM-suggested short call DTE
    # LLM-suggested short call STRIKE (Item 3 — 2026-05-03). When set,
    # `_find_best_weekly` picks the listed strike CLOSEST to this value
    # subject to the standard liquidity gate, overriding the
    # delta-distance ranking. Used when a rule (e.g. Major Breach
    # halfway-roll) prescribes a specific strike target that the
    # delta-only picker would miss. None = fall back to delta-distance
    # ranking (original behavior).
    target_strike: float | None = None
    # δ BAND (P1, 2026-07-31): the consent ENVELOPE the deterministic pricing
    # refresh selects the concrete strike within — persisted on the decision record
    # so pricing can rebuild the roll WITHOUT re-running the LLM. Derived from
    # `target_delta` ± a config half-width at judgment time (`_apply_delta_band`);
    # None on both = fall back to the point/config-default selection.
    target_delta_low: float | None = None
    target_delta_high: float | None = None
    # Phase-2 override contract (2026-07-21): structured escape hatch letting the
    # LLM authorize a deterministic gate to permit what it would otherwise block.
    # {"kind": "hold_override"|"net_debit_justified"|"earnings_override",
    #  "reason": <str>} or None. Read via `_override_kind`; a malformed value is
    # treated as no override (fail-safe — the gate applies).
    override: dict | None = None

    def format_brief(self) -> str:
        """Single-line summary for scan preamble messages."""
        emoji = _URGENCY_EMOJI.get(self.urgency, "⚪")
        return (
            f"{emoji} **{self.symbol}** → {self.action.upper()} "
            f"({self.confidence*100:.0f}% conf) — {self.summary}"
        )

    def format_rich(self) -> str:
        """Multi-line rich format for approval request detail."""
        emoji = _URGENCY_EMOJI.get(self.urgency, "⚪")
        lines = [
            f"[LLM Expert {emoji} | {self.action.upper()} | "
            f"{self.urgency} | {self.confidence*100:.0f}% confidence]",
            self.rationale,
        ]
        if self.warnings:
            lines.append("⚠️ " + " | ".join(self.warnings))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scout — surveys the market for NEW PMCC opening candidates
# ---------------------------------------------------------------------------

@dataclass
class ScoutCandidate:
    """One underlying the scout proposes for a fresh PMCC entry.

    Holds the concrete LEAP + first-short pair so the user can see the dollar
    cost AND so the same legs can be routed through `_propose_open_pmcc` →
    risk → DataExec when the user clicks Approve.
    """
    symbol: str
    spot_price: float | None

    # Concrete legs (None when the chain didn't yield a qualifying contract)
    leap_leg: TradeLegDetail | None
    short_leg: TradeLegDetail | None

    # Opening economics (rounded at render time, not here)
    leap_debit_dollars: float            # what we'd pay for the LEAP
    short_credit_dollars: float          # what we'd collect for the first short
    net_opening_debit: float             # leap_debit - short_credit
    weekly_yield_pct: float | None       # short_credit / leap_debit (per-cycle yield)
    annualized_yield_pct: float | None   # rough extrapolation: weekly × 52

    # Ranking
    score: float                         # higher = better
    notes: list[str]                     # informational ("high IV", "near earnings")
    blockers: list[str]                  # if non-empty, candidate is shown but NOT executable


@dataclass
class ScoutStatus:
    """Single status block the dashboard banner reads from."""
    state: str                  # "go" | "hold" | "halt"
    headline: str               # 1-line: "Room for 2 more PMCCs" / "At capacity"
    detail: str                 # multi-line reasoning
    open_positions: int         # current PMCC pair count
    max_positions: int          # ceiling from config
    cash_available: float       # buying-power-ish; what we could deploy
    cash_required_estimate: float | None  # cheapest candidate's net debit, if any
    pct_allocated: float        # gross PMCC notional / equity
    blockers: list[str]         # account-level reasons we shouldn't open more


@dataclass
class ScoutReport:
    """What the scout endpoint returns to the dashboard."""
    status: ScoutStatus
    candidates: list[ScoutCandidate]    # sorted by score desc
    universe_scanned: list[str]
    excluded_existing: list[str]        # symbols skipped because already a PMCC
    generated_at: str                   # ISO timestamp


# ---------------------------------------------------------------------------
# Option selection helpers
# ---------------------------------------------------------------------------

def _select_leap_strike(calls: list[dict]) -> dict | None:
    """Pick best LEAP strike: delta >= 0.80 (deepest qualifying ITM).
    Falls back to highest available delta if no strike qualifies.
    """
    eligible = [c for c in calls if c.get("delta") is not None and c["delta"] >= 0.80]
    if eligible:
        return min(eligible, key=lambda c: c["strike_price"])   # lowest strike = deepest ITM
    with_delta = [c for c in calls if c.get("delta") is not None]
    if with_delta:
        return max(with_delta, key=lambda c: c["delta"])
    return None


def _short_roll_credit(
    new_weekly: dict, close_mark: float,
) -> tuple[float, float, float | None]:
    """Conservative + mark net credit of a short roll's short-leg pair.

    Sell the new weekly at BID (conservative — the existing short exposes mark
    only, no ask); buy the old short back at MARK. Returns
    (conservative_net, mark_net, open_bid). Pure; PRE-FEE (captures spread, not
    fees). Shared by `_propose_roll_short` (B2) and both roll_leap sites (Phase
    2.5) so the credit basis can't drift across the three when fees or a
    configurable floor land.
    """
    open_bid = new_weekly.get("bid")
    open_credit_conservative = (
        open_bid if open_bid is not None else (new_weekly.get("mark_price") or 0.0)
    )
    conservative_net = open_credit_conservative - close_mark
    mark_net = (
        (new_weekly.get("mark_price") or new_weekly.get("bid") or 0.0) - close_mark
    )
    return conservative_net, mark_net, open_bid


def _select_weekly_strike(
    calls: list[dict],
    target_delta: float = 0.30,
    target_strike: float | None = None,
    target_delta_low: float | None = None,
    target_delta_high: float | None = None,
) -> dict | None:
    """Pick weekly short strike.

    When `target_strike` is set: pick the listed strike CLOSEST to
    target_strike, regardless of delta. Used when a rule (e.g. halfway-
    roll on a Major Breach) prescribes a specific strike that the
    delta-only ranking would miss. Caller is responsible for sanity —
    we don't second-guess (the LLM cited the strike per its rules).

    δ BAND (P1, 2026-07-31): when `target_delta_low`/`target_delta_high` are BOTH
    given (and `target_strike` is None), pick the best liquid strike whose delta
    falls WITHIN [low, high] — the consent envelope — choosing the one closest to
    the band midpoint (stable/predictable). If no listed strike's delta lands in
    the band, fall through to point selection at the band MIDPOINT. Either bound
    None = no band = the original point/default behavior below.

    When `target_strike` is None and no band: pick the strike whose delta
    is closest to `target_delta` but below 0.40 (OTM only), falling
    back to the full delta pool if no OTM strikes exist. Original
    behavior, preserved for backwards-compat.

    Returns None if no eligible strike (no `delta` field, or no calls).
    """
    if target_strike is not None:
        with_strike = [c for c in calls if c.get("strike_price") is not None]
        if not with_strike:
            return None
        return min(
            with_strike,
            key=lambda c: abs(float(c["strike_price"]) - target_strike),
        )
    if target_delta_low is not None and target_delta_high is not None:
        lo, hi = min(target_delta_low, target_delta_high), max(target_delta_low, target_delta_high)
        in_band = [
            c for c in calls
            if c.get("delta") is not None and lo <= c["delta"] <= hi
        ]
        if in_band:
            mid = (lo + hi) / 2.0
            return min(in_band, key=lambda c: abs(c["delta"] - mid))
        # No liquid strike lands in the band — price at the band midpoint so the
        # roll still builds (the panel/consent shows the actual selected strike).
        target_delta = (lo + hi) / 2.0
    otm = [c for c in calls if c.get("delta") is not None and c["delta"] < 0.40]
    pool = otm if otm else [c for c in calls if c.get("delta") is not None]
    if not pool:
        return None
    return min(pool, key=lambda c: abs(c["delta"] - target_delta))


def _days_to(expiry: str) -> int:
    try:
        return max(0, (date.fromisoformat(expiry) - date.today()).days)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# PMCCAgent
# ---------------------------------------------------------------------------

# Allowed PMCC actions (SHORT-side + hold/watch). close_all is REMOVED: the division
# manages the short weekly calls ONLY and never sells/closes the LEAP. Enforced at the
# LLM parse boundary — any non-allowed action (incl. a hallucinated close_all) is
# normalized to "watch" (no actionable order), belt-and-suspenders with the removed
# prompt option + the removed propose/scan branches.
_PMCC_VALID_ACTIONS = frozenset({
    "hold", "roll_short", "roll_short_early", "roll_leap",
    "close_short", "open_short", "watch",
})


class PMCCAgent:
    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        risk_yaml: Path = Path("config/risk.yaml"),
        db_url: str | None = None,
        *,
        research_firm_deps: "ResearchFirmDeps | None" = None,
        logger_agent: "LoggerAgent | None" = None,
        notify_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._strategies_yaml = strategies_yaml
        self._risk_yaml = risk_yaml
        # Phase 2 (2026-04-30): used by `_build_position_context` to query
        # prior-roll history from the proposed_order table for richer
        # Telegram approval messages. None = skip the audit query, fall
        # back to context-without-roll-count. Tests + ad-hoc CLI default
        # to None so they don't need a DB.
        self._db_url = db_url
        self._cfg: dict = {}
        self._cfg_risk: dict = {}
        self._chat: Any = None

        # Phase 1a-2 (2026-05-01): research firm integration. All three
        # are optional — when None the scout falls back to today's
        # behavior even if `universe_source: research_on_demand` is set
        # in strategies.yaml (with a one-shot warning). Logger is
        # required for the division-side audit kinds; if missing we
        # silently skip writes (tests can pass None to exercise the
        # non-audited path).
        self._research_firm_deps = research_firm_deps
        self._logger_agent = logger_agent
        self._notify_callback = notify_callback

        # Process-memory consecutive-failure counter for extended-outage
        # alerting (§8.A clause c). Resets on any successful engagement;
        # at threshold (default 3, configurable via risk.yaml
        # `pmcc.research_outage_alert_threshold`) we emit
        # `pmcc_research_extended_outage` + Telegram-notify the Board.
        # `_outage_alerted` suppresses re-alerting on every subsequent
        # scan within the same outage streak.
        self._consec_research_failures: int = 0
        self._first_research_failure_ts: str | None = None
        self._last_successful_engagement_id: str | None = None
        self._outage_alerted: bool = False

        # B4 (Phase 1): last chain-pick diagnostics, stashed by the finders so an
        # aborted roll/open can audit WHY no re-open leg was found (distinguish
        # "the fix works" from "chains are thin and we now do nothing").
        self._last_weekly_diag: dict | None = None
        self._last_leap_diag: dict | None = None
        # Brokerage-first earnings (2026-07-28): last resolution stashed by
        # `_earnings_gate_state` so the roll ship path can emit an "earnings
        # unverified" alert when a roll proceeds with no confident earnings date.
        self._last_earnings_resolution = None

        self._reload()

    def attach_research_firm(
        self,
        deps: "ResearchFirmDeps",
        *,
        logger_agent: "LoggerAgent | None" = None,
        notify_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Wire the research-firm deps after construction.

        main.py constructs PMCCAgent BEFORE the checkpointer context
        opens (where research_firm_deps gets built). Rather than
        reorder construction, main.py calls this method once
        `build_research_firm_deps()` returns. Idempotent.
        """
        self._research_firm_deps = deps
        if logger_agent is not None:
            self._logger_agent = logger_agent
        if notify_callback is not None:
            self._notify_callback = notify_callback

    def _reload(self) -> None:
        try:
            with self._strategies_yaml.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._cfg = data.get("robinhood_pmcc", {}) or {}
        except Exception as e:
            log.warning("PMCCAgent: failed to load strategies.yaml: %s", e)

        try:
            with self._risk_yaml.open("r", encoding="utf-8") as f:
                rdata = yaml.safe_load(f) or {}
            self._cfg_risk = rdata
        except Exception as e:
            log.warning("PMCCAgent: failed to load risk.yaml: %s", e)

    # -- Config properties ---------------------------------------------------

    @property
    def universe_source(self) -> str:
        return self._cfg.get("universe_source", "positions")

    @property
    def position_exclude(self) -> set[str]:
        return set(self._cfg.get("position_exclude", []) or [])

    @property
    def watchlist(self) -> list[str]:
        return list(self._cfg.get("watchlist", []) or [])

    # ── Config accessors: prefer strategies.yaml `strategy.*`, fall back to risk.yaml ──

    @property
    def _pmcc_cfg(self) -> dict:
        """Risk-based PMCC defaults (fallback when strategies.yaml is silent)."""
        return self._cfg_risk.get("pmcc", {}) or {}

    @property
    def _strategy_cfg(self) -> dict:
        """The new rich strategy block from strategies.yaml."""
        return self._cfg.get("strategy", {}) or {}

    @property
    def _long_leg_cfg(self) -> dict:
        return self._strategy_cfg.get("long_leg", {}) or {}

    @property
    def _short_leg_cfg(self) -> dict:
        return self._strategy_cfg.get("short_leg", {}) or {}

    @property
    def _management_cfg(self) -> dict:
        return self._strategy_cfg.get("management", {}) or {}

    @property
    def _roll_dte(self) -> int:
        # Prefer strategies.yaml strategy.management.roll_dte_trigger
        v = self._management_cfg.get("roll_dte_trigger")
        if v is not None:
            return int(v)
        return int(self._pmcc_cfg.get("short_call_roll_dte", 21))

    def _roll_dte_for(self, leg: PMCCPosition) -> int:
        """Effective roll-DTE trigger for a leg."""
        return self._roll_dte

    @property
    def _roll_profit_pct(self) -> float:
        v = self._management_cfg.get("profit_take_pct")
        if v is not None:
            return float(v)
        return float(self._pmcc_cfg.get("short_call_roll_profit_pct", 0.50))

    @property
    def _leap_min_dte(self) -> int:
        v = self._long_leg_cfg.get("dte_min")
        if v is not None:
            return int(v)
        return int(self._pmcc_cfg.get("long_call_min_dte", 365))

    # B8 (2026-07-22): the LEAP delta-band properties (_leap_min_delta /
    # _leap_max_delta) were RETIRED — no code path read them. LEAP strike selection
    # is a hard delta >= 0.80 (deepest qualifying ITM) in `_select_leap_strike`,
    # which is INTENTIONAL (module docstring; skill "0.55-0.80" band, deep end) and
    # NOT config-driven. The long_leg.delta_min/delta_max config keys were retired
    # with them. `_leap_min_dte` above IS still used (the DTE floor).

    @property
    def _short_target_delta(self) -> float:
        # Prefer the midpoint of the strategy's "balanced" range
        rng = self._short_leg_cfg.get("delta_balanced")
        if isinstance(rng, list) and len(rng) == 2:
            return (float(rng[0]) + float(rng[1])) / 2.0
        return float(self._pmcc_cfg.get("short_call_target_delta", 0.30))

    @property
    def _short_target_dte(self) -> int:
        v = self._short_leg_cfg.get("dte_target")
        if v is not None:
            return int(v)
        return _WEEKLY_MIN_DTE

    @property
    def _short_delta_band_half(self) -> float:
        """Half-width of the δ consent BAND derived around the LLM's point target
        (P1, 2026-07-31). The pricing refresh selects the concrete strike WITHIN
        [target_delta − half, target_delta + half]. Tunable via strategies.yaml
        `short_leg.delta_band_half` (falls back to 0.05)."""
        v = self._short_leg_cfg.get("delta_band_half")
        return float(v) if v is not None else 0.05

    def _apply_delta_band(self, analysis: "PMCCAnalysis | None") -> "PMCCAnalysis | None":
        """Derive + stamp the δ BAND on a fresh judgment (P1). low/high =
        `target_delta` ± `_short_delta_band_half`, clamped to a sane OTM window
        [0.10, 0.45]. No-op when the LLM gave no `target_delta` (band stays None →
        pricing falls back to the config-default point) or a band is already set.
        Mutates + returns `analysis` so the SAME band the operator consents to is
        both used for the render AND persisted on the decision record."""
        if analysis is None:
            return analysis
        if analysis.target_delta_low is not None or analysis.target_delta_high is not None:
            return analysis
        td = analysis.target_delta
        if td is None:
            return analysis
        half = self._short_delta_band_half
        analysis.target_delta_low = max(0.10, round(float(td) - half, 4))
        analysis.target_delta_high = min(0.45, round(float(td) + half, 4))
        return analysis

    @property
    def _contracts_per_25k(self) -> int:
        return int(self._pmcc_cfg.get("contracts_per_25k_equity", 1))

    # ── Liquidity gate (strategies.yaml strategy.liquidity) ──

    @property
    def _liquidity_cfg(self) -> dict:
        return self._strategy_cfg.get("liquidity", {}) or {}

    @property
    def _max_bid_ask_spread_pct(self) -> float:
        return float(self._liquidity_cfg.get("max_bid_ask_spread_pct", 0.10))

    @property
    def _min_open_interest(self) -> int:
        return int(self._liquidity_cfg.get("min_open_interest", 100))

    @property
    def _min_avg_volume(self) -> int:
        return int(self._liquidity_cfg.get("min_avg_volume", 50))

    @property
    def _oi_bypass_min_volume(self) -> int:
        # 1(b) 2026-07-23: today's volume at/above which a contract clears the
        # liveness gate DESPITE low open interest. OI accumulates over a
        # contract's life; a genuinely-traded FRESH near-dated expiry hasn't had
        # time to build OI, so an OI-only floor wrongly rejects it. Set high
        # enough that a phantom/untraded strike (which trades ~0) cannot clear
        # it — the observed fresh-daily roll targets traded ~5k-8k.
        return int(self._liquidity_cfg.get("oi_bypass_min_volume", 500))

    def _passes_liquidity(self, opt: dict, *, symbol: str | None = None) -> tuple[bool, str]:
        """Return (passes, reason). Uses the strategies.yaml liquidity gates.

        Liveness = established OR actively-traded: pass when open interest >=
        `min_open_interest` OR today's volume >= `oi_bypass_min_volume` (1(b)).
        OI is a stock that accumulates over a contract's life while volume is the
        live signal, so a FRESH near-dated expiry with real volume + tight
        spreads — which an OI-only floor wrongly rejected — still qualifies. A
        modest per-contract volume floor (`min_avg_volume`) and the bid-ask
        spread cap then apply to every contract.

        `symbol` is retained for API stability (callers pass it) but no longer
        selects the gate. 1(a) 2026-07-23: a per-contract volume floor of 10000
        (from the since-retired black_sheep `eligibility_criteria`) was mis-applied
        here — no OTM weekly trades 10k/day, so it silently blocked every normal
        roll on the affected names for ~2.7 months. Removed; all contracts use the
        same per-contract volume floor.
        """
        bid = float(opt.get("bid") or 0)
        ask = float(opt.get("ask") or 0)
        oi = int(opt.get("open_interest") or 0)
        vol = int(opt.get("volume") or 0)

        # Liveness: established (open interest) OR actively-traded today (volume).
        if oi < self._min_open_interest and vol < self._oi_bypass_min_volume:
            return False, (
                f"OI={oi} < {self._min_open_interest} AND "
                f"vol={vol} < {self._oi_bypass_min_volume}"
            )

        # Basic per-contract volume floor (traded at all today).
        if vol < self._min_avg_volume:
            return False, f"vol={vol} < {self._min_avg_volume}"

        # Bid-ask spread (skip if no bid — can't compute meaningfully)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid if mid > 0 else 1.0
            if spread_pct > self._max_bid_ask_spread_pct:
                return False, (
                    f"spread={spread_pct*100:.1f}% > "
                    f"{self._max_bid_ask_spread_pct*100:.1f}%"
                )
        elif ask <= 0:
            return False, "no ask price"

        return True, "ok"

    # ── Earnings-buffer gate (strategies.yaml underlying_criteria) ──

    @property
    def _earnings_buffer_days(self) -> int:
        crit = self._strategy_cfg.get("underlying_criteria", {}) or {}
        return int(crit.get("earnings_buffer_days", 7))

    def _earnings_gate_state(self, symbol: str) -> tuple[str, str]:
        """B9: TRI-STATE earnings read, now BROKERAGE-FIRST (2026-07-28).

        Resolves the next-earnings date via `resolve_earnings` — Robinhood's
        VERIFIED date is authoritative; the EODHD/yfinance feed is fallback
        (flagged UNVERIFIED). Fixes the RIOT false-block (feed carried a stale
        2025 date, broker said 08-05 = clear) AND the reverse danger (a stale
        feed FALSELY CLEARING a real upcoming print), because the broker's
        verified date wins in BOTH directions.
          - "blocked"          : earnings within the buffer window → gate blocks.
          - "clear"            : a future date exists, none within the buffer
                                 (also returned when the gate is config-disabled).
          - "data_unavailable" : NEITHER broker nor feed has a future date →
                                 FAIL-OPEN (never silently block a liquid name);
                                 the roll ship path emits an "earnings unverified"
                                 alert so this is never silent.
        Returns (state, reason). Stashes `self._last_earnings_resolution` (source /
        verified / disagreement) for the ship path. Single source of truth for both
        the roll gate and `_blocked_by_earnings` (open path), so the two can never
        drift.
        """
        buffer_days = self._earnings_buffer_days
        if buffer_days <= 0:
            self._last_earnings_resolution = None
            return "clear", ""

        from trading_corp.utils.market_data import resolve_earnings
        res = resolve_earnings(symbol)
        self._last_earnings_resolution = res

        if res.date is None:
            # Neither broker nor feed has a future date. Fail-open (do NOT block a
            # liquid name — the RIOT failure), but never silently: the ship path
            # emits an "earnings unverified" alert.
            return "data_unavailable", "no earnings date from broker or feed (fail-open; UNVERIFIED)"

        from datetime import datetime, timezone
        days = (res.date - datetime.now(timezone.utc)).days  # future date → days_until, floored
        src = f"source={res.source}" + ("" if res.verified else "; UNVERIFIED")
        if 0 <= days <= buffer_days:
            return "blocked", (
                f"earnings on {res.date.date().isoformat()} ({days}d away, "
                f"buffer={buffer_days}d; {src})"
            )
        return "clear", ""

    def _blocked_by_earnings(self, symbol: str) -> tuple[bool, str]:
        """Return (blocked, reason) honoring `earnings_buffer_days`.

        None from yfinance is treated as "no data — don't block" rather than
        fail-safe-escalate, because thinly-traded names commonly lack earnings
        dates in yfinance and we don't want to silently kill the universe.

        Delegates to `_earnings_gate_state` (blocked iff state == "blocked") so the
        open path and the B9 roll gate share one implementation. Behavior is
        byte-identical to the pre-Phase-2 version.
        """
        state, reason = self._earnings_gate_state(symbol)
        if state == "blocked":
            return True, reason
        return False, ""

    def earnings_card_state(
        self, symbol: str, short_strike: float | None = None,
        spot: float | None = None,
    ) -> dict:
        """DISPLAY-layer earnings state for the roll consent card (Enhancement A,
        2026-07-28). Drives off the SAME `_earnings_gate_state` the backend roll
        path uses (via `resolve_earnings`), so the UI and the gate can never
        disagree. Read-only; no order/broker side effects.

        Returns a dict:
          kind         : "blocked" | "unverified" | "clear"
          date         : ISO date of the next earnings (or None)
          verified     : True only when the broker CONFIRMED the date
          source       : "broker" | "feed" | "none" | None
          recommendation: the "let it expire" text (blocked only), else None
          flag         : the unverified-confirm text (unverified only), else None
          caveat       : assignment-risk caveat (blocked AND short ITM), else None
          offer_roll   : False iff blocked (card hides Approve); True otherwise

        `short_strike` + `spot` are optional; when both are given and the short is
        ITM (spot ≥ strike) under a BLOCKED state, a "let it expire risks
        assignment" caveat is surfaced — but the operator's stated default ("let it
        expire") is kept, not overridden."""
        state, _reason = self._earnings_gate_state(symbol)
        res = getattr(self, "_last_earnings_resolution", None)
        date_iso = res.date.date().isoformat() if (res and res.date) else None
        verified = bool(res.verified) if res else False
        source = res.source if res else None
        out = {
            "kind": "clear", "date": date_iso, "verified": verified,
            "source": source, "recommendation": None, "flag": None,
            "caveat": None, "offer_roll": True,
        }
        if state == "blocked":
            out["kind"] = "blocked"
            out["offer_roll"] = False
            out["recommendation"] = (
                f"Earnings {date_iso} — let the current short call expire, then sell "
                "a new call after earnings is announced and the stock has moved."
            )
            try:
                itm = (spot is not None and short_strike is not None
                       and float(spot) >= float(short_strike))
            except (TypeError, ValueError):
                itm = False
            if itm:
                out["caveat"] = (
                    f"Short strike {short_strike:g} is in-the-money (spot {float(spot):g}) "
                    "— letting it expire risks assignment; consider closing before the "
                    "print. Default remains: let it expire."
                )
        elif state == "data_unavailable":
            out["kind"] = "unverified"
            out["offer_roll"] = True
            out["flag"] = "earnings date unverified — confirm before rolling"
        return out

    @staticmethod
    def _classify_liquidity_reason(reason: str) -> str:
        """Bucket a _passes_liquidity reason into the sub-gate that bound:
        liveness (OI-and-volume), volume, spread, no_ask. Observability for the
        abort diagnostics (2026-07-24: 'all failed liquidity gate' hid WHICH gate,
        so the opening-rotation volume/spread cause wasn't visible)."""
        r = reason or ""
        if "no ask" in r:
            return "no_ask"
        if "OI=" in r:                       # "OI=.. < .. AND vol=.. < .." (liveness)
            return "liveness"
        if "spread=" in r:
            return "spread"
        if "vol=" in r:                      # volume-only floor
            return "volume"
        return "other"

    def _filter_liquid(self, opts: list[dict], symbol: str) -> list[dict]:
        """Drop illiquid contracts. Logs each rejection at debug level and
        aggregates which sub-gate bound (liveness / volume / spread / no_ask),
        stored on self._last_liquidity_breakdown for the abort diagnostics."""
        out: list[dict] = []
        breakdown: dict[str, int] = {}
        for o in opts:
            ok, reason = self._passes_liquidity(o, symbol=symbol)
            if ok:
                out.append(o)
            else:
                b = self._classify_liquidity_reason(reason)
                breakdown[b] = breakdown.get(b, 0) + 1
                log.debug(
                    "PMCCAgent liquidity gate dropped %s C%.0f (%s): %s",
                    symbol, o.get("strike_price", 0), o.get("expiration_date"), reason,
                )
        self._last_liquidity_breakdown = dict(breakdown)
        if breakdown:
            log.info(
                "PMCCAgent liquidity: %s — %d/%d passed; failed by sub-gate: %s",
                symbol, len(out), len(opts), dict(breakdown),
            )
        return out

    # -- LLM support ---------------------------------------------------------

    def _ensure_chat(self) -> Any:
        """Lazily build the LangChain chat model. Returns None if unavailable."""
        if self._chat is None:
            try:
                from trading_corp.agents.llm import build_chat_model, is_llm_available
                if is_llm_available():
                    self._chat = build_chat_model("pmcc_robinhood", max_tokens=2048)
            except Exception as e:
                log.warning("PMCCAgent: LLM init failed: %s", e)
        return self._chat

    async def _llm_analyze_position(
        self,
        pos: PMCCPosition,
        underlying_price: float | None,
        regime: str,
        vix: float | None = None,
    ) -> PMCCAnalysis | None:
        """Ask Claude to analyze a single PMCC position. Returns None if LLM unavailable."""
        chat = self._ensure_chat()
        if not chat:
            return None

        price_str = f"${underlying_price:.2f}" if underlying_price else "unknown"
        vix_str = f"{vix:.2f}" if vix is not None else "unknown"

        # Estimate intrinsic / P&L on the LEAP
        intrinsic_note = ""
        if underlying_price and pos.long_leg_strike:
            intrinsic = max(0.0, underlying_price - pos.long_leg_strike) * 100
            pnl = intrinsic - pos.long_leg_avg_price
            coverage = (underlying_price / pos.long_leg_strike - 1) * 100
            intrinsic_note = (
                f"\n  - Intrinsic value: ${intrinsic:.2f}/contract "
                f"(P&L vs avg cost: ${pnl:+.2f}) | "
                f"underlying is {coverage:+.1f}% vs strike"
            )

        # Build short call section
        short_section: str
        if pos.short_leg_expiry is None:
            short_section = "**UNCOVERED** — no short call currently sold against this LEAP"
        else:
            # Use abs() — robin_stocks reports short avg_price inconsistently
            credit_per_sh = abs(pos.short_leg_avg_price or 0) / 100
            mark = pos.short_leg_mark or 0.0
            pnl_pct = (pos.short_leg_pnl_pct or 0) * 100

            # Intrinsic / extrinsic split (for terminal-DTE decision making)
            intrinsic_per_sh = 0.0
            extrinsic_per_sh = 0.0
            spot_vs_strike_pct = None
            if underlying_price and pos.short_leg_strike:
                intrinsic_per_sh = max(0.0, underlying_price - pos.short_leg_strike)
                extrinsic_per_sh = max(0.0, mark - intrinsic_per_sh)
                spot_vs_strike_pct = (underlying_price - pos.short_leg_strike) / pos.short_leg_strike

            # ITM / ATM-zone classification
            itm_label = ""
            if spot_vs_strike_pct is not None:
                if spot_vs_strike_pct > 0.015:
                    itm_label = f" ⚠️ ITM by {spot_vs_strike_pct*100:.1f}%"
                elif abs(spot_vs_strike_pct) <= 0.015:
                    itm_label = f" ⚪ ATM zone ({spot_vs_strike_pct*100:+.1f}% from strike)"

            # Theta-decision context (only meaningful at terminal DTE)
            theta_block = ""
            if pos.short_leg_dte is not None and pos.short_leg_dte <= 2 and underlying_price:
                qty = max(1, int(abs(pos.short_leg_qty or 1)))
                close_cost_now = mark * 100 * qty
                breakeven_spot = (
                    pos.short_leg_strike + close_cost_now / 100 / qty
                    if pos.short_leg_strike else None
                )
                theta_block = (
                    f"\n  - **TERMINAL-DTE THETA WINDOW** (≤2 DTE):\n"
                    f"    Close cost NOW: ${close_cost_now:.2f} "
                    f"(intrinsic ${intrinsic_per_sh*100*qty:.2f} + extrinsic ${extrinsic_per_sh*100*qty:.2f})\n"
                    f"    If held to expiry with stock unchanged: closes at "
                    f"${intrinsic_per_sh*100*qty:.2f} (extrinsic decays to $0.00)\n"
                    f"    Theta saved by waiting (worst case): ${extrinsic_per_sh*100*qty:.2f}\n"
                )
                if breakeven_spot is not None:
                    breakeven_pct = (breakeven_spot - underlying_price) / underlying_price
                    theta_block += (
                        f"    Roll-vs-wait breakeven: stock at expiration = "
                        f"${breakeven_spot:.2f} ({breakeven_pct*100:+.2f}% from current)\n"
                        f"    → Wait WINS if stock closes below ${breakeven_spot:.2f} at expiry\n"
                    )

            short_section = (
                f"Strike: ${pos.short_leg_strike:.2f} "
                f"({pos.short_leg_expiry}, {pos.short_leg_dte} DTE){itm_label}\n"
                f"  - Original credit received: ${credit_per_sh:.2f}/sh "
                f"(${abs(pos.short_leg_avg_price or 0):.2f}/contract)\n"
                f"  - Current mark: ${mark:.2f}/sh "
                f"(intrinsic ${intrinsic_per_sh:.2f}/sh + extrinsic ${extrinsic_per_sh:.2f}/sh)\n"
                f"  - Profit captured so far: {pnl_pct:.0f}%\n"
                f"  - Spread width (short − LEAP strike): "
                f"${(pos.short_leg_strike or 0) - pos.long_leg_strike:.2f}"
                f"{theta_block}"
            )

        # Inject the rule block that applies to this position (all names: STANDARD).
        rules_block = _STANDARD_RULES.format(symbol=pos.symbol)

        # ROLL HISTORY block — tell the LLM what just happened to this LEAP
        # so it doesn't recommend back-to-back halfway rolls. The
        # underlying data also feeds `_recent_halfway_roll_cooldown`'s
        # deterministic guard; the prompt presence keeps the LLM's
        # narration coherent with what the guard does.
        history_block = self._format_roll_history_block(pos)

        prompt = f"""Analyze this PMCC (Poor Man's Covered Call) position and recommend a specific action.

{rules_block}

## Position: {pos.symbol}
- Current underlying price: {price_str}
- Market regime: {regime}
- VIX (spot): {vix_str}

## LEAP (Long Call — synthetic stock replacement)
- Contract: {pos.long_leg_symbol}
- Strike: ${pos.long_leg_strike:.2f} | Expiry: {pos.long_leg_expiry} | DTE remaining: {pos.long_leg_dte}
- Delta: {pos.long_leg_delta:.2f}
- Contracts: {pos.long_leg_qty:.0f}
- Average cost paid: ${pos.long_leg_avg_price:.2f}/contract (${pos.long_leg_avg_price/100:.2f}/share equivalent){intrinsic_note}

## Short Call (Weekly income leg)
{short_section}

{history_block}
Respond with ONLY this JSON (no other text, no markdown):
{{
  "action": "<hold|roll_short|roll_short_early|roll_leap|close_short|open_short|watch>",
  "confidence": <float 0.0-1.0>,
  "urgency": "<routine|elevated|urgent>",
  "summary": "<one clear sentence: situation + recommended action>",
  "rationale": "<2-4 sentences with specific Greek / IV / structural reasoning>",
  "warnings": ["<specific risk>", "<specific risk>"],
  "target_delta": <recommended short call delta as float, or null>,
  "target_dte": <recommended short call DTE target as integer, or null>,
  "target_strike": <recommended short call STRIKE as float, or null — set this when a rule prescribes a specific strike (e.g. a cited resistance level). When set, the strike picker honors this directly, overriding delta-distance ranking. Leave null when delta-targeting is correct (standard cycles).>,
  "override": <null, OR {{"kind": "hold_override"|"net_debit_justified"|"earnings_override", "reason": "<one clause>"}} — set ONLY when a rule you cite explicitly permits an action a deterministic guard would otherwise block (a HOLD you want rolled, a small net-debit roll, or a roll inside the earnings buffer); otherwise null.>
}}

Action reference:
- hold: all criteria healthy, manage at next scheduled trigger
- roll_short: normal roll (<=2 DTE OR >=50% profit captured)
- roll_short_early: roll before the normal trigger (defensive on a breached short)
- roll_leap: LEAP needs to be rolled (delta drift below 0.40, DTE < 120, or strike compromised)
- close_short: close/buy-back the short call — appropriate for assignment / earnings risk
- open_short: LEAP is uncovered — sell a new weekly call
- watch: no action but flag for close monitoring next cycle

This division manages the SHORT weekly calls ONLY. NEVER recommend selling or closing
the LEAP; there is no "close the whole position" action — an exit is an operator action.
"""

        try:
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
            resp = await chat.ainvoke([
                SystemMessage(content=_PMCC_EXPERT_SYSTEM),
                HumanMessage(content=prompt),
            ])
            raw = resp.content.strip()
            # Strip markdown code fences if present
            if "```" in raw:
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else parts[0]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            data = json.loads(raw)
            # ACTION ALLOWLIST (2026-07-31): reject any non-allowed action — a stale or
            # hallucinated close_all (or anything outside _PMCC_VALID_ACTIONS) becomes
            # "watch" so no LEAP-touching / close-all order can ever be built.
            _action = str(data.get("action", "watch"))
            if _action.strip().lower() not in _PMCC_VALID_ACTIONS:
                log.warning("PMCCAgent: LLM returned non-allowed action %r for %s -> watch",
                            _action, pos.symbol)
                _action = "watch"
            _analysis = PMCCAnalysis(
                symbol=pos.symbol,
                action=_action,
                confidence=float(data.get("confidence", 0.5)),
                urgency=str(data.get("urgency", "routine")),
                summary=str(data.get("summary", "")),
                rationale=str(data.get("rationale", "")),
                warnings=list(data.get("warnings", []) or []),
                target_delta=float(data["target_delta"]) if data.get("target_delta") is not None else None,
                target_dte=int(data["target_dte"]) if data.get("target_dte") is not None else None,
                target_strike=float(data["target_strike"]) if data.get("target_strike") is not None else None,
                override=data.get("override") if isinstance(data.get("override"), dict) else None,
            )
            # Stamp the δ consent BAND (P1) so the SAME envelope is used for the
            # render and persisted for the LLM-free pricing refresh.
            return self._apply_delta_band(_analysis)
        except Exception as e:
            log.warning("PMCCAgent: LLM analysis failed for %s: %s", pos.symbol, e)
            return None

    @staticmethod
    async def _fetch_prices(symbols: list[str]) -> dict[str, float]:
        """Fetch current spot prices via yfinance (runs in thread pool to avoid blocking)."""
        if not symbols:
            return {}

        def _sync() -> dict[str, float]:
            prices: dict[str, float] = {}
            try:
                import yfinance as yf  # type: ignore
                tickers = yf.Tickers(" ".join(symbols))
                for sym in symbols:
                    try:
                        hist = tickers.tickers[sym].history(period="1d")
                        if not hist.empty:
                            prices[sym] = float(hist["Close"].iloc[-1])
                    except Exception:
                        pass
            except ImportError:
                pass
            return prices

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def propose_orders_for_pair(
        self,
        broker: Broker,
        symbol: str,
        analysis: PMCCAnalysis,
        *,
        preview: bool = False,
    ) -> list[ProposedOrder]:
        """Translate an LLM action recommendation into concrete ProposedOrders.

        Used by:
          - Dashboard's "Approve & Execute" button
          - Telegram's per-pair approval flow (Stage B)

        Reuses the existing _propose_* helpers so the rationale + sizing logic
        stays consistent with the scheduled scan path. Actions that need no
        order ('hold', 'watch') return [].

        `preview=True` (2026-07-30): this call is a card render / Re-analyze /
        estimate build, NOT a dispatch attempt. It still resolves the concrete
        target strikes + prices (so the consent card can show them), but every
        abort gate suppresses its `pmcc_roll_aborted` audit row and its exec-alert
        (see `_audit_roll_abort(preview=...)`) and the earnings-unverified alert is
        withheld. Exec-alerts fire ONLY on a genuine dispatch attempt.
        """
        # Detect position FIRST so the 0-DTE wall-clock gate can override
        # action="hold"/"watch" before the early-return below (Board
        # direction 2026-05-01 — see _terminal_dte_time_release).
        positions = await self.detect_existing_legs(broker)
        target = symbol.upper()
        pos = next((p for p in positions if p.symbol.upper() == target), None)

        # Apply 0-DTE wall-clock time gate. No-op when pos is None or
        # the position isn't 0-DTE; otherwise may rewrite hold/watch →
        # roll_short (15:00–15:30 ET) or close_short urgent (>= 15:30 ET).
        # The deep-OTM early-release moneyness gate needs a Robinhood spot
        # (matching the band's evidence source) — quote only when `pos` is an
        # early-roll candidate; 0.0 / failure → None → gate fails safe (no fire).
        _early_spot = None
        if pos is not None and self._early_release_needs_spot(pos, analysis):
            _q = await broker.quote(symbol)
            _early_spot = _q if _q and _q > 0 else None
        analysis = self._terminal_dte_time_release(analysis, pos, spot=_early_spot)
        # Apply LEAP Hard Rule promotion (Item 2 — 2026-05-02). When LEAP
        # delta>=0.95 OR long_leg_dte<120, promote roll_short → roll_leap
        # so the recommendation includes the LEAP roll legs, not just
        # the short roll. Order: AFTER terminal-DTE (which may have
        # promoted hold→roll_short and now needs Hard-Rule lifting too).
        analysis = self._promote_to_roll_leap_if_hard_rule(analysis, pos)
        # Apply halfway-roll cooldown (Item 1 — 2026-05-02). Downgrades
        # roll_short → hold when a recent roll-up was executed. Order:
        # AFTER Hard-Rule promotion (which may have already moved past
        # roll_short to roll_leap — cooldown is a no-op on roll_leap).
        analysis = self._recent_halfway_roll_cooldown(analysis, pos)

        action = (analysis.action or "").lower()
        if action in ("hold", "watch"):
            return []

        if pos is None:
            log.warning(
                "propose_orders_for_pair: %s has no detected open position; "
                "no orders proposed", symbol,
            )
            return []

        # Default contract count = whatever the LEAP holds (1:1 short-cover
        # per LEAP is standard PMCC sizing)
        contracts = max(1, int(abs(pos.long_leg_qty)))

        # ── Roll short (with or without "early" trigger) ──
        if action in ("roll_short", "roll_short_early"):
            return await self._propose_roll_short(
                symbol, pos, broker, analysis=analysis, preview=preview)

        # ── Open a new short on an uncovered LEAP ──
        if action == "open_short":
            return await self._propose_sell_weekly(
                symbol, broker, contracts, analysis=analysis, leg=pos,
            )

        # ── Close the short call (urgent buy-to-close, no replacement) ──
        if action == "close_short":
            if not pos.short_leg_expiry or pos.short_leg_strike is None:
                log.info("close_short for %s: no short leg to close", symbol)
                return []
            return [self._make_option_order(
                underlying=symbol,
                side="buy",
                contracts=int(abs(pos.short_leg_qty or 1)),
                expiry=pos.short_leg_expiry,
                strike=pos.short_leg_strike,
                mark_price=pos.short_leg_mark or 0.0,
                position_effect="close",
                action="close_short_urgent",
                dte=pos.short_leg_dte,
                rationale=self._build_rationale(
                    f"Approved close: buy {symbol} {pos.short_leg_expiry} "
                    f"C{pos.short_leg_strike:.2f}",
                    analysis,
                ),
            )]

        # ── Roll the LEAP (close existing pair, open new LEAP) ──
        if action == "roll_leap":
            # B9 (earnings) — a roll_leap's 4th leg opens new short premium (Phase 2.5).
            rl_override = self._override_kind(analysis)
            rl_gates: dict = {}
            rl_estate, rl_ereason = self._earnings_gate_state(symbol)
            rl_gates["earnings"] = rl_estate
            if rl_estate == "blocked" and rl_override != "earnings_override":
                self._audit_roll_abort(
                    reason="earnings_window", symbol=symbol,
                    extra={"gates": dict(rl_gates), "earnings_reason": rl_ereason},
                    preview=preview,
                )
                return []
            # B4 (atomic roll_leap): resolve BOTH new legs BEFORE proposing any
            # close; abort the whole roll + audit if either is missing.
            new_leap = await self._find_best_leap(symbol, broker)
            if not new_leap:
                self._audit_roll_abort(
                    reason="sparse_chain_no_leap", symbol=symbol,
                    missing_leg="new_leap", diag=self._last_leap_diag,
                    preview=preview,
                )
                return []
            new_weekly = await self._find_best_weekly(
                symbol, broker,
                target_delta=analysis.target_delta if analysis else None,
                target_dte=analysis.target_dte if analysis else None,
                target_strike=analysis.target_strike if analysis else None,
                target_delta_low=analysis.target_delta_low if analysis else None,
                target_delta_high=analysis.target_delta_high if analysis else None,
                after_dte=pos.short_leg_dte,  # B7: new short must roll OUT
            )
            if not new_weekly:
                self._audit_roll_abort(
                    reason="sparse_chain_no_weekly_for_new_leap",
                    symbol=symbol, missing_leg="new_short_on_new_leap",
                    diag=self._last_weekly_diag,
                    preview=preview,
                )
                return []
            rl_gates["selection"] = "ok"
            # B2 (short-leg credit) — close-old-short vs open-new-short pair ONLY;
            # the LEAP legs (2+3) are B3's domain (do NOT re-derive compound cost).
            rl_close_mark = pos.short_leg_mark or 0.0
            rl_cons_net, rl_mark_net, rl_open_bid = _short_roll_credit(new_weekly, rl_close_mark)
            if rl_cons_net < 0 and rl_override != "net_debit_justified":
                rl_gates["credit"] = "blocked"
                self._audit_roll_abort(
                    reason="net_debit_roll", symbol=symbol,
                    extra={"gates": dict(rl_gates), "conservative_net": round(rl_cons_net, 4),
                           "mark_net": round(rl_mark_net, 4), "close_mark": round(rl_close_mark, 4),
                           "open_bid": rl_open_bid, "fees_included": False,
                           "fee_gap": "pre-fee: spread only"},
                    preview=preview,
                )
                return []
            rl_gates["credit"] = "clear"

            orders: list[ProposedOrder] = []
            pair_id = str(uuid.uuid4())[:8]
            # 1. Close the short (if any)
            if pos.short_leg_expiry and pos.short_leg_strike is not None:
                orders.append(self._make_option_order(
                    underlying=symbol, side="buy", contracts=contracts,
                    expiry=pos.short_leg_expiry,
                    strike=pos.short_leg_strike,
                    mark_price=pos.short_leg_mark or 0.0,
                    position_effect="close",
                    action="roll_leap_close_short",
                    dte=pos.short_leg_dte,
                    rationale=self._build_rationale(
                        f"Roll LEAP prep: close short {symbol} {pos.short_leg_expiry} "
                        f"C{pos.short_leg_strike:.2f}",
                        analysis,
                    ),
                    pair_id=pair_id,
                ))
            # 2. Close the existing LEAP
            orders.append(self._make_option_order(
                underlying=symbol, side="sell", contracts=contracts,
                expiry=pos.long_leg_expiry,
                strike=pos.long_leg_strike,
                mark_price=(float(pos.long_leg_mark) if pos.long_leg_mark is not None else None),  # B3
                position_effect="close",
                action="roll_leap_close",
                dte=pos.long_leg_dte,
                rationale=self._build_rationale(
                    f"Roll LEAP: sell old LEAP {symbol} {pos.long_leg_expiry} "
                    f"C{pos.long_leg_strike:.2f}",
                    analysis,
                ),
                pair_id=pair_id,
            ))
            # 3. Open new LEAP
            orders.append(self._make_option_order(
                underlying=symbol, side="buy", contracts=contracts,
                expiry=new_leap["expiration_date"],
                strike=new_leap["strike_price"],
                mark_price=new_leap.get("mark_price") or new_leap.get("ask") or 0,
                bid=new_leap.get("bid"), ask=new_leap.get("ask"),
                position_effect="open",
                action="roll_leap_open",
                delta=new_leap.get("delta"),
                dte=new_leap.get("dte"),
                rationale=self._build_rationale(
                    f"Roll LEAP: buy new {symbol} {new_leap['expiration_date']} "
                    f"C{new_leap['strike_price']:.2f}",
                    analysis,
                ),
                pair_id=pair_id,
            ))
            # 4. Open new short on the new LEAP (guaranteed present — atomic
            # invariant checked above).
            orders.append(self._make_option_order(
                underlying=symbol, side="sell", contracts=contracts,
                expiry=new_weekly["expiration_date"],
                strike=new_weekly["strike_price"],
                mark_price=(
                    new_weekly.get("mark_price")
                    or new_weekly.get("bid") or 0
                ),
                bid=new_weekly.get("bid"), ask=new_weekly.get("ask"),
                position_effect="open",
                action="roll_leap_open_short",
                delta=new_weekly.get("delta"),
                dte=new_weekly.get("dte"),
                rationale=self._build_rationale(
                    f"Roll LEAP: sell new short on the fresh LEAP "
                    f"{symbol} {new_weekly['expiration_date']} "
                    f"C{new_weekly['strike_price']:.2f}",
                    analysis,
                ),
                pair_id=pair_id,
            ))
            if not preview:
                # preview: don't write a shipped-roll audit for a mere card render.
                self._audit_division("pmcc_roll_gates", {
                    "symbol": symbol, "gates": dict(rl_gates),
                    "conservative_net": round(rl_cons_net, 4),
                    "mark_net": round(rl_mark_net, 4), "override_kind": rl_override,
                })
            # Phase A: roll_leap legs are ADVISORY — the operator executes the LEAP
            # roll MANUALLY; the agent never places them. (Fresh local `orders`
            # holds only the 4 roll_leap legs here.) The fail-closed dispatch guard
            # also refuses any advisory / roll_leap order.
            for _rl_leg in orders:
                _rl_leg.dispatch = "advisory"
            return orders

        log.warning(
            "propose_orders_for_pair: action=%r not yet handled for %s",
            action, symbol,
        )
        return []

    async def build_trade_recommendation(
        self,
        broker: Broker,
        symbol: str,
        analysis: PMCCAnalysis,
        *,
        preview: bool = False,
        prebuilt_orders: list[ProposedOrder] | None = None,
    ) -> TradeRecommendation | None:
        """Build a concrete TradeRecommendation (dollar-priced legs + benefits).

        Used by the dashboard's expert-analysis panel to show specifically what
        will happen if the user clicks Approve & Execute. Returns None for
        actions that don't require any orders (hold/watch).

        `preview=True` (2026-07-30): the panel/Re-analyze render is NOT a dispatch
        attempt, so the underlying `propose_orders_for_pair` build suppresses its
        abort/earnings exec-alerts + audit rows. The returned recommendation
        (legs, strikes, prices) is identical to the non-preview build.

        `prebuilt_orders` (2026-07-30): pass an already-built order list to derive
        the recommendation from WITHOUT re-proposing. The web Re-analyze handler
        builds the combo once, then feeds the SAME list here (display), to the
        consent estimate, and to the dispatch stash — so the strike shown, the
        estimate shown, and the strike fired are guaranteed identical (one build,
        no re-quote drift between them).
        """
        action = (analysis.action or "").lower()
        if action in ("", "hold", "watch"):
            return None

        # Reuse the existing order-proposal logic. These ProposedOrders carry
        # mark_per_share / bid / ask / delta / dte in extra (we just made them).
        orders = (
            prebuilt_orders if prebuilt_orders is not None
            else await self.propose_orders_for_pair(
                broker, symbol, analysis, preview=preview)
        )
        if not orders:
            return None

        # Find the existing position so we can compute trade-vs-current benefits
        positions = await self.detect_existing_legs(broker)
        existing = next(
            (p for p in positions if p.symbol.upper() == symbol.upper()),
            None,
        )

        # Convert each ProposedOrder → TradeLegDetail
        legs: list[TradeLegDetail] = []
        net_dollars = 0.0
        for o in orders:
            extra = o.extra or {}
            qty = int(abs(o.qty))
            mark = extra.get("mark_per_share")
            bid = extra.get("bid")
            ask = extra.get("ask")
            position_effect = str(extra.get("position_effect", "open"))
            opt_type = str(extra.get("option_type", "call"))

            # Action label: buy/sell × open/close → human phrase
            label_map = {
                ("buy", "open"):   "Buy to open",
                ("buy", "close"):  "Buy to close",
                ("sell", "open"):  "Sell to open",
                ("sell", "close"): "Sell to close",
            }
            action_label = label_map.get(
                (o.side, position_effect),
                o.side.capitalize(),
            )

            # Cost: per-contract = mark × 100. Signed by side.
            per_contract = (float(mark) * 100.0) if mark else 0.0
            signed = per_contract * qty * (1.0 if o.side == "buy" else -1.0)
            net_dollars += signed

            legs.append(TradeLegDetail(
                action_label=action_label,
                side=o.side,
                position_effect=position_effect,
                underlying=str(extra.get("underlying", o.symbol)),
                expiry=str(extra.get("expiration", "")),
                strike=float(extra.get("strike", 0)),
                option_type=opt_type,
                qty=qty,
                dte=extra.get("dte"),
                delta=extra.get("delta"),
                mark_per_share=mark,
                bid=bid,
                ask=ask,
                estimated_dollars=signed,
            ))

        # Cost confidence: based on spread quality of the new (open) legs
        # — close legs lack bid/ask so we can't measure them. If any new
        # leg is wide, drop confidence accordingly.
        opening_legs = [l for l in legs if l.position_effect == "open"]
        qualities = [l.spread_quality for l in opening_legs]
        if not qualities or all(q == "unknown" for q in qualities):
            cost_confidence = "medium"   # no data → middling default
        elif "wide" in qualities:
            cost_confidence = "low"
        elif "medium" in qualities:
            cost_confidence = "medium"
        else:
            cost_confidence = "high"

        benefits = self._compute_benefits(action, legs, existing)

        # Wait-vs-roll scenario analysis (only relevant when the recommendation
        # involves closing a near-expiry short — the user's "why roll now when
        # theta will eat $X by tomorrow?" question).
        wait_alt: WaitAlternative | None = None
        if self._wait_alternative_relevant(action, existing):
            spot = await self._fetch_underlying_spot(symbol)
            if spot is not None:
                wait_alt = self._build_wait_alternative(existing, spot)

        return TradeRecommendation(
            action=action,
            legs=legs,
            net_cost_dollars=net_dollars,
            cost_confidence=cost_confidence,
            benefits=benefits,
            wait_alternative=wait_alt,
        )

    @staticmethod
    def _wait_alternative_relevant(action: str, existing: PMCCPosition | None) -> bool:
        """True if the wait-vs-roll comparison would be meaningful for this action."""
        closing_actions = {
            "roll_short", "roll_short_early", "close_short", "roll_leap",
        }
        if action not in closing_actions or existing is None:
            return False
        return (
            existing.short_leg_expiry is not None
            and existing.short_leg_strike is not None
            and existing.short_leg_dte is not None
            and existing.short_leg_dte <= 7
            and existing.short_leg_mark is not None
            and existing.short_leg_mark > 0
        )

    @staticmethod
    async def _fetch_underlying_spot(symbol: str) -> float | None:
        prices = await PMCCAgent._fetch_prices([symbol])
        return prices.get(symbol.upper())

    @staticmethod
    def _build_wait_alternative(
        existing: PMCCPosition,
        underlying_price: float,
    ) -> WaitAlternative | None:
        """Compute the wait-vs-roll-now scenario table.

        Math:
          - close_cost_now    = mark × 100 × qty (dollars to BTC right now)
          - close_cost_at_X   = max(0, scen_spot − strike) × 100 × qty
          - savings_vs_now    = close_cost_now − close_cost_at_X
                                (positive = waiting saves money)
          - breakeven_spot    = strike + close_cost_now/100/qty
                                (above this, rolling-now wins)
        """
        if existing.short_leg_strike is None or existing.short_leg_mark is None:
            return None
        strike = existing.short_leg_strike
        mark = existing.short_leg_mark
        qty = max(1, int(abs(existing.short_leg_qty or 1)))
        close_cost_now = mark * 100 * qty
        if close_cost_now <= 0 or strike <= 0:
            return None

        breakeven_spot = strike + close_cost_now / 100 / qty
        breakeven_pct = ((breakeven_spot - underlying_price) / underlying_price
                         if underlying_price > 0 else 0.0)

        # Scenario percentages — chosen to span "small move" to "1.5σ-ish move
        # for high-IV names". Each scenario shows what closing-at-expiry would
        # cost, vs what closing-now costs.
        pcts = [-0.02, 0.0, 0.01, 0.02, 0.04, 0.06]
        scenarios: list[WaitScenario] = []
        for pct in pcts:
            scen_spot = underlying_price * (1 + pct)
            close_at_expiry = max(0.0, scen_spot - strike) * 100 * qty
            savings = close_cost_now - close_at_expiry
            label = "unchanged" if pct == 0 else f"{pct*100:+.0f}%"
            scenarios.append(WaitScenario(
                label=label,
                scen_spot=scen_spot,
                close_cost=close_at_expiry,
                savings_vs_now=savings,
            ))

        # Plain-English summary
        if breakeven_spot > underlying_price * 1.025:
            summary = (
                f"Wait wins unless stock pops > {breakeven_pct*100:.1f}% by expiration. "
                f"Theta worth ${close_cost_now:,.2f} if it stays put."
            )
        elif breakeven_spot > underlying_price * 1.005:
            summary = (
                f"Wait wins on small moves. Stock must close above "
                f"${breakeven_spot:,.2f} ({breakeven_pct*100:+.1f}%) "
                "for rolling-now to win."
            )
        else:
            summary = (
                f"Already past the wait-wins zone — stock would need to close "
                f"below ${breakeven_spot:,.2f} at expiry, which is "
                f"{abs(breakeven_pct)*100:.1f}% below current."
            )

        return WaitAlternative(
            breakeven_spot=breakeven_spot,
            breakeven_pct=breakeven_pct,
            max_savings=close_cost_now,
            scenarios=scenarios,
            summary=summary,
        )

    @staticmethod
    def _compute_benefits(
        action: str,
        legs: list[TradeLegDetail],
        existing: PMCCPosition | None,
    ) -> list[str]:
        """Per-action bullet list describing what this trade buys you."""
        b: list[str] = []
        # Pull the close vs open legs we'll reference repeatedly
        close_legs = [l for l in legs if l.position_effect == "close"]
        open_legs  = [l for l in legs if l.position_effect == "open"]

        if action in ("roll_short", "roll_short_early"):
            # Identify the new short being opened and the old short being closed
            new_short = next((l for l in open_legs if l.side == "sell"), None)
            old_short = next(
                (l for l in close_legs if l.side == "buy" and l.option_type == "call"),
                None,
            )
            if new_short and new_short.dte is not None:
                b.append(f"Captures {new_short.dte} more days of theta decay")
            if new_short and old_short:
                strike_change = new_short.strike - old_short.strike
                if strike_change > 0.01:
                    b.append(
                        f"Strike pushed up ${strike_change:,.2f} "
                        f"(${old_short.strike:,.2f} → ${new_short.strike:,.2f}) "
                        "— more upside room before next breach"
                    )
                elif strike_change < -0.01:
                    b.append(
                        f"Defensive: strike lowered ${abs(strike_change):,.2f} "
                        f"to chase higher delta target"
                    )
            if new_short and new_short.delta is not None:
                b.append(f"New short delta: {new_short.delta:.2f}")
            # Realized P&L locked in on the close leg.
            # Note: robin_stocks reports `average_price` for shorts inconsistently
            # — sometimes as a positive credit-per-contract, sometimes as a
            # negative debit-basis. We treat it as the absolute credit always.
            if old_short and existing and existing.short_leg_avg_price:
                orig_credit = abs(existing.short_leg_avg_price) / 100.0
                if old_short.mark_per_share is not None:
                    locked = (orig_credit - old_short.mark_per_share) * 100 * old_short.qty
                    if abs(locked) > 1:
                        b.append(
                            f"Locks in ${locked:+,.2f} on the existing short "
                            f"(orig credit ${orig_credit:.2f}/sh − close ${old_short.mark_per_share:.2f}/sh)"
                        )

        elif action == "close_short":
            # Locks in P&L: original credit (always positive) - buyback cost
            old_short = next((l for l in close_legs if l.side == "buy"), None)
            if old_short and existing and existing.short_leg_avg_price:
                orig_credit = abs(existing.short_leg_avg_price) / 100.0
                if old_short.mark_per_share is not None:
                    locked = (orig_credit - old_short.mark_per_share) * 100 * old_short.qty
                    b.append(
                        f"Locks in ${locked:+,.2f} on the short "
                        f"(orig credit ${orig_credit:.2f}/sh − close ${old_short.mark_per_share:.2f}/sh)"
                    )
            b.append("Frees the LEAP from short coverage; you can re-sell when conditions improve")

        elif action == "open_short":
            new_short = next((l for l in open_legs if l.side == "sell"), None)
            if new_short:
                credit = (new_short.mark_per_share or 0) * 100 * new_short.qty
                b.append(f"Adds ${credit:,.2f} of short premium income")
                if new_short.dte is not None:
                    b.append(f"Captures {new_short.dte} days of theta")
                if new_short.delta is not None:
                    b.append(f"New short delta: {new_short.delta:.2f}")

        elif action == "roll_leap":
            new_leap = next(
                (l for l in open_legs if l.side == "buy" and l.option_type == "call"),
                None,
            )
            old_leap = next(
                (l for l in close_legs if l.side == "sell" and l.option_type == "call"),
                None,
            )
            if new_leap and old_leap and old_leap.dte is not None and new_leap.dte is not None:
                b.append(
                    f"LEAP DTE: {old_leap.dte}d → {new_leap.dte}d "
                    f"(+{new_leap.dte - old_leap.dte} days)"
                )
            if new_leap and new_leap.delta is not None:
                b.append(f"New LEAP delta: {new_leap.delta:.2f} "
                         "(restored to PMCC range)")
            if new_leap and old_leap:
                strike_change = new_leap.strike - old_leap.strike
                if abs(strike_change) > 0.01:
                    b.append(
                        f"Strike change: ${old_leap.strike:,.2f} → ${new_leap.strike:,.2f} "
                        f"({'+' if strike_change > 0 else ''}${strike_change:,.2f})"
                    )

        if not b:
            b.append("Result depends on fill prices — see legs above")
        return b

    async def analyze_symbol(
        self,
        broker: Broker,
        symbol: str,
        regime: str = "unknown",
        underlying_price: float | None = None,
    ) -> PMCCAnalysis | None:
        """Analyze ONE existing PMCC position by underlying symbol.

        Used by the dashboard's per-pair drill-down panel: click a pair row
        in the UI → fetches just that one position's expert analysis (much
        faster than analyze_portfolio's full sweep).

        Returns None if the symbol isn't found in the broker's open option
        positions, or if the LLM call fails.
        """
        positions = await self.detect_existing_legs(broker)
        target = symbol.upper()
        pos = next((p for p in positions if p.symbol.upper() == target), None)
        if pos is None:
            log.info("PMCCAgent.analyze_symbol: %s not found in open positions", target)
            return None

        if underlying_price is None:
            prices = await self._fetch_prices([target])
            underlying_price = prices.get(target)

        from trading_corp.utils.market_data import get_vix
        vix = get_vix()

        return await self._llm_analyze_position(
            pos, underlying_price, regime, vix=vix,
        )

    # -- Portfolio analysis (standalone markdown report) ---------------------

    async def analyze_portfolio(
        self,
        broker: Broker,
        regime: str = "unknown",
        underlying_prices: dict[str, float] | None = None,
    ) -> str:
        """Full LLM expert analysis of current PMCC portfolio.

        Returns a markdown report suitable for Telegram/CLI display.
        Called by _on_scan in main.py before routing proposed orders.
        """
        positions = await self.detect_existing_legs(broker)
        if not positions:
            return "📊 **PMCC Portfolio Analysis** — No open positions detected."

        # Fetch prices for any symbols not supplied
        prices = dict(underlying_prices or {})
        missing = [p.symbol for p in positions if p.symbol not in prices]
        if missing:
            fetched = await self._fetch_prices(missing)
            prices.update(fetched)

        # Fetch VIX once so the LLM gets a consistent reading across positions
        from trading_corp.utils.market_data import get_vix
        vix = get_vix()

        # Run LLM analysis with bounded concurrency. Same rationale as
        # scan(): Anthropic's 30k input-tokens/min org cap on
        # claude-sonnet-4-6 produced 429s when 13 legs fired in parallel
        # on 2026-05-08. Tunable via strategies.yaml `pmcc.llm_concurrency`.
        llm_concurrency = max(1, int(self._cfg.get("llm_concurrency", 3)))
        sem = asyncio.Semaphore(llm_concurrency)

        async def _analyze_one(p):
            async with sem:
                return await self._llm_analyze_position(
                    p, prices.get(p.symbol), regime, vix=vix,
                )

        raw_analyses = await asyncio.gather(
            *[_analyze_one(p) for p in positions],
            return_exceptions=True,
        )
        analyses: list[PMCCAnalysis | None] = [
            a if isinstance(a, PMCCAnalysis) else None for a in raw_analyses
        ]

        lines: list[str] = [
            f"📊 **PMCC Portfolio Analysis** — {now_utc().date().isoformat()}",
            f"*Regime: {regime} | {len(positions)} position(s)*",
            "",
        ]

        for pos, analysis in zip(positions, analyses):
            price_str = f"${prices[pos.symbol]:.2f}" if pos.symbol in prices else "N/A"
            lines.append(f"**{pos.symbol}** @ {price_str}")

            # LEAP line
            lines.append(
                f"  🏛 LEAP: {pos.long_leg_expiry} C{pos.long_leg_strike:.2f} "
                f"δ={pos.long_leg_delta:.2f} | {pos.long_leg_dte}d "
                f"| avg ${pos.long_leg_avg_price:.2f}/ct"
            )

            # Short call line
            if pos.short_leg_expiry:
                pnl_pct = (pos.short_leg_pnl_pct or 0) * 100
                mark = pos.short_leg_mark or 0.0
                lines.append(
                    f"  📅 Short: {pos.short_leg_expiry} C{pos.short_leg_strike:.2f} "
                    f"| {pos.short_leg_dte}d | {pnl_pct:.1f}% captured "
                    f"| mark ${mark:.2f}"
                )
            else:
                lines.append("  ⚠️ *UNCOVERED — no short call sold*")

            # LLM expert verdict
            if analysis:
                emoji = _URGENCY_EMOJI.get(analysis.urgency, "⚪")
                lines.append(
                    f"  {emoji} **{analysis.action.upper()}** "
                    f"({analysis.confidence*100:.0f}% conf) — {analysis.summary}"
                )
                lines.append(f"  💭 _{analysis.rationale}_")
                for w in analysis.warnings:
                    lines.append(f"  ⚠️ {w}")
            else:
                lines.append("  *(LLM analysis unavailable — API key missing or call failed)*")

            lines.append("")  # spacer

        return "\n".join(lines)

    # -- Phase 2: judgment pass + digest ------------------------------------

    async def judgment_pass(
        self, broker: Broker, regime: str = "unknown",
    ) -> "dict[str, PMCCAnalysis | None]":
        """P2 judgment-ONLY pass over every HELD PMCC leg: LLM-analyze (bounded
        concurrency), apply the SAME deterministic composition as scan() (0-DTE
        terminal release -> LEAP hard-rule promotion -> halfway-roll cooldown), and
        PERSIST each final verdict (source='scan', band+DTE). Builds NO orders and
        does NO routing. Returns {symbol: PMCCAnalysis|None}.

        This DUPLICATES scan()'s judgment+store block by design — scan() is left
        byte-identical per the P2 decision. `test_pmcc_judgment_parity` asserts the
        two paths produce the same verdict so the duplication cannot silently drift.
        """
        self._reload()
        existing = await self.detect_existing_legs(broker)
        legs_by_symbol: dict[str, PMCCPosition] = {leg.symbol: leg for leg in existing}
        if not legs_by_symbol:
            return {}
        self._check_options_tier_once(broker)
        prices = await self._fetch_prices(list(legs_by_symbol.keys()))
        from trading_corp.utils.market_data import get_vix
        vix = get_vix()

        analyses: dict[str, PMCCAnalysis | None] = {}
        syms = list(legs_by_symbol.keys())
        llm_concurrency = max(1, int(self._cfg.get("llm_concurrency", 3)))
        sem = asyncio.Semaphore(llm_concurrency)

        async def _analyze_one(s: str):
            async with sem:
                return await self._llm_analyze_position(
                    legs_by_symbol[s], prices.get(s), regime, vix=vix,
                )

        raw = await asyncio.gather(
            *[_analyze_one(s) for s in syms], return_exceptions=True,
        )
        for sym, res in zip(syms, raw):
            if isinstance(res, Exception):
                log.warning("PMCCAgent.judgment_pass: LLM exception for %s: %s", sym, res)
                analyses[sym] = None
            else:
                analyses[sym] = res  # type: ignore[assignment]

        # SAME composition order as scan() / propose_orders_for_pair.
        for sym in list(analyses.keys()):
            if sym in legs_by_symbol:
                _leg = legs_by_symbol[sym]
                _early_spot = None
                if self._early_release_needs_spot(_leg, analyses[sym]):
                    _q = await broker.quote(sym)
                    _early_spot = _q if _q and _q > 0 else None
                a = self._terminal_dte_time_release(
                    analyses[sym], _leg, spot=_early_spot,
                )
                a = self._promote_to_roll_leap_if_hard_rule(a, legs_by_symbol[sym])
                a = self._recent_halfway_roll_cooldown(a, legs_by_symbol[sym])
                analyses[sym] = a

        # SAME persist as scan()'s unified tile/expert writer (source='scan').
        if self._db_url and analyses:
            from trading_corp.agents.divisions import _pmcc_status
            _now_iso = now_utc().isoformat()
            _stale_h = float((self._cfg.get("tile_status") or {}).get(
                "staleness_hours", _pmcc_status.DEFAULT_STALENESS_HOURS))
            for _sym, _a in analyses.items():
                if _a is None:
                    continue
                _pmcc_status.record_pmcc_decision(
                    _sym, status=_a.action, source="scan", computed_at=_now_iso,
                    db_url=self._db_url, urgency=_a.urgency,
                    confidence=_a.confidence, summary=_a.summary,
                    rationale=_a.rationale, warnings=_a.warnings,
                    target_delta_low=getattr(_a, "target_delta_low", None),
                    target_delta_high=getattr(_a, "target_delta_high", None),
                    target_dte=getattr(_a, "target_dte", None),
                    staleness_hours=_stale_h,
                )
        return analyses

    async def compose_slot_digest(
        self, broker: Broker, db_url: str, *, kind: str, slot_label: str,
        prev_slot_label: str, prior_decisions: dict, prior_snapshot: dict | None,
        thresholds: dict,
    ) -> "tuple[str, dict]":
        """Fresh-price ALL held holdings (correction B: warm the cache at run time)
        and build the slot's Telegram text. Returns (digest_text, new_snapshot).

        Judgment must ALREADY be stored (this reads load_decision + prices — NO LLM).
        kind='full' -> compact per-holding digest (never truncated by the caller's
        push_split); kind='delta' -> material-changes-only digest, else a heartbeat.
        The returned snapshot is persisted so the next slot can compute pricing move.
        """
        from trading_corp.web import pmcc_pricing
        from trading_corp.agents.divisions import _pmcc_status
        slug = PMCC_SLUG
        holdings = await self.detect_existing_legs(broker)
        syms = [h.symbol for h in holdings]
        priced: dict[str, Any] = {}
        for s in syms:
            try:
                priced[s] = await pmcc_pricing.price_and_stash(self, broker, slug, s, db_url)
            except Exception as e:      # noqa: BLE001 — digest must never crash the loop
                log.warning("compose_slot_digest: price(%s) failed: %s", s, e)
                priced[s] = None
        new_dec = {s: _pmcc_status.load_decision(s, db_url=db_url) for s in syms}

        def _est(s):
            pr = priced.get(s)
            return pr.estimate if (pr is not None and getattr(pr, "buildable", False)) else None

        new_snap = {s: holding_snapshot(new_dec.get(s), _est(s)) for s in syms}
        rows = [digest_row(s, new_dec.get(s), priced.get(s)) for s in syms]

        if kind == "full":
            header = (
                f"PMCC judgment {slot_label} - {now_utc().date().isoformat()} "
                f"({len(rows)} holding(s))"
            )
            text = build_full_digest(header, rows)
        else:
            prior_map = (prior_snapshot or {}).get("holdings", {}) if prior_snapshot else {}
            material: list = []
            for s in syms:
                prior_combined = _prior_combined(prior_decisions.get(s), prior_map.get(s))
                d = judgment_delta(prior_combined, new_snap[s], thresholds)
                if d["material"]:
                    material.append((digest_row(s, new_dec.get(s), priced.get(s)), d["reasons"]))
            closed = sorted(set(prior_map.keys()) - set(syms))
            text = build_delta_digest(slot_label, prev_slot_label, material, closed)

        snapshot = {"taken_at": now_utc().isoformat(), "slot": slot_label, "holdings": new_snap}
        return text, snapshot

    # -- Universe ------------------------------------------------------------

    async def get_universe(self, broker: Broker) -> list[str]:
        """Symbols to scan this cycle.

        Primary: stock positions held.
        Fallback: underlyings of existing long call positions (options-only accounts).
        """
        if self.universe_source == "watchlist":
            return [s for s in self.watchlist if s not in self.position_exclude]

        # Phase 1a-2: research_on_demand defers new-opens to the research
        # firm (separate code path in scan()). For existing-leg management
        # we still need the held-position symbol list — so this branch
        # returns the same set "positions" mode would, derived from
        # currently-held PMCC underlyings + stock positions. New-open
        # candidates do NOT come from this list when research_on_demand
        # is active.
        # (Falls through to the same `positions`-mode logic below.)

        try:
            snap = await broker.snapshot()
        except NotImplementedError:
            log.warning("PMCCAgent: broker.snapshot() not implemented; empty universe")
            return []

        symbols: list[str] = []
        for pos in snap.positions:
            if " " in pos.symbol or "#" in pos.symbol:
                continue   # skip options
            if "/" in pos.symbol:
                # Crypto held as HODL store-of-value (e.g. ETH/USD,
                # BTC/USD from RobinhoodBroker.snapshot's crypto branch
                # added 2026-05-01). Visible in dashboard equity, not
                # tradeable as PMCC underlyings — skip from scan universe
                # so they don't pre-empt the leg-underlyings fallback.
                continue
            if pos.symbol in self.position_exclude:
                continue
            if abs(pos.qty) < self._cfg.get("position_min_shares", 1):
                continue
            symbols.append(pos.symbol)

        if symbols:
            log.info("PMCCAgent universe from stock positions: %s", symbols)
            return symbols

        # No stock positions — derive universe from underlyings of long calls.
        if isinstance(broker, OptionBroker):
            try:
                opt_positions = await broker.get_option_positions_detail()
                opt_symbols: set[str] = set()
                for op in opt_positions:
                    if op.get("option_type") == "call" and (op.get("quantity") or 0) > 0:
                        sym = op.get("chain_symbol", "")
                        if sym and sym not in self.position_exclude:
                            opt_symbols.add(sym)
                if opt_symbols:
                    result = sorted(opt_symbols)
                    log.info("PMCCAgent universe from long call underlyings: %s", result)
                    return result
            except Exception as e:
                log.warning("PMCCAgent: failed to derive universe from options: %s", e)

        log.info("PMCCAgent: empty universe")
        return []

    # -- Detect existing legs ------------------------------------------------

    async def detect_existing_legs(self, broker: Broker) -> list[PMCCPosition]:
        """Read open option positions and pair LEAP + short calls by underlying."""
        if not isinstance(broker, OptionBroker):
            log.warning(
                "PMCCAgent: broker does not implement OptionBroker protocol; "
                "cannot detect existing legs"
            )
            return []

        positions = await broker.get_option_positions_detail()

        longs: dict[str, list[dict]] = {}
        shorts: dict[str, list[dict]] = {}
        for pos in positions:
            if pos.get("option_type") != "call":
                continue
            sym = pos["chain_symbol"]
            if pos["quantity"] > 0:
                longs.setdefault(sym, []).append(pos)
            elif pos["quantity"] < 0:
                shorts.setdefault(sym, []).append(pos)

        from trading_corp.utils.market_data import cache_leap_value

        result: list[PMCCPosition] = []
        for sym in set(longs):
            # Sort longs by DTE descending — highest DTE = LEAP
            long_calls = sorted(longs[sym], key=lambda p: p.get("dte") or 0, reverse=True)
            leap = long_calls[0]
            leap_dte = leap.get("dte") or 0
            leap_expiry = leap.get("expiration_date") or ""
            leap_strike = float(leap.get("strike_price") or 0)

            if leap_dte < _LEAP_DTE_FLOOR:
                log.debug(
                    "PMCCAgent: %s long call DTE=%d < %d; not treating as LEAP",
                    sym, leap_dte, _LEAP_DTE_FLOOR,
                )

            # Populate LEAP value cache so the auto-execute "5% of long" gate
            # can read it without a live broker call.
            leap_mark = leap.get("mark_price")
            if leap_mark is not None:
                try:
                    cache_leap_value(sym, float(leap_mark) * 100.0)
                except (TypeError, ValueError):
                    pass

            long_sym = f"{sym} {leap_expiry} C {leap_strike:.2f}"

            short_calls = sorted(shorts.get(sym, []), key=lambda p: p.get("dte") or 999)
            if short_calls:
                short = short_calls[0]
                short_expiry = short.get("expiration_date") or ""
                short_strike = float(short.get("strike_price") or 0)
                short_dte = short.get("dte")
                short_avg = float(short.get("avg_price") or 0)
                short_mark = short.get("mark_price")
                pnl_pct: float | None = None
                if short_mark is not None and short_avg > 0:
                    pnl_pct = 1.0 - (short_mark / short_avg)
                result.append(PMCCPosition(
                    symbol=sym,
                    long_leg_expiry=leap_expiry,
                    long_leg_strike=leap_strike,
                    long_leg_delta=float(leap.get("delta") or 0),
                    long_leg_dte=leap_dte,
                    long_leg_qty=float(leap.get("quantity") or 1),
                    long_leg_avg_price=float(leap.get("avg_price") or 0),
                    long_leg_symbol=long_sym,
                    long_leg_mark=leap.get("mark_price"),
                    short_leg_expiry=short_expiry,
                    short_leg_strike=short_strike,
                    short_leg_dte=short_dte,
                    short_leg_pnl_pct=pnl_pct,
                    short_leg_qty=float(short.get("quantity") or -1),
                    short_leg_mark=short_mark,
                    short_leg_avg_price=short_avg,
                    short_leg_symbol=f"{sym} {short_expiry} C {short_strike:.2f}",
                ))
            else:
                result.append(PMCCPosition(
                    symbol=sym,
                    long_leg_expiry=leap_expiry,
                    long_leg_strike=leap_strike,
                    long_leg_delta=float(leap.get("delta") or 0),
                    long_leg_dte=leap_dte,
                    long_leg_qty=float(leap.get("quantity") or 1),
                    long_leg_avg_price=float(leap.get("avg_price") or 0),
                    long_leg_symbol=long_sym,
                    long_leg_mark=leap.get("mark_price"),
                ))

        log.info(
            "PMCCAgent detected %d PMCC legs: %s",
            len(result), [p.symbol for p in result],
        )
        return result

    # -- Main scan -----------------------------------------------------------

    async def scan(
        self,
        broker: Broker,
        regime: str = "unknown",
        *,
        zero_dte_only: bool = False,
        skip_symbols: "set[str] | None" = None,
    ) -> list[ProposedOrder]:
        """Scan existing PMCC legs; propose rolls / new setups.

        LLM expert analysis enriches every order rationale and can surface
        additional actions beyond the deterministic roll rules:
          - roll_leap: LEAP delta has drifted, needs to be rolled
          - roll_short_early: opportunistic early roll before DTE/profit trigger
          - close_short (urgent): ITM short call requiring immediate attention
        """
        self._reload()

        snap = await broker.snapshot()
        universe = await self.get_universe(broker)

        # Detect legs before the early-return: an account can have
        # held PMCC legs even when get_universe() returns empty (e.g.
        # if the stock-position branch fired with a sparse universe
        # that excludes those underlyings). Legs always need
        # management regardless of universe shape.
        existing = await self.detect_existing_legs(broker)
        self._check_options_tier_once(broker)
        legs_by_symbol: dict[str, PMCCPosition] = {leg.symbol: leg for leg in existing}

        # B10: the 15:00-ET terminal-DTE pass evaluates ONLY 0-DTE positions — a SUBSET
        # filter, never a second full scan (no new-opens, no non-0-DTE legs). `skip_symbols`
        # drops positions already in the HITL approval queue (no duplicate proposals). The LLM
        # is still called on the surviving 0-DTE legs; the unchanged
        # `_terminal_dte_time_release` then overrides a REAL HOLD/WATCH (not a fabricated one).
        if zero_dte_only:
            # `== 0` (NOT `or -1`): short_leg_dte 0 is falsy, so `x or -1` would wrongly
            # drop the very 0-DTE legs we want. None (uncovered LEAP) != 0 → excluded.
            legs_by_symbol = {
                s: lg for s, lg in legs_by_symbol.items() if lg.short_leg_dte == 0
            }
            universe = []
        if skip_symbols:
            legs_by_symbol = {
                s: lg for s, lg in legs_by_symbol.items() if s not in skip_symbols
            }

        # Phase 1a-2: empty universe is OK when research_on_demand is
        # active — new-opens come from the research firm, not from
        # held positions. Also OK when held legs exist — the loop
        # below iterates universe ∪ legs so leg management still runs.
        if (
            not universe
            and not legs_by_symbol
            and self.universe_source != "research_on_demand"
        ):
            log.info("PMCCAgent: empty universe and no existing legs; nothing to scan")
            return []

        # Stock position lookup for sizing new PMCCs.
        # Excludes options (" ", "#") and HODL crypto ("/").
        stock_qty: dict[str, float] = {
            pos.symbol: abs(pos.qty)
            for pos in snap.positions
            if " " not in pos.symbol
            and "#" not in pos.symbol
            and "/" not in pos.symbol
        }
        equity = snap.equity

        # Fetch spot prices for LLM context
        all_symbols = list(set(universe) | set(legs_by_symbol.keys()))
        prices = await self._fetch_prices(all_symbols)

        # Single VIX read shared across all positions in this scan cycle
        from trading_corp.utils.market_data import get_vix
        vix = get_vix()

        # Run LLM analysis for all existing legs with bounded concurrency.
        # Anthropic org rate limit on claude-sonnet-4-6 is 30k input
        # tokens/min; firing all legs in parallel produced 429s on
        # 2026-05-08 (5 of 13 legs lost their verdict). A semaphore
        # caps the in-flight burst so peak token usage stays under
        # the cap. Tunable via strategies.yaml `pmcc.llm_concurrency`
        # (default 3).
        analyses: dict[str, PMCCAnalysis | None] = {}
        if legs_by_symbol:
            syms = list(legs_by_symbol.keys())
            llm_concurrency = max(1, int(self._cfg.get("llm_concurrency", 3)))
            sem = asyncio.Semaphore(llm_concurrency)

            async def _analyze_one(s: str):
                async with sem:
                    return await self._llm_analyze_position(
                        legs_by_symbol[s], prices.get(s), regime, vix=vix,
                    )

            raw = await asyncio.gather(
                *[_analyze_one(s) for s in syms],
                return_exceptions=True,
            )
            for sym, res in zip(syms, raw):
                if isinstance(res, Exception):
                    log.warning("PMCCAgent: LLM exception for %s: %s", sym, res)
                    analyses[sym] = None
                else:
                    analyses[sym] = res  # type: ignore[assignment]
                    if res and isinstance(res, PMCCAnalysis):
                        log.info(
                            "PMCCAgent LLM: %s → %s (%s, %.0f%% conf): %s",
                            sym, res.action, res.urgency,
                            res.confidence * 100, res.summary,
                        )

        # Apply Board's 0-DTE wall-clock time gates to every analysis
        # (Board direction 2026-05-01 — see _terminal_dte_time_release).
        # No-op for non-0-DTE positions and for non-HOLD/WATCH actions.
        # Then LEAP Hard Rule promotion (Item 2) and halfway-roll
        # cooldown (Item 1) — same composition order as
        # propose_orders_for_pair so the dashboard "Approve & Execute"
        # path and the scheduled-scan path apply the same overrides.
        for sym in list(analyses.keys()):
            if sym in legs_by_symbol:
                _leg = legs_by_symbol[sym]
                # The deep-OTM early-release moneyness gate MUST evaluate against
                # a Robinhood spot — the source its band was derived from — NOT
                # the yfinance `prices` dict. Quote ONLY early-roll candidates
                # (0–2 per scan); 0.0 / failure → None → gate fails safe (no fire).
                _early_spot = None
                if self._early_release_needs_spot(_leg, analyses[sym]):
                    _q = await broker.quote(sym)
                    _early_spot = _q if _q and _q > 0 else None
                a = self._terminal_dte_time_release(
                    analyses[sym], _leg, spot=_early_spot,
                )
                a = self._promote_to_roll_leap_if_hard_rule(
                    a, legs_by_symbol[sym],
                )
                a = self._recent_halfway_roll_cooldown(
                    a, legs_by_symbol[sym],
                )
                analyses[sym] = a

        # ── Unified tile/expert decision record (scan writer) ──────────────
        # Persist each analyzed symbol's FINAL verdict (source='scan') so the
        # tile badge and the Expert panel read one timestamped decision instead
        # of diverging. Precedence protects a still-fresh manual Expert; a symbol
        # whose LLM analysis aborted (None) is left UNwritten -> tile NO SIGNAL.
        # Best-effort — a status write never blocks the scan. (Pre-open triage()
        # is a separate method and deliberately writes nothing here.)
        if self._db_url and analyses:
            from trading_corp.agents.divisions import _pmcc_status
            _now_iso = now_utc().isoformat()
            _stale_h = float((self._cfg.get("tile_status") or {}).get(
                "staleness_hours", _pmcc_status.DEFAULT_STALENESS_HOURS))
            for _sym, _a in analyses.items():
                if _a is None:
                    continue
                _pmcc_status.record_pmcc_decision(
                    _sym, status=_a.action, source="scan", computed_at=_now_iso,
                    db_url=self._db_url, urgency=_a.urgency,
                    confidence=_a.confidence, summary=_a.summary,
                    rationale=_a.rationale, warnings=_a.warnings,
                    target_delta_low=getattr(_a, "target_delta_low", None),
                    target_delta_high=getattr(_a, "target_delta_high", None),
                    target_dte=getattr(_a, "target_dte", None),
                    staleness_hours=_stale_h,
                )

        orders: list[ProposedOrder] = []

        # Iterate union of universe ∪ legs_by_symbol. Held legs always
        # need management even when get_universe() returns a list that
        # doesn't include their underlyings (e.g. a sparse stock-position
        # universe that excludes leg symbols). Order is preserved:
        # universe symbols first (drives new-opens branch), then
        # leg-only symbols (always hits the legs_by_symbol branch).
        scan_symbols = list(dict.fromkeys(
            list(universe) + [s for s in legs_by_symbol if s not in universe]
        ))

        for symbol in scan_symbols:
            max_contracts = max(1, int(equity / 25_000))
            analysis = analyses.get(symbol)

            if symbol in legs_by_symbol:
                leg = legs_by_symbol[symbol]
                contracts = max(1, int(abs(leg.long_leg_qty)))

                # ── LLM-detected URGENT actions (take priority over deterministic) ──
                if analysis and analysis.urgency == "urgent":
                    if analysis.action == "close_short" and leg.short_leg_expiry:
                        log.warning("PMCCAgent: URGENT close_short for %s: %s", symbol, analysis.summary)
                        orders.append(self._make_option_order(
                            underlying=symbol, side="buy", contracts=contracts,
                            expiry=leg.short_leg_expiry,
                            strike=leg.short_leg_strike or 0.0,
                            mark_price=leg.short_leg_mark or 0.0,
                            position_effect="close",
                            action="close_short_urgent",
                            dte=leg.short_leg_dte,
                            rationale=self._build_rationale(
                                f"URGENT: buy-to-close {symbol} {leg.short_leg_expiry} "
                                f"C{leg.short_leg_strike:.2f}",
                                analysis,
                            ),
                        ))
                        continue


                # ── LLM-detected roll_leap (not in deterministic rules) ──
                if analysis and analysis.action == "roll_leap":
                    # B9 (earnings) — a roll_leap's 4th leg opens new short premium (Phase 2.5).
                    rl_override = self._override_kind(analysis)
                    rl_gates: dict = {}
                    rl_estate, rl_ereason = self._earnings_gate_state(symbol)
                    rl_gates["earnings"] = rl_estate
                    if rl_estate == "blocked" and rl_override != "earnings_override":
                        self._audit_roll_abort(
                            reason="earnings_window", symbol=symbol,
                            extra={"gates": dict(rl_gates), "earnings_reason": rl_ereason},
                        )
                        continue
                    # B4 (atomic roll_leap): resolve BOTH new legs BEFORE
                    # proposing any close. If either is missing, abort the whole
                    # roll (propose nothing) + audit — never dismantle the long
                    # or close the short and leave a fresh LEAP uncovered.
                    new_leap = await self._find_best_leap(symbol, broker)
                    if not new_leap:
                        self._audit_roll_abort(
                            reason="sparse_chain_no_leap", symbol=symbol,
                            missing_leg="new_leap", diag=self._last_leap_diag,
                        )
                        continue
                    new_weekly = await self._find_best_weekly(
                        symbol, broker,
                        target_delta=analysis.target_delta if analysis else None,
                        target_dte=analysis.target_dte if analysis else None,
                        target_strike=analysis.target_strike if analysis else None,
                        target_delta_low=analysis.target_delta_low if analysis else None,
                        target_delta_high=analysis.target_delta_high if analysis else None,
                        after_dte=leg.short_leg_dte,  # B7: new short must roll OUT
                    )
                    if not new_weekly:
                        self._audit_roll_abort(
                            reason="sparse_chain_no_weekly_for_new_leap",
                            symbol=symbol, missing_leg="new_short_on_new_leap",
                            diag=self._last_weekly_diag,
                        )
                        continue
                    rl_gates["selection"] = "ok"
                    # B2 (short-leg credit) — close-old-short vs open-new-short pair ONLY;
                    # LEAP legs (2+3) are B3's domain (do NOT re-derive compound cost).
                    rl_close_mark = leg.short_leg_mark or 0.0
                    rl_cons_net, rl_mark_net, rl_open_bid = _short_roll_credit(new_weekly, rl_close_mark)
                    if rl_cons_net < 0 and rl_override != "net_debit_justified":
                        rl_gates["credit"] = "blocked"
                        self._audit_roll_abort(
                            reason="net_debit_roll", symbol=symbol,
                            extra={"gates": dict(rl_gates), "conservative_net": round(rl_cons_net, 4),
                                   "mark_net": round(rl_mark_net, 4), "close_mark": round(rl_close_mark, 4),
                                   "open_bid": rl_open_bid, "fees_included": False,
                                   "fee_gap": "pre-fee: spread only"},
                        )
                        continue
                    rl_gates["credit"] = "clear"

                    log.info("PMCCAgent: LLM recommends roll_leap for %s", symbol)
                    pair_id = str(uuid.uuid4())[:8]
                    # Step 1: close short call if exists
                    if leg.short_leg_expiry:
                        orders.append(self._make_option_order(
                            underlying=symbol, side="buy", contracts=contracts,
                            expiry=leg.short_leg_expiry,
                            strike=leg.short_leg_strike or 0.0,
                            mark_price=leg.short_leg_mark or 0.0,
                            position_effect="close",
                            action="roll_leap_close_short",
                            dte=leg.short_leg_dte,
                            rationale=self._build_rationale(
                                f"Roll LEAP prep (1/3): close short {symbol} "
                                f"{leg.short_leg_expiry} C{leg.short_leg_strike:.2f}",
                                analysis,
                            ),
                            pair_id=pair_id,
                        ))
                    # Step 2: sell existing LEAP
                    orders.append(self._make_option_order(
                        underlying=symbol, side="sell", contracts=contracts,
                        expiry=leg.long_leg_expiry,
                        strike=leg.long_leg_strike,
                        mark_price=(float(leg.long_leg_mark) if leg.long_leg_mark is not None else None),  # B3
                        position_effect="close",
                        action="roll_leap_close",
                        dte=leg.long_leg_dte,
                        rationale=self._build_rationale(
                            f"Roll LEAP (2/3): sell old LEAP {symbol} "
                            f"{leg.long_leg_expiry} C{leg.long_leg_strike:.2f}",
                            analysis,
                        ),
                        pair_id=pair_id,
                    ))
                    # Step 3: buy new LEAP
                    orders.append(self._make_option_order(
                        underlying=symbol, side="buy", contracts=contracts,
                        expiry=new_leap["expiration_date"],
                        strike=new_leap["strike_price"],
                        mark_price=new_leap.get("mark_price") or new_leap.get("ask") or 0,
                        position_effect="open",
                        action="roll_leap_open",
                        delta=new_leap.get("delta"),
                        dte=new_leap.get("dte"),
                        rationale=self._build_rationale(
                            f"Roll LEAP (3/4): buy new LEAP {symbol} "
                            f"{new_leap['expiration_date']} C{new_leap['strike_price']:.2f}",
                            analysis,
                        ),
                        pair_id=pair_id,
                    ))
                    # Step 4: sell new short on the fresh LEAP (guaranteed
                    # present — atomic invariant checked above).
                    orders.append(self._make_option_order(
                        underlying=symbol, side="sell",
                        contracts=contracts,
                        expiry=new_weekly["expiration_date"],
                        strike=new_weekly["strike_price"],
                        mark_price=(
                            new_weekly.get("mark_price")
                            or new_weekly.get("bid") or 0
                        ),
                        bid=new_weekly.get("bid"),
                        ask=new_weekly.get("ask"),
                        position_effect="open",
                        action="roll_leap_open_short",
                        delta=new_weekly.get("delta"),
                        dte=new_weekly.get("dte"),
                        rationale=self._build_rationale(
                            f"Roll LEAP (4/4): sell new short on "
                            f"fresh LEAP {symbol} "
                            f"{new_weekly['expiration_date']} "
                            f"C{new_weekly['strike_price']:.2f}",
                            analysis,
                        ),
                        pair_id=pair_id,
                    ))
                    # Phase A: roll_leap legs are ADVISORY — operator executes the
                    # LEAP roll MANUALLY; the agent never places them. Scoped to
                    # this batch via pair_id (orders here is the scan accumulator).
                    for _rl_leg in orders:
                        if (_rl_leg.extra or {}).get("pmcc_pair_id") == pair_id:
                            _rl_leg.dispatch = "advisory"
                    self._audit_division("pmcc_roll_gates", {
                        "symbol": symbol, "gates": dict(rl_gates),
                        "conservative_net": round(rl_cons_net, 4),
                        "mark_net": round(rl_mark_net, 4), "override_kind": rl_override,
                    })
                    continue  # skip normal deterministic block

                # ── LLM-detected early roll (before DTE/profit trigger) ──
                if (
                    analysis and analysis.action == "roll_short_early"
                    and leg.short_leg_expiry
                    and not self._should_roll(leg)   # deterministic hasn't triggered yet
                ):
                    log.info("PMCCAgent: LLM recommends early roll for %s", symbol)
                    orders.extend(await self._propose_roll_short(
                        symbol, leg, broker, analysis=analysis,
                    ))
                    continue

                # ── Normal deterministic logic ──
                if leg.short_leg_expiry is None:
                    log.info("PMCCAgent: %s uncovered LEAP; proposing weekly sell", symbol)
                    orders.extend(await self._propose_sell_weekly(
                        symbol, broker, contracts, analysis=analysis, leg=leg,
                    ))
                elif self._should_roll(leg) and self._deterministic_roll_allowed(analysis):
                    log.info(
                        "PMCCAgent: %s roll triggered (DTE=%s, PnL=%.0f%%)",
                        symbol, leg.short_leg_dte, (leg.short_leg_pnl_pct or 0) * 100,
                    )
                    orders.extend(await self._propose_roll_short(
                        symbol, leg, broker, analysis=analysis,
                    ))
                else:
                    log.debug("PMCCAgent: %s PMCC healthy, no action needed", symbol)

            else:
                # No PMCC for this symbol yet
                if symbol not in stock_qty:
                    log.debug("PMCCAgent: %s in universe but no stock position; skip", symbol)
                    continue
                # Phase 1a-2: in research_on_demand mode the new-opens
                # path is owned by the research firm — skip the
                # universe-driven open branch here so we don't produce
                # duplicate or stale-source opens. The research-firm
                # call below appends its orders.
                if self.universe_source == "research_on_demand":
                    continue

                contracts = min(max_contracts, max(1, int(stock_qty[symbol] / 100)))
                log.info("PMCCAgent: %s — no PMCC yet; proposing new setup", symbol)
                new_orders, _skip_reason = await self._propose_open_pmcc(
                    symbol, broker, contracts, analysis=analysis,
                )
                orders.extend(new_orders)

        # Phase 1a-2 (design §8.A): research_on_demand new-opens path.
        # Existing-leg roll/close has already happened above; this adds
        # only NEW opens sourced from the research firm. Falls back to
        # no-new-opens when deps aren't wired (warning logged).
        if self.universe_source == "research_on_demand":
            currently_held = {s.upper() for s in legs_by_symbol.keys()}
            research_orders = await self._run_research_on_demand_new_opens(
                broker, currently_held_symbols=currently_held,
            )
            orders.extend(research_orders)

        log.info("PMCCAgent scan complete: %d order(s) proposed", len(orders))
        return orders

    # -- Roll condition ------------------------------------------------------

    def _should_roll(self, leg: PMCCPosition) -> bool:
        # Roll-DTE trigger from the global setting (defaults to 21;
        # strategies.yaml management.roll_dte_trigger overrides).
        roll_dte = self._roll_dte_for(leg)
        if leg.short_leg_dte is not None and leg.short_leg_dte <= roll_dte:
            return True
        if leg.short_leg_pnl_pct is not None and leg.short_leg_pnl_pct >= self._roll_profit_pct:
            return True
        return False

    def _deterministic_roll_allowed(self, analysis: "PMCCAnalysis | None") -> bool:
        """B1 (Phase 1): the deterministic DTE<=2 / >=50%-profit roll trigger
        yields to an explicit LLM HOLD/WATCH verdict. Returns True only when the
        LLM did NOT say hold/watch (or is unavailable). The 0-DTE terminal-DTE
        guard runs upstream and may already have rewritten HOLD->roll/close for
        0-DTE positions; this governs only the DTE 1-2 / profit fallback."""
        if analysis is None:
            return True
        if (analysis.action or "").lower() not in ("hold", "watch"):
            return True
        # B1/Phase-2: an explicit LLM `hold_override` authorizes the deterministic
        # roll despite the HOLD verdict (the escape hatch Phase-1 reserved).
        return self._override_kind(analysis) == "hold_override"

    def _override_kind(self, analysis: "PMCCAnalysis | None") -> str | None:
        """Phase-2 override contract: return the VALIDATED override kind, or None.
        A malformed value (not a dict / unknown kind / missing-or-blank reason) is
        treated as NO override — fail-safe so the gate applies. Every gate
        (B1 hold, B2 credit, B9 earnings) consults this."""
        if analysis is None:
            return None
        ov = getattr(analysis, "override", None)
        if not isinstance(ov, dict):
            return None
        kind = ov.get("kind")
        reason = ov.get("reason")
        if kind in _OVERRIDE_KINDS and isinstance(reason, str) and reason.strip():
            return kind
        return None

    # -- Terminal-DTE wall-clock time gate (Board direction 2026-05-01) ------
    #
    # For 0-DTE shorts, the Terminal-DTE Override (Rule 4 / Rule 7 in the
    # prompt corpus) yields to two wall-clock gates:
    #   - 3:00 PM ET: Override no longer applies. If the LLM said HOLD or
    #     WATCH, force action to roll_short to start the cycle while
    #     liquidity is still available.
    #   - 3:30 PM ET: hard close deadline breached. Order book thins past
    #     this point; combo rolls become unreliable. Escalate urgency to
    #     'urgent' and prefer single-leg close_short over a roll combo.
    #
    # The check is purely a function of (DTE, wall-clock, action) — no LLM
    # judgment involved. Lives in deterministic Python per CLAUDE.md §1.
    # See BACKLOG.md "0-DTE positions: Terminal-DTE Override must release
    # at 3:00 PM ET, hard close deadline 3:30 PM ET" for the full rule.

    def _terminal_dte_time_release(
        self,
        analysis: "PMCCAnalysis | None",
        leg: "PMCCPosition | None",
        *,
        now_et_dt: datetime | None = None,
        calendar: Any = None,
        spot: float | None = None,
    ) -> "PMCCAnalysis | None":
        """Override action when 0-DTE release conditions fire.

        Two release paths, both triggered only when leg.short_leg_dte == 0
        AND analysis.action is HOLD/WATCH:

          - **(P0) Time gate.** Driven by the actual session close
            from the NYSE market calendar, not hardcoded clock time.
            release_threshold = close - release_offset_min;
            hard_deadline   = close - hard_deadline_offset_min.
            On a regular 4pm-close day with default offsets these
            land at 15:00 ET / 15:30 ET. On a half-day 1pm close
            they correctly slide to 12:00 / 12:30 ET. On a
            Thursday-before-Friday-holiday they fire at the
            Thursday close.

          - **(P1) Cycle-continuity.** If the short leg's mark has
            already decayed at or below
            `cycle_continuity_extrinsic_threshold` $/share, force
            roll_short regardless of time. Mark <= threshold
            implies intrinsic == 0 by no-arbitrage. Trade-off:
            forfeit ~$threshold/contract residual decay for
            immediate next-cycle premium + no coverage gap.

        Offsets and threshold are config-driven via
        `config/strategies.yaml:robinhood_pmcc.zero_dte`. Defaults
        match the Board's 2026-05-01 direction (60/30 min, $0.15/sh).

        Returns a possibly-modified PMCCAnalysis (dataclasses.replace).
        Adds a warning explaining the override so the audit trail and
        Telegram approval message render the reason.

        `spot` (optional) enables an ADDITIVE pre-0-DTE branch — the
        deep-OTM near-worthless early release (see
        `_deep_otm_early_release`). When `spot` is None, or the branch is
        ineligible, this function's original 0-DTE behavior below is
        byte-unchanged.
        """
        import dataclasses
        from trading_corp.utils.time import ET, now_et as _now_et

        if analysis is None or leg is None:
            return analysis
        # Deep-OTM near-worthless EARLY release (2026-07-23) — additive,
        # strictly pre-0-DTE. Returns a modified analysis ONLY when it fires;
        # otherwise None, and the existing 0-DTE path below runs unchanged.
        _early = self._deep_otm_early_release(analysis, leg, spot=spot)
        if _early is not None:
            return _early
        if leg.short_leg_dte != 0:
            return analysis

        action = (analysis.action or "").lower()
        if action not in ("hold", "watch"):
            return analysis

        cfg_zd = (self._cfg.get("zero_dte") or {}) if hasattr(self, "_cfg") else {}
        release_offset_min = int(cfg_zd.get("release_offset_min", 60))
        hard_offset_min = int(cfg_zd.get("hard_deadline_offset_min", 30))
        cyc_threshold = float(
            cfg_zd.get("cycle_continuity_extrinsic_threshold", 0.15) or 0.0
        )

        # ── (P1) cycle-continuity: extrinsic-near-zero release ──────
        mark = leg.short_leg_mark
        if (
            cyc_threshold > 0
            and mark is not None
            and float(mark) <= cyc_threshold
        ):
            return dataclasses.replace(
                analysis,
                action="roll_short",
                warnings=list(analysis.warnings) + [
                    f"Cycle-continuity release: short_leg_mark "
                    f"${float(mark):.2f}/sh <= threshold "
                    f"${cyc_threshold:.2f}/sh AND short_leg_dte=0. "
                    f"Original action '{action}' overridden to "
                    f"'roll_short' to capture next-cycle premium and "
                    f"avoid post-expiry coverage gap."
                ],
            )

        # ── (P0) time gate: close-relative thresholds via NYSE calendar ──
        from trading_corp.utils.market_hours import default_calendar
        cal = calendar if calendar is not None else default_calendar()

        when = (now_et_dt or _now_et()).astimezone(ET)
        close_dt = cal.close_time_et(when)
        if close_dt is None:
            # Closed market today — no time-gate fires.
            return analysis

        from datetime import timedelta as _td
        release_threshold = close_dt - _td(minutes=release_offset_min)
        hard_deadline = close_dt - _td(minutes=hard_offset_min)

        if when < release_threshold:
            return analysis  # too early; let the LLM HOLD stand

        if when >= hard_deadline:
            return dataclasses.replace(
                analysis,
                action="close_short",
                urgency="urgent",
                warnings=list(analysis.warnings) + [
                    f"Terminal-DTE hard deadline breached "
                    f"({when.strftime('%H:%M ET')} >= "
                    f"{hard_deadline.strftime('%H:%M ET')} = close - "
                    f"{hard_offset_min}m). Override forced "
                    f"action='close_short' urgency='urgent' — order book "
                    f"is thin past this point; roll combo unlikely to "
                    f"fill, prefer single-leg buy-to-close to avoid "
                    f"expiration risk."
                ],
            )

        # release window: roll
        return dataclasses.replace(
            analysis,
            action="roll_short",
            warnings=list(analysis.warnings) + [
                f"Terminal-DTE Override released at "
                f"{when.strftime('%H:%M ET')} (>= "
                f"{release_threshold.strftime('%H:%M ET')} = close - "
                f"{release_offset_min}m). Original action '{action}' "
                f"overridden to 'roll_short' to start the cycle before "
                f"the {hard_deadline.strftime('%H:%M ET')} hard close "
                f"deadline."
            ],
        )

    # -- Deep-OTM near-worthless early release (2026-07-23) ------------------

    def _early_release_needs_spot(
        self,
        leg: "PMCCPosition | None",
        analysis: "PMCCAnalysis | None",
    ) -> bool:
        """True iff `leg`/`analysis` clear every deep-OTM near-worthless
        EARLY-release gate that does NOT require spot (config enabled, action
        HOLD/WATCH, penultimate-day window, name not excluded, near-worthless
        mark). Callers use this to decide whether to fetch a Robinhood spot for
        the moneyness gate — quoting only real candidates. `_deep_otm_early_release`
        calls the SAME predicate before applying the spot-dependent moneyness
        check, so the two cannot drift."""
        if analysis is None or leg is None:
            return False
        cfg = (self._cfg.get("near_worthless_early_roll") or {}) \
            if hasattr(self, "_cfg") else {}
        if not cfg.get("enabled", False):
            return False
        if (analysis.action or "").lower() not in ("hold", "watch"):
            return False
        # Penultimate-day window only. 0-DTE is owned by the time/cycle gates.
        dte = leg.short_leg_dte
        max_dte = int(cfg.get("max_dte", 1))
        if dte is None or dte < 1 or dte > max_dte:
            return False
        exclude = {str(s).upper() for s in (cfg.get("exclude") or [])}
        if (leg.symbol or "").upper() in exclude:
            return False
        # Near-worthless: reuse the 0-DTE cycle-continuity threshold so there is
        # ONE definition of near-worthless across the early and 0-DTE paths.
        zd = (self._cfg.get("zero_dte") or {}) if hasattr(self, "_cfg") else {}
        mark_thr = float(
            cfg.get("near_worthless_mark_threshold",
                    zd.get("cycle_continuity_extrinsic_threshold", 0.15)) or 0.0
        )
        mark = leg.short_leg_mark
        if mark_thr <= 0 or mark is None or float(mark) > mark_thr:
            return False
        return True

    def _deep_otm_early_release(
        self,
        analysis: "PMCCAnalysis | None",
        leg: "PMCCPosition | None",
        *,
        spot: float | None = None,
    ) -> "PMCCAnalysis | None":
        """Pre-0-DTE early theta-capture release.

        Additive companion to `_terminal_dte_time_release`'s 0-DTE gates:
        on the penultimate day(s), promote HOLD/WATCH → roll_short when the
        short is BOTH near-worthless AND clearly deep out-of-the-money
        relative to the underlying's typical overnight move, for eligible
        names. All numbers come from config
        (`robinhood_pmcc.near_worthless_early_roll`); the LLM rule text is
        qualitative.

        Returns a modified PMCCAnalysis, or None to leave the caller's
        existing logic untouched. FAIL-SAFE: returns None unless EVERY
        condition is satisfiable with the data in hand — config enabled,
        action HOLD/WATCH, 1 <= short_leg_dte <= max_dte, name not excluded,
        mark <= near-worthless threshold, spot present, and the spot-relative
        OTM distance >= the name's band. Any missing input ⇒ no fire.
        """
        import dataclasses
        # Pre-spot eligibility (config enabled, action HOLD/WATCH, penultimate-day
        # window, not excluded, near-worthless mark) is the SHARED predicate — the
        # SAME one the call sites use to decide whether to fetch a Robinhood spot,
        # so the gate and the callers cannot drift.
        if not self._early_release_needs_spot(leg, analysis):
            return None
        cfg = self._cfg.get("near_worthless_early_roll") or {}
        zd = self._cfg.get("zero_dte") or {}
        action = (analysis.action or "").lower()
        dte = leg.short_leg_dte
        max_dte = int(cfg.get("max_dte", 1))
        symbol = (leg.symbol or "").upper()
        mark_thr = float(
            cfg.get("near_worthless_mark_threshold",
                    zd.get("cycle_continuity_extrinsic_threshold", 0.15)) or 0.0
        )
        mark = leg.short_leg_mark

        # Deep-OTM: SPOT-relative distance (strike - spot)/spot, directly
        # comparable to the overnight up-gap the band was derived from — NOT
        # the display code's strike-normalized (spot - strike)/strike.
        strike = leg.short_leg_strike
        if spot is None or float(spot) <= 0 or strike is None:
            return None
        otm = (float(strike) - float(spot)) / float(spot)
        tame = {str(s).upper() for s in (cfg.get("tame_allowlist") or [])}
        band = (float(cfg.get("moneyness_band_tame", 0.05)) if symbol in tame
                else float(cfg.get("moneyness_band_default", 0.08)))
        if otm < band:
            return None

        return dataclasses.replace(
            analysis,
            action="roll_short",
            warnings=list(analysis.warnings) + [
                f"Deep-OTM near-worthless early release: short_leg_mark "
                f"${float(mark):.2f}/sh <= ${mark_thr:.2f}/sh AND spot "
                f"${float(spot):.2f} is {otm*100:.1f}% below strike "
                f"${float(strike):.2f} (>= {band*100:.1f}% band) AND "
                f"short_leg_dte={dte} (<= {max_dte}). Original action "
                f"'{action}' overridden to 'roll_short' to capture next-cycle "
                f"premium early; the band exceeds this name's typical overnight "
                f"up-gap so the short stays OTM overnight."
            ],
        )

    # -- LEAP Hard Rule promotion (Item 2 — 2026-05-02) ----------------------

    def _promote_to_roll_leap_if_hard_rule(
        self,
        analysis: "PMCCAnalysis | None",
        leg: "PMCCPosition | None",
    ) -> "PMCCAnalysis | None":
        """Promote roll_short / roll_short_early → roll_leap when the
        LEAP Hard Rule fires.

        Conditions (either is sufficient):
          - leg.long_leg_delta >= 0.95 (Standard Rule 5: deep ITM equity)
          - leg.long_leg_dte < 120 (LEAP Management Rule: roll out at 120 DTE)

        Why: today the LLM analyzer correctly cites these rules in
        warnings but still emits action='roll_short'. Approving that
        leaves the user with a fresh weekly short on a dying LEAP —
        exactly the naked-short exposure the warning text said to
        avoid. Promoting to roll_leap routes through the 4-leg
        compound recommendation (close short + close LEAP + open new
        LEAP + open new short on the new LEAP).

        Returns possibly-modified analysis (dataclasses.replace) with
        an explanatory warning appended.
        """
        import dataclasses
        if analysis is None or leg is None:
            return analysis
        action = (analysis.action or "").lower()
        if action not in ("roll_short", "roll_short_early"):
            return analysis

        delta = leg.long_leg_delta
        dte = leg.long_leg_dte
        deep_itm = delta is not None and delta >= 0.95
        near_expiry = dte is not None and dte < 120
        if not (deep_itm or near_expiry):
            return analysis

        reasons = []
        if deep_itm:
            reasons.append(f"LEAP delta {delta:.2f} >= 0.95 (deep ITM equity)")
        if near_expiry:
            reasons.append(f"LEAP DTE {dte} < 120 (roll-out threshold)")
        reason_str = " AND ".join(reasons)

        return dataclasses.replace(
            analysis,
            action="roll_leap",
            warnings=list(analysis.warnings) + [
                f"LEAP Hard Rule promotion: {reason_str}. Original "
                f"action '{action}' overridden to 'roll_leap' so the "
                f"recommendation includes the LEAP roll legs (close "
                f"short + close LEAP + open new LEAP + open new short), "
                f"not just the short roll."
            ],
        )

    # -- Halfway-roll cooldown (Item 1 — 2026-05-02) -------------------------

    def _recent_halfway_roll_cooldown(
        self,
        analysis: "PMCCAnalysis | None",
        leg: "PMCCPosition | None",
    ) -> "PMCCAnalysis | None":
        """Downgrade roll_short → hold when a recent roll-up was
        executed and the cooldown conditions hold.

        Backstop for the LLM rule clause (BREACH HANDLING COOLDOWN).
        The LLM has the ROLL HISTORY block in its prompt and should
        already prefer HOLD; this guard catches the case where it
        doesn't.

        Conditions (all must hold for cooldown to fire):
          - analysis.action in ("roll_short", "roll_short_early")
          - leg.short_leg_dte > terminal_dte_floor (default 2) —
            never block deadline-driven rolls; the terminal-DTE
            override owns those
          - leg.short_leg_mark > extrinsic_floor (default $0.50/sh) —
            if extrinsic is already near zero, cycle-continuity wants
            to roll
          - days_since_last_roll <= cooldown_days (default 7)
          - last roll was a roll-up: strike_change >= min_strike_change
            (default $1.00) — excludes near-zero strike adjustments
            that are normal cycle drift, captures halfway-style
            up-rolls into a breach

        No spot-acceleration check in the deterministic guard — that
        belongs in the LLM rule clause where regime/IV context is
        available. False-positive cooldown (block a legit re-roll)
        costs "user overrides via Telegram"; false-negative costs
        "back-to-back halfway-roll waste." Bias toward HOLD.

        Returns possibly-modified analysis (dataclasses.replace) with
        a warning explaining the cooldown.
        """
        import dataclasses
        if analysis is None or leg is None:
            return analysis
        action = (analysis.action or "").lower()
        if action not in ("roll_short", "roll_short_early"):
            return analysis

        cfg = (self._cfg.get("roll_cooldown") or {}) if hasattr(self, "_cfg") else {}
        cooldown_days = int(cfg.get("cooldown_days", 7))
        extrinsic_floor = float(cfg.get("extrinsic_floor", 0.50))
        min_strike_change = float(cfg.get("min_strike_change", 1.0))
        terminal_dte_floor = int(cfg.get("terminal_dte_floor", 2))

        # Gate: never block a deadline-driven roll
        if leg.short_leg_dte is None or leg.short_leg_dte <= terminal_dte_floor:
            return analysis
        # Gate: never block a near-zero-extrinsic roll (cycle continuity wants it)
        if leg.short_leg_mark is None or float(leg.short_leg_mark) <= extrinsic_floor:
            return analysis

        # Pull roll history for THIS LEAP (scoped by leap_lifetime_key)
        leap_key = self._compute_leap_lifetime_key(leg)
        try:
            hist = self._query_prior_rolls_detailed(
                leg.symbol, leap_lifetime_key=leap_key,
            )
        except Exception as e:
            log.debug(
                "PMCCAgent: cooldown query failed for %s: %s — guard inactive",
                leg.symbol, e,
            )
            return analysis

        days_since = hist.get("days_since_last_roll")
        strike_change = hist.get("last_roll_strike_change")
        if days_since is None or strike_change is None:
            return analysis
        if days_since > cooldown_days:
            return analysis
        if strike_change < min_strike_change:
            return analysis

        return dataclasses.replace(
            analysis,
            action="hold",
            warnings=list(analysis.warnings) + [
                f"Halfway-roll cooldown: prior roll-up "
                f"${hist.get('last_roll_short_strike_before'):.2f} → "
                f"${hist.get('last_roll_short_strike_after'):.2f} "
                f"(+${strike_change:.2f}) was {days_since}d ago, "
                f"within cooldown_days={cooldown_days}. Short DTE "
                f"{leg.short_leg_dte}d > {terminal_dte_floor}d AND "
                f"extrinsic ${float(leg.short_leg_mark):.2f}/sh > "
                f"${extrinsic_floor:.2f}/sh. Original action "
                f"'{action}' overridden to 'hold' to let the new "
                f"strike collect theta and avoid back-to-back "
                f"slippage. Override via Telegram if breach has "
                f"ACCELERATED past the prior roll's range."
            ],
        )

    # -- ROLL HISTORY prompt block (Item 1 — 2026-05-02) ---------------------

    def _format_roll_history_block(self, leg: "PMCCPosition") -> str:
        """Format the ROLL HISTORY section injected into the LLM prompt.

        Pulls from `_query_prior_rolls_detailed` scoped to this LEAP's
        lifetime key. Returns an empty string if no DB or no history,
        so the prompt stays clean for fresh positions.
        """
        if not self._db_url:
            return ""
        try:
            leap_key = self._compute_leap_lifetime_key(leg)
            hist = self._query_prior_rolls_detailed(
                leg.symbol, leap_lifetime_key=leap_key,
            )
        except Exception as e:
            log.debug(
                "PMCCAgent: roll-history query failed for %s: %s",
                leg.symbol, e,
            )
            return ""
        if hist["roll_count"] == 0:
            return (
                "## ROLL HISTORY (this LEAP)\n"
                "- No prior rolls recorded for this LEAP.\n"
            )
        lines = [
            "## ROLL HISTORY (this LEAP)",
            f"- Total prior rolls: {hist['roll_count']}",
            f"- Net credit collected from rolls: ${hist['net_dollars']:+,.0f}",
        ]
        if hist["last_roll_ts"]:
            days = hist["days_since_last_roll"]
            day_str = "today" if days == 0 else f"{days}d ago"
            before = hist["last_roll_short_strike_before"]
            after = hist["last_roll_short_strike_after"]
            change = hist["last_roll_strike_change"]
            if before is not None and after is not None and change is not None:
                if change > 0:
                    label = "roll-up"
                elif change < 0:
                    label = "roll-down"
                else:
                    label = "same-strike"
                lines.append(
                    f"- Most recent roll: {day_str} — "
                    f"${before:.2f} → ${after:.2f} "
                    f"(${change:+.2f} = {label})"
                )
            else:
                lines.append(f"- Most recent roll: {day_str}")
        return "\n".join(lines) + "\n"

    # -- Rationale builder ---------------------------------------------------

    @staticmethod
    def _build_rationale(base: str, analysis: PMCCAnalysis | None) -> str:
        """Attach LLM expert commentary to a base rationale string."""
        if not analysis:
            return base
        return (
            f"{base}\n\n"
            + analysis.format_rich()
        )

    # -- Order builders (accept optional LLM analysis for rationale) --------

    # ------------------------------------------------------------------
    # Position context for Telegram approval messages (Phase 2 — 2026-04-30)
    # ------------------------------------------------------------------
    # The Telegram approval formatter (comms/approval_format.py) renders
    # a "📊 Position context" block whenever the order's
    # `extra["position_context"]` dict is populated. This helper builds
    # that dict for orders that act on an EXISTING PMCC pair (rolls and
    # selling-against-existing-LEAP). For pure opens, no prior context
    # exists yet, so we skip.
    #
    # The formatter accepts any subset of fields and gracefully omits
    # missing ones. We populate what's cheaply available:
    #   - LEAP basics from PMCCPosition (free)
    #   - Mark from PMCCPosition.long_leg_mark (free if the chain query
    #     captured it; None otherwise)
    #   - Unrealized P&L (computed if mark + cost present)
    #   - roll_count + prior_credit_total (queried from proposed_order
    #     table when db_url is configured)
    #
    # Days-held is intentionally skipped for v1 — Robinhood's option
    # snapshot doesn't expose opened_ts cleanly, and trying to derive it
    # adds plumbing without much UX win. Backlog item if it bites.

    def _build_position_context(self, leg: PMCCPosition) -> dict:
        """Build the position_context dict for a roll/sell-weekly order.

        Synchronous + cheap — no broker calls, no async. Just composes
        what's already on `leg` plus an optional DB query for prior rolls.
        Safe to call inline during order construction.
        """
        ctx: dict = {}

        # ── LEAP basics ──
        leap_dict: dict = {
            "underlying": leg.symbol,
            "strike": leg.long_leg_strike,
            "expiration": leg.long_leg_expiry,
            "dte": leg.long_leg_dte,
        }
        # Cost basis: PMCCPosition stores avg_price as per-CONTRACT (Robinhood
        # convention); the formatter expects per-share. Divide by 100.
        if leg.long_leg_avg_price:
            leap_dict["cost_basis"] = float(leg.long_leg_avg_price) / 100.0
        # Mark per-share — already per-share when populated from broker
        # chain query (see PMCCPosition construction sites).
        if leg.long_leg_mark is not None:
            leap_dict["mark"] = float(leg.long_leg_mark)
        ctx["leap"] = leap_dict

        # ── Unrealized P&L (computed) ──
        if (
            leg.long_leg_mark is not None
            and leg.long_leg_avg_price
            and leg.long_leg_qty
        ):
            cost_per_share = float(leg.long_leg_avg_price) / 100.0
            mark_per_share = float(leg.long_leg_mark)
            qty = abs(float(leg.long_leg_qty))
            upl_dollars = (mark_per_share - cost_per_share) * 100 * qty
            ctx["unrealized_pnl_dollars"] = upl_dollars
            if cost_per_share > 0:
                ctx["unrealized_pnl_pct"] = (mark_per_share / cost_per_share) - 1

        # ── Prior-roll history (from proposed_order table) ──
        if self._db_url:
            try:
                leap_key = self._compute_leap_lifetime_key(leg)
                roll_count, prior_credit = self._query_prior_rolls(
                    leg.symbol, leap_lifetime_key=leap_key,
                )
                if roll_count > 0:
                    ctx["roll_count"] = roll_count
                    ctx["prior_credit_total"] = prior_credit
            except Exception as e:
                # Audit query failures must not block order proposal.
                log.debug(
                    "PMCCAgent: prior-roll query failed for %s: %s",
                    leg.symbol, e,
                )

        return ctx

    def _query_prior_rolls(
        self,
        symbol: str,
        leap_lifetime_key: str | None = None,
    ) -> tuple[int, float]:
        """Return (count, net_dollars) of past filled rolls on `symbol`.

        A "roll" is a paired close+open sharing a `pmcc_pair_id` in the
        order's extra_json. Net dollars = sum over each pair's legs:
            +qty * fill_price * 100  for sell legs (credit)
            -qty * fill_price * 100  for buy legs (debit)

        `leap_lifetime_key`: when provided, scope to rolls on the SAME
        underlying LEAP (identified by `{symbol}:{strike}:{expiry}`).
        This avoids cross-counting when the user holds multiple LEAPs
        on one underlying (e.g. two RKLB LEAPs at different strikes).
        Pre-fix DB rows lack this key — pairs whose fills have neither
        a key set fall through to the legacy symbol-only aggregation,
        so old history isn't lost; only key-tagged pairs from a
        DIFFERENT LEAP are filtered out.

        Returns (0, 0.0) if db_url is unset or the query fails.
        """
        from trading_corp.persistence import db
        sql = """
            SELECT side, qty, fill_price, extra_json
            FROM proposed_order
            WHERE strategy='robinhood_pmcc'
              AND symbol=?
              AND status='filled'
              AND fill_price IS NOT NULL
              AND extra_json LIKE '%roll_short_call%'
            ORDER BY fill_ts ASC
        """
        with db.connect(self._db_url) as conn:
            rows = conn.execute(sql, (symbol,)).fetchall()

        # Group fills by pair_id, also tracking the lifetime_key seen on
        # each pair (any leg's key applies to the whole pair).
        pair_nets: dict[str, float] = {}
        pair_keys: dict[str, str] = {}
        for r in rows:
            try:
                extra = json.loads(r["extra_json"] or "{}")
                pair_id = extra.get("pmcc_pair_id")
                if not pair_id:
                    continue
                qty = float(r["qty"] or 0)
                price = float(r["fill_price"] or 0)
                # sell-to-open = credit (cash IN); buy-to-close = debit (cash OUT)
                sign = 1 if r["side"] == "sell" else -1
                pair_nets[pair_id] = (
                    pair_nets.get(pair_id, 0.0) + sign * qty * price * 100
                )
                row_key = extra.get("leap_lifetime_key")
                if row_key and pair_id not in pair_keys:
                    pair_keys[pair_id] = row_key
            except Exception:
                continue

        if leap_lifetime_key:
            # Keep pairs whose recorded key matches, OR pairs with no key
            # at all (pre-fix history — preserve rather than silently drop).
            filtered = {
                pid: net
                for pid, net in pair_nets.items()
                if pair_keys.get(pid) in (None, leap_lifetime_key)
            }
            return len(filtered), sum(filtered.values())

        return len(pair_nets), sum(pair_nets.values())

    def _query_prior_rolls_detailed(
        self,
        symbol: str,
        leap_lifetime_key: str | None = None,
    ) -> dict:
        """Detailed view of prior rolls for prompt-injection + cooldown gate.

        Same SQL/grouping as `_query_prior_rolls` but returns per-pair
        metadata for the most recent roll:

          - roll_count: int (same as _query_prior_rolls' first tuple slot)
          - net_dollars: float (same as second slot)
          - last_roll_ts: ISO ts of the max fill_ts across all pairs (None
            if no rolls)
          - last_roll_short_strike_before: short strike that was CLOSED
            in the most recent roll (None if not recoverable)
          - last_roll_short_strike_after: short strike that was OPENED
            (None if the roll's open leg didn't fill or wasn't recorded)
          - last_roll_strike_change: after - before (positive = roll-up,
            negative = roll-down). None if either side is missing.
          - days_since_last_roll: int days from last_roll_ts to now_utc
            (None if no rolls)

        `leap_lifetime_key` scoping mirrors `_query_prior_rolls` —
        pairs with NULL keys (pre-fix history) are kept; pairs tagged
        with a different key are filtered out.

        Used by `_format_roll_history_block` (LLM prompt) and
        `_recent_halfway_roll_cooldown` (deterministic guard). Returns
        all-zero/None defaults if db_url unset or query fails.
        """
        from datetime import datetime, timezone
        from trading_corp.persistence import db

        out = {
            "roll_count": 0,
            "net_dollars": 0.0,
            "last_roll_ts": None,
            "last_roll_short_strike_before": None,
            "last_roll_short_strike_after": None,
            "last_roll_strike_change": None,
            "days_since_last_roll": None,
        }
        if not self._db_url:
            return out

        sql = """
            SELECT side, qty, fill_price, fill_ts, extra_json
            FROM proposed_order
            WHERE strategy='robinhood_pmcc'
              AND symbol=?
              AND status='filled'
              AND fill_price IS NOT NULL
              AND extra_json LIKE '%roll_short_call%'
            ORDER BY fill_ts ASC
        """
        try:
            with db.connect(self._db_url) as conn:
                rows = conn.execute(sql, (symbol,)).fetchall()
        except Exception as e:
            log.debug(
                "PMCCAgent: detailed prior-roll query failed for %s: %s",
                symbol, e,
            )
            return out

        pair_nets: dict[str, float] = {}
        pair_keys: dict[str, str] = {}
        pair_max_ts: dict[str, str] = {}
        pair_close_strike: dict[str, float] = {}
        pair_open_strike: dict[str, float] = {}

        for r in rows:
            try:
                extra = json.loads(r["extra_json"] or "{}")
                pair_id = extra.get("pmcc_pair_id")
                if not pair_id:
                    continue
                qty = float(r["qty"] or 0)
                price = float(r["fill_price"] or 0)
                sign = 1 if r["side"] == "sell" else -1
                pair_nets[pair_id] = (
                    pair_nets.get(pair_id, 0.0) + sign * qty * price * 100
                )

                row_key = extra.get("leap_lifetime_key")
                if row_key and pair_id not in pair_keys:
                    pair_keys[pair_id] = row_key

                ts = r["fill_ts"]
                if ts and (pair_id not in pair_max_ts or ts > pair_max_ts[pair_id]):
                    pair_max_ts[pair_id] = ts

                strike_raw = extra.get("strike")
                action = (extra.get("action") or "").lower()
                if strike_raw is not None:
                    try:
                        strike = float(strike_raw)
                    except (TypeError, ValueError):
                        strike = None
                    if strike is not None:
                        # Buy-to-close on a roll = the OLD short being closed
                        if r["side"] == "buy" and "close" in action:
                            pair_close_strike[pair_id] = strike
                        # Sell-to-open on a roll = the NEW short being opened
                        elif r["side"] == "sell" and "open" in action:
                            pair_open_strike[pair_id] = strike
            except Exception:
                continue

        # Apply leap_lifetime_key scoping (same rule as _query_prior_rolls)
        if leap_lifetime_key:
            keep = {
                pid for pid in pair_nets
                if pair_keys.get(pid) in (None, leap_lifetime_key)
            }
            pair_nets = {pid: v for pid, v in pair_nets.items() if pid in keep}
            pair_max_ts = {pid: v for pid, v in pair_max_ts.items() if pid in keep}
            pair_close_strike = {
                pid: v for pid, v in pair_close_strike.items() if pid in keep
            }
            pair_open_strike = {
                pid: v for pid, v in pair_open_strike.items() if pid in keep
            }

        out["roll_count"] = len(pair_nets)
        out["net_dollars"] = sum(pair_nets.values())

        if pair_max_ts:
            last_pair_id = max(pair_max_ts, key=pair_max_ts.get)
            last_ts = pair_max_ts[last_pair_id]
            out["last_roll_ts"] = last_ts
            before = pair_close_strike.get(last_pair_id)
            after = pair_open_strike.get(last_pair_id)
            out["last_roll_short_strike_before"] = before
            out["last_roll_short_strike_after"] = after
            if before is not None and after is not None:
                out["last_roll_strike_change"] = after - before
            try:
                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                delta_sec = (datetime.now(timezone.utc) - last_dt).total_seconds()
                out["days_since_last_roll"] = max(0, int(delta_sec // 86400))
            except Exception:
                pass

        return out

    async def _propose_open_pmcc(
        self,
        symbol: str,
        broker: Broker,
        contracts: int,
        analysis: PMCCAnalysis | None = None,
    ) -> tuple[list[ProposedOrder], str | None]:
        """Open new PMCC: buy LEAP + sell weekly call.

        Returns (orders, skip_reason). `orders` is 0–2 ProposedOrders.
        `skip_reason` is None on success; on full skip it's a stable enum
        string (`earnings_within_buffer` / `leap_unavailable` /
        `weekly_unavailable`) consumed by the research-on-demand integration
        (Phase 1a-2) to populate `research_candidate_skipped` audit rows.
        Public `propose_opening_orders` wrapper drops the reason for
        dashboard-side callers that don't need diagnostics.

        Honors the `earnings_buffer_days` gate from strategies.yaml — skips
        opening a new PMCC if earnings fall inside the buffer window.
        """
        # Earnings buffer gate (skip new entries near earnings)
        blocked, why = self._blocked_by_earnings(symbol)
        if blocked:
            log.warning(
                "PMCCAgent: skipping new PMCC open on %s — %s", symbol, why,
            )
            return [], "earnings_within_buffer"

        # B4 (atomic open): both legs must resolve or we open NOTHING — never a
        # LEAP without its covering short (nor a short without its LEAP = a naked
        # short). Abort + audit on either miss.
        #
        # SEMANTIC CHANGE (Phase 1, 2026-07-21): a PARTIAL open (exactly one leg
        # found) previously returned that 1-leg order with skip_reason=None and
        # was classified `research_candidate_acted_on`; it now returns ([], reason)
        # and classifies `research_candidate_skipped`. Consumers: scan new-opens
        # (reason discarded — no partial order emitted), propose_opening_orders /
        # scout-execute route (returns [] not a partial), and
        # _run_research_on_demand_new_opens (acted_on -> skipped) + the research
        # dashboard (web/routes.py). The last two are DORMANT in prod
        # (universe_source: positions). Recording a partial as acted_on / routing
        # it to the Board was the pre-B4 bug.
        leap_call = await self._find_best_leap(symbol, broker)
        if not leap_call:
            self._audit_roll_abort(
                reason="sparse_chain_no_leap_for_open", symbol=symbol,
                missing_leg="leap", diag=self._last_leap_diag,
            )
            log.warning("PMCCAgent: no qualifying LEAP found for %s", symbol)
            return [], "leap_unavailable"
        weekly_call = await self._find_best_weekly(
            symbol, broker,
            target_delta=analysis.target_delta if analysis else None,
            target_dte=analysis.target_dte if analysis else None,
            target_strike=analysis.target_strike if analysis else None,
        )
        if not weekly_call:
            self._audit_roll_abort(
                reason="sparse_chain_no_weekly_for_open", symbol=symbol,
                missing_leg="short", diag=self._last_weekly_diag,
            )
            log.warning("PMCCAgent: no qualifying weekly call found for %s", symbol)
            return [], "weekly_unavailable"

        orders: list[ProposedOrder] = []
        pair_id = str(uuid.uuid4())[:8]
        orders.append(self._make_option_order(
            underlying=symbol, side="buy", contracts=contracts,
            expiry=leap_call["expiration_date"],
            strike=leap_call["strike_price"],
            mark_price=leap_call.get("mark_price") or leap_call.get("ask") or 0,
            bid=leap_call.get("bid"), ask=leap_call.get("ask"),
            position_effect="open", action="open_leap",
            delta=leap_call.get("delta"), dte=leap_call.get("dte"),
            rationale=self._build_rationale(
                f"Open PMCC LEAP: {symbol} {leap_call['expiration_date']} "
                f"C{leap_call['strike_price']:.2f} "
                f"delta={leap_call.get('delta', '?'):.2f}",
                analysis,
            ),
            pair_id=pair_id,
        ))
        orders.append(self._make_option_order(
            underlying=symbol, side="sell", contracts=contracts,
            expiry=weekly_call["expiration_date"],
            strike=weekly_call["strike_price"],
            mark_price=weekly_call.get("mark_price") or weekly_call.get("bid") or 0,
            bid=weekly_call.get("bid"), ask=weekly_call.get("ask"),
            position_effect="open", action="open_short_call",
            delta=weekly_call.get("delta"), dte=weekly_call.get("dte"),
            rationale=self._build_rationale(
                f"Open PMCC short call: {symbol} {weekly_call['expiration_date']} "
                f"C{weekly_call['strike_price']:.2f} "
                f"delta={weekly_call.get('delta', '?'):.2f}",
                analysis,
            ),
            pair_id=pair_id,
        ))
        # Route the OPEN as ONE atomic diagonal spread (buy LEAP + sell short) —
        # net DEBIT (the LEAP costs more than the short's credit). Placeholder net
        # from marks; the dispatch path re-prices from live quotes. B4 guarantees
        # both legs here, but guard on len for safety.
        if len(orders) == 2:
            _leap_mark = float(leap_call.get("mark_price") or leap_call.get("ask") or 0)
            _short_mark = float(weekly_call.get("mark_price") or weekly_call.get("bid") or 0)
            _open_net = _short_mark - _leap_mark      # sell short (+), buy LEAP (−)
            _open_dir = "credit" if _open_net >= 0 else "debit"
            for _leg in orders:
                _leg.extra.update({
                    "is_multi_leg": True,
                    "combo_id": pair_id,
                    "combo_direction": _open_dir,
                    "net_limit_price": round(abs(_open_net), 2) or 0.01,
                    "ratio_quantity": 1,
                })
        return orders, None

    @staticmethod
    def _compute_leap_lifetime_key(leg: PMCCPosition | None) -> str | None:
        """Stable identifier for one LEAP's lifetime: '{symbol}:{strike:.2f}:{expiry}'.

        Returns None if any field is missing — the caller treats None as
        "don't scope, aggregate by symbol only" (preserves pre-fix
        history for rows without this key).
        """
        if not leg or not leg.long_leg_expiry or leg.long_leg_strike is None:
            return None
        return f"{leg.symbol}:{leg.long_leg_strike:.2f}:{leg.long_leg_expiry}"

    async def _propose_sell_weekly(
        self,
        symbol: str,
        broker: Broker,
        contracts: int,
        analysis: PMCCAnalysis | None = None,
        leg: PMCCPosition | None = None,
    ) -> list[ProposedOrder]:
        """Sell a new weekly call against an uncovered LEAP.

        `leg`: when supplied (Phase 2 — 2026-04-30), the existing LEAP's
        details + prior-roll history are stashed on the order's
        `extra["position_context"]` so the Telegram approval message
        renders rich context. When None, the order ships without the
        context block (graceful fallback for callers that don't have
        the leg data on hand).
        """
        weekly_call = await self._find_best_weekly(
            symbol, broker,
            target_delta=analysis.target_delta if analysis else None,
            target_dte=analysis.target_dte if analysis else None,
            target_strike=analysis.target_strike if analysis else None,
        )
        if not weekly_call:
            log.warning("PMCCAgent: no weekly call found for uncovered LEAP on %s", symbol)
            return []
        position_context = self._build_position_context(leg) if leg else None
        leap_key = self._compute_leap_lifetime_key(leg)
        return [self._make_option_order(
            underlying=symbol, side="sell", contracts=contracts,
            expiry=weekly_call["expiration_date"],
            strike=weekly_call["strike_price"],
            mark_price=weekly_call.get("mark_price") or weekly_call.get("bid") or 0,
            bid=weekly_call.get("bid"), ask=weekly_call.get("ask"),
            position_effect="open", action="open_short_call",
            delta=weekly_call.get("delta"), dte=weekly_call.get("dte"),
            rationale=self._build_rationale(
                f"Cover LEAP: sell {symbol} {weekly_call['expiration_date']} "
                f"C{weekly_call['strike_price']:.2f}",
                analysis,
            ),
            position_context=position_context,
            leap_lifetime_key=leap_key,
        )]

    async def _propose_roll_short(
        self,
        symbol: str,
        leg: PMCCPosition,
        broker: Broker,
        analysis: PMCCAnalysis | None = None,
        *,
        preview: bool = False,
    ) -> list[ProposedOrder]:
        """Roll short call: buy-to-close existing + sell-to-open new weekly.

        Phase-2 gate order (pmcc_phase2_plan §5): B9 earnings → B7 selection (in
        `_find_best_weekly`) → B2 credit. Abort on the FIRST failing gate; the
        `pmcc_roll_aborted` payload carries a `gates` map of every gate evaluated
        up to the abort. B9/B2 are overridable via the LLM override contract
        (earnings_override / net_debit_justified); B7 is hard-enforced.

        `preview=True` (2026-07-30): render/estimate build, not a dispatch attempt.
        Selection still runs (so the card shows the real target strike + prices),
        but every abort audit/alert and the earnings-unverified alert are withheld
        — exec-alerts fire only on a genuine dispatch. The proposed legs are
        identical to the non-preview build.
        """
        if not leg.short_leg_expiry or leg.short_leg_strike is None:
            return []

        contracts = max(1, int(abs(leg.short_leg_qty or 1)))
        close_mark = leg.short_leg_mark or 0.0
        pnl_pct = (leg.short_leg_pnl_pct or 0) * 100
        override_kind = self._override_kind(analysis)
        gates: dict = {}

        roll_reason = (
            f"DTE={leg.short_leg_dte}" if (leg.short_leg_dte or 0) <= self._roll_dte
            else f"profit={pnl_pct:.0f}%"
        )

        # ── B9 (earnings gate) ── skill HARD RULE (L257): "No new short premium
        # within 7 DTE of earnings" — a roll OPENS a new short. Overridable via
        # earnings_override (e.g. a deliberate roll that must not let a
        # breached short run into earnings). FAIL-OPEN on missing data, but the
        # state (blocked/clear/data_unavailable) is recorded so a roll that shipped
        # only because the data source was DOWN is distinguishable from one that
        # shipped because earnings were genuinely clear.
        earnings_state, earnings_reason = self._earnings_gate_state(symbol)
        gates["earnings"] = earnings_state
        if earnings_state == "blocked" and override_kind != "earnings_override":
            self._audit_roll_abort(
                reason="earnings_window", symbol=symbol,
                extra={"gates": dict(gates), "earnings_reason": earnings_reason},
                preview=preview,
            )
            return []

        # 2026-07-28: a roll that PROCEEDS with no confident earnings date (neither
        # broker nor feed) must not do so SILENTLY — it may sell new short premium
        # into an unseen print. Emit a deduped "earnings unverified" alert + audit
        # so the operator sees it. Fires ONLY for source="none" (the RIOT-class
        # fail-open, == the data_unavailable state); a resolved broker/feed date is
        # already handled by the block/clear decision above. Never blocks/raises.
        _eres = getattr(self, "_last_earnings_resolution", None)
        if _eres is not None and getattr(_eres, "source", None) == "none" and not preview:
            # preview: a render must not emit the unverified-earnings audit/alert —
            # it fires only when a real roll is being dispatched (invariant 2026-07-30).
            self._audit_division("pmcc_earnings_unverified", {
                "symbol": symbol,
                "reason": "no broker/feed earnings date; roll allowed (fail-open)",
                "gate_state": earnings_state,
            })
            try:
                from trading_corp.comms.exec_alert import ExecOutcome, emit_exec_alert
                emit_exec_alert(ExecOutcome(
                    tier="EARN_UNVERIF", symbol=symbol, strategy="robinhood_pmcc",
                    reason=("earnings unverified (no broker/feed date) — roll allowed; "
                            "check for an upcoming print"),
                ))
            except Exception:
                log.debug("earnings-unverified alert failed for %s (isolated)", symbol)

        # B4 (atomic roll) + B7 (roll-out): resolve the re-open leg BEFORE
        # proposing the close. No qualifying roll-out weekly → abort the WHOLE roll
        # (propose nothing) + audit — never ship a close-only "roll" that leaves the
        # LEAP uncovered. A deliberate bare close is the LLM's explicit close_short.
        new_weekly = await self._find_best_weekly(
            symbol, broker,
            target_delta=analysis.target_delta if analysis else None,
            target_dte=analysis.target_dte if analysis else None,
            target_strike=analysis.target_strike if analysis else None,
            target_delta_low=analysis.target_delta_low if analysis else None,
            target_delta_high=analysis.target_delta_high if analysis else None,
            after_dte=leg.short_leg_dte,  # B7: new short must roll OUT
        )
        if not new_weekly:
            gates["selection"] = "blocked"
            self._audit_roll_abort(
                reason="sparse_chain_no_weekly", symbol=symbol,
                missing_leg="new_short", diag=self._last_weekly_diag,
                extra={"gates": dict(gates)},
                preview=preview,
            )
            return []
        gates["selection"] = "ok"

        # ── B2 (credit gate) ── skill HARD RULE: rolls are for credit (BS L102
        # "Always for credit"; STANDARD Major breach L199 "MUST credit"; FORBIDDEN
        # L170 "Roll for debit to chase OTM"). STANDARD permits a small debit
        # (≤8% LEAP, L255) — that latitude flows through the net_debit_justified
        # override. CONSERVATIVE basis (amendment 2): sell the new weekly at BID,
        # buy the old short back at MARK (the existing short exposes mark only, no
        # ask). PRE-FEE (amendment 3): no fee data exists at proposal time (RH
        # broker: none; FillEvent.fee is post-fill only) — the conservative net
        # captures SPREAD, not fees. Both the conservative net and the card's mark
        # net go in the audit so a blocked roll is understandable without re-derive.
        conservative_net, mark_net, open_bid = _short_roll_credit(new_weekly, close_mark)
        if conservative_net < 0 and override_kind != "net_debit_justified":
            gates["credit"] = "blocked"
            self._audit_roll_abort(
                reason="net_debit_roll", symbol=symbol,
                extra={
                    "gates": dict(gates),
                    "conservative_net": round(conservative_net, 4),
                    "mark_net": round(mark_net, 4),
                    "close_mark": round(close_mark, 4),
                    "open_bid": open_bid,
                    "fees_included": False,
                    "fee_gap": ("pre-fee: RH per-contract regulatory/exchange "
                                "fees excluded; net captures spread only"),
                },
                preview=preview,
            )
            return []
        gates["credit"] = "clear"

        orders: list[ProposedOrder] = []
        pair_id = str(uuid.uuid4())[:8]
        # Phase 2 Telegram approval enrichment: build position context once
        # and attach to BOTH legs of the roll. The formatter renders the
        # same block on each, so approving close OR open shows the LEAP
        # details, unrealized P&L, and prior-roll history identically.
        position_context = self._build_position_context(leg)
        leap_key = self._compute_leap_lifetime_key(leg)

        orders.append(self._make_option_order(
            underlying=symbol, side="buy", contracts=contracts,
            expiry=leg.short_leg_expiry, strike=leg.short_leg_strike,
            mark_price=close_mark, position_effect="close",
            action="roll_short_call_close", dte=leg.short_leg_dte,
            rationale=self._build_rationale(
                f"Roll close ({roll_reason}): buy {symbol} {leg.short_leg_expiry} "
                f"C{leg.short_leg_strike:.2f} @ ~${close_mark:.2f}",
                analysis,
            ),
            pair_id=pair_id,
            position_context=position_context,
            leap_lifetime_key=leap_key,
        ))
        orders.append(self._make_option_order(
            underlying=symbol, side="sell", contracts=contracts,
            expiry=new_weekly["expiration_date"],
            strike=new_weekly["strike_price"],
            mark_price=new_weekly.get("mark_price") or new_weekly.get("bid") or 0,
            bid=new_weekly.get("bid"), ask=new_weekly.get("ask"),
            position_effect="open", action="roll_short_call_open",
            delta=new_weekly.get("delta"), dte=new_weekly.get("dte"),
            rationale=self._build_rationale(
                f"Roll open: sell {symbol} {new_weekly['expiration_date']} "
                f"C{new_weekly['strike_price']:.2f}",
                analysis,
            ),
            pair_id=pair_id,
            position_context=position_context,  # same context as the close leg
            leap_lifetime_key=leap_key,
        ))
        # Amendment 4 (fail-open observability): record the gate states — incl. the
        # earnings-data state — on every SHIPPED roll, as a SEPARATE audit so the
        # order legs stay byte-identical to pre-Phase-2. Distinguishes a roll that
        # shipped because earnings were genuinely CLEAR from one that shipped only
        # because the earnings source was DOWN (`gates.earnings == data_unavailable`).
        if not preview:
            # preview: don't write a shipped-roll audit for a mere card render.
            self._audit_division("pmcc_roll_gates", {
                "symbol": symbol,
                "gates": dict(gates),
                "conservative_net": round(conservative_net, 4),
                "mark_net": round(mark_net, 4),
                "override_kind": override_kind,
            })
        # Phase A: tag the two roll_short legs as ONE atomic combo so they dispatch
        # through place_combo -> place_multi_leg (a single all-or-nothing POST)
        # instead of two independent single-leg orders — closing the naked-leg fill
        # window B4 fixed only at the proposal layer. combo_direction + net_limit_price
        # REUSE mark_net from _short_roll_credit above (computed for the B2 gate) — no
        # re-derivation: net credit -> "credit", net debit -> "debit"; the limit is the
        # mark-based net, matching today's per-leg mark limits.
        _combo_direction = "credit" if mark_net >= 0 else "debit"
        _combo_net = round(abs(mark_net), 2)
        for _leg in orders:
            _leg.extra.update({
                "is_multi_leg": True,
                "combo_id": pair_id,
                "combo_direction": _combo_direction,
                "net_limit_price": _combo_net,
                "ratio_quantity": 1,
            })
        return orders

    # -- Option chain queries ------------------------------------------------

    async def _find_best_leap(self, symbol: str, broker: Broker) -> dict | None:
        """Find the best LEAP call: DTE >= leap_min_dte (config), delta >= 0.80
        (deepest qualifying ITM, hard-coded in `_select_leap_strike` — NOT
        config-driven; B8 2026-07-22), passes liquidity gate. Stashes
        `self._last_leap_diag` (chain state) so a B4 abort can audit WHY no LEAP
        was found."""
        if not isinstance(broker, OptionBroker):
            self._last_leap_diag = {"reason": "broker_not_option", "considered": 0}
            return None
        dates = await broker.get_expiration_dates(symbol)
        leap_dates = [d for d in dates if _days_to(d) >= self._leap_min_dte]
        if not leap_dates:
            self._last_leap_diag = {"reason": "no_leap_expiry_dates",
                                    "considered": 0, "min_dte": self._leap_min_dte}
            log.warning("PMCCAgent: no expiry dates >= %d days for %s", self._leap_min_dte, symbol)
            return None
        target_date = leap_dates[0]
        calls = await broker.get_calls_for_expiry(symbol, target_date)
        liquid = self._filter_liquid(calls, symbol)
        if not liquid:
            self._last_leap_diag = {"reason": "no_liquid_leap_contracts",
                                    "considered": len(calls), "liquid": 0,
                                    "target_date": target_date}
            log.warning(
                "PMCCAgent: no liquid LEAP contracts for %s on %s "
                "(%d candidates, all failed liquidity gate)",
                symbol, target_date, len(calls),
            )
            return None
        best = _select_leap_strike(liquid)
        if not best:
            self._last_leap_diag = {"reason": "no_qualifying_leap_strike",
                                    "considered": len(liquid), "target_date": target_date}
            return None
        best["expiration_date"] = target_date
        best["dte"] = _days_to(target_date)
        self._last_leap_diag = {"reason": "ok", "considered": len(liquid),
                                "target_date": target_date}
        return best

    async def _find_best_weekly(
        self,
        symbol: str,
        broker: Broker,
        target_delta: float | None = None,
        target_dte: int | None = None,
        target_strike: float | None = None,
        after_dte: int | None = None,
        target_delta_low: float | None = None,
        target_delta_high: float | None = None,
    ) -> dict | None:
        """Find the best weekly short call, optionally using LLM-suggested
        delta / DTE / strike.

        `target_strike` (Item 3 — 2026-05-03): when set, the strike picker
        selects the listed strike CLOSEST to target_strike (subject to
        liquidity gate), overriding the delta-distance ranking. Used when
        a rule (e.g. Major Breach halfway-roll) prescribes a specific
        strike that the delta-only picker would miss. None = original
        delta-distance behavior.

        `after_dte` (B7 — 2026-07-21): on roll paths, the new short must expire
        STRICTLY LATER than the current short. Callers pass the current short's
        DTE; every candidate expiry must satisfy `_days_to(d) > after_dte`. None
        on open paths (no prior short to roll out of) = original behavior. This
        enforces "roll OUT" and, together with the DTE-ceiling fallback below,
        blocks the same-expiry re-qualification (B7) and the LEAP-as-weekly
        sparse-chain fallback.

        Stashes `self._last_weekly_diag` (chain state) so a B4 abort can audit
        WHY no weekly was found."""
        if not isinstance(broker, OptionBroker):
            self._last_weekly_diag = {"reason": "broker_not_option", "considered": 0}
            return None
        dates = await broker.get_expiration_dates(symbol)

        # B7 roll-out predicate: opens (after_dte=None) accept any expiry; rolls
        # require a strictly-later expiry than the current short.
        def _rolls_out(d: str) -> bool:
            return after_dte is None or _days_to(d) > after_dte

        # Use LLM-suggested DTE window if provided, otherwise default 7–21d range
        if target_dte is not None:
            dte_lo = max(3, target_dte - 7)
            dte_hi = target_dte + 14
            weekly_dates = [
                d for d in dates if dte_lo <= _days_to(d) <= dte_hi and _rolls_out(d)
            ]
        else:
            weekly_dates = [
                d for d in dates
                if _WEEKLY_MIN_DTE <= _days_to(d) <= _WEEKLY_MAX_DTE and _rolls_out(d)
            ]

        if not weekly_dates:
            # Fallback refinement (B7, 2026-07-21): only accept a fallback expiry
            # that is (a) a plausible weekly by DTE ceiling and (b) rolls out past
            # the current short. This blocks the LEAP-as-weekly pathology where the
            # old fallback (`future[0]`) could return a 365+ DTE LEAP call as the
            # "weekly" when the chain is sparse. No qualifying fallback → abort
            # (return None) so the caller's B4 path audits + proposes nothing.
            fallback = [
                d for d in dates
                if 0 < _days_to(d) <= _WEEKLY_FALLBACK_MAX_DTE and _rolls_out(d)
            ]
            if not fallback:
                # Distinguish an empty/expired chain (no future dates at all)
                # from a chain that has dates but none that roll out past the
                # current short — so a B4 abort audit says WHICH.
                any_future = any(_days_to(d) > 0 for d in dates)
                if not any_future:
                    reason = "no_future_expiry_dates"
                elif after_dte is not None:
                    reason = "no_rollout_weekly"
                else:
                    reason = "no_weekly_within_ceiling"
                self._last_weekly_diag = {
                    "reason": reason, "considered": 0, "after_dte": after_dte,
                }
                log.warning(
                    "PMCCAgent: no qualifying weekly for %s (reason=%s after_dte=%s)",
                    symbol, reason, after_dte,
                )
                return None
            weekly_dates = [fallback[0]]

        target_date = weekly_dates[0]
        calls = await broker.get_calls_for_expiry(symbol, target_date)
        liquid = self._filter_liquid(calls, symbol)
        if not liquid:
            self._last_weekly_diag = {"reason": "no_liquid_weekly_contracts",
                                      "considered": len(calls), "liquid": 0,
                                      "target_date": target_date,
                                      "failed_by_gate": getattr(
                                          self, "_last_liquidity_breakdown", {})}
            log.warning(
                "PMCCAgent: no liquid weekly contracts for %s on %s "
                "(%d candidates, all failed liquidity gate)",
                symbol, target_date, len(calls),
            )
            return None

        # Use LLM-suggested delta if provided. When target_strike is set,
        # _select_weekly_strike honors it directly and ignores delta —
        # see the helper's docstring.
        delta = target_delta if target_delta is not None else self._short_target_delta
        best = _select_weekly_strike(
            liquid, delta, target_strike=target_strike,
            target_delta_low=target_delta_low, target_delta_high=target_delta_high,
        )
        if not best:
            self._last_weekly_diag = {"reason": "no_qualifying_weekly_strike",
                                      "considered": len(liquid), "target_date": target_date}
            return None
        best["expiration_date"] = target_date
        best["dte"] = _days_to(target_date)
        self._last_weekly_diag = {"reason": "ok", "considered": len(liquid),
                                  "target_date": target_date}
        return best

    # -- ProposedOrder factory -----------------------------------------------

    def _make_option_order(
        self,
        underlying: str,
        side: str,
        contracts: int,
        expiry: str,
        strike: float,
        mark_price: float | None,
        position_effect: str,
        action: str,
        rationale: str,
        delta: float | None = None,
        dte: int | None = None,
        pair_id: str | None = None,
        bid: float | None = None,
        ask: float | None = None,
        position_context: dict | None = None,
        leap_lifetime_key: str | None = None,
        preserve_market_sell: bool = False,
    ) -> ProposedOrder:
        extra: dict = {
            "is_option": True,
            "underlying": underlying,
            "option_type": "call",
            "expiration": expiry,
            "strike": strike,
            "position_effect": position_effect,
            "action": action,
            "mark_per_share": mark_price,    # for "anticipated cost" math in UI
        }
        if mark_price is None:
            # B3: an unresolvable mark must be DISTINGUISHABLE from a genuine 0.0 —
            # never silently priced at zero (an unrecorded cost would look free).
            extra["mark_unavailable"] = True
        if delta is not None:
            extra["delta"] = delta
        if dte is not None:
            extra["dte"] = dte
        if pair_id:
            extra["pmcc_pair_id"] = pair_id
        if leap_lifetime_key:
            # Stable identifier shared across all rolls on the same underlying
            # LEAP. `pmcc_pair_id` is per-roll (regenerated each time);
            # leap_lifetime_key is per-LEAP. Used by _query_prior_rolls to
            # scope roll history to one LEAP when the user has multiple
            # LEAPs on a single underlying.
            extra["leap_lifetime_key"] = leap_lifetime_key
        # Bid/ask are stashed when known (always available for newly-selected
        # contracts via the chain query; missing for buy-to-close legs whose
        # price comes from get_option_positions_detail and only has mark).
        if bid is not None and bid > 0:
            extra["bid"] = float(bid)
        if ask is not None and ask > 0:
            extra["ask"] = float(ask)
        # Phase 2 Telegram approval enrichment: position_context dict is
        # surfaced by ceo_graph (reads order.extra["position_context"])
        # and rendered by approval_format._format_position_context.
        if position_context:
            extra["position_context"] = position_context

        # B3 decoupling: `preserve_market_sell` keeps the 0.0 sell-limit (a 0.0
        # sell-limit fills at market — used by URGENT structural closes that MUST
        # fill) while the real mark is STILL recorded in extra["mark_per_share"]
        # above for cost visibility. Otherwise the limit is the mark (every normal
        # PMCC leg is limit-at-mark); an unresolvable mark yields no limit.
        if preserve_market_sell:
            limit_price = 0.0
        elif mark_price is not None:
            limit_price = round(mark_price, 2)
        else:
            limit_price = None
        return ProposedOrder(
            strategy="robinhood_pmcc",
            symbol=underlying,
            side=side,                    # type: ignore[arg-type]
            qty=float(contracts),
            order_type="limit",
            limit_price=limit_price,
            rationale=rationale,
            extra=extra,
        )

    def on_combo_filled(self, combo_id: str, fills: list) -> None:
        """State-update callback after an HITL-approved roll_short combo fills
        (contract required by dispatch_approved_ic_combo). PMCC re-derives all
        positions from the broker on every scan (no persistent position belief),
        so there is no in-memory book to mutate — we AUDIT the atomic fill and let
        the next scan pick up the new short. Must not raise (the dispatcher
        re-raises on failure)."""
        try:
            self._audit_division("pmcc_combo_filled", {
                "combo_id": combo_id,
                "leg_count": len(fills or []),
                "fills": [
                    {"order_id": getattr(f, "order_id", None),
                     "symbol": getattr(f, "symbol", None),
                     "side": getattr(f, "side", None),
                     "qty": getattr(f, "qty", None),
                     "price": getattr(f, "price", None),
                     "broker_order_id": getattr(f, "broker_order_id", None)}
                    for f in (fills or [])
                ],
            })
        except Exception:
            log.exception("PMCC on_combo_filled audit failed for combo %s", combo_id)

    @property
    def _combo_give_up_dollars(self) -> float:
        """Marketable give-up ($/share) shaved off (credit) / added to (debit) the
        NATURAL when re-pricing a ROLL/OPEN combo at dispatch, so it fills instead
        of resting. Config `robinhood_pmcc.combo.give_up_dollars`; default $0.02."""
        v = (self._cfg.get("combo") or {}).get("give_up_dollars")
        try:
            return float(v) if v is not None else 0.02
        except (TypeError, ValueError):
            return 0.02

    @property
    def _close_all_give_up_dollars(self) -> float:
        """Give-up for close_all — its OWN, MUCH LARGER knob because close_all is a
        'get out now' exit (it was a market-out / 0.0 sell). A big give_up crosses
        decisively so the exit FILLS instead of resting as a too-optimistic credit
        limit (and can flip to a small debit — pay to get out). Config
        `robinhood_pmcc.combo.close_all_give_up_dollars`; default $0.25."""
        v = (self._cfg.get("combo") or {}).get("close_all_give_up_dollars")
        try:
            return float(v) if v is not None else 0.25
        except (TypeError, ValueError):
            return 0.25

    async def reprice_combo(self, legs: list["ProposedOrder"], broker: Broker):
        """Re-price a combo from LIVE per-leg quotes at dispatch time (the
        proposal-time mid is stale by approval). Mutates each leg's
        combo_direction/net_limit_price; returns (direction, limit). Fail-safe:
        keeps the proposal-time limit if any quote is unavailable. close_all uses
        its own (larger) give_up so an urgent exit stays marketable-through, not a
        resting limit."""
        from trading_corp.agents.strategies._pmcc_combo import (
            reprice_combo_from_quotes,
        )
        _actions = {(l.extra or {}).get("action") for l in legs}
        _is_close_all = bool(_actions & {"close_short_urgent", "close_leap_urgent"})
        give_up = (self._close_all_give_up_dollars if _is_close_all
                   else self._combo_give_up_dollars)
        return await reprice_combo_from_quotes(
            legs, broker, give_up=give_up,
            max_spread_pct=self._reprice_max_spread_pct,
            min_sell_bid=self._reprice_min_sell_bid,
            min_spread_abs=self._reprice_min_spread_abs,
        )

    @property
    def _reprice_max_spread_pct(self) -> float:
        """Max per-leg bid/ask spread as a fraction of mid before reprice HOLDs
        (opening-rotation garbage). Config
        `robinhood_pmcc.combo.reprice_max_spread_pct`; default 0.60 (60% of mid)."""
        v = (self._cfg.get("combo") or {}).get("reprice_max_spread_pct")
        try:
            return float(v) if v is not None else 0.60
        except (TypeError, ValueError):
            return 0.60

    @property
    def _reprice_min_sell_bid(self) -> float:
        """Min sell-leg bid before reprice HOLDs (a 0-bid sell leg is garbage).
        Config `robinhood_pmcc.combo.reprice_min_sell_bid`; default 0.0."""
        v = (self._cfg.get("combo") or {}).get("reprice_min_sell_bid")
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    @property
    def _reprice_min_spread_abs(self) -> float:
        """Absolute $ spread floor that a leg must ALSO exceed (in addition to
        reprice_max_spread_pct) before reprice HOLDs — so a 1-tick spread on a
        cheap leg doesn't false-trigger. Config
        `robinhood_pmcc.combo.reprice_min_spread_abs`; default 0.10."""
        v = (self._cfg.get("combo") or {}).get("reprice_min_spread_abs")
        try:
            return float(v) if v is not None else 0.10
        except (TypeError, ValueError):
            return 0.10

    @property
    def _combo_max_adverse_net_deviation(self) -> float:
        """Max $/share the dispatch credit may fall BELOW the approved credit
        before the consent guard bails. Config
        `robinhood_pmcc.combo.max_adverse_net_deviation_dollars`; default 0.25."""
        v = (self._cfg.get("combo") or {}).get("max_adverse_net_deviation_dollars")
        try:
            return float(v) if v is not None else 0.25
        except (TypeError, ValueError):
            return 0.25

    def assess_combo_consent(self, legs, snapshot):
        """Defense-in-depth: compare the dispatch-repriced combo to the approved
        `snapshot`; return (ok, reason). Called by dispatch_approved_ic_combo after
        reprice, before place_combo — a bail books nothing and re-surfaces for
        re-approval. See _pmcc_combo.assess_combo_reprice_consent."""
        from trading_corp.agents.strategies._pmcc_combo import (
            assess_combo_reprice_consent,
        )
        return assess_combo_reprice_consent(
            legs, snapshot,
            max_adverse_net_deviation=self._combo_max_adverse_net_deviation,
        )

    # ------------------------------------------------------------------
    # Scan split (2026-07-24): pre-open TRIAGE + post-settle liveness probe
    # ------------------------------------------------------------------

    @property
    def _triage_near_dte_days(self) -> int:
        """Short-leg DTE at/under which a leg enters the morning triage watchlist
        ('expiring today/this week'). Config
        `robinhood_pmcc.scan.triage_near_dte_days`; default 5."""
        v = (self._cfg.get("scan") or {}).get("triage_near_dte_days")
        try:
            return int(v) if v is not None else 5
        except (TypeError, ValueError):
            return 5

    @property
    def _liveness_ref_symbols(self) -> list:
        """Broadly-liquid reference underlyings for the GLOBAL post-settle liveness
        probe (NOT thin single names). Config
        `robinhood_pmcc.scan.liveness_ref_symbols`; default [SPY, QQQ]."""
        v = (self._cfg.get("scan") or {}).get("liveness_ref_symbols")
        if isinstance(v, (list, tuple)) and v:
            return [str(s) for s in v]
        return ["SPY", "QQQ"]

    @property
    def _liveness_max_spread_pct(self) -> float:
        """Max reference-chain bid/ask spread (fraction of mid) that still counts
        as 'quotes live'. Config `robinhood_pmcc.scan.liveness_max_spread_pct`;
        default 0.15."""
        v = (self._cfg.get("scan") or {}).get("liveness_max_spread_pct")
        try:
            return float(v) if v is not None else 0.15
        except (TypeError, ValueError):
            return 0.15

    async def triage(self, broker: Broker) -> "list[dict]":
        """Pre-open TRIAGE (Phase A only). Which shorts are near-DTE / breached /
        assignment-risk, using ONLY static data + live-underlying spot
        (broker.quote). NO option-chain reads, NO strike selection, NO credit/greek
        math, NO Approve cards, NO ABORTED alerts. Writes a `pmcc_morning_triage`
        audit and returns per-short-leg triage dicts (register: breach|routine)."""
        self._reload()
        existing = await self.detect_existing_legs(broker)
        self._check_options_tier_once(broker)
        near = self._triage_near_dte_days
        out: list[dict] = []
        for leg in existing:
            if leg.short_leg_strike is None or leg.short_leg_dte is None:
                continue                       # uncovered LEAP — no short to triage
            dte = int(leg.short_leg_dte)
            if dte > near:
                continue                       # not near-term
            strike = float(leg.short_leg_strike)
            spot = None
            try:
                q = await broker.quote(leg.symbol)
                spot = float(q) if q else None
            except Exception as e:              # noqa: BLE001 — spot is best-effort
                log.debug("triage: quote(%s) failed: %s", leg.symbol, e)
            itm = bool(spot is not None and spot >= strike)
            out.append({
                "symbol": leg.symbol, "short_strike": strike, "short_dte": dte,
                "spot": spot, "itm": itm,
                "register": "breach" if itm else "routine",
            })
        self._audit_division("pmcc_morning_triage",
                             {"near_dte_days": near, "legs": out})
        return out

    async def reference_quotes_live(self, broker: Broker) -> "tuple[bool, str]":
        """GLOBAL liveness probe for the post-settle actionable pass: a broadly-
        liquid reference (SPY/QQQ) returns two-sided option quotes with a sane
        spread. Returns (live, reason). Never raises. Per-name thin-ness is handled
        downstream by the existing liquidity gate, not here."""
        for sym in self._liveness_ref_symbols:
            try:
                dates = await broker.get_expiration_dates(sym)
                if not dates:
                    continue
                calls = await broker.get_calls_for_expiry(sym, dates[0])
                for c in (calls or []):
                    bid = float(c.get("bid") or 0)
                    ask = float(c.get("ask") or 0)
                    if bid > 0 and ask > 0:
                        mid = (bid + ask) / 2.0
                        if mid > 0 and (ask - bid) / mid <= self._liveness_max_spread_pct:
                            return True, f"{sym} live (bid {bid:.2f}/ask {ask:.2f})"
            except Exception as e:              # noqa: BLE001 — probe is best-effort
                log.debug("liveness probe %s failed: %s", sym, e)
        return False, "no reference chain returned sane two-sided quotes"

    @staticmethod
    def _format_triage_digest(report: "list") -> str:
        """Calm two-register morning digest. Routine near-DTE -> reassuring +
        'cards after open'; breach/assignment -> escalated. No per-name aborts."""
        rep = report or []
        breach = [r for r in rep if r.get("register") == "breach"]
        routine = [r for r in rep if r.get("register") == "routine"]
        if not breach and not routine:
            return "PMCC morning triage: no shorts near expiry. Nothing to do pre-open."
        lines = ["PMCC morning triage"]
        if breach:
            lines.append("")
            lines.append(f"** BREACH / ASSIGNMENT RISK ({len(breach)}) — needs eyes:")
            for r in breach:
                spot_txt = f" (spot {r['spot']:g})" if r.get("spot") is not None else ""
                lines.append(f"  - {r['symbol']} short {r['short_strike']:g}C, "
                             f"{r['short_dte']}DTE, ITM{spot_txt}")
        if routine:
            lines.append("")
            lines.append(f"Routine near-DTE ({len(routine)}):")
            for r in routine:
                spot_txt = f", spot {r['spot']:g}" if r.get("spot") is not None else ""
                lines.append(f"  - {r['symbol']} short {r['short_strike']:g}C, "
                             f"{r['short_dte']}DTE, OTM{spot_txt}")
            lines.append("The engine will present roll cards after the open — "
                         "no manual action needed before then.")
        return "\n".join(lines)

    @staticmethod
    def _option_level_int(level: str) -> "int | None":
        """Parse an RH options tier ('option_level_3') to its int (3), else None."""
        try:
            s = str(level or "").strip().lower()
            if s.startswith("option_level_"):
                return int(s.rsplit("_", 1)[1])
            return int(s) if s.isdigit() else None
        except (TypeError, ValueError, IndexError):
            return None

    def _check_options_tier_once(self, broker) -> None:
        """B-ARM #6: once per process, verify a LIVE PMCC broker's options tier is
        spread-capable (level_3 — roll_short is a multi-leg spread). Below that, or
        unverifiable, log + audit so it's visible at startup instead of only
        surfacing as a live order reject. Never blocks; never raises."""
        if getattr(self, "_options_tier_checked", False):
            return
        self._options_tier_checked = True
        try:
            if getattr(broker, "paper", True):
                return                       # paper handle — tier is not exercised
            level = getattr(broker, "option_level", "")
            lvl = self._option_level_int(level)
            if lvl is None:
                log.warning("PMCC options-tier UNVERIFIED (option_level=%r) — cannot "
                            "confirm spread eligibility", level)
                self._audit_division("pmcc_options_tier_check",
                                     {"ok": False, "verified": False, "level": str(level)})
            elif lvl < 3:
                log.warning("PMCC options-tier INSUFFICIENT: option_level_%d < 3 "
                            "(spreads/roll_short need level_3) — live rolls will REJECT", lvl)
                self._audit_division("pmcc_options_tier_check",
                                     {"ok": False, "verified": True, "level": lvl, "required": 3})
            else:
                log.info("PMCC options-tier OK: option_level_%d (>= 3)", lvl)
                self._audit_division("pmcc_options_tier_check",
                                     {"ok": True, "verified": True, "level": lvl})
        except Exception as e:               # never let a diagnostic break the scan
            log.debug("PMCC options-tier check failed: %s", e)

    # ------------------------------------------------------------------
    # B-AE assignment/exercise monitoring (2026-07-24) — MONITORING ONLY
    # ------------------------------------------------------------------

    @property
    def _assignment_risk_dte(self) -> int:
        """Short-leg DTE at/under which an ITM short is flagged for assignment RISK
        (0 = expiring today, 1 = expiring tomorrow -> alert EOD *before* expiry).
        Config `robinhood_pmcc.scan.assignment_risk_dte`; default 1."""
        v = (self._cfg.get("scan") or {}).get("assignment_risk_dte")
        try:
            return int(v) if v is not None else 1
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _assignment_risk_items(shorts, spots, within_dte) -> "list[dict]":
        """PRE-event risk: SHORT calls at DTE<=within_dte that are ITM (spot>=strike).
        `shorts` = option-position dicts; `spots` = {symbol: spot|None}. Pure."""
        out: list[dict] = []
        for p in (shorts or []):
            if (p.get("option_type") or "call") != "call":
                continue                          # PMCC shorts are calls
            dte = p.get("dte")
            if dte is None or int(dte) > int(within_dte):
                continue
            strike = float(p.get("strike_price") or 0)
            sym = (p.get("chain_symbol") or "").upper()
            spot = (spots or {}).get(sym)
            if spot is None or float(spot) < strike:
                continue                          # not ITM, or spot unknown -> don't escalate
            out.append({"symbol": sym, "strike": strike, "dte": int(dte),
                        "spot": float(spot), "itm": True})
        return out

    @staticmethod
    def _assignment_event_items(shorts) -> "list[dict]":
        """EVENTS: any short with a non-zero pending assignment/exercise/expiration."""
        out: list[dict] = []
        for p in (shorts or []):
            pa = float(p.get("pending_assignment_quantity") or 0)
            pe = float(p.get("pending_exercise_quantity") or 0)
            px = float(p.get("pending_expiration_quantity") or 0)
            if pa or pe or px:
                out.append({
                    "symbol": (p.get("chain_symbol") or "").upper(),
                    "strike": float(p.get("strike_price") or 0),
                    "expiration": p.get("expiration_date"),
                    "pending_assignment": pa, "pending_exercise": pe,
                    "pending_expiration": px,
                })
        return out

    @staticmethod
    def _format_assignment_alert(items, kind: str) -> str:
        """Urgent assignment alert body. STATES the manual remedy options — this is
        HITL; the engine does NOT auto-act."""
        if not items:
            return ""
        if kind == "event":
            head = f"ASSIGNMENT/EXERCISE PENDING on {len(items)} PMCC short(s)"
            rows = [f"  - {i['symbol']} {i['strike']:g}C exp {i.get('expiration')} "
                    f"(assign={i['pending_assignment']:g} exer={i['pending_exercise']:g} "
                    f"exp={i['pending_expiration']:g})" for i in items]
        else:
            head = f"ASSIGNMENT RISK: {len(items)} ITM PMCC short(s) near expiry"
            rows = [f"  - {i['symbol']} {i['strike']:g}C {i['dte']}DTE, spot {i['spot']:g} (ITM)"
                    for i in items]
        remedy = ("Manual remedy (HITL): EXERCISE THE LEAP to cover delivery, OR "
                  "BUY-TO-CLOSE the assigned short-stock position. Engine does NOT auto-act.")
        return "\n".join([head, *rows, "", remedy])

    @staticmethod
    def _emit_assignment_exec_alert(tier, reason, *, symbol) -> None:
        try:
            from trading_corp.comms.exec_alert import ExecOutcome, emit_exec_alert
            emit_exec_alert(ExecOutcome(
                tier=tier, symbol=str(symbol), strategy="robinhood_pmcc",
                reason=reason, position_changed=(tier == "NAKED_LEG")))
        except Exception:
            pass

    def _emit_assignment_alerts(self, risk, events) -> None:
        """Audit + urgent exec-alert for events (NAKED_LEG) and risk (EXEC_FAIL). Both
        tiers are no-dedupe so assignment alerts are never swallowed. Never raises."""
        try:
            if events:
                self._audit_division("pmcc_assignment_detected", {"items": events})
                self._emit_assignment_exec_alert(
                    "NAKED_LEG", self._format_assignment_alert(events, "event"),
                    symbol=(events[0].get("symbol") or "?"))
            if risk:
                self._audit_division("pmcc_assignment_risk", {"items": risk})
                self._emit_assignment_exec_alert(
                    "EXEC_FAIL", self._format_assignment_alert(risk, "risk"),
                    symbol=(risk[0].get("symbol") or "?"))
        except Exception as e:
            log.debug("assignment alert emit failed: %s", e)

    async def assignment_watch(self, broker) -> "dict":
        """B-AE MONITORING: detect near-expiry ITM PMCC shorts (assignment RISK) and
        non-zero pending_* signals (assignment/exercise EVENTS); alert + audit; surface
        for HITL action. Monitoring ONLY — never auto-closes, never raises."""
        try:
            positions = await broker.get_option_positions_detail()
        except Exception as e:
            log.warning("assignment_watch: get_option_positions_detail failed: %s", e)
            return {"risk": 0, "events": 0}
        shorts = [p for p in (positions or []) if float(p.get("quantity") or 0) < 0]
        events = self._assignment_event_items(shorts)
        near = [p for p in shorts
                if p.get("dte") is not None and int(p.get("dte")) <= self._assignment_risk_dte]
        spots: dict = {}
        for p in near:
            sym = (p.get("chain_symbol") or "").upper()
            if sym and sym not in spots:
                try:
                    q = await broker.quote(sym)
                    spots[sym] = float(q) if q else None
                except Exception:
                    spots[sym] = None
        risk = self._assignment_risk_items(near, spots, self._assignment_risk_dte)
        self._emit_assignment_alerts(risk, events)
        return {"risk": len(risk), "events": len(events)}

    # ------------------------------------------------------------------
    # Scout — survey the market for NEW PMCC opening candidates
    # ------------------------------------------------------------------

    async def scout_candidates(
        self,
        broker: Broker,
        *,
        regime: str = "unknown",
        max_candidates: int = 12,
    ) -> ScoutReport:
        """Survey the scout universe for fresh PMCC opening candidates.

        Always returns the candidate list regardless of headroom — when the
        account is at capacity the status block flips to HOLD and the user
        sees the candidates anyway (per Board policy: "show plausible
        candidates regardless of position-to-cash ratio").

        For each symbol in the scout universe (minus existing PMCC
        underlyings) we:
          1. Fetch spot price (yfinance via _fetch_prices)
          2. Check earnings buffer
          3. Find best LEAP via existing _find_best_leap()
          4. Find best weekly short via existing _find_best_weekly()
          5. Compute opening economics + heuristic score
        """
        scout_cfg = self._cfg.get("scout", {}) or {}
        universe = list(scout_cfg.get("universe", []) or [])
        max_positions = int(scout_cfg.get("max_concurrent_pmccs", 8))
        cap_per_pos = float(scout_cfg.get("capital_per_position_dollars", 25000))
        cash_floor_pct = float(scout_cfg.get("cash_reserve_floor_pct", 0.10))

        # Account snapshot
        account_equity = 0.0
        account_cash = 0.0
        try:
            snap = await broker.snapshot()
            account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
            # Some brokers expose buying_power, others cash — use whichever
            account_cash = float(
                getattr(snap, "buying_power", 0.0)
                or getattr(snap, "cash", 0.0)
                or 0.0
            )
        except Exception as e:
            log.warning("Scout: broker.snapshot() failed: %s", e)

        # Existing PMCC pairs — exclude their underlyings
        existing_symbols: set[str] = set()
        try:
            existing = await self.detect_existing_legs(broker)
            existing_symbols = {p.symbol.upper() for p in existing}
        except Exception as e:
            log.warning("Scout: detect_existing_legs failed: %s", e)

        universe_filtered = [
            s.upper() for s in universe
            if s.upper() not in existing_symbols
            and s.upper() not in self.position_exclude
        ]

        # Run chain scans in parallel — yfinance + broker calls are slow
        candidates: list[ScoutCandidate] = []
        if universe_filtered:
            results = await asyncio.gather(
                *[self._build_scout_candidate(s, broker) for s in universe_filtered],
                return_exceptions=True,
            )
            for sym, r in zip(universe_filtered, results):
                if isinstance(r, Exception):
                    log.warning("Scout: %s analysis raised: %s", sym, r)
                    continue
                if r is not None:
                    candidates.append(r)

        # Score + sort
        weights = scout_cfg.get("weights", {}) or {}
        for c in candidates:
            c.score = self._score_candidate(c, weights)
        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[:max_candidates]

        # Build status
        open_positions = len(existing_symbols)
        # Equity-based cap (1 contract per $25k equity per underlying)
        cap_count = int(account_equity // cap_per_pos) if cap_per_pos > 0 else max_positions
        effective_max = min(max_positions, cap_count) if cap_count > 0 else max_positions

        actionable = [c for c in candidates if not c.blockers]
        cheapest_debit = min(
            (c.net_opening_debit for c in actionable),
            default=None,
        )

        blockers: list[str] = []
        if open_positions >= max_positions:
            blockers.append(
                f"At config ceiling: {open_positions}/{max_positions} "
                "concurrent PMCCs."
            )
        if cap_count > 0 and open_positions >= cap_count:
            blockers.append(
                f"Equity-based size cap: {open_positions}/{cap_count} positions "
                f"(1 per ${cap_per_pos:,.0f})."
            )

        # Cash floor — would the cheapest candidate breach the reserve?
        cash_floor = account_equity * cash_floor_pct
        if cheapest_debit is not None and account_equity > 0:
            projected_cash = account_cash - cheapest_debit
            if projected_cash < cash_floor:
                blockers.append(
                    f"Cheapest candidate (~${cheapest_debit:,.2f}) would breach "
                    f"cash floor (${cash_floor:,.2f})."
                )

        # Determine state
        if blockers:
            state = "hold"
            headline = "HOLD — at capacity (candidates shown for awareness)"
        elif not actionable:
            state = "halt"
            headline = "HALT — no actionable candidates today"
        else:
            state = "go"
            room = max(0, effective_max - open_positions)
            plural = "s" if room != 1 else ""
            headline = f"GO — room for {room} more PMCC{plural}"

        detail = (
            f"Equity ${account_equity:,.2f} · cash ${account_cash:,.2f} · "
            f"{open_positions} of {effective_max} slots used."
        )
        if not candidates:
            detail += "  No symbols cleared the chain / liquidity gates today."

        pct_allocated = (
            (open_positions * cap_per_pos / account_equity)
            if account_equity > 0 else 0.0
        )

        status = ScoutStatus(
            state=state,
            headline=headline,
            detail=detail,
            open_positions=open_positions,
            max_positions=effective_max,
            cash_available=account_cash,
            cash_required_estimate=cheapest_debit,
            pct_allocated=pct_allocated,
            blockers=blockers,
        )

        return ScoutReport(
            status=status,
            candidates=candidates,
            universe_scanned=universe_filtered,
            excluded_existing=sorted(existing_symbols),
            generated_at=now_utc().isoformat(timespec="seconds"),
        )

    async def _build_scout_candidate(
        self,
        symbol: str,
        broker: Broker,
    ) -> ScoutCandidate | None:
        """Build one ScoutCandidate by scanning the option chain for `symbol`.

        Always returns a candidate (never None) — the `blockers` list captures
        why a candidate may not be executable so the dashboard can show it
        as informational rather than hide it.
        """
        notes: list[str] = []
        blockers: list[str] = []

        # Spot price
        spot: float | None = None
        try:
            prices = await self._fetch_prices([symbol])
            spot = prices.get(symbol)
        except Exception as e:
            log.debug("Scout %s: _fetch_prices failed: %s", symbol, e)
        if spot is None:
            blockers.append("no spot price")

        # Earnings buffer
        try:
            blocked, why = self._blocked_by_earnings(symbol)
            if blocked:
                blockers.append(f"earnings buffer: {why}")
        except Exception as e:
            log.debug("Scout %s: earnings check failed: %s", symbol, e)

        # Find LEAP + first weekly short
        leap = None
        short = None
        try:
            leap = await self._find_best_leap(symbol, broker)
        except Exception as e:
            log.debug("Scout %s: _find_best_leap raised: %s", symbol, e)

        if leap is None:
            blockers.append("no qualifying LEAP in chain")
        else:
            try:
                short = await self._find_best_weekly(symbol, broker)
            except Exception as e:
                log.debug("Scout %s: _find_best_weekly raised: %s", symbol, e)
            if short is None:
                blockers.append("no qualifying weekly short")

        # Build legs
        leap_leg: TradeLegDetail | None = None
        short_leg: TradeLegDetail | None = None
        leap_debit = 0.0
        short_credit = 0.0

        if leap:
            leap_mark = float(leap.get("mark_price") or leap.get("ask") or 0)
            leap_debit = leap_mark * 100.0   # 1 contract = 100 shares
            leap_leg = TradeLegDetail(
                action_label="Buy to open",
                side="buy",
                position_effect="open",
                underlying=symbol,
                expiry=leap.get("expiration_date", ""),
                strike=float(leap.get("strike_price") or 0),
                option_type="call",
                qty=1,
                dte=leap.get("dte"),
                delta=leap.get("delta"),
                mark_per_share=leap_mark if leap_mark > 0 else None,
                bid=leap.get("bid"),
                ask=leap.get("ask"),
                estimated_dollars=leap_debit,   # debit = positive
            )

        if short:
            short_mark = float(short.get("mark_price") or short.get("bid") or 0)
            short_credit = short_mark * 100.0
            short_leg = TradeLegDetail(
                action_label="Sell to open",
                side="sell",
                position_effect="open",
                underlying=symbol,
                expiry=short.get("expiration_date", ""),
                strike=float(short.get("strike_price") or 0),
                option_type="call",
                qty=1,
                dte=short.get("dte"),
                delta=short.get("delta"),
                mark_per_share=short_mark if short_mark > 0 else None,
                bid=short.get("bid"),
                ask=short.get("ask"),
                estimated_dollars=-short_credit,   # credit = negative
            )

        # Yield math (only when both legs are present and priced)
        weekly_yield: float | None = None
        annualized: float | None = None
        if leap_debit > 0 and short_credit > 0:
            weekly_yield = short_credit / leap_debit
            annualized = weekly_yield * 52.0

        # Notes
        if leap and (leap.get("delta") or 0) >= 0.85:
            notes.append("Deep ITM LEAP (delta ≥ 0.85)")
        if weekly_yield is not None and weekly_yield >= 0.025:
            notes.append(f"Strong weekly yield: {weekly_yield*100:.2f}%")
        if leap_leg and short_leg and short_leg.strike > leap_leg.strike:
            notes.append("Short strike above LEAP strike — full vertical capture")

        return ScoutCandidate(
            symbol=symbol,
            spot_price=spot,
            leap_leg=leap_leg,
            short_leg=short_leg,
            leap_debit_dollars=leap_debit,
            short_credit_dollars=short_credit,
            net_opening_debit=(leap_debit - short_credit),
            weekly_yield_pct=weekly_yield,
            annualized_yield_pct=annualized,
            score=0.0,           # populated by _score_candidate
            notes=notes,
            blockers=blockers,
        )

    def _score_candidate(
        self,
        c: ScoutCandidate,
        weights: dict,
    ) -> float:
        """Heuristic ranking score. Higher = better.

        Candidates with blockers always sort to the bottom regardless of yield
        (so the user sees actionable picks first, with informational ones below).
        """
        if c.blockers:
            return -1.0
        score = 0.0
        if c.weekly_yield_pct is not None:
            score += float(weights.get("weekly_yield_pct", 1.0)) * (c.weekly_yield_pct * 100.0)
        if c.short_leg and c.short_leg.delta is not None:
            target = self._short_target_delta
            dist = abs(c.short_leg.delta - target)
            score -= float(weights.get("delta_distance_to_target", 0.30)) * (dist * 10.0)
        return score

    async def propose_opening_orders(
        self,
        symbol: str,
        broker: Broker,
    ) -> list[ProposedOrder]:
        """Build ProposedOrder list to OPEN a fresh PMCC on `symbol`.

        Public wrapper around `_propose_open_pmcc` so the scout-execute route
        has a clean entry point. Always sizes at 1 contract; the risk gate
        downstream may resize/reject before placement.

        Drops the per-skip diagnostic the internal helper returns —
        dashboard-side callers only care about the order list. The
        research-on-demand integration (Phase 1a-2) calls
        `_propose_open_pmcc` directly to get the reason.
        """
        orders, _skip_reason = await self._propose_open_pmcc(
            symbol, broker, contracts=1, analysis=None,
        )
        return orders

    # ──────────────────────────────────────────────────────────────────────
    # Research-firm-on-demand integration (Phase 1a-2, design §8.A)
    # ──────────────────────────────────────────────────────────────────────

    @property
    def _research_outage_alert_threshold(self) -> int:
        """N consecutive `pmcc_scan_research_unavailable` rows before
        emitting `pmcc_research_extended_outage` + Telegram-notify.

        Default 3 (per design §3.6 footnote). Configurable in risk.yaml
        as `pmcc.research_outage_alert_threshold`.
        """
        cfg = self._cfg_risk.get("pmcc", {}) or {}
        try:
            return max(1, int(cfg.get("research_outage_alert_threshold", 3)))
        except (TypeError, ValueError):
            return 3

    async def _compute_research_capacity(
        self, broker: Broker,
    ) -> tuple[float, int]:
        """Return (available_buying_power, n_candidates_to_request).

        DOC DIVERGENCE FROM §8.A (a) STEP 1 (Board direction 2026-05-01):
        Design doc as written uses
        `available_slots = max_concurrent_pmccs - currently_held_count`.
        Board direction overrides: capacity is governed by buying power
        (subject to `cash_reserve_floor_pct` portfolio-weighting rule),
        NOT by a hard count cap. There is no max number of positions —
        if buying power supports a 9th (or 15th, or 20th) PMCC, it's
        allowed. The cash floor is the only safety rail.

        n_candidates = how many full-size positions we could afford at
        `capital_per_position_dollars` (default $25k), capped at the
        Pydantic-enforced CandidateScope limit of 5. Returns (0, 0)
        when buying power is below the cash floor — the engagement is
        skipped entirely (no LLM cost on a no-op cycle).
        """
        scout_cfg = self._cfg.get("scout", {}) or {}
        cash_floor_pct = float(scout_cfg.get("cash_reserve_floor_pct", 0.10))
        cap_per_pos = float(scout_cfg.get("capital_per_position_dollars", 25000))

        try:
            snap = await broker.snapshot()
        except Exception as e:
            log.warning("PMCCAgent research capacity: broker.snapshot failed: %s", e)
            return 0.0, 0

        equity = float(getattr(snap, "equity", 0.0) or 0.0)
        buying_power = float(
            getattr(snap, "buying_power", 0.0)
            or getattr(snap, "cash", 0.0)
            or 0.0
        )
        cash_floor_dollars = equity * cash_floor_pct
        available = max(0.0, buying_power - cash_floor_dollars)

        if available <= 0 or cap_per_pos <= 0:
            return 0.0, 0
        # n_candidates: at least 1 if we have any room, capped at 5
        # (CandidateScope.n_candidates Pydantic max).
        n = min(5, max(1, int(available // cap_per_pos)))
        return available, n

    def _audit_division(self, kind: str, payload: dict) -> None:
        """Write a division-side audit row (actor=robinhood_pmcc).

        Best-effort: missing logger = silent skip (tests pass None).
        """
        if self._logger_agent is None:
            return
        try:
            self._logger_agent.log_event(
                actor="robinhood_pmcc", kind=kind, payload=payload,
            )
        except Exception as e:
            log.warning("robinhood_pmcc audit write failed (%s): %s", kind, e)

    def _audit_roll_abort(self, *, reason: str, symbol: str, missing_leg: str = "",
                          diag: dict | None = None, extra: dict | None = None,
                          preview: bool = False) -> None:
        """B4 (Phase 1): record an aborted roll/open so `b4 -> 0` is
        distinguishable from 'chains are thin and we now do nothing'. Writes a
        structured `pmcc_roll_aborted` division audit + a loud log line. NO order
        is proposed when this fires (atomic-roll invariant).

        Phase-2 (B9/B2): also used for proceed-gate aborts (earnings/credit),
        which pass `missing_leg=""` and an `extra` dict carrying the `gates` map
        (every gate evaluated up to the abort) + the gate's figures.

        `preview=True` (2026-07-30): the caller is a side-effect-free card render /
        Re-analyze / estimate build, NOT a dispatch attempt. The invariant is that
        exec-alerts (ABORTED &c.) fire ONLY on a genuine dispatch attempt — never a
        render — so in preview we resolve the reason for on-screen display but
        write NO `pmcc_roll_aborted` audit row and emit NO Telegram alert. The log
        drops to debug so a mere preview can't read as a real abort in the logs."""
        payload = {
            "reason": reason,
            "symbol": symbol,
            "missing_leg": missing_leg,
            "chain_state": diag or {},
        }
        if extra:
            payload.update(extra)
        log.log(
            logging.DEBUG if preview else logging.WARNING,
            "PMCCAgent: %sABORTED roll/open on %s -- %s%s; chain=%s",
            "PREVIEW " if preview else "",
            symbol, reason,
            f" (missing {missing_leg})" if missing_leg else "",
            payload["chain_state"],
        )
        if preview:
            # Render-only path: no audit row, no exec-alert (invariant above).
            return
        self._audit_division("pmcc_roll_aborted", payload)

        # Observability: 🟡 ABORTED — self-blocked at build; nothing sent to broker.
        # Deduped on (tier, symbol, reason) so the 08:30–09:25 scan can't spam the
        # same abort every cycle. Double-isolated; never affects the build path.
        try:
            from trading_corp.comms.exec_alert import ExecOutcome, emit_exec_alert
            d = diag or {}
            # Reassuring wording: an ABORT means the engine chose NOT to act — no
            # order was sent and the position is untouched. The old alarming
            # "sparse_chain_no_weekly ... missing new_short" body triggered a
            # panic manual roll (2026-07-24). Keep a short diagnostic tail with
            # the sub-gate that bound so it's still actionable.
            _detail = f" [{reason}"
            if d.get("considered") is not None:
                _detail += f": considered={d.get('considered')}, liquid={d.get('liquid', 0)}"
                _fbg = d.get("failed_by_gate")
                if _fbg:
                    _detail += f", failed_by={_fbg}"
            elif missing_leg:
                _detail += f": missing {missing_leg}"
            _detail += "]"
            _r = ("no action - no order sent, position unchanged; "
                  "will retry next scan." + _detail)
            emit_exec_alert(ExecOutcome(
                tier="ABORTED", symbol=symbol, strategy="robinhood_pmcc",
                reason=_r, position_changed=False,
            ))
        except Exception:
            pass

    async def _record_research_unavailable(
        self, *, engagement_id: str | None, reason: str,
    ) -> None:
        """Increment consecutive-failure counter, write
        `pmcc_scan_research_unavailable` audit, and (at threshold) emit
        the extended-outage row + Telegram-notify (§8.A clause c).
        """
        now = iso(now_utc())
        self._consec_research_failures += 1
        if self._first_research_failure_ts is None:
            self._first_research_failure_ts = now

        self._audit_division("pmcc_scan_research_unavailable", {
            "engagement_id": engagement_id,
            "reason": reason,
            "consecutive_failures": self._consec_research_failures,
            "first_failure_ts": self._first_research_failure_ts,
        })

        threshold = self._research_outage_alert_threshold
        if (
            self._consec_research_failures >= threshold
            and not self._outage_alerted
        ):
            self._outage_alerted = True
            payload = {
                "consecutive_failures": self._consec_research_failures,
                "first_failure_ts": self._first_research_failure_ts,
                "last_successful_engagement_id": self._last_successful_engagement_id,
                "threshold": threshold,
            }
            self._audit_division("pmcc_research_extended_outage", payload)
            if self._notify_callback is not None:
                msg = (
                    f"⚠️ PMCC research firm extended outage: "
                    f"{self._consec_research_failures} consecutive "
                    f"failed engagements (since {self._first_research_failure_ts}). "
                    f"Scout produced no orders this cycle. "
                    f"Investigate research firm health; the safety bias is "
                    f"'no trade is better than a stale trade'."
                )
                try:
                    await self._notify_callback(msg)
                except Exception as e:
                    log.warning("PMCC outage notify_callback raised: %s", e)

    def _reset_research_failure_streak(self, *, engagement_id: str) -> None:
        """Called on any successful engagement (returned a non-None product)."""
        self._consec_research_failures = 0
        self._first_research_failure_ts = None
        self._outage_alerted = False
        self._last_successful_engagement_id = engagement_id

    async def _run_research_on_demand_new_opens(
        self,
        broker: Broker,
        *,
        currently_held_symbols: set[str],
    ) -> list[ProposedOrder]:
        """Phase 1a-2 new-opens path for `universe_source: research_on_demand`.

        Implements §8.A clauses (a)-(d):
          1. Compute capacity (buying-power-based per Board direction —
             see _compute_research_capacity).
          2. If capacity == 0: skip engagement entirely (no LLM cost).
          3. Build CandidateScope, call run_engagement.
          4. On None: write pmcc_scan_research_unavailable, increment
             counter, maybe emit extended-outage. Return [].
          5. Per candidate: run existing per-symbol gates via
             `_propose_open_pmcc`. Write `research_candidate_acted_on`
             when orders produce, `research_candidate_skipped` (with
             reason) when not.
          6. Return all produced orders.
        """
        from trading_corp.agents.research.engagement import run_engagement
        from trading_corp.agents.research.schemas import (
            CandidateScope, EngagementSpec,
        )

        if self._research_firm_deps is None:
            log.warning(
                "PMCCAgent: universe_source='research_on_demand' but no "
                "research_firm_deps wired — falling back to no new opens "
                "this scan. Wire deps in main.py to enable.",
            )
            return []

        available_dollars, n_candidates = await self._compute_research_capacity(broker)
        if n_candidates <= 0:
            log.info(
                "PMCCAgent research_on_demand: capacity exhausted "
                "(available=$%.2f, cash floor enforced) — no engagement",
                available_dollars,
            )
            return []

        # Pull mandate from strategies.yaml verbatim (Q4 — research firm
        # stays stateless; division loads its own config).
        mandate = (self._strategy_cfg.get("underlying_criteria") or {})
        earnings_buffer = int(
            mandate.get("earnings_buffer_days", 7)
        )

        spec = EngagementSpec(
            requesting_division="robinhood_pmcc",
            product_type="candidate_recommendation",
            asset_class="equity",
            scope=CandidateScope(
                mandate=mandate,
                capacity_dollars=available_dollars,
                current_holdings=sorted(currently_held_symbols),
                n_candidates=n_candidates,
                starter_universe_key="large_mid_cap",
                earnings_buffer_days=earnings_buffer,
            ),
            triggered_by="division_agent",
            triggered_ts=iso(now_utc()),
        )

        try:
            rec = await run_engagement(spec, deps=self._research_firm_deps)
        except Exception as e:
            log.warning("PMCCAgent research_on_demand: run_engagement raised: %s", e)
            await self._record_research_unavailable(
                engagement_id=spec.engagement_id,
                reason=f"engagement_exception: {e}",
            )
            return []

        if rec is None:
            await self._record_research_unavailable(
                engagement_id=spec.engagement_id,
                reason="engagement returned None (kill_switch / out_of_scope / "
                       "no_action / validation_failed / cost_cap_exceeded — "
                       "see research_engagement_* audit rows for engagement_id)",
            )
            return []

        # Successful engagement — reset outage counter
        self._reset_research_failure_streak(engagement_id=spec.engagement_id)

        if not rec.candidates:
            log.info(
                "PMCCAgent research_on_demand: engagement %s returned 0 candidates",
                spec.engagement_id[:8],
            )
            return []

        # Process each candidate through the existing per-symbol gates.
        # Per §8.A (a) step 4: research firm narrowed structurally;
        # _propose_open_pmcc applies the live-microstructure gates
        # (earnings buffer, LEAP qualifying, weekly qualifying).
        all_orders: list[ProposedOrder] = []
        scout_cfg = self._cfg.get("scout", {}) or {}
        cap_per_pos = float(scout_cfg.get("capital_per_position_dollars", 25000))

        # Sizing: 1 contract per `cap_per_pos` of equity (matches the
        # scout's existing scaling). Floor at 1 contract.
        try:
            snap = await broker.snapshot()
            equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception:
            equity = 0.0
        max_contracts = max(1, int(equity / cap_per_pos)) if cap_per_pos > 0 else 1

        for idx, candidate in enumerate(rec.candidates):
            sym = candidate.symbol.upper()
            try:
                orders, skip_reason = await self._propose_open_pmcc(
                    sym, broker, max_contracts, analysis=None,
                )
            except Exception as e:
                log.warning(
                    "PMCCAgent research_on_demand: _propose_open_pmcc(%s) raised: %s",
                    sym, e,
                )
                self._audit_division("research_candidate_skipped", {
                    "engagement_id": rec.engagement_id,
                    "requesting_division": "robinhood_pmcc",
                    "symbol": sym,
                    "candidate_index": idx,
                    "fit_score": candidate.fit_score,
                    "conviction": candidate.conviction,
                    "reason": f"propose_open_exception: {e}",
                })
                continue

            if orders:
                all_orders.extend(orders)
                # Use the LEAP order's id as the proposed_order_id for the
                # audit-row join (the LEAP is the structural anchor).
                proposed_order_id = orders[0].id
                self._audit_division("research_candidate_acted_on", {
                    "engagement_id": rec.engagement_id,
                    "requesting_division": "robinhood_pmcc",
                    "symbol": sym,
                    "candidate_index": idx,
                    "fit_score": candidate.fit_score,
                    "conviction": candidate.conviction,
                    "proposed_order_id": proposed_order_id,
                })
            else:
                self._audit_division("research_candidate_skipped", {
                    "engagement_id": rec.engagement_id,
                    "requesting_division": "robinhood_pmcc",
                    "symbol": sym,
                    "candidate_index": idx,
                    "fit_score": candidate.fit_score,
                    "conviction": candidate.conviction,
                    "reason": skip_reason or "no_qualifying_chain",
                })

        log.info(
            "PMCCAgent research_on_demand: engagement %s → %d candidates → "
            "%d orders proposed",
            spec.engagement_id[:8], len(rec.candidates), len(all_orders),
        )
        return all_orders


# ── Phase 2 (2026-07-31): judgment-digest pure helpers ─────────────────────
# Snapshot + delta + full/delta/heartbeat builders, kept at MODULE scope (no
# `self`) so tests exercise them directly. compose_slot_digest() assembles the
# rows/snapshots; the scheduler slot handler in main.py captures the prior and
# persists the snapshot returned here.

PMCC_SLUG = "robinhood_pmcc"

_ACTION_LABEL = {
    "hold": "HOLD", "watch": "WATCH", "roll_short": "ROLL",
    "roll_short_early": "ROLL-EARLY", "roll_leap": "ROLL-LEAP",
    "close_short": "CLOSE", "open_short": "OPEN",
}


def format_action(action) -> str:
    a = (action or "none").lower()
    return _ACTION_LABEL.get(a, a.upper())


def mid_delta_from_record(rec) -> "float | None":
    """Midpoint of a stored judgment's delta band, or None when absent."""
    if not rec:
        return None
    lo, hi = rec.get("target_delta_low"), rec.get("target_delta_high")
    if lo is None or hi is None:
        return None
    try:
        return (float(lo) + float(hi)) / 2.0
    except (TypeError, ValueError):
        return None


def holding_snapshot(decision, estimate) -> dict:
    """Compact per-holding snapshot for delta comparison + persistence. `decision`
    is a load_decision() dict (or None); `estimate` is a PricedRoll.estimate (or None)."""
    return {
        "action": (decision.get("status") if decision else None),
        "mid_delta": mid_delta_from_record(decision),
        "target_dte": (decision.get("target_dte") if decision else None),
        "confidence": (decision.get("confidence") if decision else None),
        "net": (estimate.get("net") if estimate else None),
        "urgency": (decision.get("urgency") if decision else None),
        "warnings": (decision.get("warnings") if decision else []) or [],
    }


def digest_row(symbol, decision, priced) -> dict:
    """One holding's render row: symbol + action/conf/urgency (from the stored
    decision) + the live estimate (from the fresh price pull, only when buildable)."""
    est = None
    if priced is not None and getattr(priced, "buildable", False):
        est = priced.estimate
    return {
        "symbol": symbol,
        "action": (decision.get("status") if decision else "none"),
        "confidence": (decision.get("confidence") if decision else None),
        "urgency": (decision.get("urgency") if decision else None),
        "estimate": est,
        "warnings": (decision.get("warnings") if decision else []) or [],
    }


def _fmt_strike(strike) -> str:
    try:
        return f"{float(strike):g}"
    except (TypeError, ValueError):
        return str(strike)


def format_digest_line(row) -> str:
    """`SYM - ACTION - BTC $0.12 / STO $0.38 - net +$0.26 - new 185C - 82%`; the
    pricing segment is omitted when there is no buildable estimate (hold/watch). An
    urgent row carries the D1 deep-link."""
    sym = row["symbol"]
    parts = [sym, format_action(row.get("action"))]
    est = row.get("estimate")
    if est:
        seg = (
            f"BTC ${float(est.get('debit', 0) or 0):.2f} / "
            f"STO ${float(est.get('credit', 0) or 0):.2f}"
        )
        net = est.get("net")
        if net is not None:
            seg += f" - net {'+' if float(net) >= 0 else '-'}${abs(float(net)):.2f}"
        strike = est.get("open_strike")
        if strike is not None:
            seg += f" - new {_fmt_strike(strike)}C"
        parts.append(seg)
    conf = row.get("confidence")
    parts.append(f"{float(conf) * 100:.0f}%" if conf is not None else "--%")
    line = " - ".join(parts)
    if (row.get("urgency") or "").lower() == "urgent":
        line += f"  -> /division/{PMCC_SLUG}?pair={sym}"
    return line


def build_full_digest(header, rows) -> str:
    """Full slot digest: header + one compact line per holding (ALL holdings, no
    truncation — the caller sends via push_split so nothing is dropped)."""
    lines = [header]
    if not rows:
        lines.append("(no open PMCC holdings)")
    else:
        lines.extend(format_digest_line(r) for r in rows)
    return "\n".join(lines)


def build_delta_digest(slot_label, prev_slot_label, material, closed) -> str:
    """Material-changes-only digest, or a heartbeat when nothing is material.
    `material` = list of (row, reasons); `closed` = symbols gone since the prior slot
    (added holdings surface inside `material` via a None-prior 'added' reason)."""
    if not material and not closed:
        return f"{slot_label} - scan ran - no changes since {prev_slot_label}"
    lines = [f"PMCC {slot_label} - changes since {prev_slot_label}:"]
    for row, reasons in material:
        suffix = f"  [{', '.join(reasons)}]" if reasons else ""
        lines.append(format_digest_line(row) + suffix)
    for s in closed:
        lines.append(f"- {s} closed")
    return "\n".join(lines)


def judgment_delta(prior, new, thresholds) -> dict:
    """Is the change from `prior` to `new` MATERIAL? Returns {material, reasons}.
    Material iff: prior is None (added); action flip; a new earnings/assignment
    warning; |mid-delta| shift >= target_delta_shift; |target_dte| shift >=
    target_dte_shift; OR a pricing move >= net_move_dollars or >= net_move_pct of
    |prior net|. Confidence drift alone is NEVER material."""
    if prior is None:
        return {"material": True, "reasons": ["added"]}
    reasons: list[str] = []
    if (prior.get("action") or None) != (new.get("action") or None):
        reasons.append(f"action {prior.get('action')}->{new.get('action')}")
    pw = " ".join(prior.get("warnings") or []).lower()
    nw = " ".join(new.get("warnings") or []).lower()
    for kw in ("earning", "assign"):
        if kw in nw and kw not in pw:
            reasons.append(f"{kw} flag")
    pmd, nmd = prior.get("mid_delta"), new.get("mid_delta")
    if pmd is not None and nmd is not None and abs(float(nmd) - float(pmd)) >= (
        float(thresholds.get("target_delta_shift", 0.05)) - 1e-9   # float-boundary safe
    ):
        reasons.append(f"delta {float(pmd):.2f}->{float(nmd):.2f}")
    pdte, ndte = prior.get("target_dte"), new.get("target_dte")
    if pdte is not None and ndte is not None and abs(int(ndte) - int(pdte)) >= int(
        thresholds.get("target_dte_shift", 2)
    ):
        reasons.append(f"dte {int(pdte)}->{int(ndte)}")
    pnet, nnet = prior.get("net"), new.get("net")
    if pnet is not None and nnet is not None:
        move = abs(float(nnet) - float(pnet))
        pct = float(thresholds.get("net_move_pct", 0.20))
        if move >= (float(thresholds.get("net_move_dollars", 0.10)) - 1e-9) or (
            abs(float(pnet)) > 0 and move >= pct * abs(float(pnet)) - 1e-9
        ):
            reasons.append(f"net {float(pnet):+.2f}->{float(nnet):+.2f}")
    return {"material": bool(reasons), "reasons": reasons}


def _prior_combined(prior_decision, prior_snap) -> "dict | None":
    """Merge the delta baseline: action/band/DTE/conf/warnings from the prior stored
    DECISION (load_decision at slot start -> reflects a manual Re-analyze between
    slots); the prior NET from the last slot's persisted snapshot. None only when
    BOTH are absent (a brand-new holding -> 'added')."""
    if not prior_decision and not prior_snap:
        return None
    base = dict(prior_snap or {})
    if prior_decision:
        base["action"] = prior_decision.get("status")
        base["mid_delta"] = mid_delta_from_record(prior_decision)
        base["target_dte"] = prior_decision.get("target_dte")
        base["confidence"] = prior_decision.get("confidence")
        base["warnings"] = prior_decision.get("warnings") or []
    return base
