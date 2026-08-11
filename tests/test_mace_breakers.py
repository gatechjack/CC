"""Phase-2 tests: MACE breaker math (alert-only; enforcement ships 'off')."""
from __future__ import annotations

from pathlib import Path

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

E = 50_000.0
HWM = 50_000.0


def test_day_loss_threshold():
    # day_loss_pct 0.05 -> -2500 threshold
    hit = st.evaluate_breakers(-2500.0, 0.0, E, HWM, CFG)
    assert hit.day_loss_hit and hit.any_hit
    miss = st.evaluate_breakers(-2499.0, 0.0, E, HWM, CFG)
    assert not miss.day_loss_hit


def test_week_loss_threshold():
    # week_loss_pct 0.08 -> -4000 threshold
    assert st.evaluate_breakers(0.0, -4000.0, E, HWM, CFG).week_loss_hit
    assert not st.evaluate_breakers(0.0, -3999.0, E, HWM, CFG).week_loss_hit


def test_gain_does_not_trip():
    b = st.evaluate_breakers(5000.0, 5000.0, E, HWM, CFG)
    assert not b.day_loss_hit and not b.week_loss_hit


def test_hwm_soft():
    # hwm_soft_pct 0.85 -> equity < 42500
    assert st.evaluate_breakers(0.0, 0.0, 42_000.0, HWM, CFG).hwm_soft_hit
    assert not st.evaluate_breakers(0.0, 0.0, 43_000.0, HWM, CFG).hwm_soft_hit


def test_hwm_hard_implies_soft():
    # hwm_hard_pct 0.75 -> equity < 37500
    b = st.evaluate_breakers(0.0, 0.0, 37_000.0, HWM, CFG)
    assert b.hwm_hard_hit and b.hwm_soft_hit


def test_no_equity_or_hwm_no_hits():
    assert not st.evaluate_breakers(-9999.0, -9999.0, None, None, CFG).any_hit
    assert not st.evaluate_breakers(-9999.0, -9999.0, 0.0, 0.0, CFG).any_hit


def test_carries_inputs():
    b = st.evaluate_breakers(-100.0, -200.0, E, HWM, CFG)
    assert b.day_realized == -100.0 and b.week_realized == -200.0
    assert b.equity == E and b.hwm == HWM


def test_enforcement_ships_off():
    # breaker_enforcement is 'off' in the shipped config (branches exist, inert)
    assert CFG.breakers.breaker_enforcement == "off"
