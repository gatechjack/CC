"""Per-whale audit for the Polymarket watch-list review phase.

Deterministic computation core for the on-demand "Analyze Whale" feature.
Generalizes the Magamyman hand-proof (2026-05-26 session) into a reusable
module: decision clustering, sell footprint, edge profile, category
concentration, and — most importantly — REDEEM-grounded realized PnL
that distinguishes held-to-resolution conviction from churn / partial
exits / round-trip inflation.

This module performs NO I/O, NO LLM calls, and NO DB writes. Inputs are
the same `ActivityRow` + `market_resolutions` shapes the watchlist seed
already consumes. The CLI / web-route layer fetches activity, calls
`build_audit_report`, optionally hands the result to `WhaleAnalyst.narrate`
for a plain-language verdict, and (in dashboard mode) caches the report
keyed on `(wallet, activity_max_ts)` so re-analysis with no new fills is
free.

Why this exists separately from `polymarket_whale_stats.py`:
  `compute_polymarket_stats` was designed for the windowed *screening*
  pass — it sums per-fill PnL on BUY rows and explicitly assumes
  held-to-resolution. That assumption is fine for the screening floor
  (you can't promote on inflated PnL anyway since promotion is paused
  until the screen is right), but it breaks at REVIEW time: an operator
  picking from PnL-sorted rows wants to know which numbers are real
  cash and which are "PnL IF the whale had held instead of partially
  selling." This module answers that.

Realized-PnL ground truth — REDEEM-grounded:

  Polymarket's `/activity` feed surfaces REDEEM events at sentinel
  `outcomeIndex=999`, carrying `size = usdcSize = held_quantity` in
  USDC paid out (since winning contracts settle at $1.00). The REDEEM
  ONLY appears for the WINNING side; losing-side held positions settle
  to $0 and emit no row. Verified 2026-05-26 against Magamyman:

    US strikes Iran by Feb 28 — winning side (oi=0):
      BUYs:   861,154.15 (68 fills, wavg $0.211)
      SELLs:  570,098.04 (4 fills, exited 66% pre-resolution)
      REDEEM (cid, 999): 291,056.11   <- IDENTICAL to BUY-SELL = held qty
      Aggregated held-to-resolution PnL ≈ $679k (the watchlist figure)
      REDEEM-grounded realized PnL ≈ (SELL_usdc + 291,056) - BUY_usdc
                                   = much less than $679k once SELL price
                                     is factored in

  For each `(cid, oi)` decision we therefore compute:

    is_winning_side = (oi == winner_idx)
    sum_buy_usdc    = Σ buy_size * buy_price        (cost basis)
    sum_sell_usdc   = Σ sell_size * sell_price      (exit proceeds)
    if is_winning_side:
        redeem_payout = REDEEM row's size at (cid, 999), or 0.0 if no row
    else:
        redeem_payout = 0.0                          (losing side settles to $0)
    realized_pnl_decision = sum_sell_usdc + redeem_payout - sum_buy_usdc

  This is the differentiator vs the watchlist's `compute_polymarket_stats`,
  which computes `Σ (1-price) * size if win else -price * size` over BUYs
  — equivalent to "PnL if every bought contract had held to resolution."

Round-trip vs partial-sell composition (no-gap guarantee):

  `sell_share = sum_sell_size / sum_buy_size`

  Round-trip flag:   sell_share >= 0.95            (effectively fully exited)
  Partial-sell flag: sell_share >= 0.20            (catches everything 20%+,
                                                    INCLUDING round-trips)

  Round-trip is a strict subset of partial-sell. The 20% threshold is the
  inclusive cutoff; the 95% threshold is the "look really hard, this is
  essentially closed" subset cue. A 95%-sold-5%-held position registers
  as BOTH partial-sell (yes, 95% > 20%) and round-trip (yes, 95% ≥ 95%) —
  so the operator cannot miss inflation by an off-by-one categorization.

  Aggregate signal: `pnl_inflation_ratio` measures how much the held-to-
  resolution PnL would have to be reduced to match REDEEM-grounded
  realized PnL. Catches round-trips, partial-sells, AND small leakage
  uniformly:

    hold_to_resolution_pnl_attribution = Σ_decisions (1-wavg)*total_size_if_win
                                                   else -wavg*total_size
    pnl_inflation_ratio = (hold_to_resolution_pnl - realized_pnl)
                        / max(hold_to_resolution_pnl, 1.0)

  A whale with > 0.5 inflation ratio is one whose headline PnL is
  largely paper / round-trip churn, not held conviction.

References:
  - `polymarket_data_api_client.py:127` (ActivityRow dataclass)
  - `polymarket_data_api_client.py:470` (fetch_market_resolutions)
  - `polymarket_whale_stats.py:135` (the per-fill PnL formula this
    module replaces for review purposes)
  - `agents/risk.py:437` (RiskAgent.narrate — the deterministic-then-
    narrate pattern the LLM narrator mirrors)
  - `reports/2026-05-26_polymarket_pnl_aggregation_fix_plan.md`
    (background on the PnL-aggregation fix that closed the WATCHLIST
    side; this module closes the REVIEW side)
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from trading_corp.data.polymarket_data_api_client import (
    ActivityRow,
    LeaderboardEntry,
)

log = logging.getLogger(__name__)


# Sentinel `outcomeIndex` Polymarket uses for REDEEM rows. The actual
# outcome the trader bet on (0 / 1) lives on the corresponding BUY rows
# — REDEEM is a market-level redemption record, not a per-side trade.
REDEEM_OUTCOME_INDEX_SENTINEL = 999

# Default thresholds. The module exposes them as parameters so the CLI
# can tune; the dataclass instance carries the threshold used so the
# narrator can cite it verbatim.
DEFAULT_PARTIAL_SELL_THRESHOLD = 0.20
ROUND_TRIP_SELL_SHARE_THRESHOLD = 0.95
FAVORITE_FARMING_PRICE_THRESHOLD = 0.85
SUB_70_PRICE_THRESHOLD = 0.70

# Inclusion of decisions in the per-decision computation requires both
# (a) a `(cid, oi)` BUY-side cluster AND (b) a resolved gamma resolution.
# We DO NOT compute on unresolved markets — the WR / PnL math is
# meaningless without a winner_idx.


# ── Dataclasses ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionKey:
    """One decision = one `(condition_id, outcome_index)` pair.

    `title` is denormalized from the first non-empty `ActivityRow.title`
    in the cluster — used by the narrator to refer to the decision by a
    human-readable name. NOT used in any computation.
    """
    condition_id: str
    outcome_index: int
    title: str = ""


@dataclass(frozen=True)
class DecisionFills:
    """All fills (BUY + SELL) for one decision, plus the (cid, 999)
    REDEEM row's size when present.

    `redeem_payout_usdc` is the USDC paid out at resolution for this
    decision IF the decision's outcome won; else 0.0. It is read from
    the REDEEM row at `(cid, REDEEM_OUTCOME_INDEX_SENTINEL)` — the row
    represents the market's settlement, not a per-side event, so we
    attribute it only to the winning side.
    """
    key: DecisionKey
    buy_rows: tuple[ActivityRow, ...]
    sell_rows: tuple[ActivityRow, ...]
    redeem_payout_usdc: float
    is_resolved: bool
    is_winning_side: bool

    @property
    def sum_buy_size(self) -> float:
        return sum(r.size for r in self.buy_rows)

    @property
    def sum_sell_size(self) -> float:
        return sum(r.size for r in self.sell_rows)

    @property
    def sum_buy_usdc(self) -> float:
        return sum(r.usdc_size for r in self.buy_rows)

    @property
    def sum_sell_usdc(self) -> float:
        return sum(r.usdc_size for r in self.sell_rows)

    @property
    def sell_share(self) -> float:
        """sum_sell_size / sum_buy_size, in [0, 1+]. >1 means the whale
        sold more than they bought (can happen if they entered via SELL
        on the opposite side — uncommon but handled). Returns 0 on
        zero-buy degenerate."""
        b = self.sum_buy_size
        return self.sum_sell_size / b if b > 0 else 0.0

    @property
    def weighted_avg_buy_price(self) -> float:
        b = self.sum_buy_size
        if b <= 0:
            return 0.0
        return sum(r.price * r.size for r in self.buy_rows) / b

    @property
    def held_to_resolution_pnl(self) -> float:
        """Watchlist-style PnL: assumes the FULL bought position settled
        at the resolution price. (1-wavg)*total_size if win else -wavg*total_size."""
        if not self.is_resolved:
            return 0.0
        wavg = self.weighted_avg_buy_price
        total = self.sum_buy_size
        return (1.0 - wavg) * total if self.is_winning_side else -wavg * total

    @property
    def realized_pnl(self) -> float:
        """REDEEM-grounded realized PnL: sell proceeds + redemption payout
        minus cost basis. For winning-side held quantities, the REDEEM row
        is the ground-truth held qty (in USDC, $1/contract for winners)."""
        if not self.is_resolved:
            return 0.0
        return self.sum_sell_usdc + self.redeem_payout_usdc - self.sum_buy_usdc

    @property
    def is_round_trip(self) -> bool:
        """Sell share ≥ 0.95 — essentially fully exited pre-resolution.
        STRICT SUBSET of partial-sell flag."""
        return self.sell_share >= ROUND_TRIP_SELL_SHARE_THRESHOLD

    def is_partial_sell(self, threshold: float = DEFAULT_PARTIAL_SELL_THRESHOLD) -> bool:
        """Sell share ≥ threshold (default 0.20). Catches everything
        from 20% sold all the way to 100% — round-trips are INCLUDED in
        this flag (no gap with the round-trip subset)."""
        return self.sell_share >= threshold


@dataclass(frozen=True)
class ClusteringReport:
    n_raw_fills: int
    n_decisions: int
    clustering_ratio: float
    decisions_with_ge_5_fills: int
    top_clusters_by_fill_count: tuple[tuple[str, int, int], ...] = ()
    """Top 5 clusters as (cid_short, outcome_index, n_fills)."""


@dataclass(frozen=True)
class FlaggedDecision:
    """One decision worth surfacing in the audit output."""
    title: str
    condition_id_short: str
    outcome_index: int
    sum_buy_usdc: float
    sum_sell_usdc: float
    redeem_payout_usdc: float
    sell_share: float
    is_round_trip: bool
    is_winning_side: bool
    realized_pnl: float
    held_to_resolution_pnl: float


@dataclass(frozen=True)
class SellFootprintReport:
    n_decisions_total: int
    n_decisions_with_sells: int
    n_round_trips: int
    """Strict subset: sell_share ≥ 0.95."""
    n_partial_sells: int
    """Broader flag: sell_share ≥ partial_sell_threshold (default 0.20).
    INCLUDES round-trips — no gap with the strict subset."""
    partial_sell_threshold: float
    n_held_cleanly: int
    """Decisions with sell_share < partial_sell_threshold (clean holds)."""
    top_flagged_by_inflation_usdc: tuple[FlaggedDecision, ...]
    """Top 5 decisions ranked by `held_to_resolution_pnl - realized_pnl`
    in USDC — the decisions that contribute most to inflation."""


@dataclass(frozen=True)
class EdgeProfileReport:
    n_decisions: int
    avg_entry_price_decision_weighted: float
    """Mean of size-weighted-avg-prices across resolved decisions."""
    share_below_70: float
    share_above_85: float
    """Above $0.85 = favorite-farming signal."""
    p25_entry: float
    p50_entry: float
    p75_entry: float


@dataclass(frozen=True)
class CategoryConcentrationReport:
    n_distinct_event_slugs: int
    """Polymarket's `eventSlug` field: one "event" can contain many
    `condition_id`s (e.g. a playoff series with separate spread + ML +
    O/U markets). A whale whose 50 decisions span 3 events is highly
    concentrated even if 50 decisions sounds diverse."""
    top_3_event_slugs: tuple[tuple[str, int], ...]
    largest_event_share: float
    """n_decisions on the most-common event / n_decisions total. If
    > 0.5, the whale's track record is dominated by one event."""


