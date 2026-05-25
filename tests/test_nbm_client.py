"""Tests for trading_corp.data.nbm_client — parser-only (no network)."""
from __future__ import annotations

from datetime import datetime, timezone

from trading_corp.data.nbm_client import (
    NBMObservation,
    _parse_block_dates,
    cycle_url,
    latest_cycle_dt,
    parse_bulletin,
)


# Verified live 2026-05-25T13:00 UTC from KOKX block (NBPOKX bulletin via IEM).
_KOKX_BULLETIN = """000
FEUS18 KWNO 251300
NBPUSA
KOKX    NBM V5.0 NBP GUIDANCE    5/25/2026  1300 UTC
TUE 26| WED 27| THU 28| FRI 29| SAT 30| SUN 31| MON 01| TUE 02| WED 03|
UTC    12| 00  12| 00  12| 00  12| 00  12| 00  12| 00  12| 00  12| 00  12| 00
FHR    23| 35  47| 59  71| 83  95|107 119|131 143|155 167|179 191|203 215|227
TXNMN  60| 82  62| 83  65| 83  58| 77  56| 75  55| 76  57| 79  59| 80  60| 80
TXNSD   2|  3   3|  5   2|  4   4|  6   5|  7   6|  7   5|  6   5|  6   5|  6
TXNP1  57| 79  58| 76  62| 78  52| 70  50| 66  48| 68  52| 72  52| 72  53| 72
TXNP2  59| 80  60| 79  64| 81  56| 74  52| 69  51| 71  53| 75  55| 76  57| 76
TXNP5  60| 81  62| 83  65| 83  59| 77  55| 75  54| 75  57| 80  58| 80  60| 80
TXNP7  62| 84  64| 88  67| 85  61| 80  60| 80  60| 79  59| 84  62| 84  64| 84
TXNP9  63| 86  66| 90  68| 88  63| 85  63| 85  64| 86  64| 87  64| 88  67| 88
WSPP1   1|  3   2|  3   2|  2   1|  1   2|  2   1|  1   2|  1   1|  2   0|  2
"""


