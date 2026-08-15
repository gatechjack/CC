"""Recency-weighted realized whale scorer -- a SECOND, INDEPENDENT lens.

This module answers "is this whale's edge CURRENT" -- distinct from the primary
selection pipeline (`polymarket_whale_audit.build_audit_report` +
`polymarket_whale_stats.score_whale_from_audit`) which answers "has this whale
proven DURABLE edge". It does NOT call, modify, or gate the primary scorer; the
two scores are meant to be viewed together as a 2D (durability x recency) picture.

Method: exponential decay on each resolved trade's RESOLUTION DATE. A trade N
half-lives old contributes 2**-N of its realized weight -- recent trades weigh
much heavier, old trades decay toward (but never reach) zero. Half-life is a
runtime parameter so it can be calibrated against known-fading vs known-durable
whales.

INTEGRITY (the recency lens re-weights by TIME; it does NOT relax rigor):
  * Realized basis ONLY. `held_to_resolution_pnl` NEVER enters any score -- a
    held-inflated whale scores ~0 on the recency lens by construction.
  * Clean-hold component (sell_share < CLEAN_HOLD_SELL_SHARE_MAX) is scored
    separately so exit-edge-only whales are caught on the recency lens too.
  * Favorite-farming (avg entry price > FAVORITE_FARM_PRICE) is flagged,
    including a recency-WEIGHTED variant so recent favorite-farming is surfaced.

Pure functions only -- no I/O. The CLI (`scripts/score_whale_recency.py`) does
the fetching/joining and calls into here. Unit-tested in
`tests/test_polymarket_whale_recency.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- tunable defaults (all overridable by the CLI) --------------------------
DEFAULT_HALF_LIFE_DAYS = 45.0          # provisional; locked by calibration
DEFAULT_ACCEL_RATIO = 1.25             # recent_vs_lifetime >= this -> accelerating
DEFAULT_FADE_RATIO = 0.75              # recent_vs_lifetime <= this -> fading
DEFAULT_DORMANT_HALF_LIVES = 2.0       # last_active older than N*half_life -> dormant
DEFAULT_MIN_WEIGHT_MASS = 1.0          # below this recent weight -> "thin"

# Mirrors of the primary pipeline's guards. Kept as literals so this module has
# ZERO import of the audit module (it must import standalone on a prod temp run).
# `test_polymarket_whale_recency.py` asserts these equal the audit module's
# constants, so they cannot silently drift.
CLEAN_HOLD_SELL_SHARE_MAX = 0.20       # == polymarket_whale_audit.DEFAULT_PARTIAL_SELL_THRESHOLD
FAVORITE_FARM_PRICE = 0.85             # == polymarket_whale_audit.FAVORITE_FARMING_PRICE_THRESHOLD

_SECONDS_PER_DAY = 86400.0
_EPS = 1e-9


@dataclass(frozen=True)
class ResolvedTrade:
    """One resolved trade for the recency lens. Realized basis.

    `clean_hold`/`held_pnl` are None when the trade is outside the audit-core
    fills window (older than the most-recent ~5,500 activity rows) -- the
    closed-positions spine still gives realized_pnl + resolution_ts + avg_price
    for it, but sell-share (clean-hold) needs fills we no longer have. Such
    trades are old, so they carry little decayed weight.
    """
    condition_id: str
    outcome_index: int
    realized_pnl: float
    resolution_ts: int          # unix seconds -- the decay anchor
    avg_price: float            # average entry price [0,1] (favorite-farm)
    clean_hold: bool | None = None
    held_pnl: float | None = None


@dataclass(frozen=True)
class RecencyScore:
    wallet: str
    user_name: str
    half_life_days: float
    as_of_ts: int
    n_resolved: int
    recent_n: int                       # trades with age < one half-life
    weight_mass: float
    rw_realized: float                  # HEADLINE rank key (recency-weighted realized $)
    rw_realized_mean: float             # rw_realized / weight_mass
    flat_realized: float                # = lifetime realized $
    flat_mean: float
    recent_vs_lifetime: float | None    # rw_realized_mean / flat_mean; None if flat_mean<=0
    recent_minus_lifetime: float        # robust backup (always defined)
    rw_clean_hold: float
    rw_clean_hold_share: float | None   # rw_clean_hold / rw_realized (None if rw_realized<=0)
    clean_hold_coverage: float          # weight-fraction with clean-hold info
    last_active_ts: int
    last_active_age_days: float
    trend: str                          # accelerating|steady|fading|dormant|thin
    favorite_farm_share: float          # count-fraction of trades avg_price>0.85
    favorite_farm_weighted: float       # weight-fraction on favorite trades
    held_inflation_ratio: float | None  # carried through from the audit report


def decay_weight(age_days: float, half_life_days: float) -> float:
    """Exponential decay weight. A trade `age_days` old under `half_life_days`
    gets 0.5 ** (age/half_life): age=0 -> 1.0, age=H -> 0.5, age=N*H -> 2**-N.
    Negative ages (future-dated ts vs as_of) clamp to 0 -> weight 1.0.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    return 0.5 ** (max(0.0, age_days) / half_life_days)


