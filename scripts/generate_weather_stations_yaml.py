"""One-shot generator: reads planning/weather_station_xref_audit.json and
emits config/weather_stations.yaml with validated station + series data.

Idempotent: running again produces identical output unless the audit JSON
changes.

Usage:
    python scripts/generate_weather_stations_yaml.py

The output file is checked in; do not edit it by hand unless you also
update the audit JSON and re-run this script.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Station table: city_code → (icao, wfo, cli_product, cli_location_name,
#                              lat, lon, station_name)
# ---------------------------------------------------------------------------
# Multiple city codes can map to the same ICAO.

_STATION_DATA: dict[str, dict] = {
    "KSEA": {
        "icao": "KSEA",
        "name": "Seattle-Tacoma International Airport",
        "nws_wfo": "SEW",
        "cli_product": "CLISEA",
        "cli_location_name": "Seattle-Tacoma, WA",
        "coords": {"lat": 47.4502, "lon": -122.3088},
    },
    "KBOS": {
        "icao": "KBOS",
        "name": "Boston Logan International Airport",
        "nws_wfo": "BOX",
        "cli_product": "CLIBOS",
        "cli_location_name": "Boston (Logan Airport), MA",
        "coords": {"lat": 42.3656, "lon": -71.0096},
    },
    "KDCA": {
        "icao": "KDCA",
        "name": "Ronald Reagan Washington National Airport",
        "nws_wfo": "LWX",
        "cli_product": "CLIDCA",
        "cli_location_name": "Washington-National",
        "coords": {"lat": 38.8512, "lon": -77.0402},
    },
    "KATL": {
        "icao": "KATL",
        "name": "Hartsfield-Jackson Atlanta International Airport",
        "nws_wfo": "FFC",
        "cli_product": "CLIATL",
        "cli_location_name": "Atlanta, GA",
        "coords": {"lat": 33.6407, "lon": -84.4277},
    },
    "KDFW": {
        "icao": "KDFW",
        "name": "Dallas/Fort Worth International Airport",
        "nws_wfo": "FWD",
        "cli_product": "CLIDFW",
        "cli_location_name": "Dallas/Fort Worth, TX",
        "coords": {"lat": 32.8998, "lon": -97.0403},
    },
    "KPHL": {
        "icao": "KPHL",
        "name": "Philadelphia International Airport",
        "nws_wfo": "PHI",
        "cli_product": "CLIPHL",
        "cli_location_name": "Philadelphia, PA",
        "coords": {"lat": 39.8729, "lon": -75.2437},
    },
    "KOKC": {
        "icao": "KOKC",
        "name": "Will Rogers World Airport",
        "nws_wfo": "OUN",
        "cli_product": "CLIOKC",
        "cli_location_name": "Oklahoma City Will Rogers Airport",
        "coords": {"lat": 35.3931, "lon": -97.6007},
    },
    "KMIA": {
        "icao": "KMIA",
        "name": "Miami International Airport",
        "nws_wfo": "MFL",
        "cli_product": "CLIMIA",
        "cli_location_name": "Miami, FL",
        "coords": {"lat": 25.7959, "lon": -80.2870},
    },
    "KMDW": {
        "icao": "KMDW",
        "name": "Chicago Midway International Airport",
        "nws_wfo": "LOT",
        "cli_product": "CLIMDW",
        "cli_location_name": "Chicago - Midway, IL",
        "coords": {"lat": 41.7868, "lon": -87.7522},
    },
    "KAUS": {
        "icao": "KAUS",
        "name": "Austin-Bergstrom International Airport",
        "nws_wfo": "EWX",
        "cli_product": "CLIAUS",
        "cli_location_name": "Austin Bergstrom",
        "coords": {"lat": 30.1975, "lon": -97.6664},
    },
    "KMSP": {
        "icao": "KMSP",
        "name": "Minneapolis-Saint Paul International Airport",
        "nws_wfo": "MPX",
        "cli_product": "CLIMSP",
        "cli_location_name": "Minneapolis/St Paul, MN",
        "coords": {"lat": 44.8848, "lon": -93.2223},
    },
    "KSAT": {
        "icao": "KSAT",
        "name": "San Antonio International Airport",
        "nws_wfo": "EWX",
        "cli_product": "CLISAT",
        "cli_location_name": "San Antonio",
        "coords": {"lat": 29.5337, "lon": -98.4698},
    },
    "KSFO": {
        "icao": "KSFO",
        "name": "San Francisco International Airport",
        "nws_wfo": "MTR",
        "cli_product": "CLISFO",
        "cli_location_name": "San Francisco Airport",
        "coords": {"lat": 37.6213, "lon": -122.3790},
    },
    "KLAX": {
        "icao": "KLAX",
        "name": "Los Angeles International Airport",
        "nws_wfo": "LOX",
        "cli_product": "CLILAX",
        "cli_location_name": "Los Angeles Airport, CA",
        "coords": {"lat": 33.9416, "lon": -118.4085},
    },
    "KDEN": {
        "icao": "KDEN",
        "name": "Denver International Airport",
        "nws_wfo": "BOU",
        "cli_product": "CLIDEN",
        "cli_location_name": "Denver, CO",
        "coords": {"lat": 39.8561, "lon": -104.6737},
    },
    "KHOU": {
        "icao": "KHOU",
        "name": "Houston William P. Hobby Airport",
        "nws_wfo": "HGX",
        "cli_product": "CLIHOU",
        "cli_location_name": "Houston-Hobby, TX",
        "coords": {"lat": 29.6454, "lon": -95.2789},
    },
    "KPHX": {
        "icao": "KPHX",
        "name": "Phoenix Sky Harbor International Airport",
        "nws_wfo": "PSR",
        "cli_product": "CLIPHX",
        "cli_location_name": "Phoenix, AZ",
        "coords": {"lat": 33.4373, "lon": -112.0078},
    },
    "KMSY": {
        "icao": "KMSY",
        "name": "Louis Armstrong New Orleans International Airport",
        "nws_wfo": "LIX",
        "cli_product": "CLIMSY",
        "cli_location_name": "New Orleans, LA",
        "coords": {"lat": 29.9934, "lon": -90.2580},
    },
    "KNYC": {
        "icao": "KNYC",
        "name": "Central Park, New York",
        "nws_wfo": "OKX",
        "cli_product": "CLINYC",
        "cli_location_name": "Central Park NY",
        "coords": {"lat": 40.7794, "lon": -73.9692},
    },
}

# City-code → ICAO mapping (multiple codes can map to same ICAO)
_CITY_CODE_TO_ICAO: dict[str, str] = {
    "TSEA": "KSEA",
    "TBOS": "KBOS",
    "TDC": "KDCA",
    "TATL": "KATL",
    "TDAL": "KDFW",
    "PHIL": "KPHL",
    "TPHIL": "KPHL",
    "TOKC": "KOKC",
    "MIA": "KMIA",
    "TMIA": "KMIA",
    "CHI": "KMDW",
    "TCHI": "KMDW",
    "AUS": "KAUS",
    "TAUS": "KAUS",
    "TMIN": "KMSP",
    "TSATX": "KSAT",
    "TSFO": "KSFO",
    "LAX": "KLAX",
    "TLAX": "KLAX",
    "DEN": "KDEN",
    "TDEN": "KDEN",
    "THOU": "KHOU",
    "TPHX": "KPHX",
    "TNOLA": "KMSY",
    "NY": "KNYC",
    "TNYC": "KNYC",
}

# Correction notes for the 6 mismatched series
_CORRECTION_NOTES: dict[str, str] = {
    "KXLOWTNYC": (
        "Corrected 2026-05-22: was KJFK (~12 mi off, Central Park is +3°F warmer for highs)"
    ),
    "KXHIGHNY": (
        "Corrected 2026-05-22: was KJFK (~12 mi off, Central Park is +3°F warmer for highs)"
    ),
    "KXHIGHCHI": (
        "Corrected 2026-05-22: was KORD (~17 mi off)"
    ),
    "KXLOWTCHI": (
        "Corrected 2026-05-22: was KORD (~17 mi off)"
    ),
    "KXHIGHTHOU": (
        "Corrected 2026-05-22: was KIAH (~24 mi off)"
    ),
    "KXLOWTHOU": (
        "Corrected 2026-05-22: was KIAH (~24 mi off)"
    ),
}

_SERIES_RE = re.compile(r"^KX(HIGH|LOW|TEMP)([A-Z]+?)$")


def _series_prefix_to_city_code(series: str) -> tuple[str, str] | None:
    """Extract (measure_type, city_code) from a series prefix like KXHIGHTSEA."""
    m = _SERIES_RE.match(series)
    if not m:
        return None
    return m.group(1), m.group(2)


def _settles_what(measure_type: str) -> str:
    if measure_type == "HIGH":
        return "daily_max_temp"
    if measure_type == "LOW":
        return "daily_min_temp"
    return "hourly_temp_at_hour"


def _build_feeds(icao: str, lat: float, lon: float, wfo: str) -> dict:
    icao_3 = icao[1:]  # strip leading K
    wfo_lower = wfo.lower()
    return {
        "nws_points": f"https://api.weather.gov/points/{lat},{lon}",
        "nbm_bulletin": (
            f"https://mesonet.agron.iastate.edu/mos/csv.php?station={icao}&model=nbe"
        ),
        "mos_mav": (
            f"https://mesonet.agron.iastate.edu/mos/csv.php?station={icao}&model=gfs"
        ),
        "mos_mex": (
            f"https://mesonet.agron.iastate.edu/mos/csv.php?station={icao}&model=eta"
        ),
        "metar_obs": (
            f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json&hours=24"
        ),
        "asos_history": (
            f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station={icao_3}"
        ),
        "cli_observed_html": (
            f"https://www.weather.gov/wrh/Climate?wfo={wfo_lower}"
        ),
    }


def _build_stations_block() -> dict:
    """Build the stations dict in canonical order."""
    stations: dict[str, dict] = {}
    # Emit stations in the same order as _STATION_DATA
    for icao, info in _STATION_DATA.items():
        lat = info["coords"]["lat"]
        lon = info["coords"]["lon"]
        wfo = info["nws_wfo"]
        stations[icao] = {
            "icao": icao,
            "name": info["name"],
            "nws_wfo": wfo,
            "cli_product": info["cli_product"],
            "cli_location_name": info["cli_location_name"],
            "coords": {"lat": lat, "lon": lon},
            "feeds": _build_feeds(icao, lat, lon, wfo),
        }
    return stations


def _build_series_block(audit_entries: list[dict]) -> dict:
    """Build the series dict, preserving audit order."""
    series: dict[str, dict] = {}

    for entry in audit_entries:
        sname = entry["series"]

        # Special-case: KXTEMPNYCH
        if sname == "KXTEMPNYCH":
            entry_dict: dict = {
                "settles_at": None,
                "settles_what": "hourly_temp_at_hour",
                "source": "accuweather",
                "rules_excerpt": entry.get("rules_primary_excerpt", "").rstrip(),
                "verified": False,
                "disabled": True,
                "disabled_reason": "no AccuWeather feed; refuse to model",
                "live_trading_blocked": True,
                "cited_coords": {
                    "lat": 40.7812,
                    "lon": -73.9665,
                },
            }
            series[sname] = entry_dict
            continue

        parsed = _series_prefix_to_city_code(sname)
        if parsed is None:
            raise ValueError(f"Cannot parse series prefix: {sname!r}")
        measure_type, city_code = parsed

        icao = _CITY_CODE_TO_ICAO.get(city_code)
        if icao is None:
            raise ValueError(
                f"No ICAO mapping for city_code={city_code!r} (series={sname!r})"
            )

        entry_dict = {
            "settles_at": icao,
            "settles_what": _settles_what(measure_type),
            "source": "nws_cli",
            "rules_excerpt": entry.get("rules_primary_excerpt", "").rstrip(),
            "verified": False,
        }

        correction = _CORRECTION_NOTES.get(sname)
        if correction:
            entry_dict["correction_note"] = correction

        series[sname] = entry_dict

    return series


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    audit_path = repo_root / "planning" / "weather_station_xref_audit.json"
    out_path = repo_root / "config" / "weather_stations.yaml"

    with audit_path.open("r", encoding="utf-8") as f:
        audit_entries: list[dict] = json.load(f)

    stations = _build_stations_block()
    series = _build_series_block(audit_entries)

    doc = {
        "schema_version": 1,
        "stations": stations,
        "series": series,
    }

    yaml_body = yaml.safe_dump(
        doc,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )

    header = (
        "# config/weather_stations.yaml\n"
        "# Generated 2026-05-22 by scripts/generate_weather_stations_yaml.py from\n"
        "# planning/weather_station_xref_audit.json. DO NOT EDIT BY HAND unless\n"
        "# you also update the audit JSON; re-run the generator after edits.\n"
        "#\n"
        "# All series initialized with verified: false. Phase 2 of the cross-ref\n"
        "# rollout flips the verified flag after a human reviews each entry.\n"
        "# See planning/weather_station_xref_design.md.\n"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + yaml_body, encoding="utf-8")
    print(f"Wrote {out_path} ({len(series)} series, {len(stations)} stations)")


if __name__ == "__main__":
    main()
