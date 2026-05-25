"""NBM (National Blend of Models) probabilistic forecast client.

Pulls per-station MaxT/MinT decile and standard-deviation forecasts from
the NOMADS-distributed NBP text bulletins. Source of calibrated forecast
uncertainty for the Tier 1 data foundation
(``plans/tier1-data-foundation-kalshi-weather.md``).

Bulletin format (verified live 2026-05-25 against KOKX):

    KOKX    NBM V5.0 NBP GUIDANCE    5/25/2026  1300 UTC
    TUE 26| WED 27| THU 28| FRI 29| SAT 30| SUN 31| MON 01| TUE 02| WED 03|
    UTC    12| 00  12| 00  12| 00  12| 00  12| 00  12| 00  12| 00  12| 00  12| 00
    FHR    23| 35  47| 59  71| 83  95|107 119|131 143|155 167|179 191|203 215|227
    TXNMN  60| 82  62| 83  65| 83  58| 77  56| 75  55| 76  57| 79  59| 80  60| 80
    TXNSD   2|  3   3|  5   2|  4   4|  6   5|  7   6|  7   5|  6   5|  6   5|  6
    TXNP1  57| 79  58| 76  62| 78  52| 70  50| 66  48| 68  52| 72  52| 72  53| 72
    ...

Each TXN row has 9 (MinT|MaxT) column-pairs corresponding to 9 successive
days. The date header (TUE 26| ...) gives the day-of-month for each
column. Per the NBM v4.2 card: MinT is listed at 12z, MaxT at 00z of the
following day; both refer to the same calendar day in the column header.

NBM cycles release 4x daily at 01z/07z/13z/19z. The probabilistic bulletin
(blend_nbptx.tCCz) is ~33 MB containing all ~9,000 NWS stations
concatenated. The client downloads once per cycle and extracts only the
target ICAOs.

Strictly write-only ingestion source. No live decision path reads this
client today (gated-consumption per the Tier 1 plan).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx

log = logging.getLogger(__name__)

NOMADS_BASE = (
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
    "blend.{date}/{hh}/text/blend_nbptx.t{hh}z"
)
USER_AGENT = "trading-corp-weather-arb (+https://trading.jacksumner.com)"

_REQUEST_TIMEOUT_SEC = 90.0  # 33 MB file; generous
_CACHE_TTL_SEC = 6 * 3600    # NBM cycles 4x daily (every 6h)

_NBM_CYCLE_HOURS = (1, 7, 13, 19)
_PUBLISH_LAG_MIN = 30  # NOMADS upload typically completes within 30 min

_BLOCK_HEADER_RE = re.compile(r"^\s*(K[A-Z0-9]{3})\s+NBM V[\d.]+\s+NBP GUIDANCE\s+")
_DATE_HEADER_RE = re.compile(r"([A-Z]{3})\s+(\d{1,2})\|")
_PAIR_RE = re.compile(r"(-?\d+)\|\s*(-?\d+)")


@dataclass(frozen=True)
class NBMObservation:
    """One NBM probabilistic row for a single (station, valid_date, kind)."""
    station_id: str
    cycle_iso: str       # ISO-8601 UTC, e.g. '2026-05-25T13:00:00+00:00'
    valid_iso: str       # ISO-8601 UTC at 00:00:00 of the valid calendar day
    kind: Literal["daily_min", "daily_max"]
    horizon_hours: float
    temp_p10_f: float
    temp_p20_f: float
    temp_p50_f: float
    temp_p70_f: float
    temp_p90_f: float
    temp_sigma_f: float
    temp_mean_f: float


def latest_cycle_dt(now_utc: datetime | None = None) -> datetime:
    """Return the most recent NBM cycle datetime ≥ _PUBLISH_LAG_MIN ago."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    candidate = now_utc - timedelta(minutes=_PUBLISH_LAG_MIN)
    for hour in reversed(_NBM_CYCLE_HOURS):
        if candidate.hour >= hour:
            return candidate.replace(hour=hour, minute=0, second=0, microsecond=0)
    # candidate hour < 1: roll back to yesterday's 19z
    prev_day = candidate - timedelta(days=1)
    return prev_day.replace(hour=19, minute=0, second=0, microsecond=0)


def cycle_url(cycle_dt: datetime) -> str:
    return NOMADS_BASE.format(
        date=cycle_dt.strftime("%Y%m%d"),
        hh=cycle_dt.strftime("%H"),
    )


def _parse_block_dates(date_header_line: str, cycle_dt: datetime) -> list[datetime]:
    """Convert the 'TUE 26| WED 27| ...' header into UTC datetimes.

    Each returned datetime is at 00:00 UTC of the calendar day shown.
    Year roll-over is inferred from the cycle date (day-number monotonic
    forward across the next 9 days, with month roll at end-of-month).
    """
    pairs = _DATE_HEADER_RE.findall(date_header_line)
    dates: list[datetime] = []
    if not pairs:
        return dates
    year = cycle_dt.year
    month = cycle_dt.month
    prev_day = 0
    for _dow, day_str in pairs:
        day = int(day_str)
        if prev_day and day < prev_day:
            # month roll-over
            month += 1
            if month > 12:
                month = 1
                year += 1
        dates.append(datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc))
        prev_day = day
    return dates


def _parse_value_row(label: str, line: str, n_columns: int) -> list[tuple[int, int]] | None:
    """Extract (MinT, MaxT) pairs from a TXNMN/TXNSD/TXNP* line.

    Returns None if the row label isn't found or the column count
    doesn't match the expected n_columns.
    """
    if not line.lstrip().startswith(label):
        return None
    pairs = [(int(a), int(b)) for a, b in _PAIR_RE.findall(line)]
    if len(pairs) != n_columns:
        log.warning(
            "NBM parse: row %s column count mismatch: got %d, expected %d",
            label, len(pairs), n_columns,
        )
        return None
    return pairs


