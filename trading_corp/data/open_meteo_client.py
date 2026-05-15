"""Open-Meteo multi-model ensemble client.

Pulls temperature forecasts from multiple weather models (GFS, ICON,
ECMWF, etc.) in a single call. Each model contributes one temperature
per hour; we expose the per-model values so the caller can compute
ensemble mean + σ (standard deviation across models) as a *measured*
uncertainty instead of the heuristic `sigma_for_horizon`.

Free API, no auth, generous rate limits for non-commercial use.
Trading Corp paper-mode qualifies; if Open-Meteo rate-limits in the
future the caller falls back to the heuristic sigma.

Docs: https://open-meteo.com/en/docs
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "trading-corp-weather-arb (+https://trading.jacksumner.com)"

# Each model contributes one temperature per hour. Some models may be
# unavailable for a given location (e.g. HRRR is US-only) — the response
# silently omits those, and we use whatever subset succeeded.
DEFAULT_MODELS = (
    "gfs_global",
    "icon_global",
    "ecmwf_ifs04",
    "meteofrance_seamless",
    "gem_global",
)

_REQUEST_TIMEOUT_SEC = 15.0
_CACHE_TTL_SEC = 30 * 60  # forecasts update hourly; 30 min keeps it fresh


@dataclass(frozen=True)
class EnsembleObservation:
    """Per-model temperatures for one target hour (or daily extremum)."""
    target_iso: str
    members: list[float]      # temperatures in Fahrenheit, one per model
    models: list[str]         # parallel to members
    source: str = "open_meteo"

    @property
    def n_members(self) -> int:
        return len(self.members)

    @property
    def mean_f(self) -> float:
        return statistics.fmean(self.members) if self.members else 0.0

    @property
    def std_f(self) -> float:
        """Sample standard deviation across models; 0 if <2 members."""
        if len(self.members) < 2:
            return 0.0
        return statistics.stdev(self.members)


class OpenMeteoClient:
    """Async multi-model temperature client.

    Cache key is (rounded lat, rounded lon, forecast_days) so multiple
    ticker evaluations in the same scan cycle reuse one HTTP roundtrip.
    """

    def __init__(self, *, models: tuple[str, ...] = DEFAULT_MODELS) -> None:
        self._models = models
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[tuple[float, float, int], tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT_SEC,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _fetch_payload(
        self, lat: float, lon: float, forecast_days: int,
    ) -> dict[str, Any] | None:
        key = (round(lat, 3), round(lon, 3), forecast_days)
        cached = self._cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
            return cached[1]

        http = await self._ensure_http()
        params = {
            "latitude": str(key[0]),
            "longitude": str(key[1]),
            "hourly": "temperature_2m",
            "models": ",".join(self._models),
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
            "forecast_days": str(forecast_days),
        }
        try:
            r = await http.get(OPEN_METEO_BASE, params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("Open-Meteo fetch failed for (%s,%s): %s", key[0], key[1], e)
            return None
        self._cache[key] = (time.time(), data)
        return data

    async def get_ensemble_at(
        self, lat: float, lon: float, target_iso: str,
    ) -> EnsembleObservation | None:
        """Per-model temperatures for the hour containing `target_iso`."""
        target_dt = _parse_iso(target_iso)
        if target_dt is None:
            return None
        delta_hours = (target_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        forecast_days = max(1, min(4, int(delta_hours // 24) + 2))

        async with self._lock:
            payload = await self._fetch_payload(lat, lon, forecast_days)
        if payload is None:
            return None
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return None

        target_hour = target_dt.replace(minute=0, second=0, microsecond=0)
        target_str = target_hour.strftime("%Y-%m-%dT%H:00")
        idx: int | None
        try:
            idx = times.index(target_str)
        except ValueError:
            idx = None
            best_diff: float | None = None
            for i, ts in enumerate(times):
                dt = _parse_iso(ts)
                if dt is None:
                    continue
                diff = abs((dt - target_hour).total_seconds())
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    idx = i
            if idx is None:
                return None

        members: list[float] = []
        models_used: list[str] = []
        for model in self._models:
            arr = hourly.get(f"temperature_2m_{model}")
            if not arr:
                continue
            try:
                v = arr[idx]
            except IndexError:
                continue
            if v is None:
                continue
            try:
                members.append(float(v))
                models_used.append(model)
            except (TypeError, ValueError):
                continue

        if not members:
            return None
        return EnsembleObservation(
            target_iso=target_iso, members=members, models=models_used,
        )

    async def get_ensemble_daily_extremum(
        self, lat: float, lon: float, target_date_iso: str, kind: str,
    ) -> EnsembleObservation | None:
        """Per-model max/min across all hourly values on a given date.

        Each model contributes one daily extremum; σ across these tells
        you cross-model agreement on the day's high/low — exactly what
        Kalshi KXHIGH/KXLOW markets care about.
        """
        try:
            target_date = datetime.fromisoformat(target_date_iso).date()
        except (TypeError, ValueError):
            return None
        now_date = datetime.now(timezone.utc).date()
        delta_days = (target_date - now_date).days
        forecast_days = max(1, min(4, delta_days + 2))

        async with self._lock:
            payload = await self._fetch_payload(lat, lon, forecast_days)
        if payload is None:
            return None
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return None

        date_indices: list[int] = []
        for i, ts in enumerate(times):
            dt = _parse_iso(ts)
            if dt is None:
                continue
            if dt.date() == target_date:
                date_indices.append(i)
        if not date_indices:
            return None

        members: list[float] = []
        models_used: list[str] = []
        for model in self._models:
            arr = hourly.get(f"temperature_2m_{model}")
            if not arr:
                continue
            day_temps: list[float] = []
            for i in date_indices:
                try:
                    v = arr[i]
                except IndexError:
                    continue
                if v is None:
                    continue
                try:
                    day_temps.append(float(v))
                except (TypeError, ValueError):
                    continue
            if not day_temps:
                continue
            extremum = max(day_temps) if kind == "high" else min(day_temps)
            members.append(extremum)
            models_used.append(model)

        if not members:
            return None
        return EnsembleObservation(
            target_iso=f"{target_date.isoformat()}T00:00:00+00:00",
            members=members, models=models_used,
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