@dataclass(frozen=True)
class RealizedPnLReport:
    realized_pnl_usdc: float
    """Σ realized_pnl across all resolved decisions in the window."""
    held_to_resolution_pnl_usdc: float
    """Σ held-to-resolution PnL across all resolved decisions — what the
    watchlist would have reported for this whale on the same window."""
    pnl_inflation_usdc: float
    """held_to_resolution_pnl - realized_pnl. The dollars of PnL that
    are NOT real cash flow — they would have been earned only if the
    whale had held instead of partially selling."""
    pnl_inflation_ratio: float
    """pnl_inflation_usdc / max(held_to_resolution_pnl_usdc, 1.0).
    > 0.5 → most of the headline PnL is paper / churn / unrealized.
    Catches round-trips, partial-sells, AND small leakage uniformly."""
    pnl_from_clean_holds_usdc: float
    """Realized PnL contribution from decisions with sell_share <
    partial_sell_threshold. The "genuinely held conviction" portion."""
    pnl_from_partial_sells_usdc: float
    """Realized PnL contribution from decisions flagged as partial-sell
    (includes round-trips per the no-gap composition)."""


@dataclass(frozen=True)
class WhaleAuditReport:
    proxy_wallet: str
    user_name: str
    """Polymarket `name` field — empty if Polymarket has no record."""
    activity_max_ts: int
    """Most-recent activity row's timestamp (unix sec). USED AS CACHE
    KEY — re-analyzing the same whale with no new fills is a hit."""
    activity_min_ts: int
    n_raw_rows_examined: int
    n_resolved_decisions: int
    clustering: ClusteringReport
    sell_footprint: SellFootprintReport
    edge: EdgeProfileReport
    category: CategoryConcentrationReport
    realized_pnl: RealizedPnLReport
    partial_sell_threshold_used: float
    verdict_narration: str | None = None
    verdict_null_reason: str | None = None
    """When `verdict_narration` is None, this carries the WHY. One of:
    'daily_cap_hit', 'llm_unavailable', 'disabled_by_flag', 'llm_error'."""
    llm_cost_usd: float = 0.0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0


