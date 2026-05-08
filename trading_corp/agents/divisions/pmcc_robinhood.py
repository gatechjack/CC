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
- Some symbols are designated BLACK SHEEP and follow special rules
  (perpetual roll, never accept assignment, halfway-roll on breach, trust
  mean reversion within 1-3 weekly cycles).

You have deep expertise in:
- Options Greeks (delta, theta, gamma, vega) and their evolution
- IV rank/percentile and premium-selling environments
- Rolling mechanics: standard, halfway, OTM, defensive
- Assignment risk on cash-constrained accounts
- Mean-reversion timing on high-IV names

The user message will tell you whether the position is BLACK SHEEP or STANDARD
and give you the specific rules that apply. Apply ONLY those rules — do not
mix the two regimes.

Respond ONLY with valid JSON. No markdown fences, no preamble, no explanation outside the JSON object.
"""

# Concise rule blocks injected into the user prompt
_BLACK_SHEEP_RULES = """\
## RULES: BLACK SHEEP — {symbol}
This symbol is designated BLACK SHEEP. Apply these rules INSTEAD of standard PMCC management:

1. PERPETUAL ROLL — never accept assignment, never close LEAP to fund a short buyback.
2. CASH NOT AVAILABLE for assignment. LEAP exercise is NOT permitted.
3. ROLL TRIGGER: 2 DTE (1 DTE if OTM). NEVER let a breached short run to expiry.
4. SHORT LEG: 7-DTE target, delta 0.20-0.35.
5. ROLLING PHILOSOPHY (high-IV mean reversion is the edge):
   - Always for credit. Always to a higher strike.
   - "Halfway roll" when stock has moved >3% above short strike:
       new strike = halfway between current short strike and underlying price.
       7 DTE preferred. If no credit available, extend DTE to 14 (max 21).
   - "Standard roll" when within 3% of strike: roll to slightly above
       underlying, 7 DTE, must be credit.
   - "OTM roll" at 50%+ profit: close at 70%, open new 7-DTE delta 0.20-0.30.
6. BREACH HANDLING:
   - Minor (0-3% above strike): standard roll at 2 DTE.
   - Major (3-10% above strike): halfway roll, must collect credit.
   - Runaway (10%+ above strike): halfway roll with 14-DTE extension.
   COOLDOWN (back-to-back halfway-roll guard): When ROLL HISTORY
   shows a recent halfway roll (positive strike_change >= $1 within
   `cooldown_days`, default 7) AND the current short_leg_dte > 2
   AND extrinsic > `extrinsic_floor` ($0.50/sh default) — choose
   `hold` directly. The expectation after a halfway roll is "collect
   theta + wait for whipsaw"; back-to-back halfway rolls in one
   weekly cycle compound slippage and lock in losses.
   Override `hold` and choose roll_short ONLY if the breach has
   ACCELERATED past the prior roll's projected range — concretely:
   spot now > prior_short_strike_after + |prior_strike_change|. (i.e.
   the underlying has moved at least as far again as the prior
   roll caught it, so the prior roll's "new range" is already
   breached.)
   BACKSTOP: deterministic Python guard (`_recent_halfway_roll_cooldown`)
   downgrades roll_short → hold when the cooldown conditions hold,
   regardless of LLM judgment. If the LLM picks roll_short despite
   the conditions, the guard rewrites the action and the
   recommendation card shows a HOLD with a cooldown warning — but
   the LLM's rationale text will read as "roll", which is
   confusing. So: HONOR the cooldown directly when ROLL HISTORY
   shows a recent halfway roll; let the backstop catch only the
   genuine acceleration-override-vs-cooldown edge cases.
   STRIKE TARGETING: when prescribing a halfway roll, ALSO populate
   `target_strike` in the JSON response with the computed midpoint
   (rounded to the nearest listed strike). Without `target_strike`
   the picker falls back to `target_delta` ranking, which on a
   high-IV underlying typically picks a strike well above the
   halfway midpoint and silently breaks the rule. Setting
   `target_strike` makes the strike picker honor the rule directly.
