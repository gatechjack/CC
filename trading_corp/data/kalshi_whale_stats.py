"""K3 whale scoring + selection engine.

Venue-agnostic. Same math applies to Polymarket whales when that copy-trading
division is revived — only the upstream `WhaleStats` construction needs to
adapt to the source data shape.

Composite skill score per whale:

    score = wilson_lcb_95(wins, n)
          * (1 + clip(avg_pnl_per_contract / 100, -0.5, +2.0))
          * category_bonus(top_categories, target_category)

Components:
  - **wilson_lcb_95**: 95% Wilson lower confidence bound on win rate. Encodes
    "what's the floor on this trader's true win-rate given the sample?"
    Inherently penalizes small samples: 8/10 (80%) → LCB 0.49;
    75/100 (75%) → LCB 0.66.
  - **avg_pnl_per_contract**: realized PnL ÷ contracts. Edge-quality proxy
    when entry prices are unavailable from Apify. Higher = better picks
    on the price curve (winning bets at long odds, not just favorites).
  - **category_bonus**: 1.0 generalist, up to 1.5 if the whale's
    `top_categories` includes a target Kalshi category — subject-matter
    expert weighting.

Selection consumes a list of `ScoredWhale` and returns top-N — either
globally or per-category.

Data limitation: Apify's `closed_positions` row carries `{ticker, pnl,
contracts, is_open=false}` but NOT entry price. So we infer win/loss from
`pnl > 0` and edge-quality from `pnl / contracts` rather than from a
clean ROI. Acceptable proxies for ranking; would be cleaner if Hashdive
or DIY surfaces entry price.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from trading_corp.data.kalshi_apify_client import (
    LeaderboardEntry, WhaleProfile, WhalePosition,
)

log = logging.getLogger(__name__)

# 1.96 — the 0.975 quantile of the standard normal, used for the 95% Wilson CI.
# Imported as a constant so the LCB function is a pure expression in the test
# log rather than carrying scipy as a dependency.
_Z_95 = 1.959963984540054

# Inclusion thresholds for selection. A whale with fewer than this many
# resolved positions doesn't have enough sample for Wilson LCB to mean
# anything useful — score is computed but selection drops them.
DEFAULT_MIN_CLOSED_POSITIONS = 20

# Multiplier applied when a whale's top_categories overlaps the target.
# 1.0 = generalist; CATEGORY_MATCH_BONUS = strong specialist signal.
CATEGORY_MATCH_BONUS = 1.5

# Kalshi-side category names as they appear in `WhaleProfile.top_categories`.
# These match the Apify response, NOT the URL-encoded form used as input to
# the leaderboard actor (where "Climate+and+Weather" replaces "Climate").
KALSHI_CATEGORIES: tuple[str, ...] = (
    "Politics", "Sports", "Entertainment", "Crypto",
    "Climate", "Economics", "Mentions", "Companies",
    "Financials", "Science & Technology", "Elections",
)


@dataclass
class WhaleStats:
    """Aggregated stats for one whale, built from Apify closed_positions + profile.

    `venue` is a tag so the same scoring pipeline can mix Kalshi + Polymarket
    candidates when the cross-venue copy-trading division goes live."""
    nickname: str
    venue: str  # "kalshi" | "polymarket"
    closed_positions_count: int
    wins: int  # pnl > 0
    losses: int  # pnl <= 0
    total_pnl: float
    total_contracts: int
    top_categories: tuple[str, ...]
    profile_pnl_units: int = 0  # lifetime; unit-unclear, for context only
    lifetime_num_markets_traded: int = 0

    @property
    def win_rate(self) -> float:
        if self.closed_positions_count == 0:
            return 0.0
        return self.wins / self.closed_positions_count

    @property
    def avg_pnl_per_contract(self) -> float:
        if self.total_contracts == 0:
            return 0.0
        return self.total_pnl / self.total_contracts


@dataclass
class ScoredWhale:
    """Scored whale, with breakdown for audit/transparency."""
    stats: WhaleStats
    wilson_lcb: float
    edge_factor: float
    category_bonus: float
    composite_score: float
    target_category: str | None = None
    excluded: bool = False
    exclusion_reason: str = ""


def wilson_lcb_95(wins: int, n: int) -> float:
    """95% Wilson lower confidence bound on a binomial proportion.

    Returns 0.0 for n=0 (no data, no skill floor). Penalizes small samples
    automatically: 8/10 → ~0.49, 75/100 → ~0.66, both at 80%/75% raw rate.
    """
    if n == 0:
        return 0.0
    p_hat = wins / n
    z = _Z_95
    denom = 1.0 + z * z / n
    center = p_hat + z * z / (2.0 * n)
    spread = z * math.sqrt(p_hat * (1.0 - p_hat) / n + z * z / (4.0 * n * n))
    lcb = (center - spread) / denom
    return max(0.0, lcb)


def wilson_lcb_95_weighted(weighted_wins: float, n_eff: float) -> float:
    """Time-weighted Wilson 95% LCB using effective sample size.

    For weighted observations with outcomes y_i ∈ {0,1} and weights w_i:
      p̂   = Σ(w_i · y_i) / Σw_i
      n_eff = (Σw_i)² / Σ(w_i²)        — Kish's effective sample size

    Pass `weighted_wins` = p̂ × W and `n_eff` = effective N. We rebuild the
    binomial Wilson math at n_eff samples. n_eff < n always (n_eff = n when
    all weights are equal), so this correctly penalizes whales whose history
    is dominated by a few old/heavy trades.
    """
    if n_eff <= 0:
        return 0.0
    # p̂ implied by (weighted_wins, n_eff): only meaningful if we know the
    # ORIGINAL weighted denominator. Caller passes weighted_wins normalized
    # against Σw_i (the weighted win rate), and we treat p̂ = weighted_wins
    # for the Wilson math.
    p_hat = max(0.0, min(1.0, weighted_wins))
    z = _Z_95
    denom = 1.0 + z * z / n_eff
    center = p_hat + z * z / (2.0 * n_eff)
    spread = z * math.sqrt(p_hat * (1.0 - p_hat) / n_eff + z * z / (4.0 * n_eff * n_eff))
    lcb = (center - spread) / denom
    return max(0.0, lcb)


def time_weighted_outcomes(
    samples: list[tuple[bool, float]],
    *,
    now_ts: float,
    half_life_days: float = 30.0,
) -> tuple[float, float]:
    """Compute (weighted_win_rate, n_eff) from time-stamped outcomes.

    Each sample is `(is_win, sample_ts_unix_seconds)`. The exponential decay
    weight is `0.5 ** (age_days / half_life_days)` — so a trade `half_life`
    days ago counts half as much as one made now.

    Returns:
      weighted_win_rate: Σ(w_i · y_i) / Σw_i, in [0.0, 1.0]
      n_eff:             Kish's effective sample size (always ≤ raw N)

    Returns (0.0, 0.0) for empty input. Both values are pass-through inputs
    to `wilson_lcb_95_weighted`.
    """
    if not samples:
        return (0.0, 0.0)
    if half_life_days <= 0:
        # Degenerate case — treat as unweighted.
        wins = sum(1 for is_win, _ in samples if is_win)
        return (wins / len(samples), float(len(samples)))

    lambda_ = math.log(2.0) / half_life_days
    sum_w = 0.0
    sum_w_y = 0.0
    sum_w2 = 0.0
    for is_win, ts in samples:
        age_days = max(0.0, (now_ts - ts) / 86400.0)
        w = math.exp(-lambda_ * age_days)
        sum_w += w
        sum_w_y += w * (1.0 if is_win else 0.0)
        sum_w2 += w * w
    if sum_w <= 0 or sum_w2 <= 0:
        return (0.0, 0.0)
    weighted_rate = sum_w_y / sum_w
    n_eff = (sum_w * sum_w) / sum_w2
    return (weighted_rate, n_eff)


def _edge_factor(avg_pnl_per_contract: float) -> float:
    """Map avg PnL/contract → multiplicative score factor in roughly [0.5, 3.0].

    Kalshi contracts settle at $1 binary. Per-contract realized PnL is bounded
    in [-1.0, +1.0] but typically scales by `contracts` magnitude in raw $ — the
    Apify pnl field is dollars, not per-contract. So pnl/contracts is in
    roughly [-$1, +$1] but can exceed that for partial fills / wash sales.

    Clipping prevents extreme outliers (one giant scalp on a single contract
    distorting the whole score) from dominating selection.
    """
    # Clip to [-0.5, +2.0] so a whale with avg +$0.30/contract gets a 1.3x
    # boost; one with -$0.10/contract gets a 0.9x penalty. The +2.0 cap means
    # no whale can score >3x on edge alone (defense against outlier extremes).
    clipped = max(-0.5, min(2.0, avg_pnl_per_contract))
    return 1.0 + clipped


def _category_bonus(top_categories: tuple[str, ...], target: str | None) -> float:
    """Multiplier when the whale's top_categories overlaps the target category."""
    if not target:
        return 1.0
    # Apify category strings can drift slightly (e.g. "Climate" in profile vs
    # "Climate+and+Weather" in leaderboard input). Match by substring both ways.
    target_norm = target.lower().replace("+", " ").replace("_", " ")
    for cat in top_categories:
        cat_norm = cat.lower()
        if target_norm in cat_norm or cat_norm in target_norm:
            return CATEGORY_MATCH_BONUS
    return 1.0


