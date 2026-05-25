"""Pure helpers for computing weather_forecast_residuals row metadata.

Extracted from ``scripts/ingest_iem_cli_residuals.py`` so the
contamination-guard logic (logic_era assignment + season derivation)
can be unit-tested without DB or HTTP fixtures.

See plans/tier1-data-foundation-kalshi-weather.md §C2 for the design
rationale: the logic_era field is the structural guard that keeps
pre-2026-05-22-station-fix forecasts (which used wrong-station coords
for NYC/CHI/HOU) out of any per-station calibration baseline.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

# 2026-05-22T16:25:00 UTC — commit f5a5fd5 shipped the 6-station xref
# corrections (KJFK→KNYC, KORD→KMDW, KIAH→KHOU, +3 others). Audit rows
# with ts < this used the WRONG coords for the corrected cities and
# MUST be excluded from per-station calibration.
STATION_FIX_CUTOFF_ISO = "2026-05-22T16:25:00"
STATION_FIX_CUTOFF_DT = datetime.fromisoformat(STATION_FIX_CUTOFF_ISO)

# The 6 series-prefix groups that got coord corrections on 2026-05-22.
# Audit rows for these series MUST have coord_source='yaml_verified'
# to be considered post-fix; an unverified coord_source for these
# series forces logic_era='pre_station_fix' regardless of ts.
CORRECTED_SERIES_PREFIXES: tuple[str, ...] = (
    "KXHIGHNY", "KXLOWTNYC",       # NYC: was KJFK → now KNYC
    "KXHIGHCHI", "KXLOWTCHI",      # CHI: was KORD → now KMDW
    "KXHIGHTHOU", "KXLOWTHOU",     # HOU: was KIAH → now KHOU
)

LogicEra = Literal["pre_station_fix", "post_station_fix", "native_post_fix"]
Season = Literal["winter", "spring", "summer", "fall"]


def derive_season(d: date) -> Season:
    """Meteorological 4-bucket season derivation per Board direction Q3.

    Convention: Dec/Jan/Feb=winter, Mar/Apr/May=spring,
    Jun/Jul/Aug=summer, Sep/Oct/Nov=fall.
    """
    m = d.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


def assign_logic_era(
    *,
    forecast_source: str,
    audit_ts_iso: str | None,
    audit_coord_source: str | None,
    series_prefix: str | None,
) -> LogicEra:
    """Determine logic_era for one residual row.

    Args:
        forecast_source: 'nbm_p50' / 'nbm_mean' / 'nws_blend' / 'hrrr' / etc.
        audit_ts_iso: For audit-derived rows, the audit_event.ts ISO string.
            None for NBM-native rows that didn't come from audit.
        audit_coord_source: For audit-derived rows, the payload's
            `coord_source` field (e.g. 'yaml_verified' / 'legacy_fallback'
            / 'disabled_skip' / 'none'). None for NBM-native.
        series_prefix: Ticker series prefix, e.g. 'KXHIGHNY'. Used to
            apply the corrected-series safety rule.

    Returns:
        'native_post_fix' | 'post_station_fix' | 'pre_station_fix'

    Decision rules (per Tier 1 plan §C2 "logic_era assignment"):
      1. NBM-native (source startswith 'nbm_' AND no audit context) →
         'native_post_fix'. Registry-direct ingestion by construction
         uses correct coords.
      2. Audit-derived, ts >= cutoff → 'post_station_fix'.
      3. Audit-derived, ts < cutoff → 'pre_station_fix'.
      4. Safety override: audit-derived row for a corrected-series
         prefix with coord_source != 'yaml_verified' → 'pre_station_fix'
         REGARDLESS of ts. Catches edge cases where a post-cutoff row
         may still have evaluated through a non-yaml path.
    """
    if forecast_source.startswith("nbm_") and audit_ts_iso is None:
        return "native_post_fix"

    if audit_ts_iso is None:
        # Audit-derived sources MUST carry the audit ts. Treat missing
        # as pre-fix (most conservative — won't contaminate the
        # calibration baseline).
        return "pre_station_fix"

    # Safety rule 4: corrected-series row with non-yaml-verified coord
    # source is pre-fix regardless of ts.
    if (
        series_prefix in CORRECTED_SERIES_PREFIXES
        and audit_coord_source != "yaml_verified"
    ):
        return "pre_station_fix"

    # Compare against cutoff (string compare works for ISO-8601 'T' format).
    if audit_ts_iso < STATION_FIX_CUTOFF_ISO:
        return "pre_station_fix"
    return "post_station_fix"