7. TERMINAL-DTE OVERRIDE (CRITICAL — applies before breach rules above):
   When the short has ≤2 DTE AND the underlying is within ±1.5% of the strike
   (the "ATM zone"), DEFAULT TO HOLD. The mark is almost entirely extrinsic
   premium that decays to zero by expiration. Rolling at this stage locks in
   theta you would otherwise collect for free.
   Override HOLD and roll/close ONLY if any are true:
     (a) Underlying is more than 1.5% above strike (genuine breach)
     (b) Overnight gap risk is unacceptable for this account size
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
8. FORBIDDEN ACTIONS (recommend "watch" or "roll_short_early" instead):
   - Buy back fully at a loss then resell OTM (locks in loss before MR whipsaw).
   - Close full PMCC to recover a short loss (abandons long thesis).
   - Accept assignment.
   - Roll for debit to chase OTM.
9. EXIT ONLY when: thesis explicitly broken, LEAP DTE < 90 with no credit roll
   available, fundamental deterioration, or 3 attempts yielded no credit.
   NOTE: a deterministic Python guard
   (`_promote_to_roll_leap_if_hard_rule`) promotes any roll_short →
   roll_leap when LEAP delta >= 0.95 OR long_leg_dte < 120, regardless
   of regime. For BLACK SHEEP this guard fires more aggressively than
   BS philosophy normally would (BS defers LEAP exit until DTE < 90
   with no credit roll). The guard's intent is to surface a 4-leg
   compound recommendation (close short + close LEAP + open new LEAP +
   open new short) so the user sees ALL the legs — they can still
   reject the LEAP roll and approve only the short roll if BS perpetual-
   roll philosophy applies. To AVOID the guard firing on a BS position
   you don't want to roll deep, choose `hold` or `watch` instead of
   `roll_short` until DTE crosses below the BS exit threshold.

