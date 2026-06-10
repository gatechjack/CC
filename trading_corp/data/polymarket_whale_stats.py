"""Polymarket-specific whale-stats adapter for K3-equivalent copy trading.

Bridges `polymarket_data_api_client` outputs (LeaderboardEntry + ActivityRow)
into the venue-agnostic `WhaleStats` dataclass + scoring pipeline defined
in `kalshi_whale_stats`. The composite-score math (Wilson LCB, edge factor,
category bonus) is reused as-is — same selection logic, just different
input shape.

Differences vs Kalshi/Apify:
  - Real entry price + USDC size per trade → real ROI, not per-contract proxy
  - Win/loss inferred by joining each BUY's `outcome_index` against the
    market's resolved winning outcome (looked up via the existing
    `PolymarketBroker.get_market_resolution(condition_id)` path)
  - Time-weighted Wilson LCB with configurable half-life (default 30d)
  - Sells are exits, not new entries — only BUYs count toward win rate
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from trading_corp.data.kalshi_whale_stats import (
    ScoredWhale, WhaleStats, _category_bonus, _edge_factor,
    time_weighted_outcomes, wilson_lcb_95, wilson_lcb_95_weighted,
)
from trading_corp.data.polymarket_data_api_client import (
    ActivityRow, LeaderboardEntry,
)
from trading_corp.data.polymarket_whale_audit import WhaleAuditReport

log = logging.getLogger(__name__)


DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_MIN_RESOLVED = 10

# Aggregate inflation gate for the realized-basis selection scorer
# (`score_whale_from_audit`, option (c) Phase 1). A whale whose headline PnL is
# more than this fraction paper/churn (`pnl_inflation_ratio`) is excluded from
# selection. STRICTLY greater than the threshold excludes; a ratio exactly at
# the threshold is KEPT. Default pinned at 0.5 (scoping doc F-1); calibrate
# against live data in Phase E before any merge.
DEFAULT_INFLATION_RATIO_THRESHOLD = 0.5


@dataclass
class PolymarketTradeOutcome:
    """One resolved BUY trade with its outcome and the resolution context."""
    timestamp: int  # unix seconds
    condition_id: str
    outcome_index: int  # which leg the whale bought
    price: float  # entry price [0,1]
    usdc_size: float
    is_win: bool
    title: str = ""
    outcome: str = ""  # human label of the leg ("Yes", "Spurs", etc.)


def _is_win_for_buy(
    activity: ActivityRow, resolution: dict | None,
) -> bool | None:
    """Determine whether this BUY trade was a win, given the market resolution.

    `resolution` is the dict returned by `PolymarketBroker.get_market_resolution(
    condition_id)` — shape `{status, result, yes_won, winning_outcome_index, ...}`
    (we read fields defensively since the exact key set varies). Returns:
      True  — the whale's bought outcome matches the winning outcome
      False — bought the losing outcome
      None  — market not yet resolved (skip this row for win-rate stats)
    """
    if resolution is None:
        return None
    status = (resolution.get("status") or "").lower()
    if status != "resolved":
        return None
    # Common resolution shapes:
    #   {"yes_won": True/False, "winning_outcome_index": 0/1}
    #   {"result": "yes"/"no", "winning_outcome_index": 0/1}
    win_idx = resolution.get("winning_outcome_index")
    if win_idx is None:
        # Fall back to yes_won / result if explicit index isn't surfaced.
        yes_won = resolution.get("yes_won")
        if isinstance(yes_won, bool):
            win_idx = 0 if yes_won else 1
        else:
            r = (resolution.get("result") or "").lower()
            if r == "yes":
                win_idx = 0
            elif r == "no":
                win_idx = 1
            else:
                return None
    try:
        return int(win_idx) == int(activity.outcome_index)
    except (TypeError, ValueError):
        return None


def compute_polymarket_stats(
    *,
    leaderboard_entry: LeaderboardEntry,
    activity_rows: list[ActivityRow],
    market_resolutions: dict[str, dict] | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    now_ts: float | None = None,
) -> tuple[WhaleStats, list[PolymarketTradeOutcome]]:
    """Build a WhaleStats record for one Polymarket whale.

    Inputs:
      leaderboard_entry: row from `/v1/leaderboard` (gives lifetime vol + pnl)
      activity_rows:     last N trades from `/activity?user=<wallet>`
      market_resolutions: {condition_id: resolution_dict} — pre-fetched in
                          batch; missing entries treated as unresolved (skip)
      half_life_days:    exponential decay for time-weighted win rate

    Returns:
      WhaleStats:    fits the venue-agnostic score_whale pipeline
      list[PolymarketTradeOutcome]: per-trade audit detail for the dashboard
    """
    market_resolutions = market_resolutions or {}
    now_ts = now_ts if now_ts is not None else time.time()

    resolved: list[PolymarketTradeOutcome] = []
    weighted_samples: list[tuple[bool, float]] = []
    total_pnl_usdc = 0.0  # realized P&L over resolved BUYs
    total_usdc_size = 0.0
    cat_counter: Counter[str] = Counter()

    for a in activity_rows:
        if a.type != "TRADE":
            continue
        if a.side != "BUY":
            # SELL = exit. We don't count exits toward win rate — they're
            # part of the BUY's lifecycle. (Whales who flip in/out get
            # counted on the BUY only.)
            continue
        resolution = market_resolutions.get(a.condition_id)
        win = _is_win_for_buy(a, resolution)
        if win is None:
            continue
        # P&L per contract = (1 - price) if win else -price (binary settlement)
        per_contract_pnl = (1.0 - a.price) if win else -a.price
        trade_pnl_usdc = per_contract_pnl * a.size
        total_pnl_usdc += trade_pnl_usdc
        total_usdc_size += a.usdc_size
        weighted_samples.append((win, float(a.timestamp)))
        # Simple market category inference from the title — not perfect but
        # serves as a hint for the score's category_bonus. Real category
        # comes from leaderboard query, not from per-trade metadata.
        if a.event_slug:
            cat_counter[a.event_slug] += 1
        resolved.append(PolymarketTradeOutcome(
            timestamp=a.timestamp,
            condition_id=a.condition_id,
            outcome_index=a.outcome_index,
            price=a.price,
            usdc_size=a.usdc_size,
            is_win=win,
            title=a.title,
            outcome=a.outcome,
        ))

    wins = sum(1 for o in resolved if o.is_win)
    losses = len(resolved) - wins

    # Time-weighted win-rate + effective sample size (Kish), passed to
    # the time-weighted Wilson LCB downstream.
    weighted_rate, n_eff = time_weighted_outcomes(
        weighted_samples, now_ts=now_ts, half_life_days=half_life_days,
    )

    # Pack the time-weighted metrics into WhaleStats. The standard fields
    # (closed_positions_count, wins, losses) carry the RAW counts so the
    # min_sample filter works as designed. The time-weighted Wilson LCB
    # is exposed via the `extra` channel below (see score_polymarket_whale).
    # avg_pnl_per_contract is computed from real USDC math, not Kalshi's
    # contracts-as-proxy approach.
    avg_pnl_per_contract = (
        total_pnl_usdc / total_usdc_size if total_usdc_size > 0 else 0.0
    )

    stats = WhaleStats(
        nickname=leaderboard_entry.user_name or leaderboard_entry.proxy_wallet,
        venue="polymarket",
        closed_positions_count=len(resolved),
        wins=wins,
        losses=losses,
        total_pnl=total_pnl_usdc,
        total_contracts=int(total_usdc_size),
        top_categories=(),  # filled in by selection (leaderboard category)
        profile_pnl_units=int(leaderboard_entry.pnl * 100),
        lifetime_num_markets_traded=len(set(a.condition_id for a in activity_rows)),
    )
    # Stash time-weighted intermediates on the stats object via setattr —
    # the score function reads them when present, falls back to the
    # un-weighted Wilson otherwise.
    setattr(stats, "_pm_weighted_rate", weighted_rate)
    setattr(stats, "_pm_n_eff", n_eff)
    setattr(stats, "_pm_lifetime_vol", leaderboard_entry.vol)
    setattr(stats, "_pm_lifetime_pnl", leaderboard_entry.pnl)
    setattr(stats, "_pm_proxy_wallet", leaderboard_entry.proxy_wallet)
    return stats, resolved


def score_polymarket_whale(
    stats: WhaleStats,
    *,
    target_category: str | None = None,
    min_resolved: int = DEFAULT_MIN_RESOLVED,
) -> ScoredWhale:
    """Score a Polymarket whale using the time-weighted Wilson LCB.

    Mirrors `kalshi_whale_stats.score_whale` shape but uses the time-weighted
    intermediates (`_pm_weighted_rate`, `_pm_n_eff`) attached to `stats` by
    `compute_polymarket_stats`. Falls back to the raw `wilson_lcb_95` if
    those aren't attached (e.g. unit tests).
    """
    if stats.closed_positions_count == 0:
        return ScoredWhale(
            stats=stats, wilson_lcb=0.0, edge_factor=1.0, category_bonus=1.0,
            composite_score=0.0, target_category=target_category,
            excluded=True, exclusion_reason="no_resolved_trades",
        )

    weighted_rate = getattr(stats, "_pm_weighted_rate", None)
    n_eff = getattr(stats, "_pm_n_eff", None)
    if isinstance(weighted_rate, float) and isinstance(n_eff, float) and n_eff > 0:
        wlcb = wilson_lcb_95_weighted(weighted_rate, n_eff)
    else:
        # Fallback: unweighted on raw counts.
        from trading_corp.data.kalshi_whale_stats import wilson_lcb_95
        wlcb = wilson_lcb_95(stats.wins, stats.closed_positions_count)

    edge = _edge_factor(stats.avg_pnl_per_contract)
    cat_bonus = _category_bonus(stats.top_categories, target_category)
    composite = wlcb * edge * cat_bonus

    excluded = stats.closed_positions_count < min_resolved
    return ScoredWhale(
        stats=stats, wilson_lcb=wlcb, edge_factor=edge, category_bonus=cat_bonus,
        composite_score=composite, target_category=target_category,
        excluded=excluded,
        exclusion_reason=f"resolved<{min_resolved}" if excluded else "",
    )


def score_whale_from_audit(
    report: WhaleAuditReport,
    *,
    target_category: str | None = None,
    whale_categories: tuple[str, ...] = (),
    min_resolved: int = DEFAULT_MIN_RESOLVED,
    inflation_threshold: float = DEFAULT_INFLATION_RATIO_THRESHOLD,
) -> ScoredWhale:
    """Selection score on the REDEEM-grounded realized basis (option (c), F-1).

    Same composite SHAPE as `score_polymarket_whale` — Wilson LCB × edge ×
    category bonus — but fed decision-unit, realized inputs:

      - Wilson LCB over RESOLVED DECISIONS: `wilson_lcb_95(n_winning_decisions,
        n_resolved_decisions)`. PLAIN (un-time-weighted) by design — the
        decision unit already removes the per-fill clustering inflation that
        motivated option (c); time-weighting is deferred to Phase 3 (the
        refresh's `--half-life-days` flag does NOT affect this score).
      - Edge factor from REALIZED ROI: `realized_pnl_usdc /
        total_buy_usdc_resolved`, mapped through the shared `_edge_factor`
        (1 + clip(roi, -0.5, +2.0)). Non-positive denominator → ROI 0.0
        (edge 1.0).
      - Category bonus: `_category_bonus(whale_categories, target_category)` —
        unchanged mechanism, so Rule-B per-category selection is preserved
        (the refresh passes `target_category=cat, whale_categories=(cat,)` per
        category, and `target_category=None` for the global pass).

    Exclusion gates (both surfaced in `exclusion_reason`, semicolon-joined):
      - `n_resolved_decisions < min_resolved` (insufficient sample).
      - `pnl_inflation_ratio > inflation_threshold` — STRICTLY greater excludes;
        a ratio exactly at the threshold is KEPT. Headline PnL that's mostly
        churn / paper. Default 0.5 (F-1); calibrated in Phase E. The composite
        is still computed for excluded whales (not zeroed) so the dry-run
        gated-out list can show their would-be score + ratio.

    Returns a `ScoredWhale` carrying a SYNTHESIZED `WhaleStats` (wins = winning
    decisions, closed = resolved decisions, total_pnl = realized, contracts ≈
    cost basis) so the existing refresh print/details path renders unchanged.
    """
    n = report.n_resolved_decisions
    wins = report.n_winning_decisions
    buy_usdc = report.total_buy_usdc_resolved
    realized = report.realized_pnl.realized_pnl_usdc
    realized_roi = (realized / buy_usdc) if buy_usdc > 0 else 0.0

    # Synthesized stats on the decision/realized basis so ScoredWhale.stats
    # flows through the refresh's print/details path unchanged.
    stats = WhaleStats(
        nickname=report.user_name or report.proxy_wallet,
        venue="polymarket",
        closed_positions_count=n,
        wins=wins,
        losses=max(0, n - wins),
        total_pnl=realized,
        total_contracts=max(0, round(buy_usdc)),
        top_categories=tuple(whale_categories),
        lifetime_num_markets_traded=report.n_raw_rows_examined,
    )

    if n == 0:
        return ScoredWhale(
            stats=stats, wilson_lcb=0.0, edge_factor=1.0, category_bonus=1.0,
            composite_score=0.0, target_category=target_category,
            excluded=True, exclusion_reason="no_resolved_decisions",
        )

    wlcb = wilson_lcb_95(wins, n)
    edge = _edge_factor(realized_roi)
    cat_bonus = _category_bonus(tuple(whale_categories), target_category)
    composite = wlcb * edge * cat_bonus

    inflation_ratio = report.realized_pnl.pnl_inflation_ratio
    reasons: list[str] = []
    if n < min_resolved:
        reasons.append(f"resolved<{min_resolved}")
    if inflation_ratio > inflation_threshold:
        reasons.append(f"inflation>{inflation_threshold:g}({inflation_ratio:.2f})")
    return ScoredWhale(
        stats=stats, wilson_lcb=wlcb, edge_factor=edge, category_bonus=cat_bonus,
        composite_score=composite, target_category=target_category,
        excluded=bool(reasons), exclusion_reason=";".join(reasons),
    )
