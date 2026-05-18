"""Tests for the BitUnix 5-factor confluence gate (Phase A).

Coverage:
  * Each factor in isolation — pass + at least one fail mode +
    `None`-input handling.
  * Threshold edge cases — score=5, score=3 (boundary), score=2
    (boundary - 1), score=0.
  * Disabled bypass.
  * Unknown side rejection.
  * `ConfluenceGateConfig.from_dict` happy path + unknown-key warning.
"""
from __future__ import annotations

import logging

import pytest

from trading_corp.agents.strategies.bitunix_confluence_gate import (
    ConfluenceGateConfig,
    CvdFactorConfig,
    EmaFactorConfig,
    GateDecision,
    GateInputs,
    VolatilityFactorConfig,
    VolumeZFactorConfig,
    VwapFactorConfig,
    evaluate_confluence_gate,
    _factor_cvd,
    _factor_ema_alignment,
    _factor_vwap,
    _factor_volatility,
    _factor_volume_z,
)


# ─── helpers ────────────────────────────────────────────────────────────


def _inputs(
    *,
    # Factor 1 (EMA 15m). v1.1: all three slopes required.
    ema_8: float | None = 105.0, ema_21: float | None = 102.0,
    ema_50: float | None = 99.0,
    ema_8_slope: float | None = 0.5,
    ema_21_slope: float | None = 0.3,
    ema_50_slope: float | None = 0.1,
    # Factor 2 (VWAP)
    price: float | None = 110.0, session_vwap: float | None = 100.0,
    prior_day_vwap: float | None = 99.0,
    # Factor 3 (Volatility 5m)
    atr: float | None = 1.5, atr_sma: float | None = 1.0,
    bb_width: float | None = 0.02, bb_pct_rank: float | None = 0.55,
    # Factor 4 (CVD)
    cvd_slope: float | None = 100.0, cvd_fallback: bool = True,
    # Factor 5 (Volume z)
    volume_z: float | None = 1.5,
) -> GateInputs:
    """All-passing buy-side baseline. Override fields to fail a factor."""
    return GateInputs(
        ema_8_15m=ema_8, ema_21_15m=ema_21, ema_50_15m=ema_50,
        ema_8_15m_slope=ema_8_slope,
        ema_21_15m_slope=ema_21_slope,
        ema_50_15m_slope=ema_50_slope,
        current_price=price, session_vwap=session_vwap,
        prior_day_session_vwap=prior_day_vwap,
        atr_5m=atr, atr_5m_sma=atr_sma,
        bb_width_5m=bb_width, bb_width_5m_pct_rank=bb_pct_rank,
        cvd_slope=cvd_slope, cvd_fallback_used=cvd_fallback,
        volume_z=volume_z,
    )


def _config(*, enabled: bool = True, min_gate_score: int = 3) -> ConfluenceGateConfig:
    return ConfluenceGateConfig(enabled=enabled, min_gate_score=min_gate_score)


# ─── Factor 1: EMA alignment ────────────────────────────────────────────


def test_ema_alignment_buy_passes_when_stacked_up_with_positive_slope():
    r = _factor_ema_alignment("buy", _inputs(), EmaFactorConfig())
    assert r.passed is True


def test_ema_alignment_buy_fails_when_emas_misaligned():
    r = _factor_ema_alignment(
        "buy", _inputs(ema_8=98.0, ema_21=102.0, ema_50=99.0), EmaFactorConfig(),
    )
    assert r.passed is False
    assert r.detail["ema_8"] == 98.0


def test_ema_alignment_buy_fails_when_slope_negative_even_if_stacked():
    r = _factor_ema_alignment(
        "buy", _inputs(ema_8_slope=-0.1), EmaFactorConfig(),
    )
    assert r.passed is False