For BLACK SHEEP: prefer "roll_short_early" or "roll_short" over "close_short".
Use "close_all" only as a last resort when exit conditions are met.
"""

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
    # open_short | watch | close_all
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
    is_black_sheep: bool

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


def _select_weekly_strike(
    calls: list[dict],
    target_delta: float = 0.30,
    target_strike: float | None = None,
) -> dict | None:
    """Pick weekly short strike.

    When `target_strike` is set: pick the listed strike CLOSEST to
    target_strike, regardless of delta. Used when a rule (e.g. halfway-
    roll on a Major Breach) prescribes a specific strike that the
    delta-only ranking would miss. Caller is responsible for sanity —
    we don't second-guess (the LLM cited the strike per its rules).

    When `target_strike` is None (default): pick the strike whose delta
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
    def _black_sheep_block(self) -> dict:
        return self._strategy_cfg.get("black_sheep", {}) or {}

    @property
    def _black_sheep_symbols(self) -> set[str]:
        entries = (self._black_sheep_block.get("symbols") or [])
        out: set[str] = set()
        for e in entries:
            sym = e.get("symbol") if isinstance(e, dict) else e
            if isinstance(sym, str):
                out.add(sym.upper())
        return out

    def is_black_sheep(self, symbol: str) -> bool:
        return symbol.upper() in self._black_sheep_symbols

    @property
    def _roll_dte(self) -> int:
        # Prefer strategies.yaml strategy.management.roll_dte_trigger
        v = self._management_cfg.get("roll_dte_trigger")
        if v is not None:
            return int(v)
        return int(self._pmcc_cfg.get("short_call_roll_dte", 21))

    def _roll_dte_for(self, leg: PMCCPosition) -> int:
        """Effective roll-DTE trigger for a leg (black sheep get tighter rule)."""
        if self.is_black_sheep(leg.symbol):
            rules = self._black_sheep_block.get("rolling_rules", {}) or {}
            return int(rules.get("roll_trigger_dte", 2))
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

    @property
    def _leap_min_delta(self) -> float:
        v = self._long_leg_cfg.get("delta_min")
        if v is not None:
            return float(v)
        return float(self._pmcc_cfg.get("long_call_min_delta", 0.80))

    @property
    def _leap_max_delta(self) -> float:
        v = self._long_leg_cfg.get("delta_max")
        if v is not None:
            return float(v)
        return 0.95

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

    def _passes_liquidity(self, opt: dict, *, symbol: str | None = None) -> tuple[bool, str]:
        """Return (passes, reason). Uses strategies.yaml liquidity gates.

        Black-sheep symbols use the tighter `eligibility_criteria` from the
        black_sheep block (min_avg_options_volume defaults to 10000) when
        available; otherwise the standard gate applies.
        """
        # Pick the right gate set
        is_bs = bool(symbol and self.is_black_sheep(symbol))
        if is_bs:
            elig = self._black_sheep_block.get("eligibility_criteria") or {}
            min_volume = int(elig.get("min_avg_options_volume", self._min_avg_volume))
        else:
            min_volume = self._min_avg_volume

        bid = float(opt.get("bid") or 0)
        ask = float(opt.get("ask") or 0)
        oi = int(opt.get("open_interest") or 0)
        vol = int(opt.get("volume") or 0)

        # Open interest
        if oi < self._min_open_interest:
            return False, f"OI={oi} < {self._min_open_interest}"

        # Volume
        if vol < min_volume:
            return False, f"vol={vol} < {min_volume}"

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

    def _blocked_by_earnings(self, symbol: str) -> tuple[bool, str]:
        """Return (blocked, reason) honoring `earnings_buffer_days`.

        None from yfinance is treated as "no data — don't block" rather than
        fail-safe-escalate, because thinly-traded names commonly lack earnings
        dates in yfinance and we don't want to silently kill the universe.
        """
        buffer_days = self._earnings_buffer_days
        if buffer_days <= 0:
            return False, ""

        from trading_corp.utils.market_data import get_next_earnings
        nxt = get_next_earnings(symbol)
        if nxt is None:
            return False, ""

        from datetime import datetime, timezone
        delta = nxt - datetime.now(timezone.utc)
        days = delta.days  # truncates toward negative; for future dates, this is days_until rounded down
        if 0 <= days <= buffer_days:
            return True, (
                f"earnings on {nxt.date().isoformat()} ({days}d away, "
                f"buffer={buffer_days}d)"
            )
        return False, ""

    def _filter_liquid(self, opts: list[dict], symbol: str) -> list[dict]:
        """Drop illiquid contracts. Logs each rejection at debug level."""
        out: list[dict] = []
        rejected = 0
        for o in opts:
            ok, reason = self._passes_liquidity(o, symbol=symbol)
            if ok:
                out.append(o)
            else:
                rejected += 1
                log.debug(
                    "PMCCAgent liquidity gate dropped %s C%.0f (%s): %s",
                    symbol, o.get("strike_price", 0), o.get("expiration_date"), reason,
                )
        if rejected:
            log.info(
                "PMCCAgent liquidity: %s — %d/%d contracts passed gate",
                symbol, len(out), len(opts),
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

        # Inject the rule block that applies to this position
        is_bs = self.is_black_sheep(pos.symbol)
        if is_bs:
            rules_block = _BLACK_SHEEP_RULES.format(symbol=pos.symbol)
            entry = next(
                (e for e in (self._black_sheep_block.get("symbols") or [])
                 if isinstance(e, dict) and e.get("symbol", "").upper() == pos.symbol.upper()),
                {},
            )
            bs_thesis = entry.get("rationale") or "core conviction; high-IV mean-reverts reliably"
            rules_block += f"\n## Black-sheep thesis for {pos.symbol}: {bs_thesis}\n"
        else:
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
- Designation: {"BLACK SHEEP" if is_bs else "STANDARD"}

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
  "action": "<hold|roll_short|roll_short_early|roll_leap|close_short|open_short|watch|close_all>",
  "confidence": <float 0.0-1.0>,
  "urgency": "<routine|elevated|urgent>",
  "summary": "<one clear sentence: situation + recommended action>",
  "rationale": "<2-4 sentences with specific Greek / IV / structural reasoning>",
  "warnings": ["<specific risk>", "<specific risk>"],
  "target_delta": <recommended short call delta as float, or null>,
  "target_dte": <recommended short call DTE target as integer, or null>,
  "target_strike": <recommended short call STRIKE as float, or null — set this when a rule prescribes a specific strike (e.g. halfway-roll midpoint per BREACH HANDLING). When set, the strike picker honors this directly, overriding delta-distance ranking. Leave null when delta-targeting is correct (standard cycles).>
}}

