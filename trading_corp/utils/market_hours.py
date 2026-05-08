"""NYSE market-hours calendar wrapper.

Built for the 0-DTE Terminal-DTE Override deadline gates (BACKLOG.md
2026-05-01, P0). The hardcoded 15:00 / 15:30 ET thresholds in the
prior implementation broke on:
  - Half-day closes (e.g. day after Thanksgiving, Christmas Eve →
    1:00 PM ET close → deadline must shift to 12:30 PM ET)
  - Friday-holiday rotations (when Friday is closed, the 0-DTE
    deadline shifts to Thursday 3:30 PM ET)

This module wraps `pandas_market_calendars` (NYSE schedule) and
exposes:
  - `MarketHoursCalendar.close_time_et(date_)` → datetime in ET, or
     None on closed days
  - `MarketHoursCalendar.is_open_at(when)` → bool

The calendar is loaded lazily and cached in-process (yearly
publication-fixed; no need to hot-reload).

**Graceful fallback.** If `pandas_market_calendars` import fails or
the lib raises, we fall back to "every weekday closes at 16:00 ET,
weekends are closed" — same shape as the legacy hardcoded helper.
This keeps the production critical-path safe even if the dep can't
load (network issue, install hiccup); the half-day awareness simply
degrades to "no half-day awareness" rather than blowing up the scan.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

log = logging.getLogger(__name__)

# Standard NYSE close in ET wall-clock — used by the fallback path.
_DEFAULT_CLOSE_HOUR_ET = 16
_DEFAULT_CLOSE_MIN_ET = 0


class MarketHoursCalendar:
    """Lazy, in-process-cached view over the NYSE trading schedule.

    Construct one instance and reuse — `close_time_et` is memoized
    per-date so repeated lookups within a scan stay cheap.
    """

    def __init__(self) -> None:
        self._cal = None
        self._loaded = False
        self._fallback_logged = False

    def close_time_et(self, when: date | datetime) -> datetime | None:
        """Return the market-close datetime in ET for the date of `when`,
        or None if the market is closed that day.

        On lib failure: falls back to "16:00 ET on weekdays, None on
        weekends" — half-day awareness is lost but core logic still
        functions.
        """
        d = when.date() if isinstance(when, datetime) else when
        return self._close_time_for_date(d)

    def is_open_at(self, when: datetime) -> bool:
        """Return True if `when` falls inside an open NYSE session."""
        d = when.date() if isinstance(when, datetime) else when
        close = self._close_time_for_date(d)
        if close is None:
            return False
        when_et = when.astimezone(ET) if when.tzinfo else when.replace(tzinfo=ET)
        # Open is 9:30 ET on every session day per NYSE schedule
        # (pmcal would give us this too — for the 0-DTE deadline gates
        # we only really need close_time_et so we approximate "open" by
        # comparing wall-clock against 9:30 ET).
        open_t = when_et.replace(hour=9, minute=30, second=0, microsecond=0)
        return open_t <= when_et < close

    # ── internal ──────────────────────────────────────────────────

    def _close_time_for_date(self, d: date) -> datetime | None:
        return _close_cached(self._get_calendar(), d, self._note_fallback)

    def _get_calendar(self):
        if self._loaded:
            return self._cal
        self._loaded = True
        try:
            import pandas_market_calendars as pmc  # local import: keep cold-start cheap
            self._cal = pmc.get_calendar("NYSE")
            log.info("MarketHoursCalendar: pandas_market_calendars loaded (NYSE)")
        except Exception as e:
            log.warning(
                "MarketHoursCalendar: pandas_market_calendars unavailable "
                "(%s) — falling back to weekday-16:00-ET assumption", e,
            )
            self._cal = None
        return self._cal

    def _note_fallback(self) -> None:
        if not self._fallback_logged:
            log.warning(
                "MarketHoursCalendar: at least one date resolved via fallback "
                "(no half-day / holiday awareness). Subsequent fallback hits "
                "won't be logged to avoid spam."
            )
            self._fallback_logged = True


@lru_cache(maxsize=2048)
def _close_cached(cal, d: date, on_fallback) -> datetime | None:
    """Cache one entry per (calendar identity, date). When `cal` is None
    we go through the fallback path. The cache is keyed on `cal` so a
    fresh instance after a failed first-load can re-attempt — though in
    practice one instance is shared per process."""
    if cal is None:
        on_fallback()
        return _fallback_close(d)
    try:
        # pandas_market_calendars.schedule() returns a DataFrame with
        # 'market_open' / 'market_close' columns indexed by date.
        sched = cal.schedule(start_date=d.isoformat(), end_date=d.isoformat())
        if sched is None or len(sched) == 0:
            return None  # closed
        close_ts = sched["market_close"].iloc[0]
        # close_ts is pandas Timestamp (UTC). Convert to ET datetime.
        if hasattr(close_ts, "to_pydatetime"):
            dt = close_ts.to_pydatetime()
        else:
            dt = datetime.fromisoformat(str(close_ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET)
    except Exception as e:
        log.warning("MarketHoursCalendar: schedule(%s) failed: %s — fallback", d, e)
        on_fallback()
        return _fallback_close(d)


def _fallback_close(d: date) -> datetime | None:
    """Pre-pmcal behaviour: 16:00 ET on weekdays, None on weekends.
    No half-day / holiday awareness."""
    if d.weekday() >= 5:   # 5=Sat, 6=Sun
        return None
    return datetime.combine(
        d, time(_DEFAULT_CLOSE_HOUR_ET, _DEFAULT_CLOSE_MIN_ET),
    ).replace(tzinfo=ET)


# Module-level singleton — most callers don't need their own instance.
_default = MarketHoursCalendar()


def default_calendar() -> MarketHoursCalendar:
    """Process-wide shared MarketHoursCalendar.

    Most callers should use this — there's no reason to construct your
    own. Tests can construct fresh instances or inject mocks instead.
    """
    return _default
