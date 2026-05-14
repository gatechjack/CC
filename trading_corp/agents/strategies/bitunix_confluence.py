"""BitUnix Futures — confluence score accumulator (Phase 3.2).

Pure-function scorer that replaces the Phase 3.1 single-bar `_tier_for`
classifier in `bitunix_futures_observer.py`. Accumulates signal weights
across bars within per-signal TTL windows; emits a tier verdict from
the net score.

Reuses the factor / guard / alert / price-context dataclasses from
`btc_accumulator` (spot variant) for consistency — the scoring
mathematics is the same; futures differs in:

  - Both sides evaluated every event (spot is state-coupled: CASH only
    checks buy, BTC only checks sell)
  - Tier bands map score magnitude to PREMIUM / STANDARD / WEAK / SKIP
    instead of binary fire/no-fire
  - Signal dedupe within TTL (most-recent fire per signal_name wins) —
    prevents `mc_a_red_diamond` re-firing every 3m from stacking 12+
    points of bear pressure on the same chart pattern
  - Cooldown gate prevents re-firing the same direction within a
    configurable window (protects against doubling into the same setup)

Caller responsibilities (mirror btc_accumulator):
  - Pre-filter `live_alerts` to those within their per-factor TTL
  - Supply current `PriceContext` (VWAP, HH/LL, volume, % change)
  - Track `last_fire_ts_by_side` across calls for the cooldown gate
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from trading_corp.agents.strategies.btc_accumulator import (
    AlertEvent,
    FactorConfig,
    GuardBracket,
    GuardConfig,
    PriceContext,
    _directional_side,
    _guard_penalty_for_pct,
    _strip_directional_suffix,
)

# Re-export for callers that want a single import surface.
__all__ = [
    "AlertEvent",
    "BitUnixConfluenceConfig",
    "BitUnixVerdict",
    "PriceContext",
    "Side",
    "Tier",
    "evaluate_confluence_futures",
    "filter_live_alerts_with_dedupe",
]


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"


class Tier(str, Enum):
    PREMIUM = "PREMIUM"
    STANDARD = "STANDARD"
    WEAK = "WEAK"
    SKIP = "SKIP"


@dataclass(frozen=True)
class BitUnixConfluenceConfig:
    """Parsed `bitunix_futures.scoring` block from strategies.yaml."""
    enabled: bool
    min_score_to_fire: int
    premium_threshold: int
    standard_threshold: int
    weak_threshold: int
    cooldown_seconds: int
    dedupe_within_ttl: bool
    factors: dict[str, FactorConfig]
    sell_on_rush: GuardConfig
    buy_on_fall: GuardConfig

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BitUnixConfluenceConfig":
        scoring = raw.get("scoring") or {}
        factors: dict[str, FactorConfig] = {}
        for name, body in (scoring.get("factors") or {}).items():
            factors[name] = FactorConfig(
                name=name,
                weight=int(body["weight"]),
                side=str(body["side"]),
                ttl_minutes=int(body.get("ttl_minutes", 0)),
            )
        guards = scoring.get("guards") or {}
        sell_brackets = tuple(
            GuardBracket(upto_pct=float(b["upto_pct"]), penalty=int(b["penalty"]))
            for b in guards.get("sell_on_rush", {}).get("brackets", [])
        )
        buy_brackets = tuple(
            GuardBracket(upto_pct=float(b["upto_drop_pct"]), penalty=int(b["penalty"]))
            for b in guards.get("buy_on_fall", {}).get("brackets", [])
        )
        tier_thresholds = scoring.get("tier_thresholds") or {}
        return cls(
            enabled=bool(scoring.get("enabled", False)),
            min_score_to_fire=int(scoring.get("min_score_to_fire", 5)),
            premium_threshold=int(tier_thresholds.get("premium", 12)),
            standard_threshold=int(tier_thresholds.get("standard", 8)),
            weak_threshold=int(tier_thresholds.get("weak", 5)),
            cooldown_seconds=int(scoring.get("cooldown_seconds", 1800)),
            dedupe_within_ttl=bool(scoring.get("dedupe_within_ttl", True)),
            factors=factors,
            sell_on_rush=GuardConfig(
                window_minutes=int(guards.get("sell_on_rush", {}).get("window_minutes", 60)),
                brackets=sell_brackets,
            ),
            buy_on_fall=GuardConfig(
                window_minutes=int(guards.get("buy_on_fall", {}).get("window_minutes", 60)),
                brackets=buy_brackets,
            ),
        )


@dataclass
class ScoreBreakdown:
    """Audit-grade breakdown of one verdict."""
    buy_contributions: list[tuple[str, int]] = field(default_factory=list)
    sell_contributions: list[tuple[str, int]] = field(default_factory=list)
    buy_guard_penalty: int = 0
    sell_guard_penalty: int = 0
    raw_buy_score: int = 0
    raw_sell_score: int = 0
    final_buy_score: int = 0
    final_sell_score: int = 0
    net_score: int = 0
    winning_side: Side = Side.FLAT


@dataclass
class BitUnixVerdict:
    tier: Tier
    side: Side
    breakdown: ScoreBreakdown
    reason: str
    cooldown_blocked: bool = False


def _resolve_factor(
    signal_name: str, factors: dict[str, FactorConfig],
) -> FactorConfig | None:
    f = factors.get(signal_name.lower())
    if f is None:
        f = factors.get(_strip_directional_suffix(signal_name))
    return f


def filter_live_alerts_with_dedupe(
    alerts: list[AlertEvent],
    config: BitUnixConfluenceConfig,
    now: datetime,
) -> list[AlertEvent]:
    """TTL filter + optional dedupe by signal_name.

    When `dedupe_within_ttl=True`, multiple fires of the same
    `signal_name` within their TTL collapse to the most-recent fire.
    This is the key change from `btc_accumulator.filter_live_alerts`:
    spot wants stacking (more confluence = more conviction); futures
    wants different signals to count, not repeated fires of the same.
    """
    live: list[AlertEvent] = []
    zero = timedelta(0)
    for a in alerts:
        f = _resolve_factor(a.signal_name, config.factors)
        if f is None:
            continue
        age = now - a.ts
        if age < zero:
            continue   # look-ahead guard
        if f.ttl_minutes <= 0:
            live.append(a)
            continue
        if age <= timedelta(minutes=f.ttl_minutes):
            live.append(a)

    if not config.dedupe_within_ttl:
        return live

    # Dedupe: keep only the most-recent fire per signal_name (lower-cased).
    most_recent: dict[str, AlertEvent] = {}
    for a in live:
        key = a.signal_name.lower()
        existing = most_recent.get(key)
        if existing is None or a.ts > existing.ts:
            most_recent[key] = a
    return sorted(most_recent.values(), key=lambda a: a.ts)


def _tier_from_net_score(
    net_score: int, config: BitUnixConfluenceConfig,
) -> Tier:
    if net_score < config.min_score_to_fire:
        return Tier.SKIP
    if net_score >= config.premium_threshold:
        return Tier.PREMIUM
    if net_score >= config.standard_threshold:
        return Tier.STANDARD
    if net_score >= config.weak_threshold:
        return Tier.WEAK
    return Tier.SKIP


def evaluate_confluence_futures(
    *,
    live_alerts: list[AlertEvent],
    price_ctx: PriceContext,
    config: BitUnixConfluenceConfig,
    now: datetime,
    last_fire_ts_buy: datetime | None = None,
    last_fire_ts_sell: datetime | None = None,
) -> BitUnixVerdict:
    """Score live alerts + price context, return tier verdict.

    Args:
        live_alerts: TTL-filtered + deduped (caller uses
            `filter_live_alerts_with_dedupe`).
        price_ctx: current price-action context.
        config: parsed scoring block.
        now: evaluation timestamp.
        last_fire_ts_buy / last_fire_ts_sell: timestamps of the last
            buy / sell tier fire (for cooldown). None = never fired.

    Returns:
        BitUnixVerdict with tier, winning side, full breakdown, and
        whether the cooldown gate blocked an otherwise-firing trade.
    """
    breakdown = ScoreBreakdown()

    # Step 1 — signal-driven contributions
    for a in live_alerts:
        f = _resolve_factor(a.signal_name, config.factors)
        if f is None:
            continue
        side = f.side
        if side == "directional":
            inferred = _directional_side(a.signal_name)
            if inferred is None:
                continue
            side = inferred
        if side == "buy":
            breakdown.buy_contributions.append((a.signal_name, f.weight))
            breakdown.raw_buy_score += f.weight
        elif side == "sell":
            breakdown.sell_contributions.append((a.signal_name, f.weight))
            breakdown.raw_sell_score += f.weight

    # Step 2 — price-action contributions (no TTL; evaluated fresh)
    pa_factors = (
        ("above_session_vwap", price_ctx.above_session_vwap),
        ("below_session_vwap", price_ctx.below_session_vwap),
        ("higher_highs_4h", price_ctx.higher_highs_4h),
        ("lower_lows_4h", price_ctx.lower_lows_4h),
        ("volume_above_20bar_avg", price_ctx.volume_above_20bar_avg),
    )
    for name, active in pa_factors:
        if not active:
            continue
        f = config.factors.get(name)
        if f is None:
            continue
        if f.side == "buy":
            breakdown.buy_contributions.append((name, f.weight))
            breakdown.raw_buy_score += f.weight
        elif f.side == "sell":
            breakdown.sell_contributions.append((name, f.weight))
            breakdown.raw_sell_score += f.weight
        elif f.side == "directional":
            # Strength-of-move indicator — adds to BOTH sides
            breakdown.buy_contributions.append((name, f.weight))
            breakdown.sell_contributions.append((name, f.weight))
            breakdown.raw_buy_score += f.weight
            breakdown.raw_sell_score += f.weight

    # Step 3 — guards (penalties)
    if price_ctx.pct_change_in_window_sell > 0:
        breakdown.sell_guard_penalty = _guard_penalty_for_pct(
            price_ctx.pct_change_in_window_sell, config.sell_on_rush.brackets,
        )
    if price_ctx.pct_change_in_window_buy < 0:
        breakdown.buy_guard_penalty = _guard_penalty_for_pct(
            abs(price_ctx.pct_change_in_window_buy), config.buy_on_fall.brackets,
        )

    breakdown.final_buy_score = breakdown.raw_buy_score + breakdown.buy_guard_penalty
    breakdown.final_sell_score = breakdown.raw_sell_score + breakdown.sell_guard_penalty

    # Step 4 — pick winning side from MAX(final scores); SKIP if tied at zero
    if breakdown.final_buy_score > breakdown.final_sell_score:
        winning_side = Side.BUY
        net = breakdown.final_buy_score - breakdown.final_sell_score
    elif breakdown.final_sell_score > breakdown.final_buy_score:
        winning_side = Side.SELL
        net = breakdown.final_sell_score - breakdown.final_buy_score
    else:
        winning_side = Side.FLAT
        net = 0

    breakdown.net_score = net
    breakdown.winning_side = winning_side

    # Step 5 — tier from net score
    tier = _tier_from_net_score(net, config)

    if tier == Tier.SKIP or winning_side == Side.FLAT:
        return BitUnixVerdict(
            tier=Tier.SKIP,
            side=winning_side,
            breakdown=breakdown,
            reason=(
                f"net_score={net} below min_score_to_fire="
                f"{config.min_score_to_fire} (buy={breakdown.final_buy_score}, "
                f"sell={breakdown.final_sell_score})"
            ),
        )

    # Step 6 — cooldown gate
    last_fire = last_fire_ts_buy if winning_side == Side.BUY else last_fire_ts_sell
    if last_fire is not None:
        elapsed = (now - last_fire).total_seconds()
        if 0 <= elapsed < config.cooldown_seconds:
            return BitUnixVerdict(
                tier=Tier.SKIP,
                side=winning_side,
                breakdown=breakdown,
                reason=(
                    f"cooldown: {winning_side.value} last fired "
                    f"{elapsed:.0f}s ago (< {config.cooldown_seconds}s)"
                ),
                cooldown_blocked=True,
            )

    return BitUnixVerdict(
        tier=tier,
        side=winning_side,
        breakdown=breakdown,
        reason=(
            f"{tier.value} {winning_side.value}: net_score={net} "
            f"(buy={breakdown.final_buy_score}, sell={breakdown.final_sell_score})"
        ),
    )
