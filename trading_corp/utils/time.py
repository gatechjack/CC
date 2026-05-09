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


# ── Display formatters (Board direction 2026-05-09: dashboard times in ET) ──

def to_et(ts: str | datetime | None) -> datetime | None:
    """Convert an ISO-8601 string OR datetime to an ET-aware datetime.
    Returns None if the input is None/empty/unparseable. Naive datetimes
    are assumed UTC (matches how audit_event ISO timestamps are written).
    """
    if ts is None or ts == "":
        return None
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        dt = ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET)


def format_et_short(ts: str | datetime | None) -> str:
    """'MM-DD HH:MM ET' — used in the Donchian decision-log + trades tiles
    where the date matters (a 6h bar can straddle UTC and ET dates)."""
    et = to_et(ts)
    return et.strftime("%m-%d %H:%M ET") if et else ""


def format_et_hm(ts: str | datetime | None) -> str:
    """'HH:MM ET' — for boundary timestamps where the date is implicit
    (e.g. the Donchian state card's 'next 6h close' that's always today
    or tomorrow)."""
    et = to_et(ts)
    return et.strftime("%H:%M ET") if et else ""


def format_et_hms(ts: str | datetime | None) -> str:
    """'HH:MM:SS ET' — for approval-page diagnostics where seconds matter."""
    et = to_et(ts)
    return et.strftime("%H:%M:%S ET") if et else ""


def format_et_full(ts: str | datetime | None) -> str:
    """'YYYY-MM-DD HH:MM ET' — for places that need an unambiguous
    full timestamp (e.g. expiry-warning UI on routes.py)."""
    et = to_et(ts)
    return et.strftime("%Y-%m-%d %H:%M ET") if et else ""
