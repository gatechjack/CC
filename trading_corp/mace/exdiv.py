"""MACE ex-dividend guard wrapper over data.ex_dividend_calendar.

The management exdiv guard (plan § Management) force-closes a rung when the
symbol's exdiv_guard is on AND the short call is ITM (spot > short-call strike)
AND ex-div falls within `exdiv_guard_sessions` business days. The short-call-ITM
comparison is PURE and lives in strategy.py; THIS module owns the calendar
(date) side — the hot-reloading YAML view — for the MACE universe.

data.ex_dividend_calendar already covers SPY (confirmed quarterly) + TLT
(monthly rule); config/ex_dividend_calendar.yaml gains EWZ/FXI/USO/IBIT this
phase. USO/IBIT pay nothing (stage-A verified) -> no entries, exdiv_guard off.
EWZ/FXI need issuer-sourced 2026 ex-div dates before they are ENABLED — they
are structured-empty until then, so their exdiv_guard (true in mace.yaml) is
INERT until dates land (see the YAML preamble; both are disabled at launch and
enablement is gated behind the expansion runbook).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from trading_corp.data.ex_dividend_calendar import ExDividendCalendar
from trading_corp.utils.time import to_et


@dataclass(frozen=True)
class ExDivCheck:
    """Calendar side of the management exdiv guard. `calendar_hit` is the
    guard-on AND within-window verdict; the caller (strategy) ANDs it with the
    short-call-ITM test to decide the force-close."""

    symbol: str
    guard_on: bool
    within_window: bool          # ex-div within `sessions` business days
    next_ex_date: date | None
    sessions: int
    detail: str

    @property
    def calendar_hit(self) -> bool:
        return self.guard_on and self.within_window


class MaceExDiv:
    """Thin MACE-scoped wrapper over the shared ExDividendCalendar (injectable
    for tests — point it at a tmp YAML)."""

    def __init__(self, calendar: ExDividendCalendar) -> None:
        self._cal = calendar

    @classmethod
    def load(cls, path="config/ex_dividend_calendar.yaml") -> "MaceExDiv":
        return cls(ExDividendCalendar.load(path))

    def next_ex_date(self, symbol: str, today: date | None = None) -> date | None:
        return self._cal.next_ex_date(symbol, today=today)

    def within_window(self, symbol: str, now: datetime, sessions: int) -> bool:
        et = to_et(now) or now
        return self._cal.is_within_window(symbol, et, trading_days=sessions)

    def check(
        self, symbol: str, now: datetime, sessions: int, *, guard_on: bool,
    ) -> ExDivCheck:
        """Evaluate the calendar side of the guard. `now` is normalized to ET so
        the business-day math anchors on the trading date. When the symbol's
        exdiv_guard is off this short-circuits (no lookup)."""
        if not guard_on:
            return ExDivCheck(symbol, False, False, None, sessions,
                              f"{symbol} exdiv guard off")
        et = to_et(now) or now
        nxt = self._cal.next_ex_date(symbol, today=et.date())
        within = self._cal.is_within_window(symbol, et, trading_days=sessions)
        if nxt is None:
            detail = f"{symbol} no upcoming ex-div on record"
        elif within:
            detail = f"{symbol} ex-div {nxt.isoformat()} within {sessions} sessions"
        else:
            detail = f"{symbol} next ex-div {nxt.isoformat()} beyond {sessions} sessions"
        return ExDivCheck(symbol, True, within, nxt, sessions, detail)
