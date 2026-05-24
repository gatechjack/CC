"""NWS hourly-forecast client.

Free, no auth, US-only. Two-step protocol:
  1. GET /points/{lat},{lon} → returns gridpoint metadata + forecast URLs
  2. GET that hourly URL → list of forecast periods

Caches:
  - (lat, lon) → gridpoint forecast URL: 24h TTL (gridpoints are stable
    for a given lat/lon; only changes if NWS reorganizes which is rare)
  - forecast URL → list of hourly periods: 30min TTL (NWS updates hourly
    forecasts ~every hour; 30min cache means each market gets fresh-ish
    data without thrashing the API)

Returns `ForecastPoint` (from `_weather_math`) for the nearest hourly
period to the requested target time. None if (a) not in NWS coverage,
(b) target is too far in the future, (c) NWS errors out.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from trading_corp.agents.strategies._weather_math import ForecastPoint, sigma_for_horizon

log = logging.getLogger(__name__)

NWS_BASE = "https://api.weather.gov"
USER_AGENT = "trading-corp-weather-arb (+https://trading.jacksumner.com)"

# Tunables (could be lifted to YAML if hot-tune becomes useful)
_GRIDPOINT_TTL_SEC = 24 * 3600
_FORECAST_TTL_SEC = 30 * 60
_REQUEST_TIMEOUT_SEC = 15.0
_MAX_RETRIES = 2


class WeatherForecastClient:
    """Async NWS hourly-forecast client with two-layer cache.

    Constructed cheaply; one shared instance per process. Caches live in
    instance memory — bounded by the number of (lat, lon) coordinates
    Kalshi runs weather markets at (~20-30 cities).
    """

    def __init__(self) -> None:
        self._gridpoint_cache: dict[tuple[float, float], tuple[float, str]] = {}
        # value: (cached_at_epoch, forecast_hourly_url)
        self._forecast_cache: dict[
            str,
            tuple[float, list[dict[str, Any]], str | None, str | None],
        ] = {}
        # value: (cached_at_epoch, periods_list, last_modified_header, fetched_at_iso)
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT_SEC,
                headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── /points lookup (gridpoint URL) ────────────────────────────────────

    async def _get_forecast_hourly_url(self, lat: float, lon: float) -> str | None:
        key = (round(lat, 4), round(lon, 4))
        cached = self._gridpoint_cache.get(key)
        if cached and (time.time() - cached[0]) < _GRIDPOINT_TTL_SEC:
            return cached[1]

        http = await self._ensure_http()
        url = f"{NWS_BASE}/points/{key[0]},{key[1]}"
        try:
            r = await http.get(url)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("NWS /points failed for (%s,%s): %s", key[0], key[1], e)
            return None
        forecast_hourly = (
            (data.get("properties") or {}).get("forecastHourly")
        )
        if not forecast_hourly:
            log.warning("NWS /points returned no forecastHourly URL for (%s,%s)",
                        key[0], key[1])
            return None
        self._gridpoint_cache[key] = (time.time(), forecast_hourly)
        return forecast_hourly

    # ── /forecast/hourly fetch ────────────────────────────────────────────

    async def _get_periods(
        self, forecast_hourly_url: str,
    ) -> tuple[list[dict[str, Any]], str | None, str | None] | None:
        """Return (periods, last_modified, fetched_at_iso) or None on failure.

        last_modified is the upstream Last-Modified header (may be None if
        Akamai strips it on a given request); fetched_at_iso is the
        wall-clock UTC when we last hit NWS for THIS url (NOT now-if-cached
        — it's the cache fill time, the actual freshness signal).
        """
        cached = self._forecast_cache.get(forecast_hourly_url)
        if cached and (time.time() - cached[0]) < _FORECAST_TTL_SEC:
            return cached[1], cached[2], cached[3]

        http = await self._ensure_http()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                r = await http.get(forecast_hourly_url)
                r.raise_for_status()
                data = r.json()
                periods = (data.get("properties") or {}).get("periods") or []
                last_modified = r.headers.get("Last-Modified")
                fetched_at_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._forecast_cache[forecast_hourly_url] = (
                    time.time(), periods, last_modified, fetched_at_iso,
                )
                return periods, last_modified, fetched_at_iso
            except Exception as e:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                log.warning("NWS forecast fetch failed %s: %s", forecast_hourly_url, e)
                return None

    # ── public surface ────────────────────────────────────────────────────

    async def get_forecast_at(
        self, lat: float, lon: float, target_iso: str,
    ) -> ForecastPoint | None:
        """Forecast for the hour-period that contains `target_iso`.

        Returns None if NWS doesn't cover the location, the target falls
        outside the forecast window, or any request fails.

        `target_iso` should be ISO 8601 with timezone (e.g.
        '2026-05-15T17:00:00+00:00' or with 'Z' suffix).
        """
        async with self._lock:
            forecast_url = await self._get_forecast_hourly_url(lat, lon)
            if forecast_url is None:
                return None
            result = await self._get_periods(forecast_url)
            if not result:
                return None
            periods, last_modified, fetched_at = result
            if not periods:
                return None

        target_dt = _parse_iso(target_iso)
        if target_dt is None:
            return None

        # Find the period whose [startTime, endTime) contains target_dt.
        chosen = None
        for p in periods:
            start = _parse_iso(p.get("startTime") or "")
            end = _parse_iso(p.get("endTime") or "")
            if start is None or end is None:
                continue
            if start <= target_dt < end:
                chosen = p
                break
        if chosen is None:
            return None

        temp = chosen.get("temperature")
        unit = (chosen.get("temperatureUnit") or "F").upper()
        if temp is None:
            return None
        try:
            temp_f = float(temp) if unit == "F" else float(temp) * 9.0 / 5.0 + 32.0
        except (TypeError, ValueError):
            return None

        horizon_hours = (target_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        sigma = sigma_for_horizon(horizon_hours)
        return ForecastPoint(
            temp_f=temp_f,
            sigma_f=sigma,
            valid_iso=str(chosen.get("startTime") or ""),
            source="nws",
            issued_at=last_modified,
            fetched_at=fetched_at,
        )

    async def get_daily_extremum(
        self, lat: float, lon: float, target_date_iso: str, kind: str,
    ) -> ForecastPoint | None:
        """High/low temperature across the hourly periods on `target_date_iso`.

        `kind` is 'high' or 'low'. `target_date_iso` is 'YYYY-MM-DD'.

        Uses each hourly period's temperature; takes max/min across all
        hours that fall within the date in UTC. Sigma is computed off the
        first hour's horizon (closest in time).
        """
        async with self._lock:
            forecast_url = await self._get_forecast_hourly_url(lat, lon)
            if forecast_url is None:
                return None
            result = await self._get_periods(forecast_url)
            if not result:
                return None
            periods, last_modified, fetched_at = result
            if not periods:
                return None

        try:
            target_date = datetime.fromisoformat(target_date_iso).date()
        except (TypeError, ValueError):
            return None

        day_periods: list[tuple[datetime, float]] = []
        for p in periods:
            start = _parse_iso(p.get("startTime") or "")
            if start is None:
                continue
            if start.astimezone(timezone.utc).date() != target_date:
                continue
            temp = p.get("temperature")
            unit = (p.get("temperatureUnit") or "F").upper()
            if temp is None:
                continue
            try:
                temp_f = float(temp) if unit == "F" else float(temp) * 9.0 / 5.0 + 32.0
            except (TypeError, ValueError):
                continue
            day_periods.append((start, temp_f))

        if not day_periods:
            return None

        if kind == "high":
            chosen_start, extremum = max(day_periods, key=lambda x: x[1])
        else:
            chosen_start, extremum = min(day_periods, key=lambda x: x[1])

        horizon_hours = (
            chosen_start - datetime.now(timezone.utc)
        ).total_seconds() / 3600.0
        sigma = sigma_for_horizon(horizon_hours)
        return ForecastPoint(
            temp_f=extremum,
            sigma_f=sigma,
            valid_iso=chosen_start.isoformat(),
            source="nws",
            issued_at=last_modified,
            fetched_at=fetched_at,
        )


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