def compute_stats(
    nickname: str,
    closed_positions: list[WhalePosition],
    *,
    profile: WhaleProfile | None = None,
    venue: str = "kalshi",
) -> WhaleStats:
    """Aggregate Apify closed_positions + profile into a single WhaleStats record.

    Filters to only this nickname's positions (the Apify profile actor returns
    positions tagged by `name`, but if the caller batched multiple names per
    actor call, the list contains a mix).
    """
    relevant = [p for p in closed_positions if p.name == nickname and not p.is_open]
    wins = sum(1 for p in relevant if p.pnl > 0)
    losses = len(relevant) - wins
    total_pnl = sum(p.pnl for p in relevant)
    total_contracts = sum(p.contracts for p in relevant)
    top_categories: tuple[str, ...] = ()
    profile_pnl_units = 0
    lifetime_markets = 0
    if profile is not None:
        top_categories = profile.top_categories
        profile_pnl_units = profile.pnl_units
        lifetime_markets = profile.num_markets_traded
    return WhaleStats(
        nickname=nickname,
        venue=venue,
        closed_positions_count=len(relevant),
        wins=wins,
        losses=losses,
        total_pnl=total_pnl,
        total_contracts=total_contracts,
        top_categories=top_categories,
        profile_pnl_units=profile_pnl_units,
        lifetime_num_markets_traded=lifetime_markets,
    )


