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
             'polymarket_order_rejected_by_risk'
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
        }
        # Polymarket-specific enrichment so the activity tile + right
        # rail can render rich content without a second DB hit. Full
        # payload (with full LLM reasoning text) is fetched via the
        # /partials/polymarket-analysis/{id} endpoint when the user
        # clicks "show analysis"; the truncated preview lives here.
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
