"""Byte-equivalence test for the inlined derive_season in _weather_math.py
vs the original in trading_corp.data.residual_logic.

Why this test exists: the bias offsets (BIAS_OFFSETS_V1) were FIT using
trading_corp.data.residual_logic.derive_season's season boundaries. On
2026-05-26, derive_season was inlined into _weather_math.py to remove a
cross-module dependency that crash-looped prod (residual_logic.py was
absent on prod; see feedback_deploy_import_graph_audit.md). If the
inlined copy drifts even one boundary day from the original, an
edge-date forecast (Feb 28, May 31, Aug 31, Nov 30) could route to the
wrong cell. This test asserts the inlined copy returns the IDENTICAL
season for every (month, day) of a non-leap year.

Test passes both implementations every day-of-year and asserts equality.
If anyone ever changes either function's logic without updating the
other, this test fails noisily.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading_corp.agents.strategies._weather_math import (
    derive_season as inlined_derive_season,
)
from trading_corp.data.residual_logic import (
    derive_season as original_derive_season,
)


def test_inlined_matches_original_every_day_of_year() -> None:
    """For every calendar day Jan 1 - Dec 31 (non-leap), assert the two
    implementations return the exact same Season literal."""
    cur = date(2025, 1, 1)  # 2025 is non-leap
    end = date(2025, 12, 31)
    mismatches: list[tuple[date, str, str]] = []
    while cur <= end:
        a = inlined_derive_season(cur)
        b = original_derive_season(cur)
        if a != b:
            mismatches.append((cur, a, b))
        cur += timedelta(days=1)
    assert not mismatches, (
        f"derive_season inlined vs original disagree on {len(mismatches)} days: "
        + ", ".join(f"{d.isoformat()}={a}!={b}" for d, a, b in mismatches[:5])
    )


def test_inlined_matches_original_leap_day() -> None:
    """Feb 29 in a leap year (2024) must also match — winter."""
    leap = date(2024, 2, 29)
    assert inlined_derive_season(leap) == original_derive_season(leap) == "winter"


@pytest.mark.parametrize("d,expected", [
    # All four season boundaries the offset table cares about.
    (date(2025, 2, 28), "winter"),  # last winter day
    (date(2025, 3, 1),  "spring"),  # first spring day
    (date(2025, 5, 31), "spring"),  # last spring day
    (date(2025, 6, 1),  "summer"),  # first summer day
    (date(2025, 8, 31), "summer"),  # last summer day
    (date(2025, 9, 1),  "fall"),    # first fall day
    (date(2025, 11, 30), "fall"),   # last fall day
    (date(2025, 12, 1), "winter"),  # first winter day (back-edge)
    (date(2025, 1, 1),  "winter"),  # New Year's Day = winter
    (date(2025, 12, 31), "winter"), # year-end = winter
])
def test_boundary_dates(d: date, expected: str) -> None:
    """Each of the 8 season boundary edges must return `expected` under
    BOTH implementations. If an offset cell is keyed on (KSEA, 'spring')
    and the forecast is for May 31 (last spring day), wrong-season routing
    would skip the offset; this test ensures it doesn't happen."""
    assert inlined_derive_season(d) == expected
    assert original_derive_season(d) == expected