def parse_bulletin(
    text: str,
    cycle_dt: datetime,
    target_icaos: set[str],
) -> dict[str, list[NBMObservation]]:
    """Parse the bulk NBP text bulletin; extract observations for target ICAOs.

    Stations not in target_icaos are skipped (cheap discard during the
    line-scan). Returns a dict keyed by ICAO. ICAOs absent from the
    bulletin are absent from the returned dict — callers must check
    presence themselves (per the §"Per-station extractability" mandate).
    """
    cycle_iso = cycle_dt.strftime("%Y-%m-%dT%H:00:00+00:00")
    out: dict[str, list[NBMObservation]] = {}

    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        match = _BLOCK_HEADER_RE.match(line)
        if not match:
            i += 1
            continue
        icao = match.group(1)
        if icao not in target_icaos:
            i += 1
            continue

        # Found a target station block. Scan forward for the date header
        # and the TXN* value rows, up to the next block header or EOF.
        block_dates: list[datetime] = []
        rows: dict[str, list[tuple[int, int]]] = {}
        j = i + 1
        while j < n:
            block_line = lines[j]
            if _BLOCK_HEADER_RE.match(block_line):
                break
            if not block_dates and _DATE_HEADER_RE.search(block_line):
                block_dates = _parse_block_dates(block_line, cycle_dt)
            for label in ("TXNMN", "TXNSD", "TXNP1", "TXNP2", "TXNP5", "TXNP7", "TXNP9"):
                if label not in rows:
                    pairs = _parse_value_row(label, block_line, len(block_dates) or 9)
                    if pairs is not None:
                        rows[label] = pairs
            j += 1

        observations = _build_observations(icao, cycle_dt, cycle_iso, block_dates, rows)
        if observations:
            out[icao] = observations
        i = j

    return out


def _build_observations(
    icao: str,
    cycle_dt: datetime,
    cycle_iso: str,
    block_dates: list[datetime],
    rows: dict[str, list[tuple[int, int]]],
) -> list[NBMObservation]:
    """Cross-tabulate rows × columns × kinds into NBMObservation rows."""
    required = {"TXNMN", "TXNSD", "TXNP1", "TXNP2", "TXNP5", "TXNP7", "TXNP9"}
    missing = required - set(rows.keys())
    if missing:
        log.warning("NBM parse: %s missing rows: %s", icao, sorted(missing))
        return []
    if not block_dates:
        log.warning("NBM parse: %s missing date header; skipping", icao)
        return []
    n_cols = len(block_dates)
    for label in required:
        if len(rows[label]) != n_cols:
            log.warning(
                "NBM parse: %s row %s has %d cols but date header has %d; skipping",
                icao, label, len(rows[label]), n_cols,
            )
            return []

    observations: list[NBMObservation] = []
    for col, valid_dt in enumerate(block_dates):
        valid_iso = valid_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        horizon_hours = (valid_dt - cycle_dt).total_seconds() / 3600.0
        for kind, slot in (("daily_min", 0), ("daily_max", 1)):
            observations.append(NBMObservation(
                station_id=icao,
                cycle_iso=cycle_iso,
                valid_iso=valid_iso,
                kind=kind,  # type: ignore[arg-type]
                horizon_hours=horizon_hours,
                temp_mean_f=float(rows["TXNMN"][col][slot]),
                temp_sigma_f=float(rows["TXNSD"][col][slot]),
                temp_p10_f=float(rows["TXNP1"][col][slot]),
                temp_p20_f=float(rows["TXNP2"][col][slot]),
                temp_p50_f=float(rows["TXNP5"][col][slot]),
                temp_p70_f=float(rows["TXNP7"][col][slot]),
                temp_p90_f=float(rows["TXNP9"][col][slot]),
            ))
    return observations


class NBMForecastClient:
    """Async fetcher for NBM probabilistic bulk text bulletins.

    One HTTP roundtrip per cycle (cached). Caller passes a target set
    of ICAOs to limit parsing work to the bet-on stations.
    """

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        # cache key = cycle_iso string; value = (cached_at_epoch, payload-text)
        self._cache: dict[str, tuple[float, str]] = {}

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT_SEC,
                headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def fetch_cycle(
        self,
        cycle_dt: datetime,
        target_icaos: set[str],
    ) -> dict[str, list[NBMObservation]] | None:
        """Download the bulk bulletin for the given cycle, parse target ICAOs.

        Returns dict[ICAO -> list[NBMObservation]] on success, None on
        any HTTP/parse failure. ICAOs absent from the bulletin are
        absent from the dict (caller verifies presence).
        """
        cycle_iso = cycle_dt.strftime("%Y-%m-%dT%H:00:00+00:00")
        cached = self._cache.get(cycle_iso)
        text: str | None = None
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
            text = cached[1]
        if text is None:
            url = cycle_url(cycle_dt)
            http = await self._ensure_http()
            async with self._lock:
                try:
                    r = await http.get(url)
                    r.raise_for_status()
                    text = r.text
                except Exception as exc:
                    log.warning("NBM fetch failed for %s: %s", url, exc)
                    return None
            self._cache[cycle_iso] = (time.time(), text)
        try:
            return parse_bulletin(text, cycle_dt, target_icaos)
        except Exception as exc:
            log.warning("NBM parse failed for cycle %s: %s", cycle_iso, exc)
            return None
