"""Pure-logic tests for the exchange-resting bracket exit.

build_bracket_legs (min-0.0003 degradation) + decide_sl_move (the (b)+(c)
tighten-only hybrid). No venue I/O.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.divisions.bitunix_bracket import (
    MIN_LEG_QTY_BTC,
    BracketLeg,
    build_bracket_legs,
    decide_sl_move,
)

# A short's v2 tp_plan: SL (66273) above entry (66047); TPs below, descending.
SHORT_TP_PLAN = [
    {"leg": "tp1", "fraction": 0.25, "price": 65928.22, "stop_action": "move_to_breakeven"},
    {"leg": "tp2", "fraction": 0.50, "price": 65801.10, "stop_action": "move_to_tp1"},
    {"leg": "tp3", "fraction": 0.25, "price": 65482.04, "stop_action": "trail_atr"},
]


def _assert_no_submin(legs):
    for leg in legs:
        assert leg.qty >= MIN_LEG_QTY_BTC - 1e-12, f"sub-min leg placed: {leg}"


def test_three_legs_when_large_enough():
    # 0.25-fraction legs bind at qty >= 4*min = 0.0012.
    legs, note = build_bracket_legs(0.0016, SHORT_TP_PLAN)
    assert [l.leg for l in legs] == ["tp1", "tp2", "tp3"]
    assert note == ""
    _assert_no_submin(legs)
    assert sum(l.qty for l in legs) == pytest.approx(0.0016)
    assert legs[0].price == 65928.22 and legs[2].price == 65482.04
    # fractions
    assert legs[0].qty == pytest.approx(0.0004)   # 0.25
    assert legs[1].qty == pytest.approx(0.0008)   # 0.50


def test_three_legs_exact_threshold():
    legs, note = build_bracket_legs(4 * MIN_LEG_QTY_BTC, SHORT_TP_PLAN)  # 0.0012
    assert len(legs) == 3
    _assert_no_submin(legs)
    assert sum(l.qty for l in legs) == pytest.approx(0.0012)


def test_degrade_to_two_legs():
    # qty in [2*min, 4*min) → 2 legs (tp1+tp3), half each.
    legs, note = build_bracket_legs(0.0008, SHORT_TP_PLAN)
    assert [l.leg for l in legs] == ["tp1", "tp3"]
    assert "2 legs" in note
    _assert_no_submin(legs)
    assert sum(l.qty for l in legs) == pytest.approx(0.0008)
    assert legs[0].qty == pytest.approx(0.0004) and legs[1].qty == pytest.approx(0.0004)


def test_degrade_to_one_leg():
    # qty in [min, 2*min) → 1 leg at tp1, full qty.
    legs, note = build_bracket_legs(0.0004, SHORT_TP_PLAN)
    assert [l.leg for l in legs] == ["tp1"]
    assert "1 leg" in note
    _assert_no_submin(legs)
    assert legs[0].qty == pytest.approx(0.0004)


def test_position_below_min_no_legs():
    legs, note = build_bracket_legs(0.0002, SHORT_TP_PLAN)
    assert legs == []
    assert "SL-only" in note


def test_two_leg_boundary_each_clears_min():
    # at exactly 2*min = 0.0006 → 2 legs of exactly min each (never sub-min).
    legs, _ = build_bracket_legs(2 * MIN_LEG_QTY_BTC, SHORT_TP_PLAN)
    assert len(legs) == 2
    _assert_no_submin(legs)
    assert all(l.qty == pytest.approx(MIN_LEG_QTY_BTC) for l in legs)


# ── decide_sl_move (short: entry 66047, structural SL 66273 above) ──

def test_sl_no_move_before_tp1():
    new_sl, _ = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66273.0,
        tp1_price=65928.0, entry_qty=0.0016, current_qty=0.0016,
    )
    assert new_sl is None


def test_sl_to_breakeven_after_tp1_short():
    # TP1 (0.25) filled → 25% closed → SL to breakeven (entry), tighter (66047<66273).
    new_sl, why = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66273.0,
        tp1_price=65928.0, entry_qty=0.0016, current_qty=0.0012,
    )
    assert new_sl == pytest.approx(66047.0)
    assert "breakeven" in why


def test_sl_to_tp1_after_tp2_short():
    # 75% closed → SL to tp1 (65928), tighter than breakeven path.
    new_sl, why = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66047.0,
        tp1_price=65928.0, entry_qty=0.0016, current_qty=0.0004,
    )
    assert new_sl == pytest.approx(65928.0)
    assert "TP1" in why


def test_sl_tighten_only_short_does_not_loosen():
    # Already at breakeven; a 25% close would target breakeven again → no move
    # (target not tighter). And never moves the stop AWAY (up, for a short).
    new_sl, _ = decide_sl_move(
        side="sell", entry_price=66047.0, current_sl=66047.0,
        tp1_price=65928.0, entry_qty=0.0016, current_qty=0.0012,
    )
    assert new_sl is None


def test_sl_to_breakeven_after_tp1_long():
    # long: entry 66000, structural SL 65800 below; TP1 → SL up to breakeven.
    new_sl, why = decide_sl_move(
        side="buy", entry_price=66000.0, current_sl=65800.0,
        tp1_price=66120.0, entry_qty=0.0016, current_qty=0.0012,
    )
    assert new_sl == pytest.approx(66000.0)
    assert "breakeven" in why


def test_sl_long_tighten_only():
    # long already above-breakeven SL; a lower target must NOT loosen it.
    new_sl, _ = decide_sl_move(
        side="buy", entry_price=66000.0, current_sl=66050.0,
        tp1_price=66120.0, entry_qty=0.0016, current_qty=0.0012,
    )
    assert new_sl is None
