"""Weather-market probability math + validation gates.

Venue-agnostic: any strategy that has (forecast_temp, sigma, threshold,
direction) can compute P(YES) and the standard skip gates here.

Used today by `kalshi_weather_arb`; future polymarket_weather_arb (if/when
Polymarket ships weather markets) would import the same module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


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


@dataclass(frozen=True)
class WeatherVerdict:
    """Output of forecast→probability evaluation."""
    prob_yes: float                # in [0, 1]
    edge_pct: float                # |prob_yes - implied_yes| × 100
    delta_f: float                 # forecast_temp - threshold (signed; F)
    sigma_used_f: float            # sigma after source-divergence augmentation
    fired: bool                    # True if all gates pass + edge ≥ min_divergence_pct
    skip_reason: str               # populated when fired=False


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