def classify_trend(
    *,
    recent_vs_lifetime: float | None,
    recent_minus_lifetime: float,
    last_active_age_days: float,
    recent_n: int,
    weight_mass: float,
    half_life_days: float,
    accel: float = DEFAULT_ACCEL_RATIO,
    fade: float = DEFAULT_FADE_RATIO,
    dormant_half_lives: float = DEFAULT_DORMANT_HALF_LIVES,
    min_weight_mass: float = DEFAULT_MIN_WEIGHT_MASS,
) -> str:
    """accelerating / steady / fading / dormant / thin.

    dormant: no trade within a half-life, or last activity older than
        `dormant_half_lives` * half_life. thin: some recent activity but too
        little weight to trust the ratio. Otherwise ratio-based (falling back
        to the sign of recent_minus_lifetime when the ratio is undefined).
    """
    if recent_n == 0 or last_active_age_days > dormant_half_lives * half_life_days:
        return "dormant"
    if weight_mass < min_weight_mass:
        return "thin"
    if recent_vs_lifetime is not None:
        if recent_vs_lifetime >= accel:
            return "accelerating"
        if recent_vs_lifetime <= fade:
            return "fading"
        return "steady"
    # ratio undefined (lifetime breakeven/negative) -> use the difference sign
    if recent_minus_lifetime > _EPS:
        return "accelerating"
    if recent_minus_lifetime < -_EPS:
        return "fading"
    return "steady"


def score_recency(
    wallet: str,
    user_name: str,
    trades: list[ResolvedTrade],
    *,
    half_life_days: float,
    as_of_ts: int,
    held_inflation_ratio: float | None = None,
    accel: float = DEFAULT_ACCEL_RATIO,
    fade: float = DEFAULT_FADE_RATIO,
    dormant_half_lives: float = DEFAULT_DORMANT_HALF_LIVES,
    min_weight_mass: float = DEFAULT_MIN_WEIGHT_MASS,
) -> RecencyScore:
    """Score one whale's resolved trades on the recency lens. Realized basis."""
    n = len(trades)
    if n == 0:
        return RecencyScore(
            wallet=wallet, user_name=user_name, half_life_days=half_life_days,
            as_of_ts=as_of_ts, n_resolved=0, recent_n=0, weight_mass=0.0,
            rw_realized=0.0, rw_realized_mean=0.0, flat_realized=0.0, flat_mean=0.0,
            recent_vs_lifetime=None, recent_minus_lifetime=0.0, rw_clean_hold=0.0,
            rw_clean_hold_share=None, clean_hold_coverage=0.0, last_active_ts=0,
            last_active_age_days=0.0, trend="dormant", favorite_farm_share=0.0,
            favorite_farm_weighted=0.0, held_inflation_ratio=held_inflation_ratio,
        )

    rw_realized = 0.0
    weight_mass = 0.0
    flat_realized = 0.0
    rw_clean_hold = 0.0
    clean_info_mass = 0.0
    fav_count = 0
    fav_weight = 0.0
    recent_n = 0
    last_active_ts = 0

    for t in trades:
        age_days = max(0.0, (as_of_ts - t.resolution_ts) / _SECONDS_PER_DAY)
        w = decay_weight(age_days, half_life_days)
        rw_realized += w * t.realized_pnl
        weight_mass += w
        flat_realized += t.realized_pnl
        if age_days < half_life_days:
            recent_n += 1
        if t.avg_price > FAVORITE_FARM_PRICE:
            fav_count += 1
            fav_weight += w
        if t.clean_hold is not None:
            clean_info_mass += w
            if t.clean_hold:
                rw_clean_hold += w * t.realized_pnl
        if t.resolution_ts > last_active_ts:
            last_active_ts = t.resolution_ts

    rw_realized_mean = rw_realized / weight_mass if weight_mass > _EPS else 0.0
    flat_mean = flat_realized / n
    recent_vs_lifetime = (rw_realized_mean / flat_mean) if flat_mean > _EPS else None
    recent_minus_lifetime = rw_realized_mean - flat_mean
    rw_clean_hold_share = (rw_clean_hold / rw_realized) if rw_realized > _EPS else None
    clean_hold_coverage = clean_info_mass / weight_mass if weight_mass > _EPS else 0.0
    favorite_farm_share = fav_count / n
    favorite_farm_weighted = fav_weight / weight_mass if weight_mass > _EPS else 0.0
    last_active_age_days = max(0.0, (as_of_ts - last_active_ts) / _SECONDS_PER_DAY)

    trend = classify_trend(
        recent_vs_lifetime=recent_vs_lifetime,
        recent_minus_lifetime=recent_minus_lifetime,
        last_active_age_days=last_active_age_days,
        recent_n=recent_n, weight_mass=weight_mass, half_life_days=half_life_days,
        accel=accel, fade=fade, dormant_half_lives=dormant_half_lives,
        min_weight_mass=min_weight_mass,
    )

    return RecencyScore(
        wallet=wallet, user_name=user_name, half_life_days=half_life_days,
        as_of_ts=as_of_ts, n_resolved=n, recent_n=recent_n, weight_mass=weight_mass,
        rw_realized=rw_realized, rw_realized_mean=rw_realized_mean,
        flat_realized=flat_realized, flat_mean=flat_mean,
        recent_vs_lifetime=recent_vs_lifetime, recent_minus_lifetime=recent_minus_lifetime,
        rw_clean_hold=rw_clean_hold, rw_clean_hold_share=rw_clean_hold_share,
        clean_hold_coverage=clean_hold_coverage, last_active_ts=last_active_ts,
        last_active_age_days=last_active_age_days, trend=trend,
        favorite_farm_share=favorite_farm_share, favorite_farm_weighted=favorite_farm_weighted,
        held_inflation_ratio=held_inflation_ratio,
    )