def test_ema_alignment_sell_passes_when_stacked_down_with_negative_slope():
    # v1.1: ALL three slopes must be negative for short. Original fixture
    # only set ema_8_slope and inherited positive defaults for the other
    # two — that worked under v1.0 (ema_8 only) but is now incorrect.
    inp = _inputs(
        ema_8=95.0, ema_21=98.0, ema_50=100.0,
        ema_8_slope=-0.5, ema_21_slope=-0.3, ema_50_slope=-0.1,
    )
    r = _factor_ema_alignment("sell", inp, EmaFactorConfig())
    assert r.passed is True


def test_ema_alignment_missing_inputs_fail_closed():
    r = _factor_ema_alignment("buy", _inputs(ema_8=None), EmaFactorConfig())
    assert r.passed is False
    assert "missing" in r.detail["reason"]


# ─── Factor 1 v1.1 — all-three-slopes (spec fix) ───────────────────────


def test_factor_ema_alignment_requires_all_three_slopes_long():
    """v1.1 spec fix: long pass requires all three EMA slopes positive.

    Stacking is correct (e8 > e21 > e50) and ema_8 slope is positive,
    but ema_50 slope is flat. Original impl passed (only checked
    slope_8); fixed impl must FAIL.
    """
    from trading_corp.agents.strategies.bitunix_confluence_gate import GateInputs
    inp = GateInputs(
        ema_8_15m=105.0, ema_21_15m=102.0, ema_50_15m=99.0,
        ema_8_15m_slope=0.5, ema_21_15m_slope=0.3, ema_50_15m_slope=0.0,
        current_price=110.0, session_vwap=100.0, prior_day_session_vwap=99.0,
        atr_5m=1.5, atr_5m_sma=1.0,
        bb_width_5m=0.02, bb_width_5m_pct_rank=0.55,
        cvd_slope=100.0, cvd_fallback_used=True,
        volume_z=1.5,
    )
    r = _factor_ema_alignment("buy", inp, EmaFactorConfig())
    assert r.passed is False, (
        "When ema_50 slope is flat, all-three-slopes check must FAIL "
        "for long. If this passes, the factor still only checks slope_8."
    )


def test_factor_ema_alignment_requires_all_three_slopes_short():
    """Mirror of the long-side test for short positions."""
    from trading_corp.agents.strategies.bitunix_confluence_gate import GateInputs
    inp = GateInputs(
        ema_8_15m=95.0, ema_21_15m=98.0, ema_50_15m=100.0,
        ema_8_15m_slope=-0.5, ema_21_15m_slope=-0.3, ema_50_15m_slope=0.0,
        current_price=90.0, session_vwap=100.0, prior_day_session_vwap=99.0,
        atr_5m=1.5, atr_5m_sma=1.0,
        bb_width_5m=0.02, bb_width_5m_pct_rank=0.55,
        cvd_slope=-100.0, cvd_fallback_used=True,
        volume_z=1.5,
    )
    r = _factor_ema_alignment("sell", inp, EmaFactorConfig())
    assert r.passed is False, (
        "When ema_50 slope is flat, all-three-slopes check must FAIL "
        "for short. If this passes, the factor still only checks slope_8."
    )


def test_factor_ema_alignment_passes_when_all_three_slopes_aligned():
    """Sanity / regression: all three EMAs stacked correctly AND all
    three slopes correctly directional → factor passes.
    """
    from trading_corp.agents.strategies.bitunix_confluence_gate import GateInputs
    inp = GateInputs(
        ema_8_15m=105.0, ema_21_15m=102.0, ema_50_15m=99.0,
        ema_8_15m_slope=0.5, ema_21_15m_slope=0.3, ema_50_15m_slope=0.1,
        current_price=110.0, session_vwap=100.0, prior_day_session_vwap=99.0,
        atr_5m=1.5, atr_5m_sma=1.0,
        bb_width_5m=0.02, bb_width_5m_pct_rank=0.55,
        cvd_slope=100.0, cvd_fallback_used=True,
        volume_z=1.5,
    )
    r = _factor_ema_alignment("buy", inp, EmaFactorConfig())
    assert r.passed is True


