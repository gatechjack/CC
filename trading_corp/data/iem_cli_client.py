"""Iowa Environmental Mesonet (IEM) Daily Climate Report (CLI) client.

The NWS CLI Daily Climate Report is the canonical ground-truth product
Kalshi settles weather markets against (daily MaxT and MinT per station).
IEM mirrors all NWS CLI products as JSON via the public json/cli.py
endpoint, keyed by ICAO.

Endpoint verified live 2026-05-25:
    https://mesonet.agron.iastate.edu/json/cli.py?station={ICAO}&year={YYYY}

Response shape:
    {"results": [
        {"valid": "YYYY-MM-DD", "high": int, "low": int, ...},
        ...
    ]}

History: 25+ years available; daily granularity; commercial use permitted.
IEM `robots.txt` Crawl-delay: 120s (advisory for crawlers; we self-limit
with a small sleep between station requests as courtesy).

Build-now-safe / write-only consumer: this client is read by
`scripts/ingest_iem_cli_residuals.py`; nothing in the live decision
path reads it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterable

import httpx

log = logging.getLogger(__name__)

IEM_CLI_BASE = "https://mesonet.agron.iastate.edu/json/cli.py"
USER_AGENT = "trading-corp-weather-arb (+https://trading.jacksumner.com)"

_REQUEST_TIMEOUT_SEC = 30.0
_CACHE_TTL_SEC = 6 * 3600  # CLI is daily; 6h is conservative
_INTER_REQUEST_SLEEP_SEC = 1.5  # courtesy; IEM permits but advise pacing


@dataclass(frozen=True)
class CLIDay:
    """One day's NWS CLI report for one station."""
    station_id: str   # ICAO (matches registry)
    valid_date: date  # the calendar day the report covers
    high_f: int       # daily MaxT, °F integer (CLI publishes as integer)
    low_f: int        # daily MinT, °F integer


class IEMCLIClient:
    """Async per-station-per-year CLI fetcher with cache."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        # cache key = (station_id, year); value = (cached_at_epoch, list[CLIDay])
        self._cache: dict[tuple[str, int], tuple[float, list[CLIDay]]] = {}

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

    async def fetch_year(self, station_id: str, year: int) -> list[CLIDay] | None:
        """Return all CLI days IEM has for (station_id, year), or None on failure."""
        key = (station_id, year)
        cached = self._cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
            return cached[1]

        http = await self._ensure_http()
        params = {"station": station_id, "year": str(year)}
        async with self._lock:
            try:
                r = await http.get(IEM_CLI_BASE, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.warning("IEM CLI fetch failed for %s/%d: %s", station_id, year, exc)
                return None
            await asyncio.sleep(_INTER_REQUEST_SLEEP_SEC)

        days = _parse_cli_results(station_id, data)
        self._cache[key] = (time.time(), days)
        return days

    async def fetch_window(
        self,
        station_id: str,
        start: date,
        end: date,
    ) -> list[CLIDay] | None:
        """Return CLI days for [start, end] inclusive across year boundaries."""
        if start > end:
            return []
        all_days: list[CLIDay] = []
        for y in range(start.year, end.year + 1):
            year_days = await self.fetch_year(station_id, y)
            if year_days is None:
                return None
            for d in year_days:
                if start <= d.valid_date <= end:
                    all_days.append(d)
        return all_days


def _parse_cli_results(station_id: str, data: dict) -> list[CLIDay]:
    """Convert IEM JSON response to CLIDay list. Drops rows missing high/low."""
    out: list[CLIDay] = []
    results = data.get("results") or []
    for row in results:
        valid_str = row.get("valid")
        high = row.get("high")
        low = row.get("low")
        if valid_str is None or high is None or low is None:
            continue
        try:
            valid_date = date.fromisoformat(valid_str[:10])
            out.append(CLIDay(
                station_id=station_id,
                valid_date=valid_date,
                high_f=int(high),
                low_f=int(low),
            ))
        except (ValueError, TypeError):
            continue
    return out


# Module-level convenience used by tests and the ingestion script.
def parse_iem_cli_response(station_id: str, data: dict) -> list[CLIDay]:
    """Public alias for the parser (testable without HTTP)."""
    return _parse_cli_results(station_id, data)


def stations_to_years(station_ids: Iterable[str], start: date, end: date) -> list[tuple[str, int]]:
    """Cartesian product of stations × years covering [start, end]."""
    out: list[tuple[str, int]] = []
    for s in station_ids:
        for y in range(start.year, end.year + 1):
            out.append((s, y))
    return out
