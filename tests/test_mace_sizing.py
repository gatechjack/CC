"""Phase-2 tests: MACE sizing + max-risk + derived P&L helpers."""
from __future__ import annotations

from datetime import date

from trading_corp.mace import strategy as st
from trading_corp.mace.domain import CondorSpec, RungState

EXPIRY = date(2026, 9, 18)


def _rung(status="closed", exit_ts=None, realized=0.0, symbol="SPY"):
    spec = CondorSpec("SPY", EXPIRY, 585, 582, 615, 618, 3.0)
    return RungState(rung_id="r", symbol=symbol, status=status, expiry=EXPIRY,
                     spec=spec, width_dollars=3.0, contracts=1,
                     exit_ts=exit_ts, realized_pnl=realized)


# ── size_contracts ───────────────────────────────────────────────────────

def test_size_floor_and_cap():
    # 0.05*50000 / ((3-1.0)*100=200) = 12.5 -> floor 12, capped to max_contracts
    assert st.size_contracts(50_000, 3.0, 1.0, 0.05, 1) == 1
    assert st.size_contracts(50_000, 3.0, 1.0, 0.05, 5) == 5     # cap 5 binds
    assert st.size_contracts(50_000, 3.0, 1.0, 0.05, 20) == 12   # floor 12 binds


def test_size_budget_zero():
    # tiny equity -> floor(0.025) = 0 -> budget skip upstream
    assert st.size_contracts(100, 3.0, 1.0, 0.05, 1) == 0


def test_size_nonpositive_risk():
    # credit >= width -> per-contract risk <= 0 -> 0 (cannot size)
    assert st.size_contracts(50_000, 3.0, 3.0, 0.05, 1) == 0
    assert st.size_contracts(50_000, 3.0, 4.0, 0.05, 1) == 0


def test_max_risk_usd():
    assert st.max_risk_usd(3.0, 1.0, 1) == 200.0
    assert st.max_risk_usd(3.0, 1.0, 2) == 400.0
    assert st.max_risk_usd(2.0, 0.7, 3) == (2.0 - 0.7) * 100 * 3


# ── derived P&L (feeds breakers) ─────────────────────────────────────────

def test_day_realized():
    rungs = [
        _rung(exit_ts="2026-08-12T18:00:00+00:00", realized=100.0),  # today (ET 14:00)
        _rung(exit_ts="2026-08-12T19:30:00+00:00", realized=-40.0),  # today
        _rung(exit_ts="2026-08-11T18:00:00+00:00", realized=999.0),  # yesterday (excluded)
        _rung(status="open", realized=None),                         # open (excluded)
    ]
    assert st.day_realized(rungs, date(2026, 8, 12)) == 60.0


def test_week_realized():
    rungs = [
        _rung(exit_ts="2026-08-10T18:00:00+00:00", realized=50.0),   # Mon this ISO week
        _rung(exit_ts="2026-08-12T18:00:00+00:00", realized=-20.0),  # Wed this ISO week
        _rung(exit_ts="2026-08-07T18:00:00+00:00", realized=1000.0), # prior Fri (prior ISO week)
    ]
    assert st.week_realized(rungs, date(2026, 8, 12)) == 30.0
