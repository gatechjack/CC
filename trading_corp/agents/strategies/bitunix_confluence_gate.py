"""BitUnix Futures — 5-factor confluence gate (replaces PA validator).

Pure-function gate that runs AFTER the score accumulator has chosen a
winning side and tier, but BEFORE the HTF regime gate. Same slot in
the pipeline that `bitunix_pa_validation.evaluate_pa_validation`
occupied previously:

    Score eval (signal-only)
            ↓ tier ≥ min_score_to_fire
    Confluence gate (this module)           ← multi-TF confirmation
            ↓ pass (score ≥ min_gate_score)
    HTF regime gate                         ← multi-TF EMA / ADX / proximity / vol
            ↓ permitted, multiplier > 0
    Risk gate → place_order

Why replace PA: the old PA gate's `structure_alignment` validator was
built on bucketed 4h HH/LL comparisons that flipped inconsistently at
bucket boundaries (see the 11/11 frozen-bucket prod observation and
`docs/memos/2026-05-18_pa_structure_backtest_results.md`). The
confluence gate replaces "is this bar's structure aligned?" with five
deterministic confirmation factors evaluated on the right timeframe
for each.

## The five factors

Each factor returns 0 or 1 (equal-weight binary). Trade fires when
`sum(factors) >= min_gate_score` (default 3-of-5).

1. **EMA alignment (15m).** EMA(8) > EMA(21) > EMA(50) AND EMA(8)
   slope positive, for buy (reversed for sell). Catches "all
   timeframes pointing the same way" trend confirmation. Uses
   `linregress_slope` over the last `slope_lookback` EMA values.

2. **VWAP (3m, session-aware).** Price > current session VWAP AND
   price > prior-day session VWAP, for buy (reversed for sell). Two
   VWAPs because a buy below either one is fighting a known seller
   level. Session boundary is `session_reset_hour_utc` (default UTC
   midnight, matches BitUnix futures clock).

3. **Volatility (5m).** ATR(14) > SMA(ATR, 50) AND BB(20, 2σ) width
   percentile rank over the last `bb_pct_rank_window` bars is
   >= `bb_pct_rank_min_excluded_pct` (default 0.10 — fails if BB
   width is in the bottom 10%). Symmetric across sides. Rejects
   range-bound chop where neither side has an edge.

4. **CVD slope (15m window from 3m bars, tick-rule fallback).**
   Bullish for buy / bearish for sell. CVD = cumulative volume delta
   = running sum of signed volume, where the sign is set by whether
   the trade was buyer-initiated or seller-initiated.

   *True CVD requires the aggressor-side flag on each individual
   trade.* BitUnix's public futures data feed does NOT expose
   aggressor side, so v1 of this gate uses a **tick-rule fallback**:
   for each 3m bar, sign = +1 if `close > prev_close`, -1 if
   `close < prev_close`, 0 if unchanged; `bar_delta = sign *
   volume`. Slope is `linregress_slope` over the last
   `slope_window_minutes / bucket_minutes` bar deltas (default
   15min / 3min = 5 bars). Buy passes if slope > 0; sell passes if
   slope < 0. Tick-rule is materially coarser than true CVD — it
   misclassifies trades inside a bar — so we always set
   `cvd_fallback_used=True` on the result. A future enhancement
   (WebSocket trade-stream consumer) would flip this flag to False
   and improve the signal-to-noise of factor 4 specifically.

   The YAML `bucket_minutes` field is documentation only for v1 —
   the tick-rule reads the 3m bar cache directly, so effective
   bucket is fixed at 3 regardless of the config value.

5. **Volume z-score (3m, 20-bar).** z = (current_volume - mean) /
   stdev over the last `period` 3m bars. Passes if z >=
   `min_z` (default 1.0). Symmetric across sides. "Real
   participation" check — confirms the move has volume behind it.

## Pure-function design

Inputs: a frozen `GateInputs` dataclass holding pre-computed
factor inputs (current price, EMA values, ATR, BB width, CVD slope,
volume z, etc.) plus the side. The caller (Phase B
`build_gate_inputs` in `data.bitunix_price_context`) reads the
3m/5m/15m bar caches and produces this dataclass. This module is
fully unit-testable with synthetic fixtures — no cache, no I/O.

Output: a frozen `GateResult` exposing decision, score, threshold,
per-factor outcomes (each with a `detail` dict carrying the raw
numbers for the audit row), and a `cvd_fallback_used` top-level
flag for the dashboard banner. The audit kind that consumes this
is `confluence_gate_decision` (written by the observer in Phase D).

Disabled state (`enabled=False`) is the emergency bypass: returns
`GateDecision.DISABLED` and the caller passes the trade through to
the next stage (HTF regime gate) without changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "ConfluenceGateConfig",
    "CvdFactorConfig",
    "EmaFactorConfig",
    "FactorResult",
    "GateDecision",
    "GateInputs",
    "GateResult",
    "VolatilityFactorConfig",
    "VolumeZFactorConfig",
    "VwapFactorConfig",
    "evaluate_confluence_gate",
]


# ─── decision enum ──────────────────────────────────────────────────────


class GateDecision(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    DISABLED = "disabled"          # config.enabled=False; trade passes through


# ─── per-factor sub-configs ─────────────────────────────────────────────


@dataclass(frozen=True)
class EmaFactorConfig:
    """Factor 1 — 15m EMA alignment + slope.

    Pass requires (v1.1, see post-mortem in
    `reports/gate_backtest_2026-05-17_v2.md`):
      - Long: ema_8 > ema_21 > ema_50 AND slope(ema_8) > 0
        AND slope(ema_21) > 0 AND slope(ema_50) > 0
      - Short: ema_8 < ema_21 < ema_50 AND all three slopes < 0

    `slope_lookback` governs the linregress window for ALL THREE EMAs,
    not just ema_8. v1.0 only checked ema_8 slope; corrected in v1.1.
    """
    periods: tuple[int, int, int] = (8, 21, 50)
    slope_lookback: int = 5            # linregress over last N EMA values, all 3


@dataclass(frozen=True)
class VwapFactorConfig:
    session_reset_hour_utc: int = 0


@dataclass(frozen=True)
class VolatilityFactorConfig:
    atr_period: int = 14                       # 5m bars
    atr_sma_period: int = 50
    bb_period: int = 20
    bb_stdev: float = 2.0
    bb_pct_rank_window: int = 100
    bb_pct_rank_min_excluded_pct: float = 0.10  # reject if BB width in bottom 10%


@dataclass(frozen=True)
class CvdFactorConfig:
    """Factor 4 config — see module docstring for the full
    tick-rule-fallback definition.

    `bucket_minutes` is documentation only for v1: the live caller
    always reads the 3m bar cache and computes per-bar tick-rule
    deltas, so the effective bucket is 3 regardless of this value.
    Kept as a config knob to make the YAML self-documenting and to
    leave room for a future per-trade-stream consumer that could
    honour a finer bucket.
    """
    slope_window_minutes: int = 15
    bucket_minutes: int = 3


@dataclass(frozen=True)
class VolumeZFactorConfig:
    period: int = 20                   # 3m bars
    min_z: float = 1.0


# ─── top-level config ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfluenceGateConfig:
    """Parsed `bitunix_futures.confluence_gate` block from strategies.yaml.

    Default state is fully disabled so this module is a no-op until
    YAML wires it on (Phase D atomic cutover). Set `enabled=False` to
    emergency-bypass after wiring — the gate returns DISABLED and the
    caller passes the trade through.
    """
    enabled: bool = False
    min_gate_score: int = 3            # 0..5
    gate_timeout_minutes: int = 15     # deferred-fire hard TTL
    ema_factor: EmaFactorConfig = field(default_factory=EmaFactorConfig)
    vwap_factor: VwapFactorConfig = field(default_factory=VwapFactorConfig)
    volatility_factor: VolatilityFactorConfig = field(
        default_factory=VolatilityFactorConfig,
    )
    cvd_factor: CvdFactorConfig = field(default_factory=CvdFactorConfig)
    volume_z_factor: VolumeZFactorConfig = field(
        default_factory=VolumeZFactorConfig,
    )

    # Whitelisted top-level + per-factor keys for unknown-key warnings.
    # The dataclass shape is the source of truth; this is just what
    # we accept silently in `from_dict`.
    _TOP_KEYS = frozenset({
        "enabled", "min_gate_score", "gate_timeout_minutes",
        "ema_factor", "vwap_factor", "volatility_factor",
        "cvd_factor", "volume_z_factor",
    })
    _EMA_KEYS = frozenset({"periods", "slope_lookback"})
    _VWAP_KEYS = frozenset({"session_reset_hour_utc"})
    _VOL_KEYS = frozenset({
        "atr_period", "atr_sma_period", "bb_period", "bb_stdev",
        "bb_pct_rank_window", "bb_pct_rank_min_excluded_pct",
    })
    _CVD_KEYS = frozenset({"slope_window_minutes", "bucket_minutes"})
    _VOLZ_KEYS = frozenset({"period", "min_z"})

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ConfluenceGateConfig":
        """Parse a strategies.yaml `bitunix_futures` block.

        Picks up the `confluence_gate` sub-key. Missing block →
        default disabled config. Unknown keys log a `WARNING`
        (config typo silent degradation is a known sharp edge —
        we want loud failure on typos).
        """
        block = (raw or {}).get("confluence_gate") or {}
        if not isinstance(block, dict):
            log.warning(
                "confluence_gate config is %s, expected dict — using defaults",
                type(block).__name__,
            )
            return cls()

        cls._warn_unknown_keys("confluence_gate", block, cls._TOP_KEYS)

        ema_raw = block.get("ema_factor") or {}
        cls._warn_unknown_keys(
            "confluence_gate.ema_factor", ema_raw, cls._EMA_KEYS,
        )
        periods_raw = ema_raw.get("periods")
        if (
            isinstance(periods_raw, (list, tuple))
            and len(periods_raw) == 3
        ):
            periods = (int(periods_raw[0]), int(periods_raw[1]), int(periods_raw[2]))
        else:
            periods = (8, 21, 50)
        ema_factor = EmaFactorConfig(
            periods=periods,
            slope_lookback=int(ema_raw.get("slope_lookback", 5)),
        )

        vwap_raw = block.get("vwap_factor") or {}
        cls._warn_unknown_keys(
            "confluence_gate.vwap_factor", vwap_raw, cls._VWAP_KEYS,
        )
        vwap_factor = VwapFactorConfig(
            session_reset_hour_utc=int(vwap_raw.get("session_reset_hour_utc", 0)),
        )

        vol_raw = block.get("volatility_factor") or {}
        cls._warn_unknown_keys(
            "confluence_gate.volatility_factor", vol_raw, cls._VOL_KEYS,
        )
        volatility_factor = VolatilityFactorConfig(
            atr_period=int(vol_raw.get("atr_period", 14)),
            atr_sma_period=int(vol_raw.get("atr_sma_period", 50)),
            bb_period=int(vol_raw.get("bb_period", 20)),
            bb_stdev=float(vol_raw.get("bb_stdev", 2.0)),
            bb_pct_rank_window=int(vol_raw.get("bb_pct_rank_window", 100)),
            bb_pct_rank_min_excluded_pct=float(
                vol_raw.get("bb_pct_rank_min_excluded_pct", 0.10),
            ),
        )

        cvd_raw = block.get("cvd_factor") or {}
        cls._warn_unknown_keys(
            "confluence_gate.cvd_factor", cvd_raw, cls._CVD_KEYS,
        )
        cvd_factor = CvdFactorConfig(
            slope_window_minutes=int(cvd_raw.get("slope_window_minutes", 15)),
            bucket_minutes=int(cvd_raw.get("bucket_minutes", 3)),
        )

        volz_raw = block.get("volume_z_factor") or {}
        cls._warn_unknown_keys(
            "confluence_gate.volume_z_factor", volz_raw, cls._VOLZ_KEYS,
        )
        volume_z_factor = VolumeZFactorConfig(
            period=int(volz_raw.get("period", 20)),
            min_z=float(volz_raw.get("min_z", 1.0)),
        )

        return cls(
            enabled=bool(block.get("enabled", False)),
            min_gate_score=int(block.get("min_gate_score", 3)),
            gate_timeout_minutes=int(block.get("gate_timeout_minutes", 15)),
            ema_factor=ema_factor,
            vwap_factor=vwap_factor,
            volatility_factor=volatility_factor,
            cvd_factor=cvd_factor,
            volume_z_factor=volume_z_factor,
        )

    @staticmethod
    def _warn_unknown_keys(
        scope: str, block: dict[str, Any], allowed: frozenset[str],
    ) -> None:
        unknown = [k for k in block.keys() if k not in allowed]
        if unknown:
            log.warning(
                "%s: unknown YAML keys %s — likely a typo; ignoring",
                scope, sorted(unknown),
            )


# ─── inputs ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateInputs:
    """Pre-computed per-factor inputs.

    Phase A: dataclass + gate function only. Phase B introduces
    `build_gate_inputs(bar_3m, bar_5m, bar_15m, *, side, config)` in
    `data.bitunix_price_context` which constructs this from the three
    bar caches. Every field is `None`-tolerant — factors that see
    `None` for any required input return `passed=False`.

    `cvd_fallback_used` is set by the input builder (always True for
    v1; flips to False if a future trade-stream consumer lands).
    """
    # Factor 1 — EMA alignment (15m). v1.1: all three slopes required
    # for pass (was: ema_8 slope only). See post-mortem in
    # reports/gate_backtest_2026-05-17_v2.md.
    ema_8_15m: float | None
    ema_21_15m: float | None
    ema_50_15m: float | None
    ema_8_15m_slope: float | None      # linregress over last N values of ema_8
    ema_21_15m_slope: float | None     # linregress over last N values of ema_21
    ema_50_15m_slope: float | None     # linregress over last N values of ema_50

    # Factor 2 — VWAP (price vs session + prior-day session VWAP)
    current_price: float | None
    session_vwap: float | None
    prior_day_session_vwap: float | None

    # Factor 3 — Volatility (5m)
    atr_5m: float | None
    atr_5m_sma: float | None
    bb_width_5m: float | None
    bb_width_5m_pct_rank: float | None   # 0..1

    # Factor 4 — CVD slope (15min window, tick-rule fallback)
    cvd_slope: float | None
    cvd_fallback_used: bool             # True for v1 (always)

    # Factor 5 — Volume z-score (3m, 20-bar)
    volume_z: float | None


# ─── result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class FactorResult:
    name: str
    passed: bool
    detail: dict[str, Any]              # raw numbers for the audit row


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    side: str                           # "buy" | "sell" (echoes input)
    score: int                          # 0..5
    threshold: int                      # min_gate_score
    factors: tuple[FactorResult, ...]
    reason: str                         # human-readable summary
    cvd_fallback_used: bool             # surfaces to dashboard banner


# ─── factor implementations ─────────────────────────────────────────────


def _factor_ema_alignment(
    side: str, inputs: GateInputs, config: EmaFactorConfig,
) -> FactorResult:
    e8, e21, e50 = inputs.ema_8_15m, inputs.ema_21_15m, inputs.ema_50_15m
    s8, s21, s50 = (
        inputs.ema_8_15m_slope, inputs.ema_21_15m_slope, inputs.ema_50_15m_slope,
    )
    detail: dict[str, Any] = {
        "ema_8": e8, "ema_21": e21, "ema_50": e50,
        "ema_8_slope": s8, "ema_21_slope": s21, "ema_50_slope": s50,
        "periods": list(config.periods),
    }
    if None in (e8, e21, e50, s8, s21, s50):
        return FactorResult(
            name="ema_alignment", passed=False,
            detail={**detail, "reason": "missing inputs"},
        )
    # v1.1: require ALL three slopes aligned with side, not just ema_8.
    if side == "buy":
        passed = (e8 > e21 > e50) and s8 > 0 and s21 > 0 and s50 > 0
    elif side == "sell":
        passed = (e8 < e21 < e50) and s8 < 0 and s21 < 0 and s50 < 0
    else:
        return FactorResult(
            name="ema_alignment", passed=False,
            detail={**detail, "reason": f"invalid side {side!r}"},
        )
    return FactorResult(name="ema_alignment", passed=passed, detail=detail)


def _factor_vwap(
    side: str, inputs: GateInputs, config: VwapFactorConfig,
) -> FactorResult:
    px, sv, pv = (
        inputs.current_price, inputs.session_vwap, inputs.prior_day_session_vwap,
    )
    detail: dict[str, Any] = {
        "current_price": px, "session_vwap": sv,
        "prior_day_session_vwap": pv,
        "session_reset_hour_utc": config.session_reset_hour_utc,
    }
    if None in (px, sv, pv):
        return FactorResult(
            name="vwap", passed=False,
            detail={**detail, "reason": "missing inputs"},
        )
    if side == "buy":
        passed = (px > sv) and (px > pv)
    elif side == "sell":
        passed = (px < sv) and (px < pv)
    else:
        return FactorResult(
            name="vwap", passed=False,
            detail={**detail, "reason": f"invalid side {side!r}"},
        )
    return FactorResult(name="vwap", passed=passed, detail=detail)


def _factor_volatility(
    side: str, inputs: GateInputs, config: VolatilityFactorConfig,
) -> FactorResult:
    a, asm, bw, bpr = (
        inputs.atr_5m, inputs.atr_5m_sma, inputs.bb_width_5m,
        inputs.bb_width_5m_pct_rank,
    )
    threshold = config.bb_pct_rank_min_excluded_pct
    detail: dict[str, Any] = {
        "atr_5m": a, "atr_5m_sma": asm,
        "bb_width_5m": bw, "bb_width_5m_pct_rank": bpr,
        "bb_pct_rank_min_excluded_pct": threshold,
    }
    if None in (a, asm, bw, bpr):
        return FactorResult(
            name="volatility", passed=False,
            detail={**detail, "reason": "missing inputs"},
        )
    passed = (a > asm) and (bpr >= threshold)
    return FactorResult(name="volatility", passed=passed, detail=detail)


def _factor_cvd(
    side: str, inputs: GateInputs, config: CvdFactorConfig,
) -> FactorResult:
    slope = inputs.cvd_slope
    detail: dict[str, Any] = {
        "cvd_slope": slope,
        "fallback_used": inputs.cvd_fallback_used,
        "slope_window_minutes": config.slope_window_minutes,
        "bucket_minutes": config.bucket_minutes,
    }
    if slope is None:
        return FactorResult(
            name="cvd", passed=False,
            detail={**detail, "reason": "missing inputs"},
        )
    if side == "buy":
        passed = slope > 0
    elif side == "sell":
        passed = slope < 0
    else:
        return FactorResult(
            name="cvd", passed=False,
            detail={**detail, "reason": f"invalid side {side!r}"},
        )
    return FactorResult(name="cvd", passed=passed, detail=detail)


def _factor_volume_z(
    side: str, inputs: GateInputs, config: VolumeZFactorConfig,
) -> FactorResult:
    z = inputs.volume_z
    detail: dict[str, Any] = {
        "volume_z": z, "min_z": config.min_z, "period": config.period,
    }
    if z is None:
        return FactorResult(
            name="volume_z", passed=False,
            detail={**detail, "reason": "missing inputs"},
        )
    passed = z >= config.min_z
    return FactorResult(name="volume_z", passed=passed, detail=detail)


# ─── main entry ─────────────────────────────────────────────────────────


def evaluate_confluence_gate(
    *,
    side: str,
    inputs: GateInputs,
    config: ConfluenceGateConfig,
) -> GateResult:
    """Run all five factors and decide PASS / REJECT / DISABLED.

    Order of operations:
      1. If `config.enabled=False` → return `DISABLED` (no-op, caller
         passes the trade through whatever the next stage does).
      2. Validate `side`. Unknown side → `REJECT` with empty score
         (does not run the factors).
      3. Run the five factors. Each returns 0 or 1.
      4. score = sum of passed factors. PASS iff
         `score >= config.min_gate_score`; otherwise REJECT.

    Always exposes all five factor results (in deterministic order)
    so the audit row can render the "which factors passed / failed"
    breakdown without re-running the gate.
    """
    side_l = (side or "").lower()

    if not config.enabled:
        # Disabled bypass — surface inputs as factor details so the
        # audit row shows the state of the world even when the gate
        # is off (helps debugging "why did this trade fire?").
        return GateResult(
            decision=GateDecision.DISABLED,
            side=side_l,
            score=0,
            threshold=config.min_gate_score,
            factors=(),
            reason="confluence_gate disabled in config",
            cvd_fallback_used=inputs.cvd_fallback_used,
        )

    if side_l not in ("buy", "sell"):
        return GateResult(
            decision=GateDecision.REJECT,
            side=side_l,
            score=0,
            threshold=config.min_gate_score,
            factors=(),
            reason=f"invalid side {side!r}",
            cvd_fallback_used=inputs.cvd_fallback_used,
        )

    factors: tuple[FactorResult, ...] = (
        _factor_ema_alignment(side_l, inputs, config.ema_factor),
        _factor_vwap(side_l, inputs, config.vwap_factor),
        _factor_volatility(side_l, inputs, config.volatility_factor),
        _factor_cvd(side_l, inputs, config.cvd_factor),
        _factor_volume_z(side_l, inputs, config.volume_z_factor),
    )
    score = sum(1 for f in factors if f.passed)
    passed_names = [f.name for f in factors if f.passed]
    failed_names = [f.name for f in factors if not f.passed]

    if score >= config.min_gate_score:
        return GateResult(
            decision=GateDecision.PASS,
            side=side_l,
            score=score,
            threshold=config.min_gate_score,
            factors=factors,
            reason=(
                f"PASS: {score}/{config.min_gate_score} required "
                f"(passed={passed_names})"
            ),
            cvd_fallback_used=inputs.cvd_fallback_used,
        )

    return GateResult(
        decision=GateDecision.REJECT,
        side=side_l,
        score=score,
        threshold=config.min_gate_score,
        factors=factors,
        reason=(
            f"REJECT: {score}/{config.min_gate_score} required "
            f"(failed={failed_names})"
        ),
        cvd_fallback_used=inputs.cvd_fallback_used,
    )
