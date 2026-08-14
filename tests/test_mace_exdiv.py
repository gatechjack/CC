"""Phase-1 tests: MACE exdiv guard wrapper (calendar/date side).

Uses tmp ex-dividend YAML fixtures for the window math and the shipped
config/ex_dividend_calendar.yaml to assert USO/EWZ are inert (the surfaced
data gap) while SPY resolves.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from trading_corp.data.ex_dividend_calendar import ExDividendCalendar
from trading_corp.mace.exdiv import MaceExDiv

ROOT = Path(__file__).resolve().parents[1]
REAL_YAML = ROOT / "config" / "ex_dividend_calendar.yaml"

_SPY_ONE = """
ex_dividends:
  - symbol: SPY
    ex_date: "2026-09-18"
    confirmed: true
    source: test
""".strip()


def _cal_from(tmp_path: Path, body: str) -> MaceExDiv:
    p = tmp_path / "exdiv.yaml"
    p.write_text(body, encoding="utf-8")
    return MaceExDiv(ExDividendCalendar.load(p))


def test_next_ex_date_and_within_window(tmp_path):
    cal = _cal_from(tmp_path, _SPY_ONE)
    assert cal.next_ex_date("SPY", today=date(2026, 9, 16)) == date(2026, 9, 18)
    now = datetime(2026, 9, 16, 19, 45, tzinfo=timezone.utc)   # Wed 15:45 ET
    assert cal.within_window("SPY", now, 5) is True


def test_check_guard_on_hit(tmp_path):
    cal = _cal_from(tmp_path, _SPY_ONE)
    now = datetime(2026, 9, 16, 19, 45, tzinfo=timezone.utc)
    c = cal.check("SPY", now, 5, guard_on=True)
    assert c.calendar_hit is True and c.within_window is True
    assert c.next_ex_date == date(2026, 9, 18)


def test_check_guard_off_shortcircuits(tmp_path):
    cal = _cal_from(tmp_path, _SPY_ONE)
    now = datetime(2026, 9, 16, 19, 45, tzinfo=timezone.utc)
    c = cal.check("SPY", now, 5, guard_on=False)
    assert c.calendar_hit is False and c.next_ex_date is None and "off" in c.detail


def test_empty_universe_symbol_inert(tmp_path):
    cal = _cal_from(tmp_path, "ex_dividends: []")
    now = datetime(2026, 9, 16, 19, 45, tzinfo=timezone.utc)
    c = cal.check("EWZ", now, 5, guard_on=True)
    assert c.next_ex_date is None and c.calendar_hit is False


def test_window_boundary_5_vs_4(tmp_path):
    cal = _cal_from(tmp_path, _SPY_ONE)
    fri = datetime(2026, 9, 11, 19, 45, tzinfo=timezone.utc)   # Fri, 5 business days out
    assert cal.check("SPY", fri, 5, guard_on=True).within_window is True
    assert cal.check("SPY", fri, 4, guard_on=True).within_window is False


def test_shipped_yaml_uso_ewz_inert_spy_live():
    cal = MaceExDiv.load(REAL_YAML)
    now = datetime(2026, 9, 16, 19, 45, tzinfo=timezone.utc)
    # USO/IBIT/EWZ carry no dates in the shipped config -> guard inert
    # (FXI got issuer-confirmed dates 2026-08-13 -> asserted live below)
    assert cal.check("USO", now, 5, guard_on=True).next_ex_date is None
    assert cal.check("EWZ", now, 5, guard_on=True).next_ex_date is None
    assert cal.check("IBIT", now, 5, guard_on=True).next_ex_date is None
    # SPY has real dates; the 3-active additions + FXI now do too
    assert cal.next_ex_date("SPY", today=date(2026, 1, 1)) is not None
    assert cal.next_ex_date("XLE", today=date(2026, 8, 14)) == date(2026, 9, 21)
    assert cal.next_ex_date("GDX", today=date(2026, 8, 14)) == date(2026, 12, 21)
    assert cal.next_ex_date("FXI", today=date(2026, 8, 14)) == date(2026, 12, 15)
