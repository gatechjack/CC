"""P3 coord-resolution tests for kalshi_weather_arb.

Exercises ``KalshiWeatherArbAgent._resolve_coords`` directly to confirm
the YAML-verified → legacy-fallback ordering and the audit fields
(``coord_source``, ``yaml_coords``, ``legacy_coords``) for each branch.

The strategy file under test is the post-P3 HEAD. The production YAML
(``config/weather_stations.yaml``) is loaded for verified-path tests; a
temp-dir fixture YAML drives the unverified-path test.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from trading_corp.agents.strategies.kalshi_weather_arb import (
    KalshiWeatherArbAgent,
)
from trading_corp.data.weather_stations import WeatherStationsRegistry


@pytest.fixture
def agent() -> KalshiWeatherArbAgent:
    """Construct an agent using the production YAML registry.

    Clients (forecast/open-meteo/METAR) are lazy — no I/O at __init__.
    """
    return KalshiWeatherArbAgent()


def test_verified_series_resolves_via_yaml(agent: KalshiWeatherArbAgent) -> None:
    info = agent._resolve_coords(
        ticker="KXHIGHTSEA-26MAY22-T72",
        rules="(no coords in rules)",
        city_code="TSEA",
    )
    assert info["coord_source"] == "yaml_verified"
    # KSEA coords per stations.KSEA.coords in config/weather_stations.yaml
    assert info["lat"] == 47.4502
    assert info["lon"] == -122.3088
    assert info["yaml_coords"] == [47.4502, -122.3088]
    # post-station-fix legacy ALSO points at KSEA; equal coords here is
    # the expected P4-ready signal (zero drift on this series).
    assert info["legacy_coords"] == [47.4502, -122.3088]


def test_corrected_nyc_uses_yaml_central_park(agent: KalshiWeatherArbAgent) -> None:
    info = agent._resolve_coords(
        ticker="KXLOWTNYC-26MAY22-T56",
        rules="(no coords)",
        city_code="TNYC",
    )
    assert info["coord_source"] == "yaml_verified"
    # KNYC Central Park, NOT KJFK (40.6413, -73.7781).
    assert info["yaml_coords"] == [40.7794, -73.9692]
    # Track-1 station fix also corrected the legacy dict to KNYC; equal
    # here means zero drift between YAML and legacy on the fixed pair.
    assert info["legacy_coords"] == [40.7794, -73.9692]


def test_corrected_chi_uses_yaml_midway(agent: KalshiWeatherArbAgent) -> None:
    info = agent._resolve_coords(
        ticker="KXHIGHCHI-26MAY22-T71",
        rules="(no coords)",
        city_code="CHI",
    )
    assert info["coord_source"] == "yaml_verified"
    assert info["yaml_coords"] == [41.7868, -87.7522]  # KMDW
    assert info["legacy_coords"] == [41.7868, -87.7522]


def test_corrected_hou_uses_yaml_hobby(agent: KalshiWeatherArbAgent) -> None:
    info = agent._resolve_coords(
        ticker="KXHIGHTHOU-26MAY22-B87.5",
        rules="(no coords)",
        city_code="THOU",
    )
    assert info["coord_source"] == "yaml_verified"
    assert info["yaml_coords"] == [29.6454, -95.2789]  # KHOU
    assert info["legacy_coords"] == [29.6454, -95.2789]


def test_disabled_series_returns_disabled_skip(
    agent: KalshiWeatherArbAgent,
) -> None:
    info = agent._resolve_coords(
        ticker="KXTEMPNYCH-26MAY2211-T70.99",
        rules="coordinates 40.7812,-73.9665",
        city_code="NYCH",
    )
    # Even though the rules string has coordinates we COULD parse, the
    # YAML disabled flag short-circuits before the legacy path runs.
    assert info["coord_source"] == "disabled_skip"
    assert info["lat"] is None and info["lon"] is None
    assert info["yaml_coords"] is None
    assert info["legacy_coords"] is None


def test_unknown_series_falls_to_legacy(agent: KalshiWeatherArbAgent) -> None:
    # Hypothetical new series not in the YAML; city_code piggybacks on
    # an existing legacy entry so legacy resolution succeeds.
    info = agent._resolve_coords(
        ticker="KXHIGHNEWCITY-26MAY22-T70",
        rules="(no coords)",
        city_code="TSEA",
    )
    assert info["coord_source"] == "legacy_fallback"
    # YAML had no entry → yaml_coords is None
    assert info["yaml_coords"] is None
    # Legacy fallback resolved via _CITY_COORDS_FALLBACK['TSEA'] = KSEA
    assert info["legacy_coords"] == [47.4502, -122.3088]
    assert info["lat"] == 47.4502 and info["lon"] == -122.3088


def test_unverified_yaml_entry_falls_to_legacy(
    agent: KalshiWeatherArbAgent, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical invariant: a YAML entry with verified=false MUST fall to
    legacy, never silently use the unverified YAML coords."""
    fixture_yaml = tmp_path / "weather_stations.yaml"
    fixture_yaml.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            stations:
              KFAKE:
                icao: KFAK
                name: Fake Airport
                nws_wfo: ZZZ
                cli_product: CLIFAK
                cli_location_name: Fake
                coords: {lat: 12.34, lon: -56.78}
                feeds: {}
            series:
              KXHIGHTSEA:
                settles_at: KFAKE
                settles_what: daily_max_temp
                source: nws_cli
                verified: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    fixture_reg = WeatherStationsRegistry.load(fixture_yaml)
    monkeypatch.setattr(agent, "_station_registry", fixture_reg)

    info = agent._resolve_coords(
        ticker="KXHIGHTSEA-26MAY22-T72",
        rules="(no coords)",
        city_code="TSEA",
    )
    # Unverified YAML must be ignored — fall to legacy KSEA.
    assert info["coord_source"] == "legacy_fallback"
    assert info["lat"] == 47.4502
    assert info["lon"] == -122.3088
    # yaml_coords is None because verified=False (we only emit yaml_coords
    # when we'd actually trust them).
    assert info["yaml_coords"] is None
    assert info["legacy_coords"] == [47.4502, -122.3088]


def test_no_coords_returns_none_source(
    agent: KalshiWeatherArbAgent, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When YAML has no entry AND legacy can't resolve, expect lat/lon=None
    and coord_source='none' so the caller can emit a no_coords skip."""
    # Use a fixture YAML with no series so YAML lookup misses
    fixture_yaml = tmp_path / "weather_stations.yaml"
    fixture_yaml.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            stations: {}
            series: {}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    fixture_reg = WeatherStationsRegistry.load(fixture_yaml)
    monkeypatch.setattr(agent, "_station_registry", fixture_reg)

    info = agent._resolve_coords(
        ticker="KXHIGHTOTALLYUNKNOWN-26MAY22-T70",
        rules="(no coords)",
        city_code="TOTALLYUNKNOWN",  # not in _CITY_COORDS_FALLBACK
    )
    assert info["coord_source"] == "none"
    assert info["lat"] is None and info["lon"] is None
    assert info["yaml_coords"] is None
    assert info["legacy_coords"] is None
