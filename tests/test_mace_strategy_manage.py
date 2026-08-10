"""Phase-2 tests: MACE management precedence (stop > time > exdiv)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec, EXIT_EXDIV, EXIT_STOP, EXIT_TIME, RungState
from trading_corp.utils.time import ET

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")
SPY = CFG.symbols["SPY"]                # exdiv_guard True
GLD = CFG.symbols["GLD"]               # exdiv_guard False


def _rung(expiry, credit=1.0, short_call=615):
    spec = CondorSpec("SPY", expiry, 585, 582, short_call, short_call + 3, 3.0)
    return RungState(rung_id="r", symbol="SPY", status="open", expiry=expiry,
                     spec=spec, width_dollars=3.0, contracts=1, credit_actual=credit)


def _now(y=2026, mo=8, d=12, h=15, mi=35):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def test_stop_fires():
    r = _rung(date(2026, 9, 18), credit=1.0)          # far DTE, no time/exdiv
    d = st.evaluate_management(r, mark=2.0, spot=600, now_et=_now(), cfg=CFG,
                               symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_STOP and d.should_exit


def test_stop_boundary_below_holds():
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=1.99, spot=600, now_et=_now(), cfg=CFG,
                               symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None


def test_time_fires_dte_and_clock():
    r = _rung(date(2026, 9, 1), credit=1.0)           # DTE 20 <= 21
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_TIME


def test_time_needs_clock_after_1530():
    r = _rung(date(2026, 9, 1), credit=1.0)           # DTE 20 <= 21
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=15, mi=0),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None                       # before 15:30 -> hold


def test_time_needs_dte():
    r = _rung(date(2026, 9, 18), credit=1.0)          # DTE 37 > 21
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=15, mi=45),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None


def test_exdiv_fires_itm_and_guard():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)   # far DTE
    d = st.evaluate_management(r, mark=1.0, spot=620, now_et=_now(h=10),   # spot > 615 ITM
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason == EXIT_EXDIV


def test_exdiv_needs_itm():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)
    d = st.evaluate_management(r, mark=1.0, spot=610, now_et=_now(h=10),   # spot < 615, not ITM
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason is None


def test_exdiv_needs_guard_on():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)
    d = st.evaluate_management(r, mark=1.0, spot=620, now_et=_now(h=10),
                               cfg=CFG, symbol_cfg=GLD, exdiv_within=True)  # guard off
    assert d.exit_reason is None


def test_precedence_stop_over_time_over_exdiv():
    r = _rung(date(2026, 9, 1), credit=1.0, short_call=615)   # DTE 20 (time-eligible)
    # all three would fire: mark 3.0 (stop), DTE20@15:35 (time), spot 620 ITM (exdiv)
    d = st.evaluate_management(r, mark=3.0, spot=620, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason == EXIT_STOP                 # stop wins


def test_precedence_time_over_exdiv():
    r = _rung(date(2026, 9, 1), credit=1.0, short_call=615)   # DTE 20
    d = st.evaluate_management(r, mark=1.0, spot=620, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason == EXIT_TIME                 # time beats exdiv (no stop)


def test_hold_when_nothing_fires():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None and not d.should_exit


def test_stop_gap_tick_0935():
    # the 09:35 tick IS the gap rule — no separate branch, stop fires on the mark
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=2.5, spot=600, now_et=_now(h=9, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_STOP
