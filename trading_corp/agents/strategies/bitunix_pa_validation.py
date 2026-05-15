"""BitUnix Futures — Price-Action validation gate (PR 3).

Pure-function gate that runs AFTER the score accumulator has chosen a
winning side and tier, but BEFORE the HTF regime gate. The architectural
intent (per the design discussion that produced PR 3):

    Score eval (signal-only)
            ↓ tier ≥ min_score_to_fire
    PA validation (this module)             ← bar-level vwap / volume / structure
            ↓ pass
    HTF regime gate                         ← multi-TF EMA / ADX / proximity / vol
            ↓ permitted, multiplier > 0
    Risk gate → place_order

The score engine sums per-signal weights; it answers "are enough things
pointing this way?" The PA gate answers a different question: "is the
*current bar's* price action actually corroborating that direction?"
A high-score buy stack into a falling bar with weak volume historically
loses — this gate filters those.

Three validators (all default-enabled):

    vwap_alignment        → buy needs price > session VWAP
                            sell needs price < session VWAP
    volume_confirmation   → current bar's volume > 20-bar SMA
    structure_alignment   → buy needs higher_highs_4h (resampled from 3m)
                            sell needs lower_lows_4h

`require_all=true` (the default per Board direction): any failed
validator → reject. `require_all=false` with `min_validators_passed=N`
allows soft-fail mode if shadow data later argues for it.

Plus two hard-reject guards (independent of the validator list):

    reject_buy_on_60m_drop_pct  → buy-side: 60min adverse move ≤ -X% → reject
    reject_sell_on_60m_rise_pct → sell-side: 60min adverse move ≥ +X% → reject

These are the "don't catch a falling knife / don't short into a rip"
checks that used to be soft-penalty guards inside the score engine
(`sell_on_rush` / `buy_on_fall` brackets). PR 3 promotes them to binary
hard-rejects — the strongest old penalty (-3pts at >5%) becomes the
binary threshold; softer tiers disappear (no penalty, no boost).

Pure-function design: input dataclasses → output dataclass. Same
shape as `bitunix_confluence.evaluate_confluence_futures`. Tested with
synthetic PriceContext fixtures — no OHLCV math here (that's the
caller's job, computed by `compute_price_context`).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from trading_corp.agents.strategies.btc_accumulator import PriceContext

__all__ = [
    "PAValidationConfig",
    "PAValidationDecision",
    "PAValidationResult",
    "evaluate_pa_validation",
]


class PAValidationDecision(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    DISABLED = "disabled"          # config.enabled=False; trade goes through


# ─── config ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PAValidationConfig:
    """Parsed `bitunix_futures.pa_validation` block from strategies.yaml.

    YAML shape (PR 3c will add this — defaults preserve a fully-disabled
    state so this module is a no-op until configured):

        pa_validation:
          enabled: true
          require_all: true
          min_validators_passed: 3      # only used when require_all=false
          validators:
            - vwap_alignment
            - volume_confirmation
            - structure_alignment
          rush_fall_guards:
            enabled: true
            reject_buy_on_60m_drop_pct: 5.0
            reject_sell_on_60m_rise_pct: 5.0
    """
    enabled: bool = False
    require_all: bool = True
    min_validators_passed: int = 0      # only consulted when require_all=False
    validators: tuple[str, ...] = (
        "vwap_alignment", "volume_confirmation", "structure_alignment",
    )
    rush_fall_enabled: bool = True
    reject_buy_on_60m_drop_pct: float = 5.0
    reject_sell_on_60m_rise_pct: float = 5.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PAValidationConfig":
        pa = (raw or {}).get("pa_validation") or {}
        validators_raw = pa.get("validators")
        if isinstance(validators_raw, list) and validators_raw:
            validators = tuple(str(v) for v in validators_raw)
        else:
            validators = (
                "vwap_alignment", "volume_confirmation", "structure_alignment",
            )
        guards = pa.get("rush_fall_guards") or {}
        return cls(
            enabled=bool(pa.get("enabled", False)),
            require_all=bool(pa.get("require_all", True)),
            min_validators_passed=int(pa.get("min_validators_passed", 0)),
            validators=validators,
            rush_fall_enabled=bool(guards.get("enabled", True)),
            reject_buy_on_60m_drop_pct=float(
                guards.get("reject_buy_on_60m_drop_pct", 5.0),
            ),
            reject_sell_on_60m_rise_pct=float(
                guards.get("reject_sell_on_60m_rise_pct", 5.0),
            ),
        )


# ─── result ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PAValidationResult:
    """Audit-grade output. Surfaces every validator outcome so the
    `pa_validation_decision` audit row can reconstruct WHY a trade was
    rejected without re-running the gate."""
    decision: PAValidationDecision
    side: str                           # "buy" | "sell" (echoes input)
    passed: tuple[str, ...]             # validator names that passed
    failed: tuple[str, ...]             # validator names that failed
    rush_fall_triggered: str | None     # "buy_falling" | "sell_rising" | None
    reason: str                         # human-readable summary


# ─── validator implementations ──────────────────────────────────────────


def _vwap_alignment(side: str, ctx: PriceContext) -> bool:
    if side == "buy":
        return bool(ctx.above_session_vwap)
    if side == "sell":
        return bool(ctx.below_session_vwap)
    return False


def _volume_confirmation(side: str, ctx: PriceContext) -> bool:
    # Symmetric: both sides want above-average volume to confirm
    # the move has real participation.
    return bool(ctx.volume_above_20bar_avg)


def _structure_alignment(side: str, ctx: PriceContext) -> bool:
    if side == "buy":
        return bool(ctx.higher_highs_4h)
    if side == "sell":
        return bool(ctx.lower_lows_4h)
    return False


_VALIDATOR_FNS = {
    "vwap_alignment": _vwap_alignment,
    "volume_confirmation": _volume_confirmation,
    "structure_alignment": _structure_alignment,
}


def _check_rush_fall(
    side: str, ctx: PriceContext, config: PAValidationConfig,
) -> str | None:
    """Return the rush/fall trigger name if a hard-reject fires, else None."""
    if not config.rush_fall_enabled:
        return None
    if side == "buy":
        if ctx.pct_change_in_window_buy <= -config.reject_buy_on_60m_drop_pct:
            return "buy_falling"
    elif side == "sell":
        if ctx.pct_change_in_window_sell >= config.reject_sell_on_60m_rise_pct:
            return "sell_rising"
    return None


# ─── main entry ─────────────────────────────────────────────────────────


def evaluate_pa_validation(
    *,
    side: str,
    price_ctx: PriceContext,
    config: PAValidationConfig,
) -> PAValidationResult:
    """Run the PA gate for a proposed `side` against current `price_ctx`.

    Returns a `PAValidationResult` with the decision (`PASS`, `REJECT`,
    `DISABLED`), every validator's individual outcome, the rush/fall
    trigger if any, and a human-readable reason.

    Order of operations:
      1. If config.enabled=False → return DISABLED (no-op, trade passes
         through whatever the caller does after this gate).
      2. Run all validators in `config.validators`. Each returns
         True (pass) or False (fail).
      3. Apply require_all rule (or min_validators_passed if not all-mode).
         Failure → return REJECT with the failing list.
      4. Run rush/fall guard. Trigger → return REJECT regardless of
         validator outcomes.
      5. Otherwise → return PASS.
    """
    side_l = (side or "").lower()
    if not config.enabled:
        return PAValidationResult(
            decision=PAValidationDecision.DISABLED,
            side=side_l,
            passed=(),
            failed=(),
            rush_fall_triggered=None,
            reason="pa_validation disabled in config",
        )
    if side_l not in ("buy", "sell"):
        return PAValidationResult(
            decision=PAValidationDecision.REJECT,
            side=side_l,
            passed=(),
            failed=(),
            rush_fall_triggered=None,
            reason=f"invalid side {side!r}",
        )

    passed: list[str] = []
    failed: list[str] = []
    for name in config.validators:
        fn = _VALIDATOR_FNS.get(name)
        if fn is None:
            # Unknown validator name in YAML — treat as a configuration
            # bug and fail closed (count as a failed validator) rather
            # than silently skip.
            failed.append(name)
            continue
        if fn(side_l, price_ctx):
            passed.append(name)
        else:
            failed.append(name)

    if config.require_all:
        validators_passed = (len(failed) == 0)
        rule_summary = f"require_all (passed {len(passed)}/{len(config.validators)})"
    else:
        validators_passed = len(passed) >= config.min_validators_passed
        rule_summary = (
            f"min_validators_passed {len(passed)}/{config.min_validators_passed} "
            f"required ({len(passed)}/{len(config.validators)} total)"
        )

    rush_fall = _check_rush_fall(side_l, price_ctx, config)

    if not validators_passed:
        return PAValidationResult(
            decision=PAValidationDecision.REJECT,
            side=side_l,
            passed=tuple(passed),
            failed=tuple(failed),
            rush_fall_triggered=rush_fall,
            reason=(
                f"REJECT: {rule_summary}; failed={list(failed)}"
                + (f"; rush_fall={rush_fall}" if rush_fall else "")
            ),
        )

    if rush_fall is not None:
        if rush_fall == "buy_falling":
            pct = price_ctx.pct_change_in_window_buy
            threshold = -config.reject_buy_on_60m_drop_pct
            detail = (
                f"60m pct_change {pct:+.2f}% ≤ {threshold:.2f}% — "
                f"don't catch falling knife"
            )
        else:
            pct = price_ctx.pct_change_in_window_sell
            threshold = config.reject_sell_on_60m_rise_pct
            detail = (
                f"60m pct_change {pct:+.2f}% ≥ +{threshold:.2f}% — "
                f"don't short into a rip"
            )
        return PAValidationResult(
            decision=PAValidationDecision.REJECT,
            side=side_l,
            passed=tuple(passed),
            failed=tuple(failed),
            rush_fall_triggered=rush_fall,
            reason=f"REJECT (rush_fall): {detail}",
        )

    return PAValidationResult(
        decision=PAValidationDecision.PASS,
        side=side_l,
        passed=tuple(passed),
        failed=tuple(failed),
        rush_fall_triggered=None,
        reason=f"PASS: {rule_summary}",
    )
