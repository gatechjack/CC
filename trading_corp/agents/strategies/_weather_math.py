"""Weather-market probability math + validation gates.

Venue-agnostic: any strategy that has (forecast_temp, sigma, threshold,
direction) can compute P(YES) and the standard skip gates here.

Used today by `kalshi_weather_arb`; future polymarket_weather_arb (if/when
Polymarket ships weather markets) would import the same module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal


# ── Constants — validation gates ──────────────────────────────────────────

MAX_HORIZON_HOURS = 72            # NWS hourly forecast precision falls off past 72h
MIN_THRESHOLD_DELTA_SIGMA = 1.0   # |threshold − forecast| ≥ sigma * this → fire
SOURCE_DIVERGENCE_SIGMA_F = 2.0   # NWS↔AccuWeather typical drift (Fahrenheit)
DEFAULT_MIN_DIVERGENCE_PCT = 10.0


# ── Result types ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ForecastPoint:
    """One forecast observation, source-agnostic."""
    temp_f: float
    sigma_f: float        # 1-sigma uncertainty estimate (Fahrenheit)
    valid_iso: str        # forecast period start ISO
    source: str = "nws"   # 'nws' | 'accuweather' | etc.
    # Item 1.2 (run-age logging) — additive only, defaults preserve callers.
    # `issued_at` is the model/forecast issue time from the upstream API
    # (NWS Last-Modified header). May be NULL on a fraction of NWS fetches
    # because Akamai CDN strips/staleifies the header per-request — NULL is
    # normal here, NOT a bug. `fetched_at` is the wall-clock UTC when we
    # actually hit the upstream (cache-refresh time, NOT now()-if-cached);
    # always populated for sources that surface freshness, used as the
    # graceful-degradation fallback for analysis.
    issued_at: str | None = None
    fetched_at: str | None = None


@dataclass(frozen=True)
class WeatherVerdict:
    """Output of forecast→probability evaluation."""
    prob_yes: float                # in [0, 1]
    edge_pct: float                # |prob_yes - implied_yes| × 100
    delta_f: float                 # forecast_temp - threshold (signed; F)
    sigma_used_f: float            # sigma after source-divergence augmentation
    fired: bool                    # True if all gates pass + edge ≥ min_divergence_pct
    skip_reason: str               # populated when fired=False


@dataclass(frozen=True)
class BucketGuardResult:
    """Output of the bucket-aware bet-side guard.

    Three cases the caller distinguishes:
      - outcome == proposed_outcome AND action is None:
          natural path, no guard intervention.
      - outcome is set AND action == "flipped_no_to_yes":
          we changed sides to YES; caller proceeds with new outcome.
      - outcome is None AND action ∈ {block_no_yes_too_expensive,
        block_yes_forecast_outside}: caller MUST skip the trade.
    """
    outcome: str | None            # "yes" | "no" | None (None = skip)
    action: str | None             # flag for audit / dashboard
    skip_reason: str | None        # populated when outcome is None


# ── Pure math ─────────────────────────────────────────────────────────────

def _normal_cdf(z: float) -> float:
    """Standard normal CDF via math.erf (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def forecast_probability(
    *,
    forecast_temp_f: float,
    sigma_f: float,
    threshold_f: float,
    direction: str,                       # 'greater' | 'less' | 'between'
    threshold_high_f: float | None = None,
) -> float:
    """P(actual temp/spot resolves the market YES).

    Gaussian model: actual ~ N(forecast_temp_f, sigma_f).
      direction='greater' → P(actual > threshold_f) = 1 - Φ((thr-μ)/σ)
      direction='less'    → P(actual < threshold_f) = Φ((thr-μ)/σ)
      direction='between' → P(threshold_f ≤ actual ≤ threshold_high_f)
                          = Φ((high-μ)/σ) - Φ((low-μ)/σ)

    For between, threshold_f is the low bound and threshold_high_f the
    high bound (the Kalshi `floor_strike` / `cap_strike` fields).
    """
    if direction == "between":
        if threshold_high_f is None:
            return 0.0
        low, high = threshold_f, threshold_high_f
        if high < low:
            low, high = high, low
        if sigma_f <= 0:
            return 1.0 if low <= forecast_temp_f <= high else 0.0
        z_low = (low - forecast_temp_f) / sigma_f
        z_high = (high - forecast_temp_f) / sigma_f
        return max(0.0, _normal_cdf(z_high) - _normal_cdf(z_low))

    if sigma_f <= 0:
        if direction == "greater":
            return 1.0 if forecast_temp_f > threshold_f else 0.0
        return 1.0 if forecast_temp_f < threshold_f else 0.0
    z = (threshold_f - forecast_temp_f) / sigma_f
    p_greater = 1.0 - _normal_cdf(z)
    return p_greater if direction == "greater" else (1.0 - p_greater)


def evaluate_weather_market(
    *,
    forecast: ForecastPoint,
    threshold_f: float,
    direction: str,
    implied_yes: float,
    horizon_hours: float,
    threshold_high_f: float | None = None,
    min_divergence_pct: float = DEFAULT_MIN_DIVERGENCE_PCT,
    max_divergence_pct: float | None = None,
    min_delta_sigma: float = MIN_THRESHOLD_DELTA_SIGMA,
    max_horizon_hours: float = MAX_HORIZON_HOURS,
    source_divergence_sigma_f: float = SOURCE_DIVERGENCE_SIGMA_F,
) -> WeatherVerdict:
    """Apply gates + compute fired-ness from a single forecast observation.

    Gates (any failure → fired=False, skip_reason set):
      1. horizon_hours ≤ max_horizon_hours (default 72h)
      2. (single-side only) |threshold − forecast| ≥ sigma_total × min_delta_sigma.
         Skipped for direction='between' — the bucket either captures the
         forecast or not; "near-threshold" doesn't apply the same way.
      3. |P(YES) − implied| × 100 ≥ min_divergence_pct

    `threshold_high_f` is required when direction='between' (Kalshi
    `cap_strike` for a bucket market). For single-side it stays None.
    """
    # Gate 1 — horizon cap
    if horizon_hours > max_horizon_hours:
        return WeatherVerdict(
            prob_yes=0.0, edge_pct=0.0,
            delta_f=forecast.temp_f - threshold_f,
            sigma_used_f=forecast.sigma_f,
            fired=False,
            skip_reason=f"horizon {horizon_hours:.1f}h > cap {max_horizon_hours}h",
        )
    if horizon_hours < 0:
        return WeatherVerdict(
            prob_yes=0.0, edge_pct=0.0,
            delta_f=forecast.temp_f - threshold_f,
            sigma_used_f=forecast.sigma_f,
            fired=False,
            skip_reason=f"horizon negative ({horizon_hours:.1f}h) — target in the past",
        )

    # Augment sigma with source-divergence allowance (NWS ↔ AccuWeather drift)
    sigma_total = math.sqrt(forecast.sigma_f ** 2 + source_divergence_sigma_f ** 2)

    if direction == "between":
        if threshold_high_f is None:
            return WeatherVerdict(
                prob_yes=0.0, edge_pct=0.0,
                delta_f=0.0, sigma_used_f=sigma_total,
                fired=False,
                skip_reason="between direction requires threshold_high_f",
            )
        bucket_mid = (threshold_f + threshold_high_f) / 2.0
        delta = forecast.temp_f - bucket_mid
    else:
        # Gate 2 — near-threshold skip (single-side only)
        delta = forecast.temp_f - threshold_f
        if abs(delta) < sigma_total * min_delta_sigma:
            return WeatherVerdict(
                prob_yes=0.0, edge_pct=0.0,
                delta_f=delta, sigma_used_f=sigma_total,
                fired=False,
                skip_reason=(
                    f"near-threshold: |delta|={abs(delta):.2f}°F < "
                    f"sigma*{min_delta_sigma:.1f}={sigma_total*min_delta_sigma:.2f}°F"
                ),
            )

    # Compute prob_yes using augmented sigma
    prob_yes = forecast_probability(
        forecast_temp_f=forecast.temp_f,
        sigma_f=sigma_total,
        threshold_f=threshold_f,
        direction=direction,
        threshold_high_f=threshold_high_f,
    )
    edge_pct = abs(prob_yes - implied_yes) * 100.0

    # Gate 3 — minimum divergence
    if edge_pct < min_divergence_pct:
        return WeatherVerdict(
            prob_yes=prob_yes, edge_pct=edge_pct,
            delta_f=delta, sigma_used_f=sigma_total,
            fired=False,
            skip_reason=(
                f"edge {edge_pct:.1f}% < min_divergence_pct {min_divergence_pct:.1f}%"
            ),
        )

    # Gate 4 — maximum divergence cap (tail/oracle-disagreement guard, paper
    # only as of 2026-05-20). Realized vol does not compress this bin
    # because spot is many sigma outside the bucket and prob_yes ~= 0
    # under any reasonable sigma; the bleed is a Kalshi-oracle vs our-
    # forecast disagreement, not a vol artifact. Cap kills the trade.
    if max_divergence_pct is not None and edge_pct > max_divergence_pct:
        return WeatherVerdict(
            prob_yes=prob_yes, edge_pct=edge_pct,
            delta_f=delta, sigma_used_f=sigma_total,
            fired=False,
            skip_reason=(
                f"edge {edge_pct:.1f}% > max_divergence_pct "
                f"{max_divergence_pct:.1f}% (block_divergence_too_high)"
            ),
        )

    return WeatherVerdict(
        prob_yes=prob_yes, edge_pct=edge_pct,
        delta_f=delta, sigma_used_f=sigma_total,
        fired=True, skip_reason="",
    )


def kelly_fraction(p_model: float, market_price: float) -> float:
    """Full-Kelly bankroll fraction for a single-side YES-style bet.

    `market_price` is the per-share cost in dollars (0-1, e.g. 0.42 for a
    42¢ Kalshi YES). `p_model` is the strategy's calibrated probability
    that the bet resolves at $1. Caller applies a *fractional* Kelly
    multiplier (typically 0.25) and any per-market / daily caps.

    Math: payoff b = (1-price)/price; f* = (p·b − (1−p)) / b. Returns 0
    when no edge (full-Kelly ≤ 0) or price is at the boundary.
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0
    if p_model <= 0:
        return 0.0
    if p_model >= 1:
        return 1.0
    b = (1.0 - market_price) / market_price
    full = (p_model * b - (1.0 - p_model)) / b
    return max(0.0, full)


def kalshi_quote_dollars(m: Any) -> tuple[float, float, float, float]:
    """Return (yes_ask, no_ask, yes_bid, no_bid) in dollars for a pykalshi Market.

    Why: Kalshi flipped weather + crypto markets to fractional_trading_enabled.
    The integer-cent fields (yes_ask/no_ask/yes_bid/no_bid) are absent from
    the API response and pykalshi exposes them as None. Only the *_dollars
    string fields populate. Prefer *_dollars; fall back to cents × 0.01 for
    any non-fractional market still in flight. 0.0 = "no quote on this side"
    (matches Kalshi's existing convention).
    """
    def _read(dollars_attr: str, cents_attr: str) -> float:
        d = getattr(m, dollars_attr, None)
        if d not in (None, ""):
            try:
                return float(d)
            except (TypeError, ValueError):
                pass
        c = getattr(m, cents_attr, None)
        if c is None:
            return 0.0
        try:
            return float(c) / 100.0
        except (TypeError, ValueError):
            return 0.0
    return (
        _read("yes_ask_dollars", "yes_ask"),
        _read("no_ask_dollars", "no_ask"),
        _read("yes_bid_dollars", "yes_bid"),
        _read("no_bid_dollars", "no_bid"),
    )


def sigma_for_horizon(horizon_hours: float) -> float:
    """Heuristic uncertainty estimate by forecast horizon (Fahrenheit).

    NWS hourly forecasts don't carry per-period sigma directly; we
    estimate based on empirical model skill:
      0-24h:  1.5°F
      24-48h: 2.5°F
      48-72h: 3.5°F
      72h+:   5.0°F (clamps to MAX_HORIZON_HOURS gate before use anyway)

    These are conservative — actual NWS hourly forecasts within 24h
    often track within ±1°F, but the heuristic widens the band so
    `min_delta_sigma` skips noisy near-threshold setups.
    """
    if horizon_hours < 0:
        return 5.0
    if horizon_hours <= 24:
        return 1.5
    if horizon_hours <= 48:
        return 2.5
    if horizon_hours <= 72:
        return 3.5
    return 5.0


# ── Bucket-aware bet-side guard ──────────────────────────────────────────
# Failure mode discovered 2026-05-16: σ-vs-bucket-width mismatch on narrow
# Kalshi temperature buckets caused the strategy to bet against its own
# forecast. For 1°F buckets with forecast σ=2.7°F, the Gaussian integral
# over a single bucket is only ~14% even at the forecast center, so the
# model said "no bucket is high probability" and we sold the modal bucket.
# 9.8% win rate, -$374 PnL on 61 trades pre-fix.

DEFAULT_FLIP_YES_IMPLIED_CEILING = 0.70


def apply_bucket_guard(
    *,
    direction: str,
    forecast_temp_f: float,
    threshold_f: float,
    threshold_high_f: float | None,
    proposed_outcome: str,
    implied_yes: float,
    flip_yes_implied_ceiling: float = DEFAULT_FLIP_YES_IMPLIED_CEILING,
) -> BucketGuardResult:
    """Decide whether a proposed bet-side is structurally aligned with the
    own forecast, and reshape (flip to YES) or block when it isn't.

    Rules (all directions):
      - "forecast predicts YES zone" ≡ the YES side of the market matches
        our forecast direction. For between: forecast ∈ [floor, cap].
        For greater: forecast > threshold. For less: forecast < threshold.
      - If forecast predicts YES AND proposed_outcome == "no":
          We're betting against our own forecast. Flip to YES if implied
          is reasonable (≤ ceiling); otherwise skip as too expensive.
      - If forecast predicts NO AND proposed_outcome == "yes":
          Long-shot YES on the wrong side of our forecast — a σ-smearing
          artifact. Skip.
      - Otherwise: natural path; return unchanged.
    """
    proposed_outcome = proposed_outcome.lower()
    if direction == "between" and threshold_high_f is not None:
        forecast_predicts_yes = (threshold_f <= forecast_temp_f <= threshold_high_f)
        zone_repr = f"[{threshold_f},{threshold_high_f}]F"
    elif direction == "greater":
        forecast_predicts_yes = (forecast_temp_f > threshold_f)
        zone_repr = f">{threshold_f}F"
    elif direction == "less":
        forecast_predicts_yes = (forecast_temp_f < threshold_f)
        zone_repr = f"<{threshold_f}F"
    else:
        return BucketGuardResult(outcome=proposed_outcome, action=None, skip_reason=None)

    if forecast_predicts_yes and proposed_outcome == "no":
        if implied_yes <= flip_yes_implied_ceiling:
            return BucketGuardResult(
                outcome="yes",
                action="flipped_no_to_yes",
                skip_reason=None,
            )
        return BucketGuardResult(
            outcome=None,
            action="block_no_yes_too_expensive",
            skip_reason=(
                f"bucket_guard ({direction}): forecast {forecast_temp_f:.1f}F "
                f"predicts YES zone {zone_repr} - refused NO; implied_yes "
                f"{implied_yes:.2f} > flip ceiling {flip_yes_implied_ceiling:.2f}"
            ),
        )
    if (not forecast_predicts_yes) and proposed_outcome == "yes":
        return BucketGuardResult(
            outcome=None,
            action="block_yes_forecast_outside",
            skip_reason=(
                f"bucket_guard ({direction}): forecast {forecast_temp_f:.1f}F "
                f"predicts NO (outside YES zone {zone_repr}) - refused YES "
                f"(sigma-smearing artifact)"
            ),
        )
    return BucketGuardResult(outcome=proposed_outcome, action=None, skip_reason=None)


def apply_entry_price_floor(
    *,
    outcome: str,
    share_price: float,
    min_yes_entry: float = 0.10,
    min_no_entry: float = 0.50,
) -> str | None:
    """Side-specific cheap-tail skip.

    Comparator asymmetry by design:
      - YES: skip when share_price <= min_yes_entry  (inclusive)
      - NO:  skip when share_price <  min_no_entry   (strict)

    The NO comparator stays strict so $0.50 itself aligns with the live
    [0.50, 0.60) entry-price band used in the post-cutoff RT analysis,
    rather than being suppressed at the boundary. YES stays inclusive
    because the cheap-YES floor sits in a region where no trades have
    been observed at all in the post-cutoff window.

    Backed by post-cutoff round-trip data (2026-05-16T19:18Z onward):
    YES entries <= $0.10 went 0/5 (-$37.50); NO entries < $0.50 went 0/5
    (-$37.50). Cheap-tail bets sized to fixed notional lose the full
    stake on every miss; with zero wins observed in either bucket, EV is
    negative regardless of model edge.

    Returns a skip_reason string when the price triggers the floor; None
    means proceed to sizing.
    """
    if outcome == "yes" and share_price <= min_yes_entry:
        return f"entry_below_floor: yes {share_price:.3f} <= {min_yes_entry:.2f}"
    if outcome == "no" and share_price < min_no_entry:
        return f"entry_below_floor: no {share_price:.3f} < {min_no_entry:.2f}"
    return None


# ---------------------------------------------------------------------------
# C3 (Tier 1 plan): F→C→F rounding-artifact deterministic predictor.
#
# Pure function; no I/O; no live decision path consumes it today
# (gated-consumption per plans/tier1-data-foundation-kalshi-weather.md).
# Used by scripts/backtest_rounding_flip.py to measure whether the ASOS
# Celsius→Fahrenheit rounding band explains autopsy anomaly #2 tail losses.
# ---------------------------------------------------------------------------


def cli_rounding_risk(
    public_temp_f: float,
    threshold_f: int,
    direction: Literal["max", "min"],
) -> dict[str, Any]:
    """Predict whether F→C→F rounding could flip CLI settlement vs the public feed.

    Mechanism: ASOS sensors observe in °C, round to 1 decimal, then convert
    to °F for the NWS CLI report (integer values). A small slice of real
    °C readings maps to either of two °F integers depending on rounding
    direction. When the public-feed (forecast or ASOS) °F value is close
    to a Kalshi market threshold, that rounding band is settlement risk.

    Algorithm:
      1. Convert public_temp_f → °C with 1-decimal rounding.
      2. Enumerate the rounding neighborhood: {c - 0.1, c, c + 0.1} °C.
      3. For each neighbor, convert back to integer °F.
      4. risk_flag = True if any neighbor's °F differs from
         round(public_temp_f) AND that difference crosses the threshold
         (given direction).

    Returns:
        {
          "risk_flag": bool,
          "delta_predicted_f": float,  # signed worst-case °F delta toward
                                       # the threshold-crossing side; 0.0
                                       # when no flip risk
          "candidate_c_values": list[float],
          "rationale": str,
        }

    Two intended use modes:
      - Entry-time: pass forecast_temp_f as public_temp_f to flag
        boundary-adjacent bets at decision time.
      - Settlement-time: pass last public ASOS reading to predict
        whether CLI will round to a different integer.

    Example:
        >>> r = cli_rounding_risk(72.5, 73, "max")
        >>> r["risk_flag"]
        True
    """
    public_int = round(public_temp_f)
    c = round((public_temp_f - 32.0) * 5.0 / 9.0, 1)
    candidates = [round(c - 0.1, 1), c, round(c + 0.1, 1)]

    flipped_fs: list[int] = []
    for cn in candidates:
        f_n = round(cn * 9.0 / 5.0 + 32.0)
        if f_n != public_int:
            flipped_fs.append(f_n)

    if not flipped_fs:
        return {
            "risk_flag": False,
            "delta_predicted_f": 0.0,
            "candidate_c_values": candidates,
            "rationale": (
                f"public {public_temp_f}F -> {c}C; all rounding-band "
                f"neighbors round back to {public_int}F"
            ),
        }

    # Worst-case delta toward the threshold-crossing side.
    deltas = [f_n - public_int for f_n in flipped_fs]
    if direction == "max":
        worst_delta = min(deltas)
    else:
        worst_delta = max(deltas)

    # Did any flipped integer cross threshold_f relative to public_int?
    threshold_crossed = False
    for f_n in flipped_fs:
        if direction == "max":
            if public_int >= threshold_f and f_n < threshold_f:
                threshold_crossed = True
                break
            if public_int < threshold_f and f_n >= threshold_f:
                threshold_crossed = True
                break
        else:  # "min"
            if public_int <= threshold_f and f_n > threshold_f:
                threshold_crossed = True
                break
            if public_int > threshold_f and f_n <= threshold_f:
                threshold_crossed = True
                break

    return {
        "risk_flag": threshold_crossed,
        "delta_predicted_f": float(worst_delta),
        "candidate_c_values": candidates,
        "rationale": (
            f"public {public_temp_f}F->{c}C; neighbors round to "
            f"{sorted(set(flipped_fs))}F; "
            f"{'threshold crossed' if threshold_crossed else 'no cross'} "
            f"vs {threshold_f}F ({direction})"
        ),
    }


# ---------------------------------------------------------------------------
# Per-(station_id, season) forecast bias offsets (Tier 1 + 2026-05-25 v1).
#
# Fit on NBM 2021-2024 train window: offset = mean(cli_actual - nbm_p50).
# Filtered per Board decision (Reading C) to cells where |offset| >= 1.0°F
# — only large, structurally-real biases ship. Small-bias cells get zero
# offset (avoids the out-of-sample overshoot failure mode the train/test
# validation surfaced — 32/76 cells WORSE when train_off was <1°F).
#
# Validation tags:
#   'fully_validated' — spring cells; NBM train/test (79% pass) AND
#                       nws_blend cross-source test on the 50K-row spring
#                       2026 audit corpus (84% pass).
#   'nbm_only'        — non-spring cells; NBM train/test only; nws_blend
#                       cross-source confirmation pending (no nws_blend
#                       data outside spring 2026 yet). Watch-item: re-
#                       validate each as live nws_blend data accumulates
#                       through summer/fall/winter; pull any that don't
#                       hold.
#
# Source: tmp/_offset_train_test.py output 2026-05-25.
# Applied additively to the forecast.temp_f before
# `evaluate_weather_market` in the calling strategy.
# Lookup is by `(station_id, season)` where station_id is the verified
# ICAO from `WeatherStationsRegistry` (registry-direct only). Season is
# the meteorological 4-bucket convention (see residual_logic.derive_season).
# ---------------------------------------------------------------------------

# Season derivation — INLINED 2026-05-26 byte-equivalent copy of
# trading_corp.data.residual_logic.derive_season. The bias offsets
# (BIAS_OFFSETS_V1) were fit using residual_logic.derive_season; the
# inlined copy must produce identical output for every date so an
# edge-date forecast (e.g., Feb 28, May 31, Aug 31, Nov 30) can never
# route to the wrong cell. Tests/test_derive_season_inlined_equiv.py
# asserts byte-equivalence across all (month, day) of a non-leap year.
# Reason for inlining: residual_logic.py was not on prod when the
# original deploy attempt (00:24 UTC 2026-05-26) shipped a strategy
# file importing from it → ModuleNotFoundError → service crash-loop
# (rolled back at 00:44 UTC). See feedback_deploy_import_graph_audit.md.
# Inlining removes the cross-module dependency; residual_logic.py
# still exists in trading_corp/data/ for the future C2/poller deploy
# where it ships as part of the bigger Tier-1 cron bundle.
Season = Literal["winter", "spring", "summer", "fall"]


def derive_season(d: date) -> Season:
    """Meteorological 4-bucket season derivation per Board direction Q3.

    Convention: Dec/Jan/Feb=winter, Mar/Apr/May=spring,
    Jun/Jul/Aug=summer, Sep/Oct/Nov=fall.
    """
    m = d.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


BIAS_OFFSET_SOURCE_TAG = "bias_offset_v1_train_2021_2024_filtered_1.0F"

BIAS_OFFSETS_V1: dict[tuple[str, str], tuple[float, str]] = {
    ("KAUS", "winter"): (-1.712, "nbm_only"),         # n_train=6,138
    ("KAUS", "spring"): (-2.464, "fully_validated"),  # n_train=6,624
    ("KAUS", "summer"): (-1.067, "nbm_only"),         # n_train=6,624
    ("KBOS", "summer"): (+1.104, "nbm_only"),         # n_train=6,624
    ("KDEN", "spring"): (-3.187, "fully_validated"),  # n_train=6,624
    ("KDEN", "fall"):   (+1.016, "nbm_only"),         # n_train=6,552
    ("KDFW", "spring"): (-1.649, "fully_validated"),  # n_train=6,624
    ("KHOU", "spring"): (-1.349, "fully_validated"),  # n_train=6,624
    ("KLAX", "fall"):   (+1.452, "nbm_only"),         # n_train=6,552
    ("KMDW", "winter"): (+1.079, "nbm_only"),         # n_train=6,138
    ("KMDW", "summer"): (-1.370, "nbm_only"),         # n_train=6,624
    ("KMDW", "fall"):   (+1.137, "nbm_only"),         # n_train=6,552
    ("KMSP", "spring"): (-1.266, "fully_validated"),  # n_train=6,624
    ("KMSY", "spring"): (-1.573, "fully_validated"),  # n_train=3,312
    ("KNYC", "spring"): (-1.187, "fully_validated"),  # n_train=6,624
    ("KNYC", "summer"): (-1.685, "nbm_only"),         # n_train=6,624
    ("KOKC", "winter"): (-1.448, "nbm_only"),         # n_train=6,138
    ("KOKC", "spring"): (-2.474, "fully_validated"),  # n_train=6,624
    ("KOKC", "summer"): (-1.072, "nbm_only"),         # n_train=6,624
    ("KSAT", "winter"): (-1.298, "nbm_only"),         # n_train=6,138
    ("KSAT", "spring"): (-1.223, "fully_validated"),  # n_train=6,588
    ("KSFO", "fall"):   (+1.357, "nbm_only"),         # n_train=6,534
}


def lookup_bias_offset(station_id: str | None, season: str) -> tuple[float, str]:
    """Return (offset_f, validation_tag) for a (station_id, season) pair.

    No match → (0.0, 'none'). station_id=None → (0.0, 'none').
    Caller is responsible for: (a) supplying station_id from registry-direct
    resolution (no hardcoded lookup), (b) deriving season from the target
    date via the same 4-bucket meteorological convention used in
    `residual_logic.derive_season`.
    """
    if station_id is None:
        return 0.0, "none"
    entry = BIAS_OFFSETS_V1.get((station_id, season))
    if entry is None:
        return 0.0, "none"
    return entry