# ── Pure functions ───────────────────────────────────────────────────────


def group_fills_by_decision(
    rows: list[ActivityRow],
    resolutions: dict[str, dict[str, Any]],
) -> dict[tuple[str, int], DecisionFills]:
    """Bucket raw activity into per-decision DecisionFills.

    REDEEM rows live at sentinel `(cid, REDEEM_OUTCOME_INDEX_SENTINEL)`
    and are extracted separately — their `size` is read as the
    redemption USDC payout for the WINNING side. The (cid, 999) row is
    market-level, not per-side, so we attribute it only to the decision
    whose `outcome_index` matches the resolved `winner_idx`. Losing-side
    decisions get redeem_payout_usdc=0.0 regardless.

    Unresolved markets are still included in the output (with
    is_resolved=False) so callers can inspect them, but the headline
    computations (`SellFootprintReport`, `RealizedPnLReport`,
    `EdgeProfileReport`) only run over the resolved subset.
    """
    # Bucket BUY/SELL by (cid, oi); collect REDEEM at (cid, 999) separately.
    buys_by_key: dict[tuple[str, int], list[ActivityRow]] = defaultdict(list)
    sells_by_key: dict[tuple[str, int], list[ActivityRow]] = defaultdict(list)
    redeem_size_by_cid: dict[str, float] = defaultdict(float)
    title_by_cid: dict[str, str] = {}

    for r in rows:
        cid = r.condition_id
        if not cid:
            continue
        try:
            oi = int(r.outcome_index)
        except (TypeError, ValueError):
            continue
        if not title_by_cid.get(cid) and r.title:
            title_by_cid[cid] = r.title

        if r.type == "REDEEM":
            # Polymarket emits REDEEM at sentinel oi=999. Treat any
            # REDEEM as market-level even if it happens to land on a
            # real oi — defensive against schema drift.
            redeem_size_by_cid[cid] += r.size
            continue
        if r.type != "TRADE":
            continue
        if r.side == "BUY":
            buys_by_key[(cid, oi)].append(r)
        elif r.side == "SELL":
            sells_by_key[(cid, oi)].append(r)

    # Compose DecisionFills. Iterate over the union of (cid, oi) keys
    # seen in BUY or SELL — REDEEM-only "decisions" without a BUY are
    # not real decisions to audit (could be from a position acquired via
    # SELL elsewhere; rare; not in scope for the audit).
    all_keys = set(buys_by_key.keys()) | set(sells_by_key.keys())
    out: dict[tuple[str, int], DecisionFills] = {}
    for (cid, oi) in all_keys:
        res = resolutions.get(cid, {})
        is_resolved = (res.get("status") or "").lower() == "resolved"
        winner_idx = res.get("winning_outcome_index")
        try:
            winner_idx_int = int(winner_idx) if winner_idx is not None else None
        except (TypeError, ValueError):
            winner_idx_int = None
        is_winning_side = is_resolved and winner_idx_int is not None and oi == winner_idx_int
        # REDEEM payout attributed only to the winning side. Losing-side
        # decisions get 0.0 (their contracts settle to $0).
        redeem_payout = redeem_size_by_cid.get(cid, 0.0) if is_winning_side else 0.0
        out[(cid, oi)] = DecisionFills(
            key=DecisionKey(
                condition_id=cid,
                outcome_index=oi,
                title=title_by_cid.get(cid, ""),
            ),
            buy_rows=tuple(buys_by_key.get((cid, oi), [])),
            sell_rows=tuple(sells_by_key.get((cid, oi), [])),
            redeem_payout_usdc=redeem_payout,
            is_resolved=is_resolved,
            is_winning_side=is_winning_side,
        )
    return out


