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
    # Item 1.2 (run-age logging) — additive only. Wall-clock UTC when we
    # last hit Open-Meteo for this payload (cache-refresh time, NOT
    # now-if-cached). Open-Meteo doesn't expose model init time, so this
    # fetch-time is the freshness proxy. May be None for legacy/test paths
    # that bypass the cache machinery.
    fetched_at: str | None = None

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
        self._cache: dict[
            tuple[float, float, int],
            tuple[float, dict[str, Any], str],
        ] = {}
        # value: (cached_at_epoch, payload, fetched_at_iso)
        # Item 1.1 HRRR sidecar cache — same shape, separate key space so
        # the single-model HRRR payload doesn't collide with the multi-
        # model ensemble payload at the same (lat, lon, forecast_days).
        self._hrrr_cache: dict[
            tuple[float, float, int],
            tuple[float, dict[str, Any], str],
        ] = {}
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
    ) -> tuple[dict[str, Any], str] | None:
        """Fetch the multi-model ensemble payload + capture fetch-time.

        Returns (payload, fetched_at_iso) on success; None on failure.
        fetched_at_iso is the wall-clock when we last hit the API for this
        cache key (cache-refresh time, the freshness signal — NOT
        now-if-served-from-cache).
        """
        key = (round(lat, 3), round(lon, 3), forecast_days)
        cached = self._cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
            return cached[1], cached[2]

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
        fetched_at_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._cache[key] = (time.time(), data, fetched_at_iso)
        return data, fetched_at_iso

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
            result = await self._fetch_payload(lat, lon, forecast_days)
        if result is None:
            return None
        payload, fetched_at = result
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
            fetched_at=fetched_at,
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
            result = await self._fetch_payload(lat, lon, forecast_days)
        if result is None:
            return None
        payload, fetched_at = result
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
            fetched_at=fetched_at,
        )

    # ── Item 1.1: HRRR-only fetch (write-only logging path) ────────────────
    # See plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md
    # for the design + coord-discipline guarantee. This method must be
    # called with the EXACT lat/lon the existing forecast path uses, NOT
    # a city-name-keyed fallback. The caller is responsible (the strategy
    # passes through its already-xref-resolved `lat, lon` locals).

    HRRR_MODEL = "ncep_hrrr_conus"

    async def _fetch_hrrr_payload(
        self, lat: float, lon: float, forecast_days: int,
    ) -> tuple[dict[str, Any], str] | None:
        """Single-model HRRR fetch. Separate cache so the multi-model
        ensemble payload at the same coords isn't displaced. Single-model
        responses use the UNSUFFIXED `hourly.temperature_2m` field (verified
        2026-05-24)."""
        key = (round(lat, 3), round(lon, 3), forecast_days)
        cached = self._hrrr_cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
            return cached[1], cached[2]

        http = await self._ensure_http()
        params = {
            "latitude": str(key[0]),
            "longitude": str(key[1]),
            "hourly": "temperature_2m",
            "models": self.HRRR_MODEL,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
            "forecast_days": str(forecast_days),
        }
        try:
            r = await http.get(OPEN_METEO_BASE, params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.debug("Open-Meteo HRRR fetch failed for (%s,%s): %s",
                      key[0], key[1], e)
            return None
        fetched_at_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._hrrr_cache[key] = (time.time(), data, fetched_at_iso)
        return data, fetched_at_iso

    async def fetch_hrrr_only(
        self, lat: float, lon: float, target_iso: str, kind: str | None = None,
    ) -> EnsembleObservation | None:
        """HRRR-only forecast at (lat, lon) for target_iso.

        `kind` ∈ {None, 'high', 'low'}. None → forecast at the hour
        containing target_iso. 'high'/'low' → daily extremum across all
        hourly values on target_iso's date.

        Returns EnsembleObservation with n_members ≤ 1 (HRRR alone). Used
        for write-only audit logging during the observation week — not
        consumed by σ or decision logic. Coord-discipline guarantee: the
        caller MUST pass the same (lat, lon) used by the existing forecast
        fetch path; this method does no city-name lookup.

        HRRR is CONUS-only. For non-CONUS coords, Open-Meteo returns no
        temperature data and this returns None. Every Kalshi weather
        station today is in CONUS.
        """
        target_dt = _parse_iso(target_iso)
        if target_dt is None:
            return None
        if kind in ("high", "low"):
            try:
                target_date = datetime.fromisoformat(
                    target_iso.split("T")[0]
                ).date()
            except (TypeError, ValueError):
                return None
            now_date = datetime.now(timezone.utc).date()
            forecast_days = max(1, min(2, (target_date - now_date).days + 2))
        else:
            delta_hours = (
                target_dt - datetime.now(timezone.utc)
            ).total_seconds() / 3600.0
            # HRRR forecast horizon is short — cap at 2 days to stay in
            # HRRR's published-skill window and minimize payload size.
            forecast_days = max(1, min(2, int(delta_hours // 24) + 2))

        async with self._lock:
            result = await self._fetch_hrrr_payload(lat, lon, forecast_days)
        if result is None:
            return None
        payload, fetched_at = result
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        # Single-model response: UNSUFFIXED field name.
        temps = hourly.get("temperature_2m") or []
        if not times or not temps:
            return None

        if kind in ("high", "low"):
            try:
                target_date = datetime.fromisoformat(
                    target_iso.split("T")[0]
                ).date()
            except (TypeError, ValueError):
                return None
            day_temps: list[float] = []
            for i, ts in enumerate(times):
                dt = _parse_iso(ts)
                if dt is None or dt.date() != target_date:
                    continue
                try:
                    v = temps[i]
                except IndexError:
                    continue
                if v is None:
                    continue
                try:
                    day_temps.append(float(v))
                except (TypeError, ValueError):
                    continue
            if not day_temps:
                return None
            extremum = max(day_temps) if kind == "high" else min(day_temps)
            return EnsembleObservation(
                target_iso=target_iso, members=[extremum],
                models=[self.HRRR_MODEL], source="open_meteo_hrrr",
                fetched_at=fetched_at,
            )

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
        try:
            v = temps[idx]
        except IndexError:
            return None
        if v is None:
            return None
        try:
            temp_f = float(v)
        except (TypeError, ValueError):
            return None
        return EnsembleObservation(
            target_iso=target_iso, members=[temp_f],
            models=[self.HRRR_MODEL], source="open_meteo_hrrr",
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
