"""Tests for the adaptive trade-plan builder.

Covers the spec's 8 numbered test cases plus invariants:
1. SL method selection (swing-within, swing-too-close, swing-too-far, no-swing)
2. TP1 fee floor (raised when target < floor; skipped when floor >= TP2)
3. TP2 snap behavior (in-band snaps; out-of-band stays at 1R)
4. Long/short symmetry
"""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies.trade_plan import (
    FeeConfig,
    StrategyConfig,
    TradePlan,
    build_trade_plan,
)


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def cfg() -> StrategyConfig:
    return StrategyConfig()


@pytest.fixture
def fees() -> FeeConfig:
    # Tiny fees so they don't accidentally trigger the floor in baseline tests.
    return FeeConfig(taker_fee_pct=0.0001, maker_fee_pct=0.0001, slippage_pct=0.0)


# ── fees ────────────────────────────────────────────────────────────


def test_fee_config_round_trip_taker_both_sides():
    f = FeeConfig(taker_fee_pct=0.0004, maker_fee_pct=0.00014, slippage_pct=0.00005,
                  entry_is_taker=True, tp_is_maker=False)
    # 0.0004 + 0.0004 + 2*0.00005 = 0.0009 (0.09% of price)
    assert f.round_trip_cost_pct() == pytest.approx(0.0009)


def test_fee_config_round_trip_taker_in_maker_out():
    f = FeeConfig(taker_fee_pct=0.0004, maker_fee_pct=0.00014, slippage_pct=0.00005,
                  entry_is_taker=True, tp_is_maker=True)
    # 0.0004 + 0.00014 + 2*0.00005 = 0.00064
    assert f.round_trip_cost_pct() == pytest.approx(0.00064)


# ── SL placement (spec test #3) ─────────────────────────────────────


def test_sl_swing_within_bounds_uses_swing(cfg, fees):
    # ATR=10; swing_low=90 below entry=100 → swing_distance ≈ 10 (1.0×ATR);
    # within [0.5, 2.5] × ATR → use swing.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=90.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.sl_method == "swing"
    assert plan.stop_loss == pytest.approx(90.0 - 0.0005 * 100.0)  # swing - buffer


def test_sl_swing_too_close_skips_trade(cfg, fees):
    # ATR=10; swing_low=96 (4 below) → swing_distance ≈ 4 = 0.4×ATR <
    # min_stop_atr_mult (0.5) → SKIP.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=96.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert not plan.should_trade
    assert plan.skip_reason == "swing_too_close"


def test_sl_swing_too_far_falls_back_to_atr(cfg, fees):
    # ATR=10; swing_low=60 (40 below) → swing_distance = 40 = 4.0×ATR
    # > max_stop_atr_mult (2.5) → fall back to 1.5×ATR=15.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=60.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.sl_method == "atr_fallback"
    assert plan.stop_loss == pytest.approx(85.0)  # 100 - 1.5×10


def test_sl_no_swing_uses_atr(cfg, fees):
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=None, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.sl_method == "atr_fallback"
    assert plan.stop_loss == pytest.approx(85.0)


def test_sl_swing_on_wrong_side_treated_as_no_swing(cfg, fees):
    # swing_low=110 above entry=100 → nonsense; should fall back to ATR.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=110.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.sl_method == "atr_fallback"


# ── TP1 fee floor (spec test #4) ────────────────────────────────────


def test_tp1_raised_when_fee_floor_exceeds_target(cfg):
    # Beef up fees so floor dominates. Entry=100, ATR=10, swing=85 (1.5×ATR).
    # R=15. tp1_target = 0.5×15 = 7.5. fee_floor = 2.0 × (0.005 × 100) = 1.0
    # — that's smaller than 7.5, doesn't trigger.
    # Instead: make fee floor = 2.0 × 0.10 × 100 = 20 — exceeds 0.5R=7.5 and TP2=15 → skip.
    # Use intermediate: 2.0 × 0.05 × 100 = 10. > 7.5 (target) but < 15 (TP2).
    fees = FeeConfig(taker_fee_pct=0.025, maker_fee_pct=0.025, slippage_pct=0.0)
    # round_trip = 0.025 + 0.025 = 0.05. floor = 2.0 × 0.05 × 100 = 10.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    # tp1 should be entry + 10 (the floor), not entry + 7.5 (target).
    assert plan.tp1 == pytest.approx(110.0)


def test_tp1_floor_exceeding_tp2_skips_trade(cfg):
    # Same as above but with fee floor pushed PAST 1R (=15).
    fees = FeeConfig(taker_fee_pct=0.05, maker_fee_pct=0.05, slippage_pct=0.0)
    # round_trip = 0.10. floor = 2.0 × 0.10 × 100 = 20. 20 >= 15 (TP2 default) → skip.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert not plan.should_trade
    assert plan.skip_reason == "fees_too_high_for_risk"


# ── TP2 level snap (spec test #5) ───────────────────────────────────


def test_tp2_snaps_to_resistance_in_band(cfg, fees):
    # Entry=100, R=15. TP2 default at 115. Resistance at 112.75 = 0.85R
    # band [0.5R=107.5, 1.3R=119.5] → snap TP2 just below resistance.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=112.75, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.tp2_method == "snap_resistance"
    # Snap = level - buffer = 112.75 - 0.0005×100 = 112.70
    assert plan.tp2 == pytest.approx(112.70)


