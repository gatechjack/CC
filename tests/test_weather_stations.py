"""Tests for trading_corp.data.weather_stations (Phase 1).

Run via:
    .\\scripts\\run_capped.ps1 python -m pytest tests/test_weather_stations.py -v

Test cases:
1. test_yaml_loads                  — production YAML parses cleanly
2. test_all_39_series_present       — registry has exactly 39 known series
3. test_settles_at_resolves         — every non-None settles_at is a valid station
4. test_disabled_kxtempnych         — KXTEMPNYCH is disabled/accuweather/settles_at=None
5. test_corrected_six               — 6 corrected series have right ICAOs + correction_note
6. test_unknown_series_returns_none — lookup_series("KXFOO") returns None
7. test_validation_rejects_orphan_settles_at  — Doc validator catches orphan ref
8. test_validation_rejects_bad_coords         — Coords validator catches lat=999
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from trading_corp.data.weather_stations import (
    Coords,
    Doc,
    WeatherStationsRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROD_YAML = _REPO_ROOT / "config" / "weather_stations.yaml"

# All 39 series expected to be present
_ALL_39_SERIES = [
    "KXLOWTNYC",
    "KXHIGHTSEA",
    "KXHIGHTBOS",
    "KXLOWTAUS",
    "KXHIGHTDC",
    "KXHIGHLAX",
    "KXLOWTSATX",
    "KXHIGHTMIN",
    "KXHIGHNY",
    "KXHIGHCHI",
    "KXLOWTMIN",
    "KXHIGHTSFO",
    "KXHIGHTATL",
    "KXHIGHAUS",
    "KXLOWTDC",
    "KXLOWTCHI",
    "KXLOWTPHIL",
    "KXLOWTHOU",
    "KXHIGHTOKC",
    "KXHIGHDEN",
    "KXLOWTDEN",
    "KXHIGHTPHX",
    "KXHIGHTNOLA",
    "KXHIGHTHOU",
    "KXLOWTSFO",
    "KXHIGHTDAL",
    "KXLOWTDAL",
    "KXHIGHMIA",
    "KXLOWTNOLA",
    "KXHIGHTSATX",
    "KXHIGHPHIL",
    "KXLOWTMIA",
    "KXLOWTOKC",
    "KXLOWTLAX",
    "KXLOWTATL",
    "KXLOWTSEA",
    "KXLOWTPHX",
    "KXLOWTBOS",
    "KXTEMPNYCH",
]

assert len(_ALL_39_SERIES) == 39, "Fixture list must have exactly 39 entries"


# ---------------------------------------------------------------------------
# Test 1: production YAML loads cleanly
# ---------------------------------------------------------------------------


def test_yaml_loads() -> None:
    """The production config/weather_stations.yaml must validate without errors."""
    reg = WeatherStationsRegistry.load(_PROD_YAML)
    # If _doc is None after load, the loader swallowed an error — fail explicitly.
    assert reg._doc is not None, (
        f"Registry failed to load {_PROD_YAML}; check log output for validation errors"
    )


# ---------------------------------------------------------------------------
# Test 2: all 39 series present
# ---------------------------------------------------------------------------


def test_all_39_series_present() -> None:
    reg = WeatherStationsRegistry.load(_PROD_YAML)
    assert reg._doc is not None
    for s in _ALL_39_SERIES:
        assert reg.lookup_series(s) is not None, f"Missing series: {s!r}"
    assert len(reg._doc.series) == 39, (
        f"Expected 39 series, got {len(reg._doc.series)}"
    )


# ---------------------------------------------------------------------------
# Test 3: every non-None settles_at resolves to a station
# ---------------------------------------------------------------------------


def test_settles_at_resolves() -> None:
    reg = WeatherStationsRegistry.load(_PROD_YAML)
    assert reg._doc is not None
    for name, s in reg._doc.series.items():
        if s.settles_at is not None:
            assert reg.lookup_station(s.settles_at) is not None, (
                f"Series {name!r}: settles_at={s.settles_at!r} not in stations"
            )


# ---------------------------------------------------------------------------
# Test 4: KXTEMPNYCH is disabled
# ---------------------------------------------------------------------------


def test_disabled_kxtempnych() -> None:
    reg = WeatherStationsRegistry.load(_PROD_YAML)
    entry = reg.lookup_series("KXTEMPNYCH")
    assert entry is not None
    assert entry.disabled is True, "KXTEMPNYCH must be disabled"
    assert entry.source == "accuweather", (
        f"Expected source='accuweather', got {entry.source!r}"
    )
    assert entry.settles_at is None, (
        f"Expected settles_at=None, got {entry.settles_at!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: 6 corrected series have the right ICAOs and a correction_note
# ---------------------------------------------------------------------------


def test_corrected_six() -> None:
    reg = WeatherStationsRegistry.load(_PROD_YAML)

    nyc_series = ["KXLOWTNYC", "KXHIGHNY"]
    chi_series = ["KXHIGHCHI", "KXLOWTCHI"]
    hou_series = ["KXHIGHTHOU", "KXLOWTHOU"]

    for s in nyc_series:
        entry = reg.lookup_series(s)
        assert entry is not None, f"Missing series {s!r}"
        assert entry.settles_at == "KNYC", (
            f"{s}: expected settles_at=KNYC, got {entry.settles_at!r}"
        )
        assert entry.correction_note, f"{s}: missing correction_note"

    for s in chi_series:
        entry = reg.lookup_series(s)
        assert entry is not None, f"Missing series {s!r}"
        assert entry.settles_at == "KMDW", (
            f"{s}: expected settles_at=KMDW, got {entry.settles_at!r}"
        )
        assert entry.correction_note, f"{s}: missing correction_note"

    for s in hou_series:
        entry = reg.lookup_series(s)
        assert entry is not None, f"Missing series {s!r}"
        assert entry.settles_at == "KHOU", (
            f"{s}: expected settles_at=KHOU, got {entry.settles_at!r}"
        )
        assert entry.correction_note, f"{s}: missing correction_note"


# ---------------------------------------------------------------------------
# Test 6: unknown series returns None
# ---------------------------------------------------------------------------


def test_unknown_series_returns_none() -> None:
    reg = WeatherStationsRegistry.load(_PROD_YAML)
    result = reg.lookup_series("KXFOO")
    assert result is None


# ---------------------------------------------------------------------------
# Test 7: orphan settles_at is rejected by Doc validator
# ---------------------------------------------------------------------------


def test_validation_rejects_orphan_settles_at() -> None:
    fixture_yaml = """
