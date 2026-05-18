"""Ex-dividend calendar — looks up upcoming ex-dividend dates for ETFs
in the iron-condor universe.

Used by the Robinhood-joint iron-condor strategy to force-close any open
IC where the short call delta is meaningful AND ex-div is imminent — a
short-call early-assignment defense.

Phase 1: read events from `config/ex_dividend_calendar.yaml`
(hand-maintained, populated for 2026). Phase 1.5 will swap in an
auto-fetcher off issuer pages into the same YAML shape.

Public API mirrors `data.macro_calendar`:

    cal = ExDividendCalendar.load()
    nxt = cal.next_ex_date("SPY")                 # date | None
    hit = cal.is_within_window(                    # bool
        "SPY", now_utc(), trading_days=3,
    )

`is_within_window` counts BUSINESS days (Mon–Fri) — it does NOT
subtract market holidays. For the iron-condor force-close check, a
3-trading-day window is conservative; a 1-day holiday inside the
window just makes the check fire slightly earlier than strict
trading-day counting would, which is the safe direction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Reload window — re-stat the YAML at most this often.
_RELOAD_SEC = 5.0


@dataclass(frozen=True)
class ExDividendEvent:
    symbol: str
    ex_date: date
    pay_date: date | None
    confirmed: bool
    source: str

    @classmethod
    def from_dict(cls, d: dict) -> "ExDividendEvent":
        raw_pay = d.get("pay_date") or ""
        pay = None
        if raw_pay:
            try:
                pay = date.fromisoformat(str(raw_pay))
            except (ValueError, TypeError):
                pay = None
        return cls(
            symbol=str(d["symbol"]).upper(),
            ex_date=date.fromisoformat(str(d["ex_date"])),
            pay_date=pay,
            confirmed=bool(d.get("confirmed", False)),
            source=str(d.get("source", "")),
        )


class ExDividendCalendar:
    """Hot-reloading view over `config/ex_dividend_calendar.yaml`.

    Pass `path` to point at a different file (used in tests). Use
    `ExDividendCalendar.load()` for the production default path.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._events: list[ExDividendEvent] = []

    @classmethod
    def load(
        cls,
        path: str | Path = "config/ex_dividend_calendar.yaml",
    ) -> "ExDividendCalendar":
        cal = cls(Path(path))
        cal._reload()
        return cal

    # --------------------------------------------------------------
    # Reload
    # --------------------------------------------------------------

    def _reload_if_stale(self) -> None:
        import time
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SEC:
            return
        self._last_check = now
        self._reload()

    def _reload(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            log.debug("ExDividendCalendar: %s does not exist (no events loaded)", self._path)
            self._events = []
            return
        if mtime == self._mtime:
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning("ExDividendCalendar: failed to load %s: %s", self._path, e)
            return
        raw = data.get("ex_dividends", []) or []
        events: list[ExDividendEvent] = []
        for d in raw:
            try:
                events.append(ExDividendEvent.from_dict(d))
            except Exception as e:
                log.warning("ExDividendCalendar: skipping bad event %r: %s", d, e)
        # Sort ascending by ex_date so lookups short-circuit fast.
        events.sort(key=lambda e: e.ex_date)
        self._events = events
        self._mtime = mtime
        log.info(
            "ExDividendCalendar reloaded %d events from %s",
            len(events), self._path,
        )

    # --------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------

    def next_ex_date(
        self,
        symbol: str,
        today: date | None = None,
    ) -> date | None:
        """Earliest ex-dividend date for `symbol` on or after `today`.

        Returns None if `symbol` has no upcoming entries (unknown symbol,
        empty universe like GLD, or all configured dates already past).
        `today` defaults to the current local date — pass an explicit
        value in tests for determinism.
        """
        self._reload_if_stale()
        ref = today or date.today()
        sym = symbol.upper()
        for e in self._events:
            if e.symbol == sym and e.ex_date >= ref:
                return e.ex_date
        return None

    def is_within_window(
        self,
        symbol: str,
        now: datetime,
        trading_days: int = 3,
    ) -> bool:
        """True iff the next ex-div for `symbol` is within `trading_days`
        business days (Mon–Fri) of `now`.

        Counts business days STRICTLY AFTER `now.date()` and up to the
        ex-date inclusive. Same-day ex-dates count as 0 trading days
        away and therefore satisfy any positive window.

        Does not subtract US market holidays. A holiday inside the
        window just makes the force-close fire one day earlier than a
        strict trading-day count would — safe direction for the IC
        early-assignment defense.

        Returns False if `symbol` is unknown or has no upcoming events.
        """
        ref_date = now.date()
        nxt = self.next_ex_date(symbol, today=ref_date)
        if nxt is None:
            return False
        if nxt < ref_date:
            return False
        return _business_days_between(ref_date, nxt) <= trading_days


def _business_days_between(start: date, end: date) -> int:
    """Count Mon–Fri days strictly after `start` up to and including `end`.

    `_business_days_between(d, d) == 0` (same day → no business days have
    elapsed yet). `_business_days_between(Fri, Mon) == 1` (only Monday).
    Raises ValueError if end < start.
    """
    if end < start:
        raise ValueError(f"end ({end}) is before start ({start})")
    count = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        # weekday(): Mon=0 .. Sun=6
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count