def test_tp2_stays_at_default_when_resistance_too_far(cfg, fees):
    # Resistance well above 1.3R band → no snap, stay at 1R.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=125.0, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.tp2_method == "default_1r"
    assert (plan.tp2 - plan.entry) == pytest.approx(plan.risk_per_unit)  # exactly 1R


def test_tp2_stays_at_default_when_resistance_too_close(cfg, fees):
    # Resistance below 0.5R band → no snap, stay at 1R.
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=104.5, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.should_trade
    assert plan.tp2_method == "default_1r"
    assert (plan.tp2 - plan.entry) == pytest.approx(plan.risk_per_unit)  # exactly 1R


# ── long/short symmetry (spec test #8) ──────────────────────────────


def test_long_short_symmetry(cfg, fees):
    # Mirror inputs around entry=100; expect mirrored prices.
    long_plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=112.75, support=None,
        cfg=cfg, fees=fees,
    )
    short_plan = build_trade_plan(
        entry=100.0, side="sell", atr=10.0,
        swing_low=None, swing_high=115.0,
        resistance=None, support=87.25,
        cfg=cfg, fees=fees,
    )
    assert long_plan.should_trade and short_plan.should_trade
    assert long_plan.sl_method == short_plan.sl_method == "swing"
    assert long_plan.tp2_method == "snap_resistance"
    assert short_plan.tp2_method == "snap_support"
    # Mirrored distances
    assert (long_plan.tp1 - 100.0) == pytest.approx(100.0 - short_plan.tp1)
    assert (long_plan.tp2 - 100.0) == pytest.approx(100.0 - short_plan.tp2)
    assert (long_plan.tp3 - 100.0) == pytest.approx(100.0 - short_plan.tp3)
    assert (100.0 - long_plan.stop_loss) == pytest.approx(short_plan.stop_loss - 100.0)


# ── tp3 + qty fractions (sanity) ────────────────────────────────────


def test_tp3_is_fixed_at_configured_r(cfg, fees):
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    # TP3 is at exactly cfg.tp3_r_target * risk_per_unit from entry.
    assert (plan.tp3 - plan.entry) == pytest.approx(cfg.tp3_r_target * plan.risk_per_unit)


def test_qty_fractions_default_to_25_50_25(cfg, fees):
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=10.0,
        swing_low=85.0, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.tp1_qty_fraction == 0.25
    assert plan.tp2_qty_fraction == 0.50
    assert plan.tp3_qty_fraction == 0.25
    # Fractions sum to 1.0
    assert plan.tp1_qty_fraction + plan.tp2_qty_fraction + plan.tp3_qty_fraction == pytest.approx(1.0)


# ── edge cases ──────────────────────────────────────────────────────


def test_invalid_entry_skips(cfg, fees):
    plan = build_trade_plan(
        entry=0.0, side="buy", atr=10.0,
        swing_low=None, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.skip_reason == "invalid_entry"


def test_invalid_atr_skips(cfg, fees):
    plan = build_trade_plan(
        entry=100.0, side="buy", atr=0.0,
        swing_low=None, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.skip_reason == "invalid_atr"

    plan = build_trade_plan(
        entry=100.0, side="buy", atr=float("nan"),
        swing_low=None, swing_high=None,
        resistance=None, support=None,
        cfg=cfg, fees=fees,
    )
    assert plan.skip_reason == "invalid_atr"


def test_invalid_side_raises(cfg, fees):
    with pytest.raises(ValueError):
        build_trade_plan(
            entry=100.0, side="long",  # type: ignore[arg-type]
            atr=10.0,
            swing_low=None, swing_high=None,
            resistance=None, support=None,
            cfg=cfg, fees=fees,
        )


# ── from_dict (PR 4) ────────────────────────────────────────────────


def test_fee_config_from_dict_empty():
    f = FeeConfig.from_dict({})
    d = FeeConfig()
    assert f.taker_fee_pct == d.taker_fee_pct
    assert f.maker_fee_pct == d.maker_fee_pct


def test_fee_config_from_dict_overrides():
    f = FeeConfig.from_dict({
        "taker_pct": 0.001,
        "maker_pct": 0.0005,
        "slippage_pct": 0.0001,
        "entry_is_taker": False,
        "tp_is_maker": True,
    })
    assert f.taker_fee_pct == pytest.approx(0.001)
    assert f.maker_fee_pct == pytest.approx(0.0005)
    assert f.slippage_pct == pytest.approx(0.0001)
    assert f.entry_is_taker is False
    assert f.tp_is_maker is True


def test_fee_config_from_dict_none_returns_defaults():
    f = FeeConfig.from_dict(None)
    d = FeeConfig()
    assert f == d


def test_strategy_config_from_dict_empty():
    s = StrategyConfig.from_dict({})
    d = StrategyConfig()
    assert s == d


def test_strategy_config_from_dict_partial_override():
    s = StrategyConfig.from_dict({
        "min_stop_atr_mult": 0.3,
        "tp1_r_target": 0.7,
        "htf_minutes": 30,
    })
    assert s.min_stop_atr_mult == pytest.approx(0.3)
    assert s.tp1_r_target == pytest.approx(0.7)
    assert s.htf_minutes == 30
    # Other fields fall back to defaults
    d = StrategyConfig()
    assert s.max_stop_atr_mult == d.max_stop_atr_mult
    assert s.tp2_r_default == d.tp2_r_default
    assert s.tp3_r_target == d.tp3_r_target


def test_strategy_config_from_dict_none_returns_defaults():
    s = StrategyConfig.from_dict(None)
    d = StrategyConfig()
    assert s == d