# ─── Factor 2: VWAP ─────────────────────────────────────────────────────


def test_vwap_buy_passes_above_both_vwaps():
    r = _factor_vwap("buy", _inputs(), VwapFactorConfig())
    assert r.passed is True


def test_vwap_buy_fails_when_below_session_vwap():
    r = _factor_vwap(
        "buy", _inputs(price=99.5, session_vwap=100.0), VwapFactorConfig(),
    )
    assert r.passed is False


def test_vwap_buy_fails_when_below_prior_day_vwap_even_if_above_session():
    r = _factor_vwap(
        "buy", _inputs(price=101.0, session_vwap=100.0, prior_day_vwap=102.0),
        VwapFactorConfig(),
    )
    assert r.passed is False


def test_vwap_sell_passes_below_both_vwaps():
    r = _factor_vwap(
        "sell",
        _inputs(price=90.0, session_vwap=100.0, prior_day_vwap=99.0),
        VwapFactorConfig(),
    )
    assert r.passed is True


def test_vwap_missing_prior_day_vwap_fails_closed():
    """Boot warm-up case: prior-day VWAP needs a full session of data."""
    r = _factor_vwap(
        "buy", _inputs(prior_day_vwap=None), VwapFactorConfig(),
    )
    assert r.passed is False


# ─── Factor 3: Volatility ───────────────────────────────────────────────


def test_volatility_passes_when_atr_above_sma_and_bb_above_min_pct():
    r = _factor_volatility("buy", _inputs(), VolatilityFactorConfig())
    assert r.passed is True


def test_volatility_fails_when_atr_below_sma():
    r = _factor_volatility(
        "buy", _inputs(atr=0.5, atr_sma=1.0), VolatilityFactorConfig(),
    )
    assert r.passed is False


def test_volatility_fails_when_bb_in_bottom_decile():
    r = _factor_volatility(
        "buy", _inputs(bb_pct_rank=0.05), VolatilityFactorConfig(),
    )
    assert r.passed is False


def test_volatility_is_symmetric_across_sides():
    """Same inputs, different sides — both should pass (range/chop is bad for both)."""
    buy = _factor_volatility("buy", _inputs(), VolatilityFactorConfig())
    sell = _factor_volatility("sell", _inputs(), VolatilityFactorConfig())
    assert buy.passed is True
    assert sell.passed is True


def test_volatility_missing_inputs_fail_closed():
    r = _factor_volatility(
        "buy", _inputs(bb_pct_rank=None), VolatilityFactorConfig(),
    )
    assert r.passed is False


# ─── Factor 4: CVD ──────────────────────────────────────────────────────


def test_cvd_buy_passes_when_slope_positive():
    r = _factor_cvd("buy", _inputs(cvd_slope=42.0), CvdFactorConfig())
    assert r.passed is True


def test_cvd_buy_fails_when_slope_negative():
    r = _factor_cvd("buy", _inputs(cvd_slope=-42.0), CvdFactorConfig())
    assert r.passed is False


def test_cvd_sell_passes_when_slope_negative():
    r = _factor_cvd("sell", _inputs(cvd_slope=-42.0), CvdFactorConfig())
    assert r.passed is True


def test_cvd_fallback_flag_surfaces_in_detail():
    r = _factor_cvd("buy", _inputs(cvd_fallback=True), CvdFactorConfig())
    assert r.detail["fallback_used"] is True


def test_cvd_missing_slope_fails_closed():
    r = _factor_cvd("buy", _inputs(cvd_slope=None), CvdFactorConfig())
    assert r.passed is False


# ─── Factor 5: Volume z-score ───────────────────────────────────────────


def test_volume_z_passes_at_threshold():
    r = _factor_volume_z("buy", _inputs(volume_z=1.0), VolumeZFactorConfig())
    assert r.passed is True