def test_parse_block_dates_basic() -> None:
    cycle = datetime(2026, 5, 25, 13, 0, 0, tzinfo=timezone.utc)
    line = "TUE 26| WED 27| THU 28| FRI 29| SAT 30| SUN 31| MON 01| TUE 02| WED 03|"
    dates = _parse_block_dates(line, cycle)
    assert len(dates) == 9
    assert dates[0] == datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc)
    assert dates[4] == datetime(2026, 5, 30, 0, 0, 0, tzinfo=timezone.utc)
    # Month roll: SUN 31 → MON 01 (next month = June)
    assert dates[5] == datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    assert dates[6] == datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert dates[8] == datetime(2026, 6, 3, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_block_dates_year_rollover() -> None:
    """Cycle 12/30 → first column 12/31, then JAN 01 should roll to next year."""
    cycle = datetime(2026, 12, 30, 13, 0, 0, tzinfo=timezone.utc)
    line = "WED 31| THU 01| FRI 02|"
    dates = _parse_block_dates(line, cycle)
    assert dates[0] == datetime(2026, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
    assert dates[1] == datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert dates[2] == datetime(2027, 1, 2, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_bulletin_kokx() -> None:
    cycle = datetime(2026, 5, 25, 13, 0, 0, tzinfo=timezone.utc)
    result = parse_bulletin(_KOKX_BULLETIN, cycle, target_icaos={"KOKX"})
    assert "KOKX" in result
    obs = result["KOKX"]
    # 9 day columns × 2 kinds = 18 observations
    assert len(obs) == 18

    # First column: TUE 26 → 2026-05-26
    # Column-0 daily_min: TXNMN[0][0]=60, TXNSD[0][0]=2, TXNP1[0][0]=57,
    # TXNP2[0][0]=59, TXNP5[0][0]=60, TXNP7[0][0]=62, TXNP9[0][0]=63
    col0_min = [o for o in obs if o.valid_iso.startswith("2026-05-26") and o.kind == "daily_min"][0]
    assert col0_min.station_id == "KOKX"
    assert col0_min.cycle_iso == "2026-05-25T13:00:00+00:00"
    assert col0_min.temp_mean_f == 60.0
    assert col0_min.temp_sigma_f == 2.0
    assert col0_min.temp_p10_f == 57.0
    assert col0_min.temp_p20_f == 59.0
    assert col0_min.temp_p50_f == 60.0
    assert col0_min.temp_p70_f == 62.0
    assert col0_min.temp_p90_f == 63.0
    # horizon: 5/26 00z − 5/25 13z = 11 hours
    assert col0_min.horizon_hours == 11.0

    # Column-0 daily_max: TXNMN[0][1]=82, TXNSD[0][1]=3, TXNP1[0][1]=79,
    # TXNP2[0][1]=80, TXNP5[0][1]=81, TXNP7[0][1]=84, TXNP9[0][1]=86
    col0_max = [o for o in obs if o.valid_iso.startswith("2026-05-26") and o.kind == "daily_max"][0]
    assert col0_max.temp_mean_f == 82.0
    assert col0_max.temp_sigma_f == 3.0
    assert col0_max.temp_p10_f == 79.0
    assert col0_max.temp_p50_f == 81.0
    assert col0_max.temp_p90_f == 86.0

    # Spot-check column-5 (SUN 31 = 2026-05-31): TXNSD MaxT = 7
    col5_max = [o for o in obs if o.valid_iso.startswith("2026-05-31") and o.kind == "daily_max"][0]
    assert col5_max.temp_sigma_f == 7.0
    assert col5_max.temp_mean_f == 76.0
    # Spot-check column-6 (MON 01 = 2026-06-01 — month rollover): TXNMN MaxT = 79
    col6_max = [o for o in obs if o.valid_iso.startswith("2026-06-01") and o.kind == "daily_max"][0]
    assert col6_max.temp_mean_f == 79.0


def test_parse_bulletin_skips_non_target_icao() -> None:
    cycle = datetime(2026, 5, 25, 13, 0, 0, tzinfo=timezone.utc)
    result = parse_bulletin(_KOKX_BULLETIN, cycle, target_icaos={"KMDW", "KSEA"})
    assert result == {}


def test_parse_bulletin_missing_target_absent_from_result() -> None:
    """Caller is responsible for detecting missing ICAOs — parser just returns
    the present subset."""
    cycle = datetime(2026, 5, 25, 13, 0, 0, tzinfo=timezone.utc)
    result = parse_bulletin(_KOKX_BULLETIN, cycle, target_icaos={"KOKX", "KMDW"})
    assert "KOKX" in result
    assert "KMDW" not in result  # KMDW block is not in this fixture


def test_cycle_url_format() -> None:
    cycle = datetime(2026, 5, 25, 13, 0, 0, tzinfo=timezone.utc)
    url = cycle_url(cycle)
    assert url == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
        "blend.20260525/13/text/blend_nbptx.t13z"
    )


def test_latest_cycle_dt_after_publish_lag() -> None:
    # 14:35 UTC: 13z cycle is ~95 min old → published → return 13z
    now = datetime(2026, 5, 25, 14, 35, 0, tzinfo=timezone.utc)
    assert latest_cycle_dt(now) == datetime(2026, 5, 25, 13, 0, 0, tzinfo=timezone.utc)
    # 13:20 UTC: 13z is only 20 min old → not yet published (lag 30) → return 07z
    now = datetime(2026, 5, 25, 13, 20, 0, tzinfo=timezone.utc)
    assert latest_cycle_dt(now) == datetime(2026, 5, 25, 7, 0, 0, tzinfo=timezone.utc)
    # 00:30 UTC: previous cycle was yesterday's 19z
    now = datetime(2026, 5, 25, 0, 30, 0, tzinfo=timezone.utc)
    assert latest_cycle_dt(now) == datetime(2026, 5, 24, 19, 0, 0, tzinfo=timezone.utc)
