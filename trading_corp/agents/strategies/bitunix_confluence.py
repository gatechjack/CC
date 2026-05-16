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
    "BitUnixAlertEvent",
    "BitUnixConfluenceConfig",
    "BitUnixVerdict",
    "PriceContext",
    "Side",
    "Tier",
    "evaluate_confluence_futures",
    "filter_live_alerts_with_dedupe",
]


@dataclass(frozen=True)
class BitUnixAlertEvent:
    """PR 3c — AlertEvent variant carrying chart timeframe.

    Observer constructs these from `bitunix_signal_ledger` rows (which
    gain a `tf` column in PR 3c). Score engine reads tf via `getattr`
    so the same code path also accepts the legacy `AlertEvent` (tf
    treated as None — drops out under any active TF filter).
    """
    ts: datetime
    signal_name: str
    tf: str | None = None


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
    """Parsed `bitunix_futures.scoring` block from strategies.yaml.

    PR 3a additions (all default to preserve pre-PR-3 behavior so this
    code change is a no-op until YAML is updated in PR 3c):

      - `score_timeframes`: tuple of chart-timeframe strings ("3m", "15m",
        "1d") that count toward score. Alerts whose `tf` is not in this
        set hit the ledger (still audited / displayed) but contribute 0
        to score. None = allow all (current behavior).

      - `factor_ttl_per_tf`: per-(signal_name, tf) TTL override. Lookup
        order in the filter: `factor_ttl_per_tf[name][tf]` →
        `FactorConfig.ttl_minutes`. Empty dict = use FactorConfig's TTL
        for all TFs (current behavior).

      - `pa_factors_in_score`: when False, price-action factors
        (above_session_vwap, higher_highs_4h, etc.) are NOT added to
        the score. They become inputs to `bitunix_pa_validation` (the
        new post-score gate) instead. Default True = current behavior.

      - `guards_in_score`: when False, sell_on_rush / buy_on_fall guard
        penalties are NOT applied to the score. They become hard-reject
        conditions in `bitunix_pa_validation` instead. Default True =
        current behavior.
    """
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
    # PR 3a additions (defaults preserve pre-PR-3 behavior)
    score_timeframes: tuple[str, ...] | None = None
    factor_ttl_per_tf: dict[str, dict[str, int]] = field(default_factory=dict)
    pa_factors_in_score: bool = True
    guards_in_score: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BitUnixConfluenceConfig":
        scoring = raw.get("scoring") or {}
        factors: dict[str, FactorConfig] = {}
        factor_ttl_per_tf: dict[str, dict[str, int]] = {}
        for name, body in (scoring.get("factors") or {}).items():
            factors[name] = FactorConfig(
                name=name,
                weight=int(body["weight"]),
                side=str(body["side"]),
                ttl_minutes=int(body.get("ttl_minutes", 0)),
            )
            # New: per-TF TTL override map. YAML shape:
            #   mc_a_blood_diamond:
            #     weight: 5
            #     side: sell
            #     ttl_per_tf: {"3m": 30, "15m": 90, "30m": 180}
            ttl_per_tf_raw = body.get("ttl_per_tf")
            if isinstance(ttl_per_tf_raw, dict):
                factor_ttl_per_tf[name] = {
                    str(tf): int(ttl) for tf, ttl in ttl_per_tf_raw.items()
                }
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
        # New: optional TF filter list. YAML shape:
        #   score_timeframes: ["3m", "15m", "30m"]
        score_timeframes_raw = scoring.get("score_timeframes")
        score_timeframes: tuple[str, ...] | None = None
        if score_timeframes_raw is not None:
            score_timeframes = tuple(str(tf) for tf in score_timeframes_raw)
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
            score_timeframes=score_timeframes,
            factor_ttl_per_tf=factor_ttl_per_tf,
            pa_factors_in_score=bool(scoring.get("pa_factors_in_score", True)),
            guards_in_score=bool(scoring.get("guards_in_score", True)),
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


def _ttl_for_alert(
    factor: FactorConfig,
    signal_name: str,
    tf: str | None,
    config: BitUnixConfluenceConfig,
) -> int:
    """Resolve effective TTL (in minutes) for a (signal, tf) pair.

    Lookup order:
      1. config.factor_ttl_per_tf[signal_name][tf] (per-TF override)
      2. factor.ttl_minutes (legacy single TTL)

    A 0 (or negative) TTL means "no expiry" — the alert is always live
    once it lands in the ledger.
    """
    if tf is not None:
        per_tf = config.factor_ttl_per_tf.get(signal_name)
        if per_tf is not None:
            override = per_tf.get(tf)
            if override is not None:
                return int(override)
    return int(factor.ttl_minutes)


def filter_live_alerts_with_dedupe(
    alerts: list[AlertEvent],
    config: BitUnixConfluenceConfig,
    now: datetime,
) -> list[AlertEvent]:
    """TTL filter + optional TF filter + optional dedupe by signal_name.

    When `dedupe_within_ttl=True`, multiple fires of the same
    `signal_name` within their TTL collapse to the most-recent fire.
    This is the key change from `btc_accumulator.filter_live_alerts`:
    spot wants stacking (more confluence = more conviction); futures
    wants different signals to count, not repeated fires of the same.

    PR 3a: when `config.score_timeframes` is set, drop alerts whose
    chart timeframe (read via `getattr(alert, 'tf', None)`) is not in
    the allowed set. Alerts without a `tf` attribute are treated as
    None — they pass through when no filter is configured (current
    behavior), and are dropped when a filter IS configured (because
    we have no way to verify they're from an allowed TF).

    Per-TF TTL lookup happens via `_ttl_for_alert`: if YAML
    declares `factor_ttl_per_tf[name][tf]`, that wins; otherwise fall
    back to the factor's single `ttl_minutes`.
    """
    live: list[AlertEvent] = []
    zero = timedelta(0)
    allowed_tfs = config.score_timeframes
    for a in alerts:
        f = _resolve_factor(a.signal_name, config.factors)
        if f is None:
            continue
        # TF filter (PR 3a). When unset (default), all TFs pass.
        alert_tf = getattr(a, "tf", None)
        if allowed_tfs is not None and alert_tf not in allowed_tfs:
            continue
        age = now - a.ts
        if age < zero:
            continue   # look-ahead guard
        ttl = _ttl_for_alert(f, a.signal_name.lower(), alert_tf, config)
        if ttl <= 0:
            live.append(a)
            continue
        if age <= timedelta(minutes=ttl):
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

    # Step 2 — price-action contributions (no TTL; evaluated fresh).
    # PR 3a: skipped when `config.pa_factors_in_score=False` because PA
    # has moved to the new post-score `bitunix_pa_validation` gate (a
    # binary pass/fail on the trade rather than a score nudge). Default
    # True preserves pre-PR-3 behavior.
    if config.pa_factors_in_score:
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

    # Step 3 — guards (penalties).
    # PR 3a: skipped when `config.guards_in_score=False` because the
    # rush/fall semantics have moved to `bitunix_pa_validation` as
    # binary hard-reject conditions (>5% adverse 60min move = reject
    # outright instead of -3 score points). Default True preserves
    # pre-PR-3 behavior.
    if config.guards_in_score:
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
