"""Board 2026-07-06 bracket changes:
  1. MIN_LEG_QTY_BTC 0.0003 -> 0.0001 (venue min order size).
  2. The 1-leg degrade rests at the FULL-PROFIT target (farthest tp: tp3 or
     tp2 or tp1), not the nearest tp1 fee-covering TP.

These tests pin the new thresholds and the full-profit selection, and prove SFP
(single-tp1 tp_plan) is unchanged.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.divisions.bitunix_bracket import (
    MIN_LEG_QTY_BTC,
    build_bracket_legs,
)

# SHORT-side futures plan: targets BELOW entry, tp3 farthest (full profit).
FUT_PLAN = [
    {"leg": "tp1", "price": 63336.19, "fraction": 0.25},
    {"leg": "tp2", "price": 63312.03, "fraction": 0.50},
    {"leg": "tp3", "price": 63104.46, "fraction": 0.25},
]
# SFP plan: a single full-profit target passed as tp1, fraction 1.0.
SFP_PLAN = [{"leg": "tp1", "price": 62900.0, "fraction": 1.0}]


def test_min_leg_is_0001():
    assert MIN_LEG_QTY_BTC == 0.0001


def test_three_legs_at_4x_min():
    # qty >= 4*min (0.0004) -> 3 legs, qtys sum to entry.
    legs, note = build_bracket_legs(0.0005, FUT_PLAN)
    assert [l.leg for l in legs] == ["tp1", "tp2", "tp3"]
    assert note == ""
    assert round(sum(l.qty for l in legs), 7) == 0.0005


def test_two_legs_between_2x_and_4x_min():
    # qty in [0.0002, 0.0004) -> 2 legs (tp1 + tp3), half each, sum preserved.
    legs, note = build_bracket_legs(0.00025, FUT_PLAN)
    assert [l.leg for l in legs] == ["tp1", "tp3"]
    assert "2 legs" in note
    assert round(sum(l.qty for l in legs), 7) == 0.00025


def test_one_leg_rests_at_full_profit_tp3_for_futures():
    # qty in [0.0001, 0.0002) -> ONE leg at the FULL-PROFIT target (tp3), full qty.
    legs, note = build_bracket_legs(0.00015, FUT_PLAN)
    assert len(legs) == 1
    assert legs[0].leg == "tp3"
    assert legs[0].price == 63104.46          # farthest target, NOT tp1 63336.19
    assert legs[0].qty == 0.00015
    assert "full-profit" in note


def test_one_leg_sfp_single_tp1_unchanged():
    # SFP passes only tp1 (its own full-profit price). tp3/tp2 are None, so the
    # farthest-available target IS tp1 -> SFP behaviour is unchanged.
    legs, note = build_bracket_legs(0.0005, SFP_PLAN)
    assert len(legs) == 1
    assert legs[0].leg == "tp1"
    assert legs[0].price == 62900.0
    assert legs[0].qty == 0.0005


def test_full_profit_prefers_tp3_then_tp2_then_tp1():
    # If only tp1+tp2 present (no tp3), the 1-leg rests at tp2 (farthest available).
    plan_no_tp3 = [
        {"leg": "tp1", "price": 63336.19, "fraction": 0.5},
        {"leg": "tp2", "price": 63312.03, "fraction": 0.5},
    ]
    legs, _ = build_bracket_legs(0.00015, plan_no_tp3)
    assert len(legs) == 1
    assert legs[0].leg == "tp2"
    assert legs[0].price == 63312.03


def test_sub_min_position_is_sl_only():
    # qty < 0.0001 -> no legs (SL-only). Below the venue min; no order placeable.
    legs, note = build_bracket_legs(0.00005, FUT_PLAN)
    assert legs == []
    assert "SL-only" in note


def test_current_live_position_size_now_gets_two_legs():
    # The 2026-07-06 live short (0.0002135 BTC) was SL-only under min=0.0003;
    # under min=0.0001 it clears the 2-leg threshold (0.0002).
    legs, _ = build_bracket_legs(0.0002135, FUT_PLAN)
    assert [l.leg for l in legs] == ["tp1", "tp3"]
    assert round(sum(l.qty for l in legs), 7) == 0.0002135


@pytest.mark.parametrize(
    "qty,expected_n",
    [
        (0.0001, 1),    # exactly 1*min -> 1 leg
        (0.00019, 1),   # just below 2*min -> 1 leg
        (0.0002, 2),    # exactly 2*min -> 2 legs
        (0.00039, 2),   # just below 4*min -> 2 legs
        (0.0004, 3),    # exactly 4*min -> 3 legs
    ],
)
def test_threshold_boundaries(qty, expected_n):
    legs, _ = build_bracket_legs(qty, FUT_PLAN)
    assert len(legs) == expected_n


def test_qty_conservation_across_sizes():
    for qty in (0.0001, 0.00015, 0.0002, 0.0003, 0.0005, 0.001):
        legs, _ = build_bracket_legs(qty, FUT_PLAN)
        assert round(sum(l.qty for l in legs), 7) == round(qty, 7)