def score_whale(
    stats: WhaleStats,
    *,
    target_category: str | None = None,
    min_closed_positions: int = DEFAULT_MIN_CLOSED_POSITIONS,
) -> ScoredWhale:
    """Compute composite score + sub-factors for one whale.

    Whales below `min_closed_positions` are flagged `excluded=True` with
    reason set; their composite_score is computed but they shouldn't be
    selected. Whales with empty `closed_positions` (visibility opt-out) score
    0 and are excluded with reason 'no_visibility'.
    """
    if stats.closed_positions_count == 0:
        return ScoredWhale(
            stats=stats, wilson_lcb=0.0, edge_factor=1.0, category_bonus=1.0,
            composite_score=0.0, target_category=target_category,
            excluded=True, exclusion_reason="no_visibility",
        )
    lcb = wilson_lcb_95(stats.wins, stats.closed_positions_count)
    edge = _edge_factor(stats.avg_pnl_per_contract)
    cat_bonus = _category_bonus(stats.top_categories, target_category)
    composite = lcb * edge * cat_bonus
    excluded = stats.closed_positions_count < min_closed_positions
    return ScoredWhale(
        stats=stats, wilson_lcb=lcb, edge_factor=edge, category_bonus=cat_bonus,
        composite_score=composite, target_category=target_category,
        excluded=excluded,
        exclusion_reason=f"sample<{min_closed_positions}" if excluded else "",
    )


def select_top_n(
    scored: list[ScoredWhale], n: int = 3, *, include_excluded: bool = False,
) -> list[ScoredWhale]:
    """Sort scored whales by composite_score desc and return top N.

    `include_excluded` defaults False — under-sample whales drop out by default.
    """
    pool = scored if include_excluded else [s for s in scored if not s.excluded]
    return sorted(pool, key=lambda s: s.composite_score, reverse=True)[:n]


def select_per_category(
    scored_by_category: dict[str, list[ScoredWhale]],
    n_per_category: int = 2,
) -> dict[str, list[ScoredWhale]]:
    """Pick top N for each Kalshi category. Caller passes a dict where each
    value is the pool of scored whales relevant to that category (typically
    via prior `score_whale(..., target_category=cat)` pass per category)."""
    return {
        cat: select_top_n(scored, n=n_per_category)
        for cat, scored in scored_by_category.items()
    }


def filter_leaderboard_for_discovery(
    leaderboard: list[LeaderboardEntry],
    *,
    skip_anonymous: bool = True,
    max_rank: int | None = None,
) -> list[str]:
    """Build the candidate-handle list to send to the profile/closed_positions
    enrichment step. Strips anonymous rows by default (we can't copy them)
    and optionally caps by rank to control enrichment-call volume."""
    candidates: list[str] = []
    for entry in leaderboard:
        if skip_anonymous and entry.is_anonymous:
            continue
        if max_rank is not None and entry.rank > max_rank:
            continue
        if entry.nickname:
            candidates.append(entry.nickname)
    return candidates
