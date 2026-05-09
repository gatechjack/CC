"""Coinbase BTC Accumulator — confluence scoring engine.

Pure-function scorer used by both the Phase 1 backtest harness and the
(forthcoming) Phase 2 live strategy module. No broker calls, no audit
writes, no I/O — caller supplies all inputs as plain dataclasses, gets
back a `ConfluenceVerdict`.

Strategy shape (full design in BACKLOG.md / 2026-05-08 chat decisions):
  - Single-instrument 100%-in/out CASH ↔ BTC state machine on coinbase_spot.
  - TV alerts from Otter (3m) and Cypher (4h/1D) trigger a confluence
    review. Alerts NEVER auto-execute.
  - Buy fires when accumulated buy_score >= min_score_buy AND state=CASH.
  - Sell fires when sell_score >= min_score_sell AND state=BTC.
  - Symmetric "wait for stabilization" guards penalize selling into fast
    rises and buying into fast falls (score adjustments, not hard vetos).
  - Cypher HTF signals carry persistent weight (24h on 1D, 4h on 4h);
    Otter 3m signals expire fast (15-30min).

Configuration: `btc_accumulator` block in `config/strategies.yaml`.
The scorer reads the block via `ConfluenceConfig.from_dict()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class Decision(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SKIP = "skip"


class State(str, Enum):
    CASH = "cash"
    BTC = "btc"


@dataclass(frozen=True)
class FactorConfig:
    """One row in the `factors` block of strategies.yaml."""
    name: str
    weight: int
    side: str            # "buy" | "sell" | "directional"
    ttl_minutes: int = 0  # 0 = price-action factor (always-evaluated, no TTL)


@dataclass(frozen=True)
class GuardBracket:
    upto_pct: float      # for sell-on-rush: BTC rose UP TO this pct
                         # for buy-on-fall: BTC fell UP TO this pct (positive number)
    penalty: int         # negative integer (subtracted from the relevant side's score)


@dataclass(frozen=True)
class GuardConfig:
    window_minutes: int
    brackets: tuple[GuardBracket, ...]   # sorted ascending by upto_pct


@dataclass(frozen=True)
class ConfluenceConfig:
    min_score_buy: int
    min_score_sell: int
    factors: dict[str, FactorConfig]
    sell_on_rush: GuardConfig
    buy_on_fall: GuardConfig
    log_confluence_negative: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ConfluenceConfig:
        """Parse the `btc_accumulator` block from strategies.yaml."""
        conf = raw["confluence"]
        factors: dict[str, FactorConfig] = {}
        for name, body in conf["factors"].items():
            factors[name] = FactorConfig(
                name=name,
                weight=int(body["weight"]),
                side=str(body["side"]),
                ttl_minutes=int(body.get("ttl_minutes", 0)),
            )
        guards = raw["guards"]
        sell_brackets = tuple(
            GuardBracket(upto_pct=float(b["upto_pct"]), penalty=int(b["penalty"]))
            for b in guards["sell_on_rush"]["brackets"]
        )
        buy_brackets = tuple(
            GuardBracket(upto_pct=float(b["upto_drop_pct"]), penalty=int(b["penalty"]))
            for b in guards["buy_on_fall"]["brackets"]
        )
        return cls(
            min_score_buy=int(conf["min_score_buy"]),
            min_score_sell=int(conf["min_score_sell"]),
            factors=factors,
            sell_on_rush=GuardConfig(
                window_minutes=int(guards["sell_on_rush"]["window_minutes"]),
                brackets=sell_brackets,
            ),
            buy_on_fall=GuardConfig(
                window_minutes=int(guards["buy_on_fall"]["window_minutes"]),
                brackets=buy_brackets,
            ),
            log_confluence_negative=bool(
                raw.get("audit", {}).get("log_confluence_negative", True)
            ),
        )


@dataclass(frozen=True)
class AlertEvent:
    """One TV alert that fired. Caller supplies a list of these as the
    "live" alert window (anything older than its ttl_minutes is filtered
    out before passing in)."""
    ts: datetime
    signal_name: str    # canonical name matching a key in factors


@dataclass
class PriceContext:
    """Price-derived context at the moment of evaluation. Caller computes
    these from OHLCV; scorer just consumes the booleans + numbers."""
    current_price: float
    pct_change_in_window_sell: float  # % change over guards.sell_on_rush.window
                                       # (positive = rose, negative = fell)
    pct_change_in_window_buy: float    # % change over guards.buy_on_fall.window
                                       # (positive = rose, negative = fell)
    above_session_vwap: bool = False
    below_session_vwap: bool = False
    higher_highs_4h: bool = False
    lower_lows_4h: bool = False
    volume_above_20bar_avg: bool = False


@dataclass
class ScoreBreakdown:
    """What contributed to each side's score. Audit-grade detail —
    every audit row that records a confluence review should embed
    this so we can later reconstruct WHY a decision was (or wasn't)
    taken."""
    buy_contributions: list[tuple[str, int]] = field(default_factory=list)
    sell_contributions: list[tuple[str, int]] = field(default_factory=list)
    buy_guard_penalty: int = 0
    sell_guard_penalty: int = 0
    raw_buy_score: int = 0
    raw_sell_score: int = 0
    final_buy_score: int = 0
    final_sell_score: int = 0


@dataclass
class ConfluenceVerdict:
    decision: Decision
    breakdown: ScoreBreakdown
    reason: str


_BULL_FRAGMENTS = ("bull", "buy", "long", "diamond_bull", "premium_bull")
_BEAR_FRAGMENTS = ("bear", "sell", "short", "diamond_bear", "premium_bear")


def _directional_side(signal_name: str) -> str | None:
    """Infer buy/sell side from a signal name when the factor's `side`
    is `directional`. Returns "buy" / "sell" / None if undetermined."""
    n = signal_name.lower()
    for frag in _BULL_FRAGMENTS:
        if frag in n:
            return "buy"
    for frag in _BEAR_FRAGMENTS:
        if frag in n:
            return "sell"
    return None


def _strip_directional_suffix(signal_name: str) -> str:
    """Map a directional signal name (e.g. `otter_diamond_bull`) to its
    factor key (`otter_diamond`). Strips trailing `_bull` / `_bear` /
    `_buy` / `_sell` segments."""
    n = signal_name.lower()
    for suffix in ("_bull", "_bear", "_buy", "_sell", "_long", "_short"):
        if n.endswith(suffix):
            return n[: -len(suffix)]
    return n


def _guard_penalty_for_pct(pct_abs: float, brackets: tuple[GuardBracket, ...]) -> int:
    """Find the bracket whose `upto_pct` first exceeds the absolute pct
    and return its penalty. Brackets are checked in order; the first
    matching one wins."""
    for bracket in brackets:
        if pct_abs <= bracket.upto_pct:
            return bracket.penalty
    # Should never reach here because last bracket has upto_pct=999,
    # but defensive: return the last bracket's penalty.
    return brackets[-1].penalty if brackets else 0


def evaluate_confluence(
    *,
    state: State,
    live_alerts: list[AlertEvent],
    price_ctx: PriceContext,
    config: ConfluenceConfig,
    now: datetime,
) -> ConfluenceVerdict:
    """Compute confluence verdict.

    Caller is responsible for filtering `live_alerts` to only those
    within their per-factor TTL window — the scorer trusts the input
    and adds every supplied alert's weight to the appropriate side.
    (Pre-filter rather than filter-in-scorer because the backtest
    harness needs the same filtering for replay-correctness, and
    duplicating the logic would be a divergence risk.)

    Args:
        state: current portfolio state (CASH or BTC).
        live_alerts: TV alerts within their TTL window. Order does not
            matter; weights are summed.
        price_ctx: OHLCV-derived context for price-action factors and
            for guard penalty computation.
        config: parsed `btc_accumulator` block.
        now: evaluation timestamp (used for the Verdict's reason text).

    Returns:
        `ConfluenceVerdict` with decision (BUY / SELL / SKIP), full
        score breakdown for audit, and a human-readable reason.
    """
    breakdown = ScoreBreakdown()

    # --- Step 1: tally alert-driven contributions -----------------------
    for alert in live_alerts:
        # Try direct key match first (e.g. signal_name="cypher_4h_bull"
        # matches factor key "cypher_4h_bull").
        factor = config.factors.get(alert.signal_name.lower())
        # Fall back to stripped key for directional factors (e.g.
        # signal_name="otter_diamond_bull" → factor key "otter_diamond").
        if factor is None:
            stripped = _strip_directional_suffix(alert.signal_name)
            factor = config.factors.get(stripped)
        if factor is None:
            # Unknown signal — silently ignore. (The backtest harness
            # logs a warning at ingest time so noise stays out of the
            # hot path.)
            continue

        side = factor.side
        if side == "directional":
            inferred = _directional_side(alert.signal_name)
            if inferred is None:
                continue
            side = inferred

        if side == "buy":
            breakdown.buy_contributions.append((alert.signal_name, factor.weight))
            breakdown.raw_buy_score += factor.weight
        elif side == "sell":
            breakdown.sell_contributions.append((alert.signal_name, factor.weight))
            breakdown.raw_sell_score += factor.weight

    # --- Step 2: tally price-action contributions -----------------------
    # These use OHLCV-derived booleans rather than TV alerts. They have
    # no TTL — they're evaluated fresh at each decision point.
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
        factor = config.factors.get(name)
        if factor is None:
            continue
        if factor.side == "buy":
            breakdown.buy_contributions.append((name, factor.weight))
            breakdown.raw_buy_score += factor.weight
        elif factor.side == "sell":
            breakdown.sell_contributions.append((name, factor.weight))
            breakdown.raw_sell_score += factor.weight
        elif factor.side == "directional":
            # Directional price-action (e.g. volume_above_20bar_avg) adds
            # to BOTH sides — it's a strength-of-move indicator, not a
            # direction-of-move indicator. Higher-order signals (cypher /
            # otter) supply the direction; volume confirms.
            breakdown.buy_contributions.append((name, factor.weight))
            breakdown.sell_contributions.append((name, factor.weight))
            breakdown.raw_buy_score += factor.weight
            breakdown.raw_sell_score += factor.weight

    # --- Step 3: apply guards (penalties) -------------------------------
    # sell-on-rush: penalize sells when BTC rose. pct_change_in_window_sell
    # is signed; penalty applies only when positive (rose).
    if price_ctx.pct_change_in_window_sell > 0:
        breakdown.sell_guard_penalty = _guard_penalty_for_pct(
            price_ctx.pct_change_in_window_sell, config.sell_on_rush.brackets,
        )
    # buy-on-fall: penalize buys when BTC fell. pct_change_in_window_buy
    # is signed; penalty applies only when negative (fell). The bracket
    # `upto_pct` values are positive (representing |drop|).
    if price_ctx.pct_change_in_window_buy < 0:
        breakdown.buy_guard_penalty = _guard_penalty_for_pct(
            abs(price_ctx.pct_change_in_window_buy), config.buy_on_fall.brackets,
        )

    breakdown.final_buy_score = breakdown.raw_buy_score + breakdown.buy_guard_penalty
    breakdown.final_sell_score = breakdown.raw_sell_score + breakdown.sell_guard_penalty

    # --- Step 4: state-aware decision -----------------------------------
    if state == State.CASH:
        if breakdown.final_buy_score >= config.min_score_buy:
            return ConfluenceVerdict(
                decision=Decision.BUY,
                breakdown=breakdown,
                reason=(
                    f"buy fired @ {now.isoformat()}: "
                    f"score={breakdown.final_buy_score} >= "
                    f"min_score_buy={config.min_score_buy}"
                ),
            )
        return ConfluenceVerdict(
            decision=Decision.SKIP,
            breakdown=breakdown,
            reason=(
                f"buy skipped @ {now.isoformat()}: "
                f"score={breakdown.final_buy_score} < "
                f"min_score_buy={config.min_score_buy} "
                f"(raw={breakdown.raw_buy_score}, guard={breakdown.buy_guard_penalty})"
            ),
        )

    # state == BTC
    if breakdown.final_sell_score >= config.min_score_sell:
        return ConfluenceVerdict(
            decision=Decision.SELL,
            breakdown=breakdown,
            reason=(
                f"sell fired @ {now.isoformat()}: "
                f"score={breakdown.final_sell_score} >= "
                f"min_score_sell={config.min_score_sell}"
            ),
        )
    return ConfluenceVerdict(
        decision=Decision.SKIP,
        breakdown=breakdown,
        reason=(
            f"sell skipped @ {now.isoformat()}: "
            f"score={breakdown.final_sell_score} < "
            f"min_score_sell={config.min_score_sell} "
            f"(raw={breakdown.raw_sell_score}, guard={breakdown.sell_guard_penalty})"
        ),
    )


def filter_live_alerts(
    alerts: list[AlertEvent],
    config: ConfluenceConfig,
    now: datetime,
) -> list[AlertEvent]:
    """Helper: keep only alerts whose age satisfies 0 <= age <= ttl.

    The lower bound (0) is critical for backtest correctness — without
    it, the filter would silently accept FUTURE alerts (negative age
    compares as <= positive TTL), giving the strategy look-ahead. The
    live strategy's input stream is naturally past-only, but the same
    helper runs in both modes so the guard is here.
    """
    live: list[AlertEvent] = []
    zero = timedelta(0)
    for a in alerts:
        # Resolve factor (try direct then directional-stripped)
        factor = config.factors.get(a.signal_name.lower())
        if factor is None:
            factor = config.factors.get(_strip_directional_suffix(a.signal_name))
        if factor is None:
            continue
        age = now - a.ts
        if age < zero:
            continue   # future alert — look-ahead guard
        if factor.ttl_minutes <= 0:
            # Price-action factors have no TTL; defensive — they
            # shouldn't appear as AlertEvents anyway.
            live.append(a)
            continue
        if age <= timedelta(minutes=factor.ttl_minutes):
            live.append(a)
    return live