def compute_clustering(
    rows: list[ActivityRow],
    decisions: dict[tuple[str, int], DecisionFills],
) -> ClusteringReport:
    """Decision-vs-fill ratio across resolved decisions.

    `n_raw_fills` counts BUY+TRADE rows (the screening unit). `n_decisions`
    counts resolved-only `(cid, oi)` pairs (the audit unit). A clustering
    ratio >> 1 means the screening unit has been over-counting cluster
    fills as independent samples — the bug the 2026-05-25 plan + 2026-05-26
    deploys fixed at the watchlist seed level.
    """
    n_raw_fills = sum(
        1 for r in rows
        if r.type == "TRADE" and r.side == "BUY" and r.condition_id
    )
    resolved_decisions = [d for d in decisions.values() if d.is_resolved]
    n_decisions = len(resolved_decisions)
    ratio = (n_raw_fills / n_decisions) if n_decisions > 0 else 0.0
    decisions_with_ge_5_fills = sum(
        1 for d in resolved_decisions if len(d.buy_rows) >= 5
    )
    # Top 5 clusters by buy-fill count for the narrator
    sorted_clusters = sorted(
        resolved_decisions, key=lambda d: -len(d.buy_rows),
    )[:5]
    top_clusters = tuple(
        (d.key.condition_id[:18], d.key.outcome_index, len(d.buy_rows))
        for d in sorted_clusters
    )
    return ClusteringReport(
        n_raw_fills=n_raw_fills,
        n_decisions=n_decisions,
        clustering_ratio=round(ratio, 2),
        decisions_with_ge_5_fills=decisions_with_ge_5_fills,
        top_clusters_by_fill_count=top_clusters,
    )