def test_volume_z_fails_below_min():
    r = _factor_volume_z("buy", _inputs(volume_z=0.5), VolumeZFactorConfig())
    assert r.passed is False


def test_volume_z_missing_fails_closed():
    r = _factor_volume_z("buy", _inputs(volume_z=None), VolumeZFactorConfig())
    assert r.passed is False


# ─── Threshold integration tests (score=5, 3, 2, 0) ─────────────────────


def test_threshold_score_5_passes():
    r = evaluate_confluence_gate(side="buy", inputs=_inputs(), config=_config())
    assert r.decision == GateDecision.PASS
    assert r.score == 5
    assert r.threshold == 3
    assert len(r.factors) == 5


def test_threshold_score_3_passes_at_boundary():
    """Three pass, two fail (volatility + volume_z) → score=3 ≥ threshold=3."""
    inp = _inputs(atr=0.5, atr_sma=1.0, volume_z=0.0)
    r = evaluate_confluence_gate(side="buy", inputs=inp, config=_config())
    assert r.decision == GateDecision.PASS
    assert r.score == 3


def test_threshold_score_2_rejects_below_boundary():
    """Two pass (VWAP + CVD), three fail → score=2 < threshold=3."""
    inp = _inputs(
        ema_8=98.0, ema_21=102.0, ema_50=99.0,    # EMA fail
        atr=0.5, atr_sma=1.0,                      # vol fail
        volume_z=0.0,                              # volume_z fail
    )
    r = evaluate_confluence_gate(side="buy", inputs=inp, config=_config())
    assert r.decision == GateDecision.REJECT
    assert r.score == 2


def test_threshold_score_0_rejects():
    """All inputs `None` → all factors fail closed → score=0."""
    inp = GateInputs(
        ema_8_15m=None, ema_21_15m=None, ema_50_15m=None,
        ema_8_15m_slope=None, ema_21_15m_slope=None, ema_50_15m_slope=None,
        current_price=None, session_vwap=None, prior_day_session_vwap=None,
        atr_5m=None, atr_5m_sma=None,
        bb_width_5m=None, bb_width_5m_pct_rank=None,
        cvd_slope=None, cvd_fallback_used=True,
        volume_z=None,
    )
    r = evaluate_confluence_gate(side="buy", inputs=inp, config=_config())
    assert r.decision == GateDecision.REJECT
    assert r.score == 0


def test_min_gate_score_tunable():
    """All five factors fail except CVD; min_gate_score=1 should PASS."""
    inp = _inputs(
        ema_8=98.0, ema_21=102.0, ema_50=99.0,
        price=99.0, session_vwap=100.0,            # VWAP fail
        atr=0.5, atr_sma=1.0,                       # vol fail
        volume_z=0.0,                               # volume_z fail
    )
    r = evaluate_confluence_gate(
        side="buy", inputs=inp,
        config=_config(min_gate_score=1),
    )
    assert r.decision == GateDecision.PASS
    assert r.score == 1


# ─── Disabled + side validation ────────────────────────────────────────


def test_gate_disabled_returns_disabled():
    """When `enabled=False` the gate is a no-op pass-through."""
    r = evaluate_confluence_gate(
        side="buy", inputs=_inputs(),
        config=ConfluenceGateConfig(enabled=False),
    )
    assert r.decision == GateDecision.DISABLED
    assert r.factors == ()


def test_unknown_side_rejects():
    r = evaluate_confluence_gate(
        side="hold", inputs=_inputs(), config=_config(),
    )
    assert r.decision == GateDecision.REJECT
    assert r.factors == ()
    assert "invalid side" in r.reason


def test_empty_side_rejects():
    r = evaluate_confluence_gate(side="", inputs=_inputs(), config=_config())
    assert r.decision == GateDecision.REJECT


# ─── cvd_fallback_used surfaces on result ─────────────────────────────


