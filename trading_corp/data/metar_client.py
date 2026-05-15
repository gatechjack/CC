"""METAR observation client for nowcasting.

Pulls recent METAR (Meteorological Aerodrome Report) observations from
aviationweather.gov for an airport station code. Provides:

  - Latest observed temperature (°F)
  - Short-term linear trend (°F/h) from the last few observations
  - `extrap_at(target_iso)` — linear extrapolation for nowcast blending

Used by `kalshi_weather_arb` on sub-6h horizon markets where current
observations should dominate the ensemble forecast.

Free, no auth.
Docs: https://aviationweather.gov/data/api/
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

METAR_BASE = "https://aviationweather.gov/api/data/metar"
USER_AGENT = "trading-corp-weather-arb (+https://trading.jacksumner.com)"

_REQUEST_TIMEOUT_SEC = 15.0
_CACHE_TTL_SEC = 5 * 60  # METAR updates ~hourly; 5 min is fresh enough


@dataclass(frozen=True)
class MetarNowcast:
    """Latest METAR + linear trend from recent observations."""
    station: str
    latest_temp_f: float
    latest_obs_iso: str
    trend_f_per_hour: float
    n_observations: int
    source: str = "metar"

    def extrap_at(self, target_iso: str) -> float | None:
        """Linear extrapolation: latest_temp + trend × Δhours.

        Only meaningful for short horizons (≤2h). Caller weights this
        against the ensemble forecast when blending.
        """
        latest = _parse_iso(self.latest_obs_iso)
        target = _parse_iso(target_iso)
        if latest is None or target is None:
            return None
        delta_h = (target - latest).total_seconds() / 3600.0
        return self.latest_temp_f + self.trend_f_per_hour * delta_h


class MetarClient:
    """Async METAR client. One instance per process."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, MetarNowcast | None]] = {}
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

    async def get_nowcast(self, station: str) -> MetarNowcast | None:
        """Latest METAR + trend for a station code (e.g. 'KJFK', 'KORD').

        Returns None if the station is invalid, the API errors out, or no
        recent observations are available.
        """
        async with self._lock:
            cached = self._cache.get(station)
            if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
                return cached[1]

            http = await self._ensure_http()
            params = {"ids": station, "format": "json", "hours": "3"}
            try:
                r = await http.get(METAR_BASE, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning("METAR fetch failed for %s: %s", station, e)
                self._cache[station] = (time.time(), None)
                return None

            if not isinstance(data, list) or not data:
                self._cache[station] = (time.time(), None)
                return None

            obs: list[tuple[datetime, float]] = []
            for row in data:
                t_iso = row.get("reportTime") or row.get("obsTime")
                temp_c = row.get("temp")
                if t_iso is None or temp_c is None:
                    continue
                t_dt = _parse_iso(t_iso)
                if t_dt is None:
                    continue
                try:
                    obs.append((t_dt, float(temp_c) * 9.0 / 5.0 + 32.0))
                except (TypeError, ValueError):
                    continue

            if not obs:
                self._cache[station] = (time.time(), None)
                return None

            obs.sort(key=lambda x: x[0], reverse=True)
            latest_t, latest_temp = obs[0]

            trend = 0.0
            if len(obs) >= 2:
                # Trend = slope between latest and oldest in our 3h window
                prior_t, prior_temp = obs[-1]
                delta_h = (latest_t - prior_t).total_seconds() / 3600.0
                # Guard near-duplicate timestamps
                if delta_h > 0.1:
                    trend = (latest_temp - prior_temp) / delta_h

            result = MetarNowcast(
                station=station,
                latest_temp_f=latest_temp,
                latest_obs_iso=latest_t.isoformat(),
                trend_f_per_hour=trend,
                n_observations=len(obs),
            )
            self._cache[station] = (time.time(), result)
            return result


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
