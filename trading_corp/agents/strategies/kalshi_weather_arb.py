"""Kalshi Weather Arbitrage — forecast-driven strategy.

Pulls Kalshi Climate/Weather markets, fetches the actual NWS hourly
forecast for each market's lat/lon + target time, computes a calibrated
P(YES) via Gaussian probability vs threshold, and emits ProposedOrders
when the forecast diverges from market-implied probability by
≥ min_divergence_pct.

Replaces the generic LLM probability call (kalshi_llm_arbitrage) on
Climate/Weather markets — the LLM was guessing from training-data
climatology, this uses tonight's actual forecast.

Skip rules (all in `_weather_math.evaluate_weather_market`):
  - Target time > 72h away (NWS forecast skill degrades)
  - |forecast − threshold| < sigma_total (near-threshold uncertainty)
  - |P(YES) − implied| < min_divergence_pct (no edge)

Audit kinds:
  - kalshi_weather_scan        — per-cycle summary
  - kalshi_weather_evaluated   — per-market forecast + verdict
  - kalshi_weather_skipped_*   — granular skip reasons (parse / horizon / near-threshold / no-edge)
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from trading_corp.agents.strategies._weather_math import (
    BucketGuardResult,
    ForecastPoint,
    WeatherVerdict,
    apply_bucket_guard,
    apply_entry_price_floor,
    evaluate_weather_market,
    kalshi_quote_dollars,
    kelly_fraction,
    sigma_for_horizon,
)
from trading_corp.data.metar_client import MetarClient, MetarNowcast
from trading_corp.data.open_meteo_client import (
    EnsembleObservation,
    OpenMeteoClient,
)
from trading_corp.data.weather_forecast import WeatherForecastClient
from trading_corp.persistence import db
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


# ── City fallback map (used when rules_primary doesn't carry coords) ──
# These match Kalshi's documented resolution-source locations. Extend
# as new city markets emerge.
_CITY_COORDS_FALLBACK: dict[str, tuple[float, float]] = {
    # Hourly NYC Central Park (the KXTEMPNYCH chain)
    "NYC_CENTRAL": (40.7812, -73.9665),
    # Daily high/low chains (KXHIGH*, KXLOW*) — Kalshi uses major-airport
    # AccuWeather points. Coordinates verified via Kalshi rules_primary
    # for the documented cities; non-documented cities fall back to LookupError.
    "NYC": (40.6413, -73.7781),       # JFK
    "TBOS": (42.3656, -71.0096),      # BOS
    "TDC": (38.8512, -77.0402),       # DCA
    "TSEA": (47.4502, -122.3088),     # SEA
    "TATL": (33.6407, -84.4277),      # ATL
    "TDAL": (32.8998, -97.0403),      # DFW
    "PHIL": (39.8729, -75.2437),      # PHL
    "TOKC": (35.3931, -97.6007),      # OKC
    "MIA": (25.7959, -80.2870),       # MIA
    "CHI": (41.9742, -87.9073),       # ORD
    "AUS": (30.1975, -97.6664),       # AUS
    "TAUS": (30.1975, -97.6664),
    "TMIN": (44.8848, -93.2223),      # MSP
    "TSATX": (29.5337, -98.4698),     # SAT
    "TSFO": (37.6213, -122.3790),     # SFO
    "LAX": (33.9416, -118.4085),      # LAX
    "DEN": (39.8561, -104.6737),      # DEN
    "TDEN": (39.8561, -104.6737),
    "THOU": (29.9844, -95.3414),      # IAH
    "TPHX": (33.4373, -112.0078),     # PHX
    "TNOLA": (29.9934, -90.2580),     # MSY
    # Aliases observed in prod 2026-05-15 — Kalshi uses both the T-prefix
    # and non-T variants interchangeably across event chains. Each alias
    # points at the same resolution station as its non-T sibling.
    "TMIA": (25.7959, -80.2870),      # = MIA (KMIA)
    "TCHI": (41.9742, -87.9073),      # = CHI (KORD)
    "TPHIL": (39.8729, -75.2437),     # = PHIL (KPHL)
    "TLAX": (33.9416, -118.4085),     # = LAX (KLAX)
    "TNYC": (40.6413, -73.7781),      # = NYC (KJFK)
    "NY": (40.6413, -73.7781),        # = NYC (KJFK), bare 2-char form
}


# ── City fallback → METAR station code (used for sub-6h nowcast blend) ─
# Most Kalshi resolution stations are airports already; this map echoes
# the coords table. Central Park (NYC_CENTRAL) is the one non-airport
# entry — KNYC is the official METAR site for the park.
_CITY_TO_METAR_STATION: dict[str, str] = {
    "NYC_CENTRAL": "KNYC",
    "NYC": "KJFK",
    "TBOS": "KBOS",
    "TDC": "KDCA",
    "TSEA": "KSEA",
    "TATL": "KATL",
    "TDAL": "KDFW",
    "PHIL": "KPHL",
    "TOKC": "KOKC",
    "MIA": "KMIA",
    "CHI": "KORD",
    "AUS": "KAUS",
    "TAUS": "KAUS",
    "TMIN": "KMSP",
    "TSATX": "KSAT",
    "TSFO": "KSFO",
    "LAX": "KLAX",
    "DEN": "KDEN",
    "TDEN": "KDEN",
    "THOU": "KIAH",
    "TPHX": "KPHX",
    "TNOLA": "KMSY",
    # Aliases for Kalshi's T-prefix + bare-short variants (2026-05-15)
    "TMIA": "KMIA",
    "TCHI": "KORD",
    "TPHIL": "KPHL",
    "TLAX": "KLAX",
    "TNYC": "KJFK",
    "NY": "KJFK",
}

# Kalshi temperature-market ticker prefixes we handle. Non-US (e.g.
# KXLOWTLV Tel Aviv) skipped — NWS is US-only.
_HANDLED_PREFIX_RE = re.compile(r"^KX(HIGH|LOW|TEMP)([A-Z]+?)-")
_NON_US_CITIES = {"TLV"}

# Rules-primary coordinate extractor (most reliable; preferred over the
# fallback table).
_COORDS_RE = re.compile(r"coordinates\s+([-\d\.]+)\s*,\s*([-\d\.]+)")


@dataclass
class _SpendCounter:
    """Per-cycle Kelly-cap counter — seeded from audit history at the
    top of `run_scan_cycle`, then incremented in-memory as the cycle
    emits orders so successive markets in the same cycle see the
    correct remaining day/city budget."""
    total_usd: float = 0.0
    per_city_usd: dict[str, float] = field(default_factory=dict)

    def add(self, *, city: str, usd: float) -> None:
        self.total_usd += usd
        self.per_city_usd[city] = self.per_city_usd.get(city, 0.0) + usd


# ── Strategy ──────────────────────────────────────────────────────────────

class KalshiWeatherArbAgent:
    """Forecast-driven Kalshi weather arbitrage.

    Hot-reloadable config in `strategies.yaml kalshi_weather_arb:`. Pure
    deterministic — no LLM calls; forecast lookups are free via NWS.
    """

    name = "kalshi_weather_arb"

    def __init__(self, *, db_url: str | None = None) -> None:
        self._db_url = db_url
        self._strategies_yaml = Path("config/strategies.yaml")
        self._strat_mtime: float = 0.0
        self._strat_cfg: dict[str, Any] = {}
        self._forecast_client = WeatherForecastClient()
        self._open_meteo_client = OpenMeteoClient()
        self._metar_client = MetarClient()
        self._discovery_cache: Any = None
        self._discovery_ts: datetime | None = None
        self._reload()

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r") as f:
                    data = yaml.safe_load(f) or {}
                self._strat_cfg = data.get(self.name) or {}
                self._strat_mtime = sm
        except Exception as e:
            log.warning("kalshi_weather_arb: yaml reload failed: %s", e)
            self._strat_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("auto_execute", False))

    @property
    def division(self) -> str:
        self._reload()
        return str(self._strat_cfg.get("division", "kalshi_weather"))

    # ── public scan entry ────────────────────────────────────────────────

    async def run_scan_cycle(
        self, kalshi_broker: Any, *, logger_agent: Any = None,
        account_equity: float = 0.0,
    ) -> list[ProposedOrder]:
        """One scan cycle. Returns ProposedOrders.

        `kalshi_broker` must be a connected KalshiBroker — used to pull
        markets (`list_markets` / discovery). The agent's own division is
        broker:paper for equity tracking; this broker is lazy-resolved
        in main.py.

        `account_equity` is the bankroll the Kelly sizer scales against.
        Caller (main.py loop) snapshots the division's paper broker first.
        """
        self._reload()
        if not self.enabled:
            return []

        disc_cfg = self._strat_cfg.get("discovery") or {}
        max_series = int(disc_cfg.get("max_series_per_category", 30))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 600))
        min_div_pct = float(self._strat_cfg.get("min_divergence_pct", 10.0))
        max_hours = float(self._strat_cfg.get("max_horizon_hours", 72))
        k_per_cycle = int(self._strat_cfg.get("k_markets_per_cycle", 30))
        cooldown_h = float(self._strat_cfg.get("market_cooldown_hours", 4))

        # 1. Discover Climate/Weather markets via the broker's wrapper.
        now = datetime.now(timezone.utc)
        need_refresh = (
            self._discovery_cache is None
            or self._discovery_ts is None
            or (now - self._discovery_ts).total_seconds() > cache_ttl
        )
        if need_refresh:
            try:
                self._discovery_cache = await kalshi_broker.list_markets(
                    categories=("Climate and Weather",),
                    max_series_per_category=max_series,
                    max_markets_per_series=max_markets,
                )
                self._discovery_ts = now
            except Exception as e:
                log.warning("kalshi_weather_arb: discovery failed: %s", e)
                return []
        events = (self._discovery_cache.events
                  if self._discovery_cache is not None else [])
        survivors: list[dict[str, Any]] = []
        n_pre_filter = 0
        n_skipped_not_weather = 0
        n_skipped_non_us = 0
        n_skipped_no_strike = 0

        for event in events:
            for m in event.markets:
                n_pre_filter += 1
                tkr = m.ticker or ""
                tm = _HANDLED_PREFIX_RE.match(tkr)
                if not tm:
                    n_skipped_not_weather += 1
                    continue
                kind, city = tm.group(1), tm.group(2)
                if city.upper() in _NON_US_CITIES or city.upper().endswith("TLV"):
                    n_skipped_non_us += 1
                    continue
                # strike + direction come from per-market fetch (need full Market)
                yes_ask_d, no_ask_d, yes_bid_d, no_bid_d = kalshi_quote_dollars(m)
                survivors.append({
                    "ticker": tkr,
                    "event_ticker": m.event_ticker,
                    "category": event.category,
                    "kind": kind,           # HIGH | LOW | TEMP
                    "city_code": city,
                    "yes_ask": yes_ask_d,
                    "no_ask": no_ask_d,
                    "yes_bid": yes_bid_d,
                    "no_bid": no_bid_d,
                    "expected_expiration_time": m.expected_expiration_time,
                })

        # 2. Drop markets with no ASK quote — implied_yes downstream
        # needs yes_ask or no_ask (not bid). Kalshi returns 0.0 (not
        # None) for unquoted sides, so check positive explicitly.
        survivors = [
            d for d in survivors
            if (d.get("yes_ask") or 0) > 0 or (d.get("no_ask") or 0) > 0
        ]
        # 3. Order by tightest spread first (most useful to evaluate)
        survivors.sort(
            key=lambda d: abs((d.get("yes_ask") or 1) - (d.get("yes_bid") or 0))
        )
        survivors = survivors[:k_per_cycle]

        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_weather_scan",
                {
                    "strategy": self.name, "division": self.division,
                    "markets_pre_filter": n_pre_filter,
                    "skipped_not_weather": n_skipped_not_weather,
                    "skipped_non_us": n_skipped_non_us,
                    "candidates": len(survivors),
                    "k_per_cycle": k_per_cycle,
                    "min_divergence_pct": min_div_pct,
                    "max_horizon_hours": max_hours,
                },
            )

        # 3. Pre-compute today's spend for the daily/per-city Kelly caps.
        # Tallied across this strategy's `would_have_placed` audit rows
        # since UTC midnight. Refreshed once per cycle so cap consumption
        # within a single cycle still uses live counters (see `spend` below).
        day_total_usd, day_per_city_usd = self._query_today_spend(now=now)

        # 4. Per-market: fetch full Market for strike + rules, forecast,
        # evaluate, emit if all gates pass.
        orders: list[ProposedOrder] = []
        new_cooldowns = self._load_cooldowns(now=now)
        spend = _SpendCounter(
            total_usd=day_total_usd, per_city_usd=dict(day_per_city_usd),
        )
        for cand in survivors:
            tkr = cand["ticker"]
            if _is_in_cooldown(tkr, new_cooldowns, now, cooldown_h):
                continue

            try:
                full = await kalshi_broker._client.get_market(tkr)
            except Exception as e:
                log.debug("kalshi_weather_arb: get_market(%s) failed: %s", tkr, e)
                continue

            verdict, order, skip_reason, payload = await self._evaluate_market(
                full=full, cand=cand, min_div_pct=min_div_pct,
                max_hours=max_hours, now=now,
                account_equity=account_equity, spend=spend,
            )

            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_weather_evaluated", payload,
                )
            if order is not None:
                orders.append(order)
                new_cooldowns[tkr] = (now + timedelta(hours=cooldown_h)).isoformat()
            elif skip_reason and logger_agent is not None:
                logger_agent.log_event(
                    self.name, f"kalshi_weather_skipped_{skip_reason['code']}",
                    skip_reason,
                )

        self._save_cooldowns(new_cooldowns)
        return orders

    # ── per-market eval ──────────────────────────────────────────────────

    async def _evaluate_market(
        self, *, full: Any, cand: dict[str, Any], min_div_pct: float,
        max_hours: float, now: datetime,
        account_equity: float, spend: "_SpendCounter",
    ) -> tuple[WeatherVerdict | None, ProposedOrder | None,
               dict[str, Any] | None, dict[str, Any]]:
        tkr = cand["ticker"]
        rules = getattr(full, "rules_primary", None) or ""
        floor_strike = getattr(full, "floor_strike", None)
        cap_strike = getattr(full, "cap_strike", None)
        strike_type = (getattr(full, "strike_type", None) or "").lower()
        title = getattr(full, "title", None) or ""
        yes_ask, no_ask, _, _ = kalshi_quote_dollars(full)

        # Threshold + direction. Kalshi conventions:
        #   strike_type='greater' → YES if actual > floor_strike
        #   strike_type='less'    → YES if actual < cap_strike (or floor)
        #   strike_type='between' → range market: YES if floor ≤ actual ≤ cap
        threshold = None
        threshold_high = None
        direction = None
        if strike_type == "greater" and floor_strike is not None:
            threshold = float(floor_strike); direction = "greater"
        elif strike_type == "less":
            threshold = float(cap_strike if cap_strike is not None else floor_strike)
            direction = "less"
        elif strike_type == "greater_or_equal" and floor_strike is not None:
            threshold = float(floor_strike); direction = "greater"
        elif strike_type == "less_or_equal" and (cap_strike is not None or floor_strike is not None):
            threshold = float(cap_strike if cap_strike is not None else floor_strike)
            direction = "less"
        elif strike_type == "between" and floor_strike is not None and cap_strike is not None:
            threshold = float(floor_strike)
            threshold_high = float(cap_strike)
            direction = "between"
        if threshold is None or direction is None:
            payload = {
                "strategy": self.name, "division": self.division,
                "ticker": tkr, "title": title,
                "skip_code": "no_strike", "strike_type": strike_type,
            }
            return None, None, {"code": "no_strike", **payload}, payload

        # Coordinates: prefer rules_primary; fall back to city map
        lat, lon = _parse_coords(rules)
        if lat is None or lon is None:
            fall = _CITY_COORDS_FALLBACK.get(cand["city_code"].upper())
            if fall is None:
                payload = {
                    "strategy": self.name, "division": self.division,
                    "ticker": tkr, "title": title,
                    "skip_code": "no_coords", "city_code": cand["city_code"],
                }
                return None, None, {"code": "no_coords", **payload}, payload
            lat, lon = fall

        # Target time + horizon
        target_iso = _parse_target_time(rules, tkr, full)
        if target_iso is None:
            payload = {
                "strategy": self.name, "division": self.division,
                "ticker": tkr, "title": title,
                "skip_code": "no_target_time",
            }
            return None, None, {"code": "no_target_time", **payload}, payload

        try:
            tgt_dt = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
            if tgt_dt.tzinfo is None:
                tgt_dt = tgt_dt.replace(tzinfo=timezone.utc)
            horizon_h = (tgt_dt - now).total_seconds() / 3600.0
        except (TypeError, ValueError):
            payload = {
                "strategy": self.name, "division": self.division,
                "ticker": tkr, "title": title,
                "skip_code": "bad_target_time", "target_iso": target_iso,
            }
            return None, None, {"code": "bad_target_time", **payload}, payload

        # Forecast lookup — primary source. For HIGH/LOW daily markets use
        # the daily extremum; for hourly TEMP markets use the hour-period.
        if cand["kind"] in ("HIGH", "LOW"):
            kind_word = "high" if cand["kind"] == "HIGH" else "low"
            forecast = await self._forecast_client.get_daily_extremum(
                lat, lon, tgt_dt.date().isoformat(), kind=kind_word,
            )
        else:
            forecast = await self._forecast_client.get_forecast_at(
                lat, lon, target_iso,
            )

        if forecast is None:
            payload = {
                "strategy": self.name, "division": self.division,
                "ticker": tkr, "title": title,
                "skip_code": "no_forecast",
                "lat": lat, "lon": lon, "target_iso": target_iso,
            }
            return None, None, {"code": "no_forecast", **payload}, payload

        # ── Sigma upgrade: Open-Meteo cross-model ensemble ────────────────
        # Replace the sigma_for_horizon heuristic with the *measured*
        # standard deviation across GFS/ICON/ECMWF/etc. when ≥3 models
        # contributed. Fall back to heuristic when ensemble unavailable.
        ensemble: EnsembleObservation | None = None
        if self._open_meteo_enabled():
            try:
                if cand["kind"] in ("HIGH", "LOW"):
                    kind_word = "high" if cand["kind"] == "HIGH" else "low"
                    ensemble = await self._open_meteo_client.get_ensemble_daily_extremum(
                        lat, lon, tgt_dt.date().isoformat(), kind=kind_word,
                    )
                else:
                    ensemble = await self._open_meteo_client.get_ensemble_at(
                        lat, lon, target_iso,
                    )
            except Exception as e:
                log.debug("kalshi_weather_arb: open-meteo lookup failed: %s", e)
                ensemble = None

        if ensemble is not None and ensemble.n_members >= 3:
            sigma_floor = float(self._strat_cfg.get("ensemble_sigma_floor_f", 0.5))
            ensemble_sigma = max(ensemble.std_f, sigma_floor)
            sigma_source = "open_meteo_ensemble"
        else:
            ensemble_sigma = forecast.sigma_f  # heuristic fallback
            sigma_source = "heuristic"

        # ── Nowcast blend: METAR-derived current-temp extrapolation ───────
        # For sub-6h horizons, blend the NWS forecast value with the
        # METAR-extrapolated value (latest obs + linear trend). Weight w
        # ramps linearly: w=0 at horizon=0 (pure nowcast), w=1 at horizon=6h
        # (pure forecast). >6h uses forecast alone.
        nowcast: MetarNowcast | None = None
        forecast_temp_blended = forecast.temp_f
        blend_w: float | None = None
        nowcast_extrap: float | None = None
        nowcast_horizon_cap = float(
            self._strat_cfg.get("nowcast_blend_horizon_hours", 6.0)
        )
        if (
            self._metar_enabled()
            and horizon_h >= 0
            and horizon_h <= nowcast_horizon_cap
            and cand["kind"] not in ("HIGH", "LOW")  # daily extrema = no nowcast
        ):
            station = _CITY_TO_METAR_STATION.get(cand["city_code"].upper())
            if station:
                try:
                    nowcast = await self._metar_client.get_nowcast(station)
                except Exception as e:
                    log.debug("kalshi_weather_arb: metar lookup failed: %s", e)
                    nowcast = None
                if nowcast is not None:
                    nowcast_extrap = nowcast.extrap_at(target_iso)
                    if nowcast_extrap is not None:
                        blend_w = max(0.0, min(1.0, horizon_h / nowcast_horizon_cap))
                        forecast_temp_blended = (
                            blend_w * forecast.temp_f
                            + (1.0 - blend_w) * nowcast_extrap
                        )

        # Rebuild the ForecastPoint with the upgraded sigma + blended temp
        # so the downstream math gets a single, coherent object.
        forecast = ForecastPoint(
            temp_f=forecast_temp_blended,
            sigma_f=ensemble_sigma,
            valid_iso=forecast.valid_iso,
            source=f"{forecast.source}+{sigma_source}"
                   + ("+metar_blend" if blend_w is not None else ""),
        )

        # Implied: YES probability from yes_ask (cheaper side trades first).
        # Use yes_ask as "buy YES cost" → implied_yes ≈ yes_ask_dollars.
        implied_yes = yes_ask if 0 < yes_ask < 1 else (1.0 - no_ask if 0 < no_ask < 1 else None)
        if implied_yes is None:
            payload = {
                "strategy": self.name, "division": self.division,
                "ticker": tkr, "title": title,
                "skip_code": "no_implied",
            }
            return None, None, {"code": "no_implied", **payload}, payload

        # Run the math + gates
        verdict = evaluate_weather_market(
            forecast=forecast, threshold_f=threshold, direction=direction,
            threshold_high_f=threshold_high,
            implied_yes=implied_yes, horizon_hours=horizon_h,
            min_divergence_pct=min_div_pct, max_horizon_hours=max_hours,
        )

        eval_payload = {
            "strategy": self.name, "division": self.division,
            "ticker": tkr, "title": title, "category": cand["category"],
            "lat": lat, "lon": lon, "target_iso": target_iso,
            "horizon_hours": round(horizon_h, 2),
            "threshold_f": threshold, "threshold_high_f": threshold_high,
            "direction": direction,
            "forecast_temp_f": round(forecast.temp_f, 2),
            "forecast_sigma_f": round(forecast.sigma_f, 2),
            "sigma_used_f": round(verdict.sigma_used_f, 2),
            "sigma_source": sigma_source,
            "ensemble_n_members": (ensemble.n_members if ensemble else 0),
            "ensemble_std_f": (round(ensemble.std_f, 2) if ensemble else None),
            "nowcast_blend_w": (round(blend_w, 2) if blend_w is not None else None),
            "metar_station": (nowcast.station if nowcast else None),
            "metar_latest_temp_f": (
                round(nowcast.latest_temp_f, 2) if nowcast else None
            ),
            "metar_extrap_f": (
                round(nowcast_extrap, 2) if nowcast_extrap is not None else None
            ),
            "delta_f": round(verdict.delta_f, 2),
            "implied_yes": round(implied_yes, 3),
            "prob_yes": round(verdict.prob_yes, 3),
            "edge_pct": round(verdict.edge_pct, 1),
            "divergence_pct": round(verdict.edge_pct, 1),
            "fired": verdict.fired,
            "skip_reason": verdict.skip_reason,
            "forecast_source": forecast.source,
        }
        if not verdict.fired:
            # Logged as skipped_* by caller based on skip_reason content.
            code = "near_threshold" if "near-threshold" in verdict.skip_reason \
                else "horizon" if "horizon" in verdict.skip_reason \
                else "no_edge"
            return verdict, None, {"code": code, **eval_payload}, eval_payload

        # Build the ProposedOrder. Buy the cheaper side that matches our
        # P(YES) verdict.
        outcome = "yes" if verdict.prob_yes > implied_yes else "no"

        # Bucket-aware bet-side guard. Pure function in _weather_math.py;
        # documents the full failure-mode rationale there.
        guard = apply_bucket_guard(
            direction=direction,
            forecast_temp_f=forecast.temp_f,
            threshold_f=threshold,
            threshold_high_f=threshold_high,
            proposed_outcome=outcome,
            implied_yes=implied_yes,
            flip_yes_implied_ceiling=float(
                self._strat_cfg.get("bucket_guard_flip_yes_implied_ceiling", 0.70)
            ),
        )
        bucket_guard_action = guard.action
        if guard.outcome is None:
            # Blocked — skip this trade.
            eval_payload["bucket_guard"] = guard.action
            eval_payload["skip_reason"] = guard.skip_reason
            eval_payload["fired"] = False
            return verdict, None, {"code": "bucket_guard", **eval_payload}, eval_payload
        outcome = guard.outcome
        if bucket_guard_action:
            eval_payload["bucket_guard"] = bucket_guard_action

        share_price = yes_ask if outcome == "yes" else no_ask
        if share_price <= 0 or share_price >= 1:
            eval_payload["skip_reason"] = f"share_price out-of-range ({share_price})"
            eval_payload["fired"] = False
            return verdict, None, {"code": "no_edge", **eval_payload}, eval_payload

        # ── Entry-price floor (config-driven; cheap-tail skip) ─────────
        # See _weather_math.apply_entry_price_floor for the data motivating
        # the side-specific defaults and the YES-inclusive / NO-strict
        # comparator asymmetry. Skips here become `entry_below_floor`
        # audit rows so suppression rate stays observable.
        floor_skip = apply_entry_price_floor(
            outcome=outcome,
            share_price=share_price,
            min_yes_entry=float(self._strat_cfg.get("min_yes_entry", 0.10)),
            min_no_entry=float(self._strat_cfg.get("min_no_entry", 0.50)),
        )
        if floor_skip is not None:
            eval_payload["skip_reason"] = floor_skip
            eval_payload["fired"] = False
            return verdict, None, {"code": "entry_below_floor", **eval_payload}, eval_payload

        # ── Sizing: fractional Kelly with per-market / day / city caps ────
        # Kelly is computed against the outcome side we're actually buying.
        # For BUY-YES at price p: prob_outcome = verdict.prob_yes.
        # For BUY-NO  at price p: prob_outcome = 1 - verdict.prob_yes.
        prob_outcome = (
            verdict.prob_yes if outcome == "yes" else (1.0 - verdict.prob_yes)
        )
        order_usd, kelly_meta = self._compute_kelly_usd(
            prob_outcome=prob_outcome,
            share_price=share_price,
            account_equity=account_equity,
            city_code=cand["city_code"].upper(),
            spend=spend,
        )
        # Add kelly_meta to eval_payload for visibility regardless of outcome.
        eval_payload.update(kelly_meta)

        if order_usd <= 0:
            eval_payload["skip_reason"] = (
                f"kelly_usd={order_usd:.2f} below floor — "
                f"{kelly_meta.get('cap_reason', 'no_size')}"
            )
            eval_payload["fired"] = False
            return verdict, None, {"code": "no_size", **eval_payload}, eval_payload

        qty = order_usd / share_price
        # Update the live cap counters so subsequent markets in this cycle
        # see the correct remaining day/city budget.
        spend.add(city=cand["city_code"].upper(), usd=order_usd)

        order = ProposedOrder(
            strategy=self.name,
            symbol=f"{tkr}:{outcome}",
            side="buy",
            qty=qty,
            order_type="limit",
            limit_price=share_price,
            rationale=(
                f"Weather: forecast={forecast.temp_f:.1f}±{verdict.sigma_used_f:.1f}°F, "
                f"threshold={threshold:.2f}°F ({direction}), "
                f"P(YES)={verdict.prob_yes:.2f} vs implied {implied_yes:.2f} "
                f"(edge {verdict.edge_pct:.1f}%); buy {outcome.upper()} @ {share_price:.3f}; "
                f"size ${order_usd:.2f} "
                f"(kelly_full={kelly_meta['kelly_full_pct']:.1f}%, "
                f"fraction={kelly_meta['kelly_fraction_used']}, "
                f"cap={kelly_meta['applied_cap']})"
            ),
            extra={
                "outcome": outcome,
                "ticker": tkr,
                "event_ticker": cand["event_ticker"],
                "event_title": title,
                "title": title,
                "category": cand["category"],
                "implied_prob_at_entry": implied_yes,
                "forecast_temp_f": forecast.temp_f,
                "forecast_sigma_f": forecast.sigma_f,
                "sigma_used_f": verdict.sigma_used_f,
                "sigma_source": sigma_source,
                "ensemble_n_members": (ensemble.n_members if ensemble else 0),
                "ensemble_std_f": (ensemble.std_f if ensemble else None),
                "nowcast_blend_w": blend_w,
                "metar_station": (nowcast.station if nowcast else None),
                "metar_latest_temp_f": (
                    nowcast.latest_temp_f if nowcast else None
                ),
                "metar_extrap_f": nowcast_extrap,
                "threshold_f": threshold,
                "threshold_high_f": threshold_high,
                "direction": direction,
                "horizon_hours": round(horizon_h, 2),
                "delta_f": round(verdict.delta_f, 2),
                "prob_yes": verdict.prob_yes,
                "divergence_pct": verdict.edge_pct,
                "expires_at": cand["expected_expiration_time"],
                # Resolution-date of the weather target (parsed from the
                # ticker, e.g. KXHIGHDEN-26MAY15-B82.5 → 2026-05-15T...).
                # Distinct from `expires_at` (Kalshi's settlement window,
                # usually 14:00 UTC the day after). Audit-only: lets us
                # verify the date-parse fix is firing on the right day.
                "target_iso": target_iso,
                "max_dollar_risk": order_usd,
                "kelly_fraction_used": kelly_meta["kelly_fraction_used"],
                "kelly_full_pct": kelly_meta["kelly_full_pct"],
                "applied_cap": kelly_meta["applied_cap"],
                "account_equity_at_size": account_equity,
                "tier": "weather_forecast_kelly",
                "source_signal": "nws+open_meteo+metar",
                "is_prediction_market": True,
                # Bucket-aware bet-side guard outcome. None if the trade
                # took its natural model-driven side; otherwise one of:
                # "flipped_no_to_yes" / "block_no_yes_too_expensive" /
                # "block_yes_forecast_outside".
                "bucket_guard": bucket_guard_action,
            },
        )
        return verdict, order, None, eval_payload

    # ── Kelly sizing helpers ─────────────────────────────────────────────

    def _open_meteo_enabled(self) -> bool:
        return bool(self._strat_cfg.get("open_meteo_enabled", True))

    def _metar_enabled(self) -> bool:
        return bool(self._strat_cfg.get("metar_enabled", True))

    def _compute_kelly_usd(
        self, *, prob_outcome: float, share_price: float,
        account_equity: float, city_code: str, spend: "_SpendCounter",
    ) -> tuple[float, dict[str, Any]]:
        """Compute the per-order $ size from fractional Kelly + caps.

        Returns `(order_usd, meta)`. `order_usd` is 0 if the Kelly result
        falls below `min_usd` or the caps leave no headroom. `meta`
        records the full-Kelly fraction, applied fraction, dominating
        cap label, and any reason for a zero-size outcome.

        Sizing flow:
          1. Compute full Kelly fraction `f*`.
          2. Multiply by `kelly_fraction` (typically 0.25) → fractional
             Kelly target $.
          3. Clamp to per-market $ cap = max_per_market_pct × equity.
          4. Clamp to per-day remaining = max_per_day_pct × equity − today's spend.
          5. Clamp to per-city remaining = max_per_city_pct × equity − city spend.
          6. Floor: if < min_usd, return 0.
        """
        sizing = self._strat_cfg.get("sizing") or {}
        mode = str(sizing.get("mode", "kelly_fractional"))
        if mode == "fixed_usd":
            fixed_usd = float(sizing.get("fixed_amount", 1.0))
            return fixed_usd, {
                "kelly_full_pct": 0.0,
                "kelly_fraction_used": 0.0,
                "applied_cap": "fixed_usd",
                "cap_reason": "",
            }

        # kelly_fractional path
        kelly_fraction_cfg = float(sizing.get("kelly_fraction", 0.25))
        min_usd = float(sizing.get("min_usd", 1.0))
        max_per_market_pct = float(sizing.get("max_per_market_pct", 5.0))
        max_per_day_pct = float(sizing.get("max_per_day_pct", 25.0))
        max_per_city_pct = float(sizing.get("max_per_city_pct", 15.0))

        full_kelly = kelly_fraction(prob_outcome, share_price)
        kelly_full_pct = full_kelly * 100.0
        kelly_target = max(0.0, account_equity * kelly_fraction_cfg * full_kelly)

        # Per-market cap (always applied)
        per_market_cap = account_equity * (max_per_market_pct / 100.0)
        # Daily remaining
        day_cap = account_equity * (max_per_day_pct / 100.0)
        day_remaining = max(0.0, day_cap - spend.total_usd)
        # Per-city remaining
        city_cap = account_equity * (max_per_city_pct / 100.0)
        city_spent = spend.per_city_usd.get(city_code, 0.0)
        city_remaining = max(0.0, city_cap - city_spent)

        # The dominating cap is whichever shrinks the order most.
        order_usd = kelly_target
        applied_cap = "kelly_target"
        if order_usd > per_market_cap:
            order_usd = per_market_cap
            applied_cap = "per_market"
        if order_usd > day_remaining:
            order_usd = day_remaining
            applied_cap = "per_day"
        if order_usd > city_remaining:
            order_usd = city_remaining
            applied_cap = "per_city"

        cap_reason = ""
        if order_usd < min_usd:
            cap_reason = (
                f"below min_usd={min_usd:.2f} "
                f"(kelly_target={kelly_target:.2f}, "
                f"per_market={per_market_cap:.2f}, "
                f"day_remaining={day_remaining:.2f}, "
                f"city_remaining={city_remaining:.2f})"
            )
            order_usd = 0.0

        return order_usd, {
            "kelly_full_pct": round(kelly_full_pct, 2),
            "kelly_fraction_used": kelly_fraction_cfg,
            "applied_cap": applied_cap,
            "cap_reason": cap_reason,
        }

    def _query_today_spend(
        self, *, now: datetime,
    ) -> tuple[float, dict[str, float]]:
        """Sum today's `would_have_placed` $ exposure by total + city.

        Reads from `audit_event` since UTC midnight today. Falls back to
        (0, {}) on any DB error so a transient SQLite hiccup doesn't
        zero-out the day caps and let through an oversized order — the
        per-market cap still bounds the worst case.
        """
        if not self._db_url:
            return 0.0, {}
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        total_usd = 0.0
        per_city: dict[str, float] = {}
        try:
            import json as _json
            with db.connect(self._db_url) as conn:
                cur = conn.execute(
                    """
                    SELECT payload_json FROM audit_event
                    WHERE actor = ? AND kind = 'would_have_placed' AND ts >= ?
                    """,
                    (self.name, day_start.isoformat()),
                )
                for (payload_json,) in cur:
                    try:
                        p = _json.loads(payload_json or "{}")
                    except Exception:
                        continue
                    try:
                        qty = float(p.get("qty") or 0.0)
                        price = float(p.get("limit_price") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    usd = qty * price
                    if usd <= 0:
                        continue
                    total_usd += usd
                    ticker = str(p.get("ticker") or "")
                    m = _HANDLED_PREFIX_RE.match(ticker)
                    if m:
                        city = m.group(2).upper()
                        per_city[city] = per_city.get(city, 0.0) + usd
        except Exception as e:
            log.warning("kalshi_weather_arb: day-spend query failed: %s", e)
            return 0.0, {}
        return total_usd, per_city

    # ── cooldown state (in-memory; persistence is best-effort) ────────────

    def _cooldown_path(self) -> Path:
        return Path("data") / f"{self.name}_cooldowns.yaml"

    def _load_cooldowns(self, *, now: datetime) -> dict[str, str]:
        p = self._cooldown_path()
        if not p.exists():
            return {}
        try:
            with p.open("r") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return {}
        # Drop expired entries
        out: dict[str, str] = {}
        for k, v in (data or {}).items():
            try:
                exp = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp > now:
                    out[k] = v
            except Exception:
                continue
        return out

    def _save_cooldowns(self, cooldowns: dict[str, str]) -> None:
        try:
            p = self._cooldown_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w") as f:
                yaml.safe_dump(cooldowns, f)
        except Exception as e:
            log.debug("kalshi_weather_arb: cooldown save failed: %s", e)


# ── parsers ───────────────────────────────────────────────────────────────

def _parse_coords(rules_primary: str) -> tuple[float | None, float | None]:
    """Extract (lat, lon) from a Kalshi rules_primary string."""
    if not rules_primary:
        return None, None
    m = _COORDS_RE.search(rules_primary)
    if not m:
        return None, None
    try:
        return float(m.group(1)), float(m.group(2))
    except (TypeError, ValueError):
        return None, None


_MONTH_MAP = {
    "JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
    "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12,
}


def _parse_target_time(rules: str, ticker: str, full_market: Any) -> str | None:
    """Resolve the target weather time for a Kalshi market.

    Bug fix 2026-05-16: previously preferred `expected_expiration_time`,
    but Kalshi's expiration is the day AFTER the weather target (the
    settlement window). Off-by-one-day caused systematic forecast misses
    of 5-20°F when the weather changed between target and expiration.

    Correct precedence:
      1. PRIMARY — parse the date segment from the ticker. Ticker shape
         is `KX(HIGH|LOW|TEMP)<CITY>-<DATE>-<STRIKE>` where <DATE> is
         `YYMMMDD` (6 chars; daily HIGH/LOW) or `YYMMMDDhh` (8 chars;
         hourly TEMP). The ticker date IS the resolution date per
         Kalshi's rules_primary.
      2. FALLBACK — expected_expiration_time / close_time, but ONLY when
         ticker parse fails AND the rules string doesn't otherwise pin
         the date. Logs a warning in that case (caller should investigate).

    Returns ISO 8601 string in UTC. For daily markets, returns the date
    at 23:59:00 UTC of the target date (so horizon_h computation reflects
    "end of resolution day"). For hourly markets, returns the specific
    hour converted from ET to UTC (Kalshi expresses hourly tickers in ET).
    """
    # 1. PRIMARY: parse from ticker.
    # 1a. Hourly TEMP: 8-char date segment YYMMMDDhh.
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})-", ticker)
    if m:
        try:
            yy = 2000 + int(m.group(1))
            mon = _MONTH_MAP[m.group(2)]
            dd = int(m.group(3))
            hh = int(m.group(4))
            # Hourly tickers are ET. EDT = UTC-4 (Mar-Nov), EST = UTC-5.
            offset_hours = 4 if 3 <= mon <= 10 else 5
            target = datetime(yy, mon, dd, hh, 0, 0, tzinfo=timezone.utc) \
                + timedelta(hours=offset_hours)
            return target.isoformat()
        except (KeyError, ValueError):
            pass

    # 1b. Daily HIGH/LOW: 6-char date segment YYMMMDD. The resolution
    # uses the official daily climatological report so the target time
    # is "end of the named UTC date" (close enough — horizon math just
    # needs sub-day precision; the forecast call uses .date() anyway).
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})-", ticker)
    if m:
        try:
            yy = 2000 + int(m.group(1))
            mon = _MONTH_MAP[m.group(2)]
            dd = int(m.group(3))
            target = datetime(yy, mon, dd, 23, 59, 0, tzinfo=timezone.utc)
            return target.isoformat()
        except (KeyError, ValueError):
            pass

    # 2. FALLBACK ONLY when ticker parse fails. WARNING: Kalshi's
    # expiration is the day AFTER the weather target — using this for
    # the forecast lookup will produce a 1-day-off-by-bug.
    t = getattr(full_market, "expected_expiration_time", None) \
        or getattr(full_market, "expiration_time", None) \
        or getattr(full_market, "close_time", None)
    if t:
        log.warning(
            "kalshi_weather_arb: _parse_target_time falling back to "
            "expected_expiration_time for %s — ticker date parse failed. "
            "Forecast may be 1 day off.", ticker,
        )
        return str(t)
    return None


# ── cooldown helper ───────────────────────────────────────────────────────

def _is_in_cooldown(
    ticker: str, cooldowns: dict[str, str], now: datetime, cooldown_h: float,
) -> bool:
    if cooldown_h <= 0:
        return False
    exp = cooldowns.get(ticker)
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return now < exp_dt
    except Exception:
        return False