def test_gate_result_surfaces_cvd_fallback_flag():
    r = evaluate_confluence_gate(
        side="buy", inputs=_inputs(cvd_fallback=True), config=_config(),
    )
    assert r.cvd_fallback_used is True


def test_gate_result_cvd_fallback_flag_propagates_when_disabled():
    """Even in disabled bypass, the flag is surfaced (used by dashboard banner)."""
    r = evaluate_confluence_gate(
        side="buy", inputs=_inputs(cvd_fallback=True),
        config=ConfluenceGateConfig(enabled=False),
    )
    assert r.cvd_fallback_used is True


# ─── ConfluenceGateConfig.from_dict ────────────────────────────────────


def test_from_dict_happy_path_parses_full_block():
    raw = {
        "confluence_gate": {
            "enabled": True,
            "min_gate_score": 4,
            "gate_timeout_minutes": 20,
            "ema_factor": {"periods": [9, 21, 55], "slope_lookback": 7},
            "vwap_factor": {"session_reset_hour_utc": 8},
            "volatility_factor": {
                "atr_period": 21, "atr_sma_period": 60,
                "bb_period": 25, "bb_stdev": 2.5,
                "bb_pct_rank_window": 200,
                "bb_pct_rank_min_excluded_pct": 0.15,
            },
            "cvd_factor": {"slope_window_minutes": 30, "bucket_minutes": 5},
            "volume_z_factor": {"period": 25, "min_z": 1.5},
        },
    }
    c = ConfluenceGateConfig.from_dict(raw)
    assert c.enabled is True
    assert c.min_gate_score == 4
    assert c.gate_timeout_minutes == 20
    assert c.ema_factor.periods == (9, 21, 55)
    assert c.ema_factor.slope_lookback == 7
    assert c.vwap_factor.session_reset_hour_utc == 8
    assert c.volatility_factor.atr_period == 21
    assert c.volatility_factor.bb_pct_rank_min_excluded_pct == 0.15
    assert c.cvd_factor.slope_window_minutes == 30
    assert c.cvd_factor.bucket_minutes == 5
    assert c.volume_z_factor.period == 25
    assert c.volume_z_factor.min_z == 1.5


def test_from_dict_missing_block_returns_disabled_defaults():
    c = ConfluenceGateConfig.from_dict({})
    assert c.enabled is False
    assert c.min_gate_score == 3
    assert c.ema_factor.periods == (8, 21, 50)


def test_from_dict_none_input_returns_defaults():
    c = ConfluenceGateConfig.from_dict(None)
    assert c.enabled is False


def test_from_dict_unknown_top_key_warns(caplog: pytest.LogCaptureFixture):
    """Config typo silent-degradation is a known sharp edge — gate
    must log loudly on unknown YAML keys."""
    raw = {"confluence_gate": {"enabled": True, "min_gate_scor": 3}}  # typo
    with caplog.at_level(logging.WARNING):
        c = ConfluenceGateConfig.from_dict(raw)
    assert c.enabled is True
    assert any("min_gate_scor" in rec.message for rec in caplog.records)


def test_from_dict_unknown_subblock_key_warns(caplog: pytest.LogCaptureFixture):
    raw = {
        "confluence_gate": {
            "enabled": True,
            "ema_factor": {"periods": [8, 21, 50], "slop_lookback": 5},  # typo
        },
    }
    with caplog.at_level(logging.WARNING):
        ConfluenceGateConfig.from_dict(raw)
    assert any("slop_lookback" in rec.message for rec in caplog.records)


# ─── factor ordering in result ─────────────────────────────────────────


def test_factor_results_in_deterministic_order():
    """Audit row depends on stable ordering — pin it."""
    r = evaluate_confluence_gate(side="buy", inputs=_inputs(), config=_config())
    assert [f.name for f in r.factors] == [
        "ema_alignment", "vwap", "volatility", "cvd", "volume_z",
    ]