def compute_sell_footprint(
    decisions: dict[tuple[str, int], DecisionFills],
    *,
    partial_sell_threshold: float = DEFAULT_PARTIAL_SELL_THRESHOLD,
) -> SellFootprintReport:
    """Round-trip + partial-sell flags + the top flagged decisions.

    Round-trip is sell_share ≥ 0.95; partial-sell is sell_share ≥
    `partial_sell_threshold` (default 0.20). The round-trip flag is a
    STRICT SUBSET of partial-sell — every round-trip is also a
    partial-sell. The composition has no gap; a 95%-sold-5%-held
    position registers under both, and the operator cannot miss
    inflation by category boundary.

    Top flagged ranked by `held_to_resolution_pnl - realized_pnl` USDC
    (the inflation contribution), capped at 5 entries.
    """
    resolved = [d for d in decisions.values() if d.is_resolved]
    n_total = len(resolved)
    n_with_sells = sum(1 for d in resolved if d.sum_sell_size > 0)
    n_round_trips = sum(1 for d in resolved if d.is_round_trip)
    n_partial = sum(1 for d in resolved if d.is_partial_sell(partial_sell_threshold))
    n_clean = n_total - n_partial  # held cleanly = NOT partial-sell

    # Rank by inflation contribution; cap at 5
    by_inflation = sorted(
        resolved,
        key=lambda d: -(d.held_to_resolution_pnl - d.realized_pnl),
    )
    top_5 = tuple(
        FlaggedDecision(
            title=d.key.title[:60],
            condition_id_short=d.key.condition_id[:18],
            outcome_index=d.key.outcome_index,
            sum_buy_usdc=round(d.sum_buy_usdc, 2),
            sum_sell_usdc=round(d.sum_sell_usdc, 2),
            redeem_payout_usdc=round(d.redeem_payout_usdc, 2),
            sell_share=round(d.sell_share, 4),
            is_round_trip=d.is_round_trip,
            is_winning_side=d.is_winning_side,
            realized_pnl=round(d.realized_pnl, 2),
            held_to_resolution_pnl=round(d.held_to_resolution_pnl, 2),
        )
        for d in by_inflation[:5]
        # Only surface decisions with actual inflation gap > $1 to avoid
        # noise from rounding on tiny decisions
        if (d.held_to_resolution_pnl - d.realized_pnl) > 1.0
    )
    return SellFootprintReport(
        n_decisions_total=n_total,
        n_decisions_with_sells=n_with_sells,
        n_round_trips=n_round_trips,
        n_partial_sells=n_partial,
        partial_sell_threshold=partial_sell_threshold,
        n_held_cleanly=n_clean,
        top_flagged_by_inflation_usdc=top_5,
    )


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile, p in [0, 1]. Returns 0.0 on empty."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    idx = (len(s) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] + frac * (s[hi] - s[lo])


