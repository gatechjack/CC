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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from trading_corp.agents.strategies._weather_math import (
    ForecastPoint,
    WeatherVerdict,
    evaluate_weather_market,
)
from trading_corp.data.weather_forecast import WeatherForecastClient
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
}

# Kalshi temperature-market ticker prefixes we handle. Non-US (e.g.
# KXLOWTLV Tel Aviv) skipped — NWS is US-only.
_HANDLED_PREFIX_RE = re.compile(r"^KX(HIGH|LOW|TEMP)([A-Z]+?)-")
_NON_US_CITIES = {"TLV"}

# Rules-primary coordinate extractor (most reliable; preferred over the
# fallback table).
_COORDS_RE = re.compile(r"coordinates\s+([-\d\.]+)\s*,\s*([-\d\.]+)")


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
    ) -> list[ProposedOrder]:
        """One scan cycle. Returns ProposedOrders.

        `kalshi_broker` must be a connected KalshiBroker — used to pull
        markets (`list_markets` / discovery). The agent's own division is
        broker:paper for equity tracking; this broker is lazy-resolved
        in main.py.
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
                survivors.append({
                    "ticker": tkr,
                    "event_ticker": m.event_ticker,
                    "category": event.category,
                    "kind": kind,           # HIGH | LOW | TEMP
                    "city_code": city,
                    "yes_ask": m.yes_ask,
                    "no_ask": m.no_ask,
                    "yes_bid": m.yes_bid,
                    "no_bid": m.no_bid,
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

        # 3. Per-market: fetch full Market for strike + rules, forecast,
        # evaluate, emit if all gates pass.
        orders: list[ProposedOrder] = []
        new_cooldowns = self._load_cooldowns(now=now)
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
    ) -> tuple[WeatherVerdict | None, ProposedOrder | None,
               dict[str, Any] | None, dict[str, Any]]:
        tkr = cand["ticker"]
        rules = getattr(full, "rules_primary", None) or ""
        floor_strike = getattr(full, "floor_strike", None)
        cap_strike = getattr(full, "cap_strike", None)
        strike_type = (getattr(full, "strike_type", None) or "").lower()
        title = getattr(full, "title", None) or ""
        yes_ask_cents = getattr(full, "yes_ask", None)
        no_ask_cents = getattr(full, "no_ask", None)

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

        # Forecast lookup. For HIGH/LOW daily markets, use daily extremum;
        # for hourly TEMP markets, use the hour-containing point.
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

        # Implied: YES probability from yes_ask (cheaper side trades first).
        # Use yes_ask as "buy YES cost" → implied_yes ≈ yes_ask_dollars.
        yes_ask = (yes_ask_cents or 0) / 100.0
        no_ask = (no_ask_cents or 0) / 100.0
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
            "forecast_temp_f": forecast.temp_f,
            "forecast_sigma_f": forecast.sigma_f,
            "sigma_used_f": verdict.sigma_used_f,
            "delta_f": round(verdict.delta_f, 2),
            "implied_yes": round(implied_yes, 3),
            "prob_yes": round(verdict.prob_yes, 3),
            "edge_pct": round(verdict.edge_pct, 1),
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
        share_price = yes_ask if outcome == "yes" else no_ask
        if share_price <= 0 or share_price >= 1:
            eval_payload["skip_reason"] = f"share_price out-of-range ({share_price})"
            eval_payload["fired"] = False
            return verdict, None, {"code": "no_edge", **eval_payload}, eval_payload

        sizing = self._strat_cfg.get("sizing") or {}
        fixed_usd = float(sizing.get("fixed_amount", 1.0))
        qty = fixed_usd / share_price
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
                f"(edge {verdict.edge_pct:.1f}%); buy {outcome.upper()} @ {share_price:.3f}"
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
                "threshold_f": threshold,
                "direction": direction,
                "horizon_hours": round(horizon_h, 2),
                "delta_f": round(verdict.delta_f, 2),
                "prob_yes": verdict.prob_yes,
                "divergence_pct": verdict.edge_pct,
                "expires_at": cand["expected_expiration_time"],
                "max_dollar_risk": fixed_usd,
                "tier": "weather_forecast_fixed_usd",
                "source_signal": "nws_forecast",
                "is_prediction_market": True,
            },
        )
        return verdict, order, None, eval_payload

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


def _parse_target_time(rules: str, ticker: str, full_market: Any) -> str | None:
    """Best-effort target time. Prefers `expected_expiration_time` field
    since it's structured and reliable; falls back to ticker-suffix
    parsing for unusual chains.
    """
    # 1. Prefer the market's expected_expiration_time (already ISO)
    t = getattr(full_market, "expected_expiration_time", None) \
        or getattr(full_market, "expiration_time", None) \
        or getattr(full_market, "close_time", None)
    if t:
        return str(t)
    # 2. Fall back to ticker-suffix parsing for KXTEMPNYCH-style hourly
    # tickers: ticker has 'YYMMMDDhh' segment (e.g., '26MAY1113' = May 11 2026 13:00).
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})-", ticker)
    if m:
        try:
            yy = 2000 + int(m.group(1))
            mon = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                   "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}[m.group(2)]
            dd = int(m.group(3))
            hh = int(m.group(4))
            # NYC EDT/EST market hours — Kalshi expresses these in ET typically.
            # Assume EDT (UTC-4) for May-Oct dates, EST (UTC-5) Nov-Apr.
            offset_hours = 4 if 3 <= mon <= 10 else 5
            target = datetime(yy, mon, dd, hh, 0, 0, tzinfo=timezone.utc) \
                + timedelta(hours=offset_hours)
            return target.isoformat()
        except (KeyError, ValueError):
            return None
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
