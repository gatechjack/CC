"""Tests for trading_corp.data.residual_logic — pure helpers."""
from __future__ import annotations

from datetime import date

from trading_corp.data.residual_logic import (
    CORRECTED_SERIES_PREFIXES,
    STATION_FIX_CUTOFF_ISO,
    assign_logic_era,
    derive_season,
)


# --- derive_season -----------------------------------------------------

def test_derive_season_4bucket_meteorological() -> None:
    """Meteorological convention per Board Q3."""
    assert derive_season(date(2026, 1, 15)) == "winter"
    assert derive_season(date(2026, 2, 28)) == "winter"
    assert derive_season(date(2026, 3, 1)) == "spring"
    assert derive_season(date(2026, 5, 31)) == "spring"
    assert derive_season(date(2026, 6, 1)) == "summer"
    assert derive_season(date(2026, 8, 31)) == "summer"
    assert derive_season(date(2026, 9, 1)) == "fall"
    assert derive_season(date(2026, 11, 30)) == "fall"
    assert derive_season(date(2026, 12, 1)) == "winter"


# --- assign_logic_era --------------------------------------------------

def test_logic_era_nbm_native() -> None:
    """NBM source without audit context → native_post_fix."""
    result = assign_logic_era(
        forecast_source="nbm_p50",
        audit_ts_iso=None,
        audit_coord_source=None,
        series_prefix=None,
    )
    assert result == "native_post_fix"


def test_logic_era_audit_post_cutoff() -> None:
    """Audit row with ts >= cutoff → post_station_fix."""
    result = assign_logic_era(
        forecast_source="nws_blend",
        audit_ts_iso="2026-05-22T16:25:00",
        audit_coord_source="yaml_verified",
        series_prefix="KXHIGHTSEA",
    )
    assert result == "post_station_fix"


def test_logic_era_audit_just_after_cutoff() -> None:
    """1 microsecond after cutoff → post_station_fix."""
    result = assign_logic_era(
        forecast_source="nws_blend",
        audit_ts_iso="2026-05-22T16:25:00.000001",
        audit_coord_source="yaml_verified",
        series_prefix="KXHIGHTSEA",
    )
    assert result == "post_station_fix"


def test_logic_era_audit_pre_cutoff() -> None:
    """Audit row with ts < cutoff → pre_station_fix.

    Applies to ALL stations, not just corrected ones (the strategy may
    have used legacy coords elsewhere too — most conservative
    treatment).
    """
    result = assign_logic_era(
        forecast_source="nws_blend",
        audit_ts_iso="2026-05-22T16:24:59",
        audit_coord_source="yaml_verified",
        series_prefix="KXHIGHTSEA",
    )
    assert result == "pre_station_fix"


def test_logic_era_pre_cutoff_nyc_with_legacy_coords() -> None:
    """NYC pre-cutoff with legacy_fallback coord_source → pre_station_fix
    (this is the case the contamination guard is built to catch).
    """
    for prefix in ("KXHIGHNY", "KXLOWTNYC"):
        result = assign_logic_era(
            forecast_source="nws_blend",
            audit_ts_iso="2026-05-20T10:00:00",
            audit_coord_source="legacy_fallback",
            series_prefix=prefix,
        )
        assert result == "pre_station_fix"


def test_logic_era_safety_rule_corrected_series_unverified_post_cutoff() -> None:
    """Safety rule 4: a corrected-series row POST-cutoff but with
    coord_source != 'yaml_verified' is forced to pre_station_fix."""
    for prefix in CORRECTED_SERIES_PREFIXES:
        result = assign_logic_era(
            forecast_source="nws_blend",
            audit_ts_iso="2026-05-23T10:00:00",  # post-cutoff
            audit_coord_source="legacy_fallback",  # but unverified
            series_prefix=prefix,
        )
        assert result == "pre_station_fix", (
            f"safety rule failed for {prefix}: should force pre_station_fix "
            "when post-cutoff but coord_source != yaml_verified"
        )


def test_logic_era_safety_rule_non_corrected_series_unverified_post_cutoff() -> None:
    """Safety rule applies only to corrected-series prefixes.
    A non-corrected series with unverified coords post-cutoff is still
    post_station_fix (the safety rule is targeted, not blanket)."""
    result = assign_logic_era(
        forecast_source="nws_blend",
        audit_ts_iso="2026-05-23T10:00:00",
        audit_coord_source="legacy_fallback",
        series_prefix="KXHIGHTSEA",  # Seattle was never coord-corrected
    )
    assert result == "post_station_fix"


def test_logic_era_audit_with_no_coord_source_corrected_series() -> None:
    """A corrected-series row with audit_coord_source=None post-cutoff
    still triggers the safety rule (None != 'yaml_verified')."""
    result = assign_logic_era(
        forecast_source="nws_blend",
        audit_ts_iso="2026-05-23T10:00:00",
        audit_coord_source=None,
        series_prefix="KXHIGHNY",
    )
    assert result == "pre_station_fix"


def test_logic_era_nbm_with_audit_context_treated_as_audit() -> None:
    """Edge case: if NBM source comes WITH an audit ts attached (shouldn't
    happen in practice but worth pinning), it should be treated as audit-
    derived (post or pre by ts), NOT native."""
    result = assign_logic_era(
        forecast_source="nbm_p50",
        audit_ts_iso="2026-05-20T10:00:00",
        audit_coord_source="yaml_verified",
        series_prefix="KXHIGHTSEA",
    )
    # ts < cutoff → pre_station_fix
    assert result == "pre_station_fix"


def test_cutoff_string_compare_is_safe_iso() -> None:
    """Sanity check: the ISO 'T' string compare matches lexicographically
    in the right direction for the chosen cutoff format."""
    assert "2026-05-22T16:24:59" < STATION_FIX_CUTOFF_ISO
    assert "2026-05-22T16:25:00" >= STATION_FIX_CUTOFF_ISO
    assert "2026-05-22T16:25:01" > STATION_FIX_CUTOFF_ISO
    assert "2026-05-23T00:00:00" > STATION_FIX_CUTOFF_ISO