Action reference:
- hold: all criteria healthy, manage at next scheduled trigger
- roll_short: normal roll (BLACK SHEEP: <=2 DTE / STANDARD: <=2 DTE OR >=50% profit captured)
- roll_short_early: roll before normal trigger (defensive on a breached short, or
    halfway-roll for black sheep)
- roll_leap: LEAP needs to be rolled (delta drift below 0.40, DTE < 120, or strike compromised)
- close_short: close/buy-back the short call. AVOID for BLACK SHEEP unless OTM at expiry —
    prefer roll_short_early. For STANDARD: appropriate for assignment/earnings risk.
- open_short: LEAP is uncovered — sell a new weekly call
- watch: no action but flag for close monitoring next cycle
- close_all: STANDARD only. NEVER for black sheep unless exit conditions strictly met.
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
            return PMCCAnalysis(
                symbol=pos.symbol,
                action=str(data.get("action", "watch")),
                confidence=float(data.get("confidence", 0.5)),
                urgency=str(data.get("urgency", "routine")),
                summary=str(data.get("summary", "")),
                rationale=str(data.get("rationale", "")),
                warnings=list(data.get("warnings", []) or []),
                target_delta=float(data["target_delta"]) if data.get("target_delta") is not None else None,
                target_dte=int(data["target_dte"]) if data.get("target_dte") is not None else None,
                target_strike=float(data["target_strike"]) if data.get("target_strike") is not None else None,
            )
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
    ) -> list[ProposedOrder]:
        """Translate an LLM action recommendation into concrete ProposedOrders.

        Used by:
          - Dashboard's "Approve & Execute" button
          - Telegram's per-pair approval flow (Stage B)

        Reuses the existing _propose_* helpers so the rationale + sizing logic
        stays consistent with the scheduled scan path. Actions that need no
        order ('hold', 'watch') return [].
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
        analysis = self._terminal_dte_time_release(analysis, pos)
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
            return await self._propose_roll_short(symbol, pos, broker, analysis=analysis)

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
                mark_price=0.0,
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
            new_leap = await self._find_best_leap(symbol, broker)
            if new_leap:
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
                # 4. Open new short on the new LEAP (Item 2 — 2026-05-02).
                # Prevents the 3-leg compound from leaving the user with a
                # fresh LEAP and no income leg. Skipped if no qualifying
                # weekly chain — the next scan cycle will catch the
                # uncovered LEAP via the open_short branch.
                new_weekly = await self._find_best_weekly(
                    symbol, broker,
                    target_delta=analysis.target_delta if analysis else None,
                    target_dte=analysis.target_dte if analysis else None,
                    target_strike=analysis.target_strike if analysis else None,
                )
                if new_weekly:
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
                else:
                    log.info(
                        "PMCCAgent: roll_leap on %s — new LEAP selected but "
                        "no qualifying weekly for the income leg; the next "
                        "scan will cover the uncovered LEAP",
                        symbol,
                    )
            return orders

        # ── Close everything on this underlying ──
        if action == "close_all":
            orders: list[ProposedOrder] = []
            pair_id = str(uuid.uuid4())[:8]
            if pos.short_leg_expiry and pos.short_leg_strike is not None:
                orders.append(self._make_option_order(
                    underlying=symbol, side="buy", contracts=contracts,
                    expiry=pos.short_leg_expiry,
                    strike=pos.short_leg_strike,
                    mark_price=pos.short_leg_mark or 0.0,
                    position_effect="close",
                    action="close_short_urgent",
                    dte=pos.short_leg_dte,
                    rationale=self._build_rationale(
                        f"CLOSE ALL: short {symbol}", analysis,
                    ),
                    pair_id=pair_id,
                ))
            orders.append(self._make_option_order(
                underlying=symbol, side="sell", contracts=contracts,
                expiry=pos.long_leg_expiry,
                strike=pos.long_leg_strike,
                mark_price=0.0,
                position_effect="close",
                action="close_leap_urgent",
                dte=pos.long_leg_dte,
                rationale=self._build_rationale(
                    f"CLOSE ALL: LEAP {symbol}", analysis,
                ),
                pair_id=pair_id,
            ))
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
    ) -> TradeRecommendation | None:
        """Build a concrete TradeRecommendation (dollar-priced legs + benefits).

        Used by the dashboard's expert-analysis panel to show specifically what
        will happen if the user clicks Approve & Execute. Returns None for
        actions that don't require any orders (hold/watch).
        """
        action = (analysis.action or "").lower()
        if action in ("", "hold", "watch"):
            return None

        # Reuse the existing order-proposal logic. These ProposedOrders carry
        # mark_per_share / bid / ask / delta / dte in extra (we just made them).
        orders = await self.propose_orders_for_pair(broker, symbol, analysis)
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
            "roll_short", "roll_short_early", "close_short",
            "roll_leap", "close_all",
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

        elif action == "close_all":
            b.append("Closes both legs — no further premium accrual")
            if existing:
                cost_basis_leap = existing.long_leg_avg_price * abs(existing.long_leg_qty)
                b.append(
                    f"Original LEAP cost basis: ${cost_basis_leap:,.2f} "
                    "— compare to expected close proceeds for realized P&L"
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
            tag = " 🐑 _BLACK SHEEP_" if self.is_black_sheep(pos.symbol) else ""
            lines.append(f"**{pos.symbol}** @ {price_str}{tag}")

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
    ) -> list[ProposedOrder]:
        """Scan existing PMCC legs; propose rolls / new setups.

        LLM expert analysis enriches every order rationale and can surface
        additional actions beyond the deterministic roll rules:
          - roll_leap: LEAP delta has drifted, needs to be rolled
          - roll_short_early: opportunistic early roll before DTE/profit trigger
          - close_short (urgent): ITM short call requiring immediate attention
          - close_all (urgent): structural failure, close full position
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
        legs_by_symbol: dict[str, PMCCPosition] = {leg.symbol: leg for leg in existing}

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
                a = self._terminal_dte_time_release(
                    analyses[sym], legs_by_symbol[sym],
                )
                a = self._promote_to_roll_leap_if_hard_rule(
                    a, legs_by_symbol[sym],
                )
                a = self._recent_halfway_roll_cooldown(
                    a, legs_by_symbol[sym],
                )
                analyses[sym] = a

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

                    if analysis.action == "close_all":
                        log.warning("PMCCAgent: URGENT close_all for %s: %s", symbol, analysis.summary)
                        pair_id = str(uuid.uuid4())[:8]
                        if leg.short_leg_expiry:
                            orders.append(self._make_option_order(
                                underlying=symbol, side="buy", contracts=contracts,
                                expiry=leg.short_leg_expiry,
                                strike=leg.short_leg_strike or 0.0,
                                mark_price=leg.short_leg_mark or 0.0,
                                position_effect="close",
                                action="close_short_urgent",
                                dte=leg.short_leg_dte,
                                rationale=self._build_rationale(
                                    f"CLOSE ALL (1/2): close short {symbol} "
                                    f"{leg.short_leg_expiry} C{leg.short_leg_strike:.2f}",
                                    analysis,
                                ),
                                pair_id=pair_id,
                            ))
                        orders.append(self._make_option_order(
                            underlying=symbol, side="sell", contracts=contracts,
                            expiry=leg.long_leg_expiry,
                            strike=leg.long_leg_strike,
                            mark_price=0.0,   # execution agent will price it
                            position_effect="close",
                            action="close_leap_urgent",
                            dte=leg.long_leg_dte,
                            rationale=self._build_rationale(
                                f"CLOSE ALL (2/2): sell LEAP {symbol} "
                                f"{leg.long_leg_expiry} C{leg.long_leg_strike:.2f}",
                                analysis,
                            ),
                            pair_id=pair_id,
                        ))
                        continue

                # ── LLM-detected roll_leap (not in deterministic rules) ──
                if analysis and analysis.action == "roll_leap":
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
                        mark_price=0.0,
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
                    new_leap = await self._find_best_leap(symbol, broker)
                    if new_leap:
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
                        # Step 4: sell new short on the fresh LEAP (Item 2 —
                        # 2026-05-02). Skipped if no qualifying weekly chain;
                        # next scan cycle covers the uncovered LEAP.
                        new_weekly = await self._find_best_weekly(
                            symbol, broker,
                            target_delta=(
                                analysis.target_delta if analysis else None
                            ),
                            target_dte=(
                                analysis.target_dte if analysis else None
                            ),
                            target_strike=(
                                analysis.target_strike if analysis else None
                            ),
                        )
                        if new_weekly:
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
                        else:
                            log.info(
                                "PMCCAgent: roll_leap scan on %s — new LEAP "
                                "selected but no qualifying weekly for the "
                                "income leg; next scan will cover the "
                                "uncovered LEAP",
                                symbol,
                            )
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
                elif self._should_roll(leg):
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
        # Black sheep use a tighter (typically 2-DTE) trigger; standard uses
        # the global setting (defaults to 21, but strategies.yaml now overrides).
        roll_dte = self._roll_dte_for(leg)
        if leg.short_leg_dte is not None and leg.short_leg_dte <= roll_dte:
            return True
        if leg.short_leg_pnl_pct is not None and leg.short_leg_pnl_pct >= self._roll_profit_pct:
            return True
        return False

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
        """
        import dataclasses
        from trading_corp.utils.time import ET, now_et as _now_et

        if analysis is None or leg is None:
            return analysis
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

        leap_call = await self._find_best_leap(symbol, broker)
        weekly_call = await self._find_best_weekly(
            symbol, broker,
            target_delta=analysis.target_delta if analysis else None,
            target_dte=analysis.target_dte if analysis else None,
            target_strike=analysis.target_strike if analysis else None,
        )

        orders: list[ProposedOrder] = []
        pair_id = str(uuid.uuid4())[:8]

        if leap_call:
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
        else:
            log.warning("PMCCAgent: no qualifying LEAP found for %s", symbol)

        if weekly_call:
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
        else:
            log.warning("PMCCAgent: no qualifying weekly call found for %s", symbol)

        # Skip-reason for empty / partial results. If at least one leg
        # produced an order we still return None — the caller can check
        # `len(orders) < 2` if it needs to know about partial fills, but
        # for research-on-demand auditing "we got at least one leg" counts
        # as acted_on, not skipped.
        skip_reason: str | None = None
        if not orders:
            if leap_call is None:
                skip_reason = "leap_unavailable"
            elif weekly_call is None:
                skip_reason = "weekly_unavailable"
            else:
                skip_reason = "no_qualifying_chain"
        return orders, skip_reason

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
    ) -> list[ProposedOrder]:
        """Roll short call: buy-to-close existing + sell-to-open new weekly."""
        if not leg.short_leg_expiry or leg.short_leg_strike is None:
            return []

        orders: list[ProposedOrder] = []
        pair_id = str(uuid.uuid4())[:8]
        contracts = max(1, int(abs(leg.short_leg_qty or 1)))
        close_mark = leg.short_leg_mark or 0.0
        pnl_pct = (leg.short_leg_pnl_pct or 0) * 100

        roll_reason = (
            f"DTE={leg.short_leg_dte}" if (leg.short_leg_dte or 0) <= self._roll_dte
            else f"profit={pnl_pct:.0f}%"
        )

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

        new_weekly = await self._find_best_weekly(
            symbol, broker,
            target_delta=analysis.target_delta if analysis else None,
            target_dte=analysis.target_dte if analysis else None,
            target_strike=analysis.target_strike if analysis else None,
        )
        if new_weekly:
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
        else:
            log.warning("PMCCAgent: no new weekly found for roll on %s", symbol)

        return orders

    # -- Option chain queries ------------------------------------------------

    async def _find_best_leap(self, symbol: str, broker: Broker) -> dict | None:
        """Find the best LEAP call: DTE >= leap_min_dte, delta >= leap_min_delta,
        passes liquidity gate."""
        if not isinstance(broker, OptionBroker):
            return None
        dates = await broker.get_expiration_dates(symbol)
        leap_dates = [d for d in dates if _days_to(d) >= self._leap_min_dte]
        if not leap_dates:
            log.warning("PMCCAgent: no expiry dates >= %d days for %s", self._leap_min_dte, symbol)
            return None
        target_date = leap_dates[0]
        calls = await broker.get_calls_for_expiry(symbol, target_date)
        liquid = self._filter_liquid(calls, symbol)
        if not liquid:
            log.warning(
                "PMCCAgent: no liquid LEAP contracts for %s on %s "
                "(%d candidates, all failed liquidity gate)",
                symbol, target_date, len(calls),
            )
            return None
        best = _select_leap_strike(liquid)
        if best:
            best["expiration_date"] = target_date
            best["dte"] = _days_to(target_date)
        return best

    async def _find_best_weekly(
        self,
        symbol: str,
        broker: Broker,
        target_delta: float | None = None,
        target_dte: int | None = None,
        target_strike: float | None = None,
    ) -> dict | None:
        """Find the best weekly short call, optionally using LLM-suggested
        delta / DTE / strike.

        `target_strike` (Item 3 — 2026-05-03): when set, the strike picker
        selects the listed strike CLOSEST to target_strike (subject to
        liquidity gate), overriding the delta-distance ranking. Used when
        a rule (e.g. Major Breach halfway-roll) prescribes a specific
        strike that the delta-only picker would miss. None = original
        delta-distance behavior."""
        if not isinstance(broker, OptionBroker):
            return None
        dates = await broker.get_expiration_dates(symbol)

        # Use LLM-suggested DTE window if provided, otherwise default 7–21d range
        if target_dte is not None:
            dte_lo = max(3, target_dte - 7)
            dte_hi = target_dte + 14
            weekly_dates = [d for d in dates if dte_lo <= _days_to(d) <= dte_hi]
        else:
            weekly_dates = [
                d for d in dates if _WEEKLY_MIN_DTE <= _days_to(d) <= _WEEKLY_MAX_DTE
            ]

        if not weekly_dates:
            future = [d for d in dates if _days_to(d) > 0]
            if not future:
                log.warning("PMCCAgent: no future expiry dates for %s", symbol)
                return None
            weekly_dates = [future[0]]

        target_date = weekly_dates[0]
        calls = await broker.get_calls_for_expiry(symbol, target_date)
        liquid = self._filter_liquid(calls, symbol)
        if not liquid:
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
        best = _select_weekly_strike(liquid, delta, target_strike=target_strike)
        if best:
            best["expiration_date"] = target_date
            best["dte"] = _days_to(target_date)
        return best

    # -- ProposedOrder factory -----------------------------------------------

    def _make_option_order(
        self,
        underlying: str,
        side: str,
        contracts: int,
        expiry: str,
        strike: float,
        mark_price: float,
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

        return ProposedOrder(
            strategy="robinhood_pmcc",
            symbol=underlying,
            side=side,                    # type: ignore[arg-type]
            qty=float(contracts),
            order_type="limit",
            limit_price=round(mark_price, 2),
            rationale=rationale,
            extra=extra,
        )

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
        if self.is_black_sheep(symbol):
            notes.append("Black Sheep — high-IV, perpetual-roll regime")
        if leap and (leap.get("delta") or 0) >= 0.85:
            notes.append("Deep ITM LEAP (delta ≥ 0.85)")
        if weekly_yield is not None and weekly_yield >= 0.025:
            notes.append(f"Strong weekly yield: {weekly_yield*100:.2f}%")
        if leap_leg and short_leg and short_leg.strike > leap_leg.strike:
            notes.append("Short strike above LEAP strike — full vertical capture")

        return ScoutCandidate(
            symbol=symbol,
            spot_price=spot,
            is_black_sheep=self.is_black_sheep(symbol),
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
        if c.is_black_sheep:
            score -= float(weights.get("black_sheep_penalty", 0.15))
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
