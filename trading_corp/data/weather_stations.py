"""Weather station cross-reference registry — pydantic-validated loader.

Reads ``config/weather_stations.yaml`` (the Phase 1 verified static
cross-reference) and exposes a hot-reloadable, fail-safe registry.

Public API::

    from trading_corp.data.weather_stations import get_registry

    reg = get_registry()
    series_entry = reg.lookup_series("KXHIGHTSEA")   # SeriesEntry | None
    station_entry = reg.lookup_station("KSEA")        # StationEntry | None

Phase 1: this module ships **dormant** — it is not imported by the
strategy. The strategy still uses ``_CITY_COORDS_FALLBACK`` unchanged.
Wiring happens in Phase 3 after the human verification pass (Phase 2).

Reload behaviour mirrors ``data.macro_calendar`` and
``data.ex_dividend_calendar``:
- mtime-checked at most every ``_RELOAD_SEC`` seconds.
- On validation failure: log a warning, keep using the previous
  valid in-memory copy (same fail-safe shape as
  ``_restore_bias_state`` in ``lord_otter.py``).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator

log = logging.getLogger(__name__)

_RELOAD_SEC = 5.0
_DEFAULT_YAML = Path("config/weather_stations.yaml")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Coords(BaseModel):
    lat: float
    lon: float

    @field_validator("lat")
    @classmethod
    def _lat_range(cls, v: float) -> float:
        if not (-90.0 <= v <= 90.0):
            raise ValueError(f"lat {v} out of range [-90, 90]")
        return v

    @field_validator("lon")
    @classmethod
    def _lon_range(cls, v: float) -> float:
        if not (-180.0 <= v <= 180.0):
            raise ValueError(f"lon {v} out of range [-180, 180]")
        return v


class StationFeeds(BaseModel):
    nws_points: Optional[str] = None
    nbm_bulletin: Optional[str] = None
    mos_mav: Optional[str] = None
    mos_mex: Optional[str] = None
    metar_obs: Optional[str] = None
    asos_history: Optional[str] = None
    cli_observed_html: Optional[str] = None


class Station(BaseModel):
    icao: str
    name: str
    nws_wfo: str
    cli_product: str
    cli_location_name: str
    coords: Coords
    feeds: StationFeeds

    @field_validator("icao")
    @classmethod
    def _icao_pattern(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[A-Z0-9]{4}", v):
            raise ValueError(f"icao {v!r} does not match ^[A-Z0-9]{{4}}$")
        return v

    @field_validator("nws_wfo")
    @classmethod
    def _wfo_pattern(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError(f"nws_wfo {v!r} does not match ^[A-Z]{{3}}$")
        return v

    @field_validator("cli_product")
    @classmethod
    def _cli_pattern(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"CLI[A-Z]{3,4}", v):
            raise ValueError(f"cli_product {v!r} does not match ^CLI[A-Z]{{3,4}}$")
        return v


class Series(BaseModel):
    settles_at: Optional[str] = None
    settles_what: Literal["daily_max_temp", "daily_min_temp", "hourly_temp_at_hour"]
    source: Literal["nws_cli", "accuweather", "other"]
    rules_excerpt: Optional[str] = None
    verified: bool = False
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    verified_via_market: Optional[str] = None
    correction_note: Optional[str] = None
    disabled: bool = False
    disabled_reason: Optional[str] = None
    live_trading_blocked: bool = False
    cited_coords: Optional[Coords] = None


class Doc(BaseModel):
    schema_version: int
    stations: dict[str, Station]
    series: dict[str, Series]

    @model_validator(mode="after")
    def _check_settles_at_refs(self) -> "Doc":
        errors: list[str] = []
        for series_name, s in self.series.items():
            if s.settles_at is not None and s.settles_at not in self.stations:
                errors.append(
                    f"series {series_name!r}: settles_at={s.settles_at!r} "
                    f"not in stations"
                )
        if errors:
            raise ValueError(
                "Orphan settles_at references: " + "; ".join(errors)
            )
        return self

    @model_validator(mode="after")
    def _check_non_nws_disabled(self) -> "Doc":
        errors: list[str] = []
        for series_name, s in self.series.items():
            if s.source != "nws_cli" and not s.disabled:
                errors.append(
                    f"series {series_name!r}: source={s.source!r} but "
                    f"disabled=False (non-nws_cli series must be disabled)"
                )
        if errors:
            raise ValueError(
                "Non-nws_cli series without disabled=True: "
                + "; ".join(errors)
            )
        return self


# Type aliases exposed for callers
StationEntry = Station
SeriesEntry = Series


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class WeatherStationsRegistry:
    """Hot-reloading, fail-safe registry over ``config/weather_stations.yaml``.

    Instantiate via ``get_registry()`` for the process-wide singleton, or
    ``WeatherStationsRegistry(path)`` for tests.
    """

    def __init__(self, path: Path = _DEFAULT_YAML) -> None:
        self._path = path
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._doc: Optional[Doc] = None

    # ------------------------------------------------------------------
    # Reload mechanics
    # ------------------------------------------------------------------

    def _reload_if_stale(self) -> None:
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SEC:
            return
        self._last_check = now
        self._reload()

    def _reload(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            log.warning(
                "WeatherStationsRegistry: %s not found; keeping previous state",
                self._path,
            )
            return
        if mtime == self._mtime and self._doc is not None:
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as exc:
            log.warning(
                "WeatherStationsRegistry: failed to read %s: %s; "
                "keeping previous state",
                self._path,
                exc,
            )
            return
        try:
            doc = Doc.model_validate(raw)
        except Exception as exc:
            log.warning(
                "WeatherStationsRegistry: validation failed for %s: %s; "
                "keeping previous state",
                self._path,
                exc,
            )
            return
        self._doc = doc
        self._mtime = mtime
        log.info(
            "WeatherStationsRegistry reloaded: %d stations, %d series from %s",
            len(doc.stations),
            len(doc.series),
            self._path,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup_series(self, series_prefix: str) -> SeriesEntry | None:
        """Return the ``SeriesEntry`` for the given prefix, or ``None``."""
        self._reload_if_stale()
        if self._doc is None:
            return None
        return self._doc.series.get(series_prefix)

    def lookup_station(self, icao: str) -> StationEntry | None:
        """Return the ``StationEntry`` for the given ICAO, or ``None``."""
        self._reload_if_stale()
        if self._doc is None:
            return None
        return self._doc.stations.get(icao)

    def list_verified_series(self) -> list[tuple[str, SeriesEntry, StationEntry]]:
        """Return ``(prefix, series_entry, station_entry)`` for every series
        where ``verified=True`` and ``disabled`` is falsy and ``settles_at``
        resolves to a known station.

        Used by ingestion paths that need to iterate the canonical bet-on
        station set — e.g. the NBM and IEM CLI residual ingestion scripts.
        Going through this method is the structural guarantee that new
        ingestion paths inherit the YAML xref discipline (KJFK→KNYC,
        KORD→KMDW, KIAH→KHOU corrections etc.) without re-implementing
        coord resolution. The strategy's ``_resolve_coords`` returns only
        lat/lon (no station id) by design; new ingestion paths consume
        this list instead.
        """
        self._reload_if_stale()
        if self._doc is None:
            return []
        out: list[tuple[str, SeriesEntry, StationEntry]] = []
        for prefix, series in self._doc.series.items():
            if not series.verified or series.disabled or series.settles_at is None:
                continue
            station = self._doc.stations.get(series.settles_at)
            if station is None:
                continue
            out.append((prefix, series, station))
        return out

    @classmethod
    def load(cls, path: str | Path = _DEFAULT_YAML) -> "WeatherStationsRegistry":
        """Load and return a registry from the given path (eager first load)."""
        reg = cls(Path(path))
        reg._reload()
        return reg


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[WeatherStationsRegistry] = None


def get_registry(path: str | Path = _DEFAULT_YAML) -> WeatherStationsRegistry:
    """Return the process-wide singleton registry (lazy-initialised).

    The ``path`` argument is honoured only on the first call; subsequent
    calls return the existing singleton regardless of the argument.
    Use ``WeatherStationsRegistry.load(path)`` directly in tests that
    need isolation.
    """
    global _registry
    if _registry is None:
        _registry = WeatherStationsRegistry.load(path)
    return _registry
