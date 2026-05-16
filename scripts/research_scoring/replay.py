"""Step 2 — vectorized replay harness for BitUnix scoring configs.

Walks the synthesized ledger chronologically through a configurable
BitUnixConfluenceConfig. Uses the REAL `evaluate_confluence_futures` so
verdict math is identical to live. When a tier fires, simulates a paper
trade with legacy _build_proposal math (stop = max(1.5×ATR, 0.3%×entry),
TP = 2R) on 3m bars, 9 bps round-trip cost.

Supports:
- Standard config (subtractive `winner − loser`)
- Asymmetric formula: `winner − α × loser`
- Conviction ratio: `winner / (winner + loser)`, threshold in [0,1]
- Family confluence: require ≥N distinct factor-families per tier
- Unified cooldown: pause BOTH sides after any fire
- Stacking allowed within TTL (cap at N fires per signal)

Reports: trade count, win rate, mean_r, sum_r, Sharpe (R-units),
profit factor, max DD (R), trades/day, per-tier breakdown.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "research_scoring"))

from synth_ledger import (  # noqa: E402
    COL_TO_FACTOR,
    SynthAlert,
    load_bars_3m_for_resolution,
    load_synth_ledger,
)
from edge_inventory import BarIndex, simulate_2r_trade, YAML_PATH  # noqa: E402
from trading_corp.agents.strategies.bitunix_confluence import (  # noqa: E402
    AlertEvent,
    BitUnixAlertEvent,
    BitUnixConfluenceConfig,
    BitUnixVerdict,
    Side,
    Tier,
    evaluate_confluence_futures,
    filter_live_alerts_with_dedupe,
    _resolve_factor,
    _ttl_for_alert,
)
from trading_corp.agents.strategies.btc_accumulator import (  # noqa: E402
    FactorConfig,
    GuardBracket,
    GuardConfig,
    PriceContext,
    _directional_side,
)


# Factor-family map (for family-confluence variants).
def factor_family(signal_name: str) -> str:
    n = signal_name
    if n.startswith("mc_a_"):
        return "cypher_a"
    if n.startswith("mc_b_"):
        return "cypher_b"
    if n.startswith("otter_"):
        return "otter_trigger"
    if n.startswith(("money_bag_", "water_", "spoon_")):
        return "otter_precision"
    if n.startswith("cvd_"):
        return "cvd"
    if n.startswith("bias_") or n.startswith("ribbon_"):
        return "ribbon"
    if "vwap" in n or "highs" in n or "lows" in n or "volume" in n:
        return "pa"
    return "other"


@dataclass
class VariantConfig:
    """Higher-level wrapper around BitUnixConfluenceConfig with the extras
    the replay supports (asymmetric, conviction-ratio, family-confluence,
    unified cooldown)."""
    name: str
    base: BitUnixConfluenceConfig
    # Formula
    asymmetric_alpha: float | None = None        # net = winner − α × loser
    conviction_ratio_threshold: float | None = None  # net = winner/(winner+loser); fire when ≥ this
    # Family confluence
    families_required_premium: int | None = None
    families_required_standard: int | None = None
    # Cooldown variant
    unified_cooldown: bool = False
    # Re-weighted factor overrides (sparse; key=name)
    factor_weight_overrides: dict[str, int] = field(default_factory=dict)
    factor_ttl_overrides: dict[str, dict[str, int]] = field(default_factory=dict)
    # Tier-threshold overrides (applies on TOP of base)
    min_score_override: int | None = None
    premium_override: int | None = None
    standard_override: int | None = None
    weak_override: int | None = None

    def materialize_base(self) -> BitUnixConfluenceConfig:
        b = self.base
        factors = {}
        for name, fc in b.factors.items():
            w = self.factor_weight_overrides.get(name, fc.weight)
            factors[name] = FactorConfig(name=fc.name, weight=w, side=fc.side,
                                          ttl_minutes=fc.ttl_minutes)
        # Per-TF TTL overrides
        ttl_per_tf = dict(b.factor_ttl_per_tf)
        for name, override in self.factor_ttl_overrides.items():
            ttl_per_tf[name] = override
        return BitUnixConfluenceConfig(
            enabled=b.enabled,
            min_score_to_fire=self.min_score_override if self.min_score_override is not None else b.min_score_to_fire,
            premium_threshold=self.premium_override if self.premium_override is not None else b.premium_threshold,
            standard_threshold=self.standard_override if self.standard_override is not None else b.standard_threshold,
            weak_threshold=self.weak_override if self.weak_override is not None else b.weak_threshold,
            cooldown_seconds=b.cooldown_seconds,
            dedupe_within_ttl=b.dedupe_within_ttl,
            factors=factors,
            sell_on_rush=b.sell_on_rush,
            buy_on_fall=b.buy_on_fall,
            score_timeframes=b.score_timeframes,
            factor_ttl_per_tf=ttl_per_tf,
            pa_factors_in_score=b.pa_factors_in_score,
            guards_in_score=b.guards_in_score,
        )


def load_baseline_config(yaml_path: Path = YAML_PATH) -> BitUnixConfluenceConfig:
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return BitUnixConfluenceConfig.from_dict(cfg["bitunix_futures"])


def _to_alert_event(a: SynthAlert) -> AlertEvent:
    """Replay uses the AlertEvent shape evaluate_confluence_futures consumes.
    We use BitUnixAlertEvent so the `tf` attribute is populated for the
    PR 3c TF filter."""
    return BitUnixAlertEvent(ts=a.ts, signal_name=a.signal_name, tf=a.tf)


def _evaluate_variant(
    *, live: list[AlertEvent], variant: VariantConfig, base_cfg: BitUnixConfluenceConfig,
    now: datetime,
    last_fire_ts_buy: datetime | None,
    last_fire_ts_sell: datetime | None,
) -> BitUnixVerdict:
    """Score with the variant's formula / family-confluence / etc.

    For 'standard subtractive', we call evaluate_confluence_futures directly.
    For 'asymmetric' and 'conviction_ratio' we replicate the scoring
    pipeline here so we can substitute the net-score formula.
    """
    # Run the official scorer FIRST to get contributions + raw scores.
    # We'll just substitute the net-score formula and tier mapping when
    # the variant calls for it.
    v = evaluate_confluence_futures(
        live_alerts=live, price_ctx=_FLAT_CTX, config=base_cfg, now=now,
        last_fire_ts_buy=last_fire_ts_buy, last_fire_ts_sell=last_fire_ts_sell,
    )

    # If asymmetric or conviction ratio, recompute tier from raw scores.
    bd = v.breakdown
    buy_score, sell_score = bd.final_buy_score, bd.final_sell_score

    if variant.conviction_ratio_threshold is not None:
        total = buy_score + sell_score
        if total == 0:
            return _skip_verdict(v, "conviction_ratio: total=0")
        ratio_buy = buy_score / total
        ratio_sell = sell_score / total
        if ratio_buy > ratio_sell:
            winner, loser = "buy", "sell"
            ratio = ratio_buy
        else:
            winner, loser = "sell", "buy"
            ratio = ratio_sell
        if ratio < variant.conviction_ratio_threshold:
            return _skip_verdict(v, f"ratio {ratio:.2f} < {variant.conviction_ratio_threshold}")
        # Force this to be a STANDARD fire (conviction-ratio doesn't have natural tiers).
        tier = Tier.PREMIUM if max(buy_score, sell_score) >= base_cfg.premium_threshold else Tier.STANDARD
        side = Side.BUY if winner == "buy" else Side.SELL
        return _maybe_fire(v, tier, side, base_cfg, now, last_fire_ts_buy, last_fire_ts_sell,
                           variant.unified_cooldown)

    if variant.asymmetric_alpha is not None:
        alpha = variant.asymmetric_alpha
        if buy_score > sell_score:
            winner_score, loser_score = buy_score, sell_score
            side = Side.BUY
        elif sell_score > buy_score:
            winner_score, loser_score = sell_score, buy_score
            side = Side.SELL
        else:
            return _skip_verdict(v, "asymmetric: tied")
        net = winner_score - alpha * loser_score
        if net < base_cfg.min_score_to_fire:
            return _skip_verdict(v, f"asymmetric net={net:.2f} < {base_cfg.min_score_to_fire}")
        if net >= base_cfg.premium_threshold:
            tier = Tier.PREMIUM
        elif net >= base_cfg.standard_threshold:
            tier = Tier.STANDARD
        elif net >= base_cfg.weak_threshold:
            tier = Tier.WEAK
        else:
            return _skip_verdict(v, f"asymmetric net={net:.2f} below tiers")
        return _maybe_fire(v, tier, side, base_cfg, now, last_fire_ts_buy, last_fire_ts_sell,
                           variant.unified_cooldown)

    # Standard formula path — we already have the verdict.
    # Family confluence overlay (applies to non-SKIP tiers only).
    if v.tier != Tier.SKIP and (variant.families_required_premium or variant.families_required_standard):
        if v.side == Side.BUY:
            contribs = bd.buy_contributions
        elif v.side == Side.SELL:
            contribs = bd.sell_contributions
        else:
            contribs = []
        n_families = len({factor_family(n) for n, _ in contribs if factor_family(n) not in ("pa",)})
        if v.tier == Tier.PREMIUM and variant.families_required_premium and n_families < variant.families_required_premium:
            # Demote to STANDARD if it still qualifies
            if bd.net_score >= base_cfg.standard_threshold:
                return _maybe_fire(v, Tier.STANDARD, v.side, base_cfg, now,
                                    last_fire_ts_buy, last_fire_ts_sell,
                                    variant.unified_cooldown)
            return _skip_verdict(v, f"family-confluence: {n_families} families < {variant.families_required_premium} required")
        if v.tier == Tier.STANDARD and variant.families_required_standard and n_families < variant.families_required_standard:
            return _skip_verdict(v, f"family-confluence: {n_families} families < {variant.families_required_standard}")
    # Unified cooldown overlay
    if variant.unified_cooldown and v.tier != Tier.SKIP:
        return _maybe_fire(v, v.tier, v.side, base_cfg, now, last_fire_ts_buy, last_fire_ts_sell, True)
    return v


def _skip_verdict(prev: BitUnixVerdict, reason: str) -> BitUnixVerdict:
    return BitUnixVerdict(tier=Tier.SKIP, side=prev.side, breakdown=prev.breakdown, reason=reason)


def _maybe_fire(
    prev: BitUnixVerdict, tier: Tier, side: Side,
    base_cfg: BitUnixConfluenceConfig,
    now: datetime,
    last_fire_ts_buy: datetime | None,
    last_fire_ts_sell: datetime | None,
    unified: bool,
) -> BitUnixVerdict:
    # Cooldown check — same semantics as evaluate_confluence_futures.
    if unified:
        # Pause both sides after any fire.
        relevant = None
        for lf in (last_fire_ts_buy, last_fire_ts_sell):
            if lf is None:
                continue
            if relevant is None or lf > relevant:
                relevant = lf
    else:
        relevant = last_fire_ts_buy if side == Side.BUY else last_fire_ts_sell
    if relevant is not None:
        elapsed = (now - relevant).total_seconds()
        if 0 <= elapsed < base_cfg.cooldown_seconds:
            return BitUnixVerdict(tier=Tier.SKIP, side=side, breakdown=prev.breakdown,
                                  reason=f"cooldown blocked",  cooldown_blocked=True)
    return BitUnixVerdict(tier=tier, side=side, breakdown=prev.breakdown,
                          reason=f"{tier.value} {side.value}")


_FLAT_CTX = PriceContext(
    current_price=0.0,
    above_session_vwap=False, below_session_vwap=False,
    higher_highs_4h=False, lower_lows_4h=False,
    volume_above_20bar_avg=False,
    pct_change_in_window_sell=0.0, pct_change_in_window_buy=0.0,
)


@dataclass
class ReplayResult:
    variant: str
    n_alerts: int
    n_fires: int
    fires_by_tier: dict[str, int]
    fires_by_side: dict[str, int]
    n_skips: int
    n_skipped_score: int
    n_skipped_cooldown: int
    n_trades_resolved: int
    win_rate: float
    mean_r: float
    sum_r: float
    median_r: float
    sharpe_r: float
    profit_factor: float
    max_drawdown_r: float
    trades_per_day: float
    n_days: float
    per_tier_stats: dict[str, dict]
    sample_trades: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "n_alerts": self.n_alerts,
            "n_fires": self.n_fires,
            "fires_by_tier": self.fires_by_tier,
            "fires_by_side": self.fires_by_side,
            "n_skips": self.n_skips,
            "n_skipped_score": self.n_skipped_score,
            "n_skipped_cooldown": self.n_skipped_cooldown,
            "n_trades_resolved": self.n_trades_resolved,
            "win_rate": round(self.win_rate, 3),
            "mean_r": round(self.mean_r, 3),
            "sum_r": round(self.sum_r, 2),
            "median_r": round(self.median_r, 3),
            "sharpe_r": round(self.sharpe_r, 2),
            "profit_factor": round(self.profit_factor, 2) if math.isfinite(self.profit_factor) else None,
            "max_drawdown_r": round(self.max_drawdown_r, 2),
            "trades_per_day": round(self.trades_per_day, 2),
            "n_days": round(self.n_days, 1),
            "per_tier_stats": self.per_tier_stats,
        }


def run_replay(
    alerts: list[SynthAlert], bars_idx: BarIndex, variant: VariantConfig,
    *, start: datetime | None = None, end: datetime | None = None,
    label: str | None = None,
) -> ReplayResult:
    """Walk alerts chronologically through the configurable scorer."""
    base = variant.materialize_base()
    # Filter alerts to window (entry must be inside bar-resolution window too)
    bars_first = datetime.fromtimestamp(bars_idx.ts[0], tz=timezone.utc)
    bars_last = datetime.fromtimestamp(bars_idx.ts[-1], tz=timezone.utc)
    window_start = max(start or bars_first, bars_first)
    window_end = min(end or bars_last, bars_last)

    alerts_window = [a for a in alerts if window_start <= a.ts <= window_end]

    # State: rolling 'live' alerts buffer trimmed to max-TTL window.
    # For replay correctness, we feed the entire history-so-far each evaluation
    # and let filter_live_alerts_with_dedupe do the TTL filtering. This is the
    # exact pattern the observer uses.
    max_ttl_minutes = max((f.ttl_minutes for f in base.factors.values()), default=180)
    for tf_map in base.factor_ttl_per_tf.values():
        for v in tf_map.values():
            max_ttl_minutes = max(max_ttl_minutes, v)
    max_ttl = timedelta(minutes=max_ttl_minutes)

    history: list[AlertEvent] = []
    last_buy_fire: datetime | None = None
    last_sell_fire: datetime | None = None

    fires_by_tier = {"PREMIUM": 0, "STANDARD": 0, "WEAK": 0}
    fires_by_side = {"buy": 0, "sell": 0}
    n_skipped_score = 0
    n_skipped_cooldown = 0
    n_fires = 0

    trades: list[tuple[str, float, str, datetime]] = []  # (outcome, R, tier, ts)

    for a in alerts_window:
        # Append to history, evict expired
        history.append(_to_alert_event(a))
        cutoff = a.ts - max_ttl
        history = [h for h in history if h.ts >= cutoff]

        # Filter for live (TTL + dedupe + TF filter)
        live = filter_live_alerts_with_dedupe(history, base, a.ts)
        if not live:
            continue

        verdict = _evaluate_variant(
            live=live, variant=variant, base_cfg=base, now=a.ts,
            last_fire_ts_buy=last_buy_fire, last_fire_ts_sell=last_sell_fire,
        )

        if verdict.tier == Tier.SKIP:
            if verdict.cooldown_blocked:
                n_skipped_cooldown += 1
            else:
                n_skipped_score += 1
            continue

        # Fire: simulate trade
        n_fires += 1
        fires_by_tier[verdict.tier.value] = fires_by_tier.get(verdict.tier.value, 0) + 1
        side_str = "buy" if verdict.side == Side.BUY else "sell"
        fires_by_side[side_str] = fires_by_side.get(side_str, 0) + 1

        ts_secs = int(a.ts.timestamp())
        res = simulate_2r_trade(bars_idx, ts_secs, side_str)
        if res is None:
            continue
        outcome, r_mult = res
        trades.append((outcome, r_mult, verdict.tier.value, a.ts))

        # Update cooldown state
        if verdict.side == Side.BUY:
            last_buy_fire = a.ts
        elif verdict.side == Side.SELL:
            last_sell_fire = a.ts
        if variant.unified_cooldown:
            # Both sides
            last_buy_fire = a.ts
            last_sell_fire = a.ts

    # Compute metrics
    n_resolved = len(trades)
    if n_resolved == 0:
        return ReplayResult(
            variant=label or variant.name, n_alerts=len(alerts_window),
            n_fires=n_fires, fires_by_tier=fires_by_tier, fires_by_side=fires_by_side,
            n_skips=n_skipped_score + n_skipped_cooldown,
            n_skipped_score=n_skipped_score, n_skipped_cooldown=n_skipped_cooldown,
            n_trades_resolved=0, win_rate=0, mean_r=0, sum_r=0, median_r=0, sharpe_r=0,
            profit_factor=float("nan"), max_drawdown_r=0, trades_per_day=0,
            n_days=(window_end - window_start).total_seconds() / 86400,
            per_tier_stats={},
        )

    r_vals = [r for _, r, _, _ in trades]
    win_rate = sum(1 for r in r_vals if r > 0) / n_resolved
    mean_r = statistics.mean(r_vals)
    sum_r = sum(r_vals)
    median_r = statistics.median(r_vals)
    stdev = statistics.stdev(r_vals) if len(r_vals) > 1 else 1
    sharpe_r = (mean_r / stdev) * math.sqrt(n_resolved) if stdev > 0 else 0
    pos = sum(r for r in r_vals if r > 0)
    neg = -sum(r for r in r_vals if r < 0)
    pf = pos / neg if neg > 0 else float("inf") if pos > 0 else 0
    # Max drawdown
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for r in r_vals:
        cum += r
        peak = max(peak, cum)
        dd = peak - cum
        mdd = max(mdd, dd)
    days = (window_end - window_start).total_seconds() / 86400
    tpd = n_resolved / days if days > 0 else 0

    # Per-tier
    per_tier: dict[str, dict] = {}
    for tier_name in ("PREMIUM", "STANDARD", "WEAK"):
        sub = [r for _, r, t, _ in trades if t == tier_name]
        if not sub:
            per_tier[tier_name] = {"n": 0}
            continue
        per_tier[tier_name] = {
            "n": len(sub),
            "win_rate": round(sum(1 for r in sub if r > 0) / len(sub), 3),
            "mean_r": round(statistics.mean(sub), 3),
            "sum_r": round(sum(sub), 2),
        }

    return ReplayResult(
        variant=label or variant.name, n_alerts=len(alerts_window),
        n_fires=n_fires, fires_by_tier=fires_by_tier, fires_by_side=fires_by_side,
        n_skips=n_skipped_score + n_skipped_cooldown,
        n_skipped_score=n_skipped_score, n_skipped_cooldown=n_skipped_cooldown,
        n_trades_resolved=n_resolved, win_rate=win_rate, mean_r=mean_r, sum_r=sum_r,
        median_r=median_r, sharpe_r=sharpe_r, profit_factor=pf, max_drawdown_r=mdd,
        trades_per_day=tpd, n_days=days, per_tier_stats=per_tier,
    )


def quick_sanity_check() -> None:
    """Run the baseline config across the full window — should reproduce
    the prior Apr 30 – May 9 verdict order-of-magnitude (~21 fires,
    42-43% WR, +0.29 avg R). Different window / different ledger source
    so exact numbers won't match, but we expect baseline to be in the
    right ballpark and the variant comparisons to be meaningful."""
    print("loading...")
    alerts = load_synth_ledger()
    bars = load_bars_3m_for_resolution()
    idx = BarIndex.build(bars)
    baseline = load_baseline_config()
    print(f"baseline: tiers premium={baseline.premium_threshold} std={baseline.standard_threshold} min={baseline.min_score_to_fire}")
    v = VariantConfig(name="baseline_current_yaml", base=baseline)
    res = run_replay(alerts, idx, v)
    import pprint
    pprint.pprint(res.to_dict())


if __name__ == "__main__":
    quick_sanity_check()