def compute_edge_profile(
    decisions: dict[tuple[str, int], DecisionFills],
) -> EdgeProfileReport:
    """Entry-price distribution across resolved decisions.

    The decision-weighted avg = mean of per-decision size-weighted avg
    prices (NOT a re-weight across decision sizes — each decision is
    one sample). Captures the operator's "is this whale a sharp/
    contrarian (low avg, high sub-70%) or a favorite-farmer (high avg,
    high above-85%)" lens at a glance.
    """
    resolved = [d for d in decisions.values() if d.is_resolved]
    if not resolved:
        return EdgeProfileReport(
            n_decisions=0, avg_entry_price_decision_weighted=0.0,
            share_below_70=0.0, share_above_85=0.0,
            p25_entry=0.0, p50_entry=0.0, p75_entry=0.0,
        )
    prices = [d.weighted_avg_buy_price for d in resolved]
    avg = sum(prices) / len(prices)
    share_below = sum(1 for p in prices if p < SUB_70_PRICE_THRESHOLD) / len(prices)
    share_above = sum(1 for p in prices if p > FAVORITE_FARMING_PRICE_THRESHOLD) / len(prices)
    return EdgeProfileReport(
        n_decisions=len(resolved),
        avg_entry_price_decision_weighted=round(avg, 4),
        share_below_70=round(share_below, 4),
        share_above_85=round(share_above, 4),
        p25_entry=round(_percentile(prices, 0.25), 4),
        p50_entry=round(_percentile(prices, 0.50), 4),
        p75_entry=round(_percentile(prices, 0.75), 4),
    )


def compute_category_concentration(
    rows: list[ActivityRow],
    decisions: dict[tuple[str, int], DecisionFills],
) -> CategoryConcentrationReport:
    """How concentrated are the whale's decisions by `eventSlug`?

    Polymarket's `eventSlug` clusters condition_ids by their parent
    market event — e.g. "knicks-vs-cavaliers-2026-playoffs" contains
    separate moneyline / spread / O/U markets. A whale with 50
    decisions across 3 event_slugs has structurally concentrated bets
    even if 50 distinct (cid, oi) sound diverse.
    """
    resolved_cids = {d.key.condition_id for d in decisions.values() if d.is_resolved}
    # Pull event_slug per cid from raw rows (any row works; pick first non-empty)
    event_slug_by_cid: dict[str, str] = {}
    for r in rows:
        if r.condition_id in resolved_cids and r.event_slug and r.condition_id not in event_slug_by_cid:
            event_slug_by_cid[r.condition_id] = r.event_slug
    if not event_slug_by_cid:
        return CategoryConcentrationReport(
            n_distinct_event_slugs=0,
            top_3_event_slugs=(),
            largest_event_share=0.0,
        )
    # Count decisions per event_slug (not cids per event — decisions are the audit unit)
    slug_counts: Counter[str] = Counter()
    for d in decisions.values():
        if not d.is_resolved:
            continue
        slug = event_slug_by_cid.get(d.key.condition_id)
        if slug:
            slug_counts[slug] += 1
    if not slug_counts:
        return CategoryConcentrationReport(
            n_distinct_event_slugs=0,
            top_3_event_slugs=(),
            largest_event_share=0.0,
        )
    total = sum(slug_counts.values())
    top_3 = tuple((slug, n) for slug, n in slug_counts.most_common(3))
    largest_share = top_3[0][1] / total if total else 0.0
    return CategoryConcentrationReport(
        n_distinct_event_slugs=len(slug_counts),
        top_3_event_slugs=top_3,
        largest_event_share=round(largest_share, 4),
    )