schema_version: 1
stations:
  KSEA:
    icao: KSEA
    name: Seattle-Tacoma International Airport
    nws_wfo: SEW
    cli_product: CLISEA
    cli_location_name: Seattle-Tacoma, WA
    coords:
      lat: 47.4502
      lon: -122.3088
    feeds:
      nws_points: https://api.weather.gov/points/47.4502,-122.3088
      nbm_bulletin: null
      mos_mav: null
      mos_mex: null
      metar_obs: null
      asos_history: null
      cli_observed_html: null
series:
  KXHIGHTSEA:
    settles_at: KXXX_DOES_NOT_EXIST
    settles_what: daily_max_temp
    source: nws_cli
    verified: false
"""
    raw = yaml.safe_load(fixture_yaml)
    with pytest.raises(ValidationError):
        Doc.model_validate(raw)


# ---------------------------------------------------------------------------
# Test 8: bad coords (lat=999) rejected
# ---------------------------------------------------------------------------


def test_list_verified_series_yields_all_38_active() -> None:
    """list_verified_series() yields every verified+enabled series with a
    settles_at, paired with its station. Skips disabled KXTEMPNYCH."""
    reg = WeatherStationsRegistry.load(_PROD_YAML)
    rows = reg.list_verified_series()
    assert len(rows) == 38, (
        f"Expected 38 verified+enabled series (39 total - 1 disabled), got {len(rows)}"
    )
    prefixes = {r[0] for r in rows}
    assert "KXTEMPNYCH" not in prefixes, "Disabled series leaked into list"

    # Every yielded row must have all three components populated
    for prefix, series, station in rows:
        assert series.verified is True, f"{prefix}: non-verified series leaked"
        assert series.disabled is False, f"{prefix}: disabled series leaked"
        assert series.settles_at is not None, f"{prefix}: no settles_at"
        assert station.icao == series.settles_at, (
            f"{prefix}: station.icao {station.icao!r} != settles_at {series.settles_at!r}"
        )

    # Dedupe by ICAO — should give 19 unique stations (the bet-on station set)
    unique_icaos = {r[2].icao for r in rows}
    assert len(unique_icaos) == 19, (
        f"Expected 19 unique bet-on station ICAOs, got {len(unique_icaos)}: {sorted(unique_icaos)}"
    )

    # All 6 corrected stations must appear
    for required_icao in ("KNYC", "KMDW", "KHOU"):
        assert required_icao in unique_icaos, (
            f"Corrected station {required_icao} missing from list_verified_series"
        )


def test_list_verified_series_empty_on_unloaded_registry() -> None:
    """A bare registry (never loaded) returns an empty list — fail-safe."""
    reg = WeatherStationsRegistry(Path("/nonexistent/never-exists.yaml"))
    rows = reg.list_verified_series()
    assert rows == []


def test_validation_rejects_bad_coords() -> None:
    fixture_yaml = """
schema_version: 1
stations:
  KSEA:
    icao: KSEA
    name: Seattle-Tacoma International Airport
    nws_wfo: SEW
    cli_product: CLISEA
    cli_location_name: Seattle-Tacoma, WA
    coords:
      lat: 999
      lon: -122.3088
    feeds:
      nws_points: null
      nbm_bulletin: null
      mos_mav: null
      mos_mex: null
      metar_obs: null
      asos_history: null
      cli_observed_html: null
series:
  KXHIGHTSEA:
    settles_at: KSEA
    settles_what: daily_max_temp
    source: nws_cli
    verified: false
"""
    raw = yaml.safe_load(fixture_yaml)
    with pytest.raises(ValidationError):
        Doc.model_validate(raw)
