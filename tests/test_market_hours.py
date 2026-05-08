"""Smoke tests for the MarketHoursCalendar wrapper.

Light coverage — most behavior is delegated to pandas_market_calendars,
which has its own test suite. These tests verify the wrapper's contract:
the right shape comes back for known dates, and the fallback path
works when the lib isn't available.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trading_corp.utils.market_hours import (
    ET, MarketHoursCalendar, _close_cached, _fallback_close,
)


def test_regular_weekday_close_is_4pm_et():
    """A known regular trading day (e.g. 2024-06-03 Monday) closes
    at 16:00 ET on the NYSE schedule."""
    cal = MarketHoursCalendar()
    close = cal.close_time_et(date(2024, 6, 3))
    assert close is not None
    assert close.hour == 16
    assert close.minute == 0
    assert close.tzinfo is not None  # tz-aware


def test_known_half_day_2024_07_03_closes_at_1pm_et():
    """July 3, 2024 was a half-day before Independence Day —
    NYSE closed at 13:00 ET. Pin this so a future calendar lib
    update that breaks half-day handling fails this test."""
    cal = MarketHoursCalendar()
    close = cal.close_time_et(date(2024, 7, 3))
    assert close is not None
    assert close.hour == 13
    assert close.minute == 0


def test_weekend_returns_none():
    """Saturday → no session, close_time_et returns None."""
    cal = MarketHoursCalendar()
    sat = date(2024, 6, 8)
    assert cal.close_time_et(sat) is None


def test_known_holiday_returns_none():
    """Christmas Day (2024-12-25) → market closed → None."""
    cal = MarketHoursCalendar()
    assert cal.close_time_et(date(2024, 12, 25)) is None


def test_fallback_close_weekday():
    """Fallback path: any weekday returns 16:00 ET regardless of
    half-day / holiday status. Used when pandas_market_calendars is
    unavailable."""
    fb = _fallback_close(date(2024, 7, 3))   # would be a half-day under pmcal
    assert fb is not None
    assert fb.hour == 16   # fallback ignores half-day → degraded behavior


def test_fallback_close_weekend():
    fb = _fallback_close(date(2024, 6, 8))
    assert fb is None


def test_close_cached_with_none_calendar_falls_back():
    """When calendar=None (lib unavailable), _close_cached uses the
    fallback path and notes the fallback flag."""
    fired = {"n": 0}
    def note():
        fired["n"] += 1
    out = _close_cached(None, date(2024, 6, 3), note)
    assert out is not None
    assert out.hour == 16
    assert fired["n"] == 1