def compute_realized_pnl(
    decisions: dict[tuple[str, int], DecisionFills],
    *,
    partial_sell_threshold: float = DEFAULT_PARTIAL_SELL_THRESHOLD,
) -> RealizedPnLReport:
    """REDEEM-grounded realized PnL + the inflation gap vs held-to-resolution.

    The inflation ratio is the AGGREGATE signal that closes the
    composition gap with the per-decision round-trip / partial-sell
    flags — it catches all forms of unrealized leakage uniformly.

    pnl_inflation_usdc = held_to_resolution_pnl - realized_pnl
        (the headline number that would disappear if we cleaned out
         all partial sells)
    pnl_inflation_ratio = pnl_inflation_usdc / max(held_to_resolution_pnl, 1.0)
        (the fraction of headline PnL that is paper / churn / unrealized;
         > 0.5 = most of the PnL is not real cash flow)

    The split into `from_clean_holds` vs `from_partial_sells` mirrors
    the SellFootprintReport's no-gap composition: partial-sell ≥
    threshold includes round-trips, so the two sums always partition
    realized PnL exactly.
    """
    resolved = [d for d in decisions.values() if d.is_resolved]
    realized_total = sum(d.realized_pnl for d in resolved)
    held_total = sum(d.held_to_resolution_pnl for d in resolved)
    inflation_usdc = held_total - realized_total
    # Use abs(held) for the ratio so we don't get nonsense signs when
    # held PnL is small or zero. The ratio is "fraction of headline
    # absolute PnL that's inflated."
    denom = max(abs(held_total), 1.0)
    inflation_ratio = inflation_usdc / denom

    from_clean = sum(
        d.realized_pnl for d in resolved
        if not d.is_partial_sell(partial_sell_threshold)
    )
    from_partial = sum(
        d.realized_pnl for d in resolved
        if d.is_partial_sell(partial_sell_threshold)
    )
    return RealizedPnLReport(
        realized_pnl_usdc=round(realized_total, 2),
        held_to_resolution_pnl_usdc=round(held_total, 2),
        pnl_inflation_usdc=round(inflation_usdc, 2),
        pnl_inflation_ratio=round(inflation_ratio, 4),
        pnl_from_clean_holds_usdc=round(from_clean, 2),
        pnl_from_partial_sells_usdc=round(from_partial, 2),
    )


def build_audit_report(
    *,
    leaderboard_entry: LeaderboardEntry | None,
    activity_rows: list[ActivityRow],
    resolutions: dict[str, dict[str, Any]],
    proxy_wallet: str,
    partial_sell_threshold: float = DEFAULT_PARTIAL_SELL_THRESHOLD,
) -> WhaleAuditReport:
    """Compose the full audit report. No I/O, no LLM. The caller fetches
    activity + resolutions; we just compute.

    `leaderboard_entry` is optional — wallets not on the leaderboard
    still get audited, but `user_name` will fall back to the activity
    feed's `name` field, then to "" if unknown.

    `proxy_wallet` is required because it's the cache key; we don't
    derive it from `leaderboard_entry` to avoid ambiguity when the
    operator passes a wallet that isn't on the leaderboard.
    """
    decisions = group_fills_by_decision(activity_rows, resolutions)
    clustering = compute_clustering(activity_rows, decisions)
    sell_footprint = compute_sell_footprint(
        decisions, partial_sell_threshold=partial_sell_threshold,
    )
    edge = compute_edge_profile(decisions)
    category = compute_category_concentration(activity_rows, decisions)
    realized_pnl = compute_realized_pnl(
        decisions, partial_sell_threshold=partial_sell_threshold,
    )

    # Activity bounds
    timestamps = [r.timestamp for r in activity_rows if r.timestamp > 0]
    activity_max_ts = max(timestamps) if timestamps else 0
    activity_min_ts = min(timestamps) if timestamps else 0

    # Resolve user_name with fallbacks
    user_name = ""
    if leaderboard_entry and leaderboard_entry.user_name:
        user_name = leaderboard_entry.user_name
    else:
        for r in activity_rows:
            if r.name:
                user_name = r.name
                break

    n_resolved = sum(1 for d in decisions.values() if d.is_resolved)
    return WhaleAuditReport(
        proxy_wallet=proxy_wallet.lower(),
        user_name=user_name,
        activity_max_ts=activity_max_ts,
        activity_min_ts=activity_min_ts,
        n_raw_rows_examined=len(activity_rows),
        n_resolved_decisions=n_resolved,
        clustering=clustering,
        sell_footprint=sell_footprint,
        edge=edge,
        category=category,
        realized_pnl=realized_pnl,
        partial_sell_threshold_used=partial_sell_threshold,
    )
