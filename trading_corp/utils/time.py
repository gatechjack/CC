"""Time helpers — ET timezone, market hours, schedule predicates."""
from __future__ import annotations

from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

US_EQUITY_OPEN = time(9, 30)
US_EQUITY_CLOSE = time(16, 0)


def now_et() -> datetime:
    return datetime.now(tz=ET)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def is_us_equity_open(when: datetime | None = None) -> bool:
    """Return True during 9:30am–4:00pm ET on a weekday.

    Does NOT account for market holidays — Phase 4 will integrate a calendar.
    """
    when = (when or now_et()).astimezone(ET)
    if when.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    t = when.time()
    return US_EQUITY_OPEN <= t < US_EQUITY_CLOSE


def next_morning_brief_at(hour: int = 8, minute: int = 30, when: datetime | None = None) -> datetime:
    """Return the next ET datetime at hour:minute. Skips weekends to next Monday."""
    when = (when or now_et()).astimezone(ET)
    target = when.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= when:
        target = target + timedelta(days=1)
    while target.weekday() >= 5:
        target = target + timedelta(days=1)
    return target


def iso(d: datetime) -> str:
    """Stable ISO-8601 string for logging/journaling."""
    return d.astimezone(UTC).isoformat(timespec="seconds")


def trading_day(when: datetime | None = None) -> date:
    return (when or now_et()).astimezone(ET).date()
