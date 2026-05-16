"""Tests for the BitUnix PA validation gate (PR 3a).

Pin every validator path: each validator's pass/fail mapping per side,
the require_all rule, the min_validators_passed soft-fail mode, and
the rush_fall hard-reject guards.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies.btc_accumulator import PriceContext
from trading_corp.agents.strategies.bitunix_pa_validation import (
    PAValidationConfig,
    PAValidationDecision,
    evaluate_pa_validation,
)


# ─── helpers ────────────────────────────────────────────────────────────


def _ctx(
    *,
    above_vwap: bool = True,
    below_vwap: bool = False,
    higher_highs_4h: bool = True,
    lower_lows_4h: bool = False,
    volume_above_avg: bool = True,
    pct_chg_buy: float = 0.0,
    pct_chg_sell: float = 0.0,
) -> PriceContext:
    return PriceContext(
        current_price=100.0,
        pct_change_in_window_sell=pct_chg_sell,
        pct_change_in_window_buy=pct_chg_buy,
        above_session_vwap=above_vwap,
        below_session_vwap=below_vwap,
        higher_highs_4h=higher_highs_4h,
        lower_lows_4h=lower_lows_4h,
        volume_above_20bar_avg=volume_above_avg,
    )


def _config(
    *,
    enabled: bool = True,
    require_all: bool = True,
    min_validators_passed: int = 0,
    rush_fall_enabled: bool = True,
    reject_buy_drop: float = 5.0,
    reject_sell_rise: float = 5.0,
    validators: tuple[str, ...] = (
        "vwap_alignment", "volume_confirmation", "structure_alignment",
    ),
) -> PAValidationConfig:
    return PAValidationConfig(
        enabled=enabled,
        require_all=require_all,
        min_validators_passed=min_validators_passed,
        validators=validators,
        rush_fall_enabled=rush_fall_enabled,
        reject_buy_on_60m_drop_pct=reject_buy_drop,
        reject_sell_on_60m_rise_pct=reject_sell_rise,
    )


# ─── disabled state ─────────────────────────────────────────────────────


def test_disabled_returns_disabled_regardless_of_inputs():
    """When config.enabled=False the gate is a no-op pass-through;
    the caller should not gate any trade on the result."""
    r = evaluate_pa_validation(
        side="buy", price_ctx=_ctx(above_vwap=False, volume_above_avg=False),
        config=_config(enabled=False),
    )
    assert r.decision == PAValidationDecision.DISABLED
    assert r.passed == ()
    assert r.failed == ()


# ─── happy path: all validators pass ────────────────────────────────────


def test_buy_with_all_three_validators_passing_returns_pass():
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(
            above_vwap=True, higher_highs_4h=True, volume_above_avg=True,
        ),
        config=_config(),
    )
    assert r.decision == PAValidationDecision.PASS
    assert set(r.passed) == {
        "vwap_alignment", "volume_confirmation", "structure_alignment",
    }
    assert r.failed == ()


def test_sell_with_all_three_validators_passing_returns_pass():
    r = evaluate_pa_validation(
        side="sell",
        price_ctx=_ctx(
            above_vwap=False, below_vwap=True,
            lower_lows_4h=True, higher_highs_4h=False,
            volume_above_avg=True,
        ),
        config=_config(),
    )
    assert r.decision == PAValidationDecision.PASS


# ─── require_all rule: any failure → reject ─────────────────────────────


def test_require_all_one_failing_validator_rejects():
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(volume_above_avg=False),     # volume fails
        config=_config(require_all=True),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert "volume_confirmation" in r.failed
    assert "vwap_alignment" in r.passed
    assert "structure_alignment" in r.passed


def test_require_all_vwap_misaligned_for_buy_rejects():
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(above_vwap=False),
        config=_config(),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert "vwap_alignment" in r.failed


def test_require_all_structure_disagrees_with_sell_rejects():
    """sell needs lower_lows_4h. higher_highs (default in _ctx) =
    structure mismatch."""
    r = evaluate_pa_validation(
        side="sell",
        price_ctx=_ctx(
            above_vwap=False, below_vwap=True,
            higher_highs_4h=True, lower_lows_4h=False,
        ),
        config=_config(),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert "structure_alignment" in r.failed


# ─── min_validators_passed soft mode ────────────────────────────────────


def test_min_validators_passed_2_allows_one_failure():
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(volume_above_avg=False),     # 2 of 3 pass
        config=_config(require_all=False, min_validators_passed=2),
    )
    assert r.decision == PAValidationDecision.PASS
    assert len(r.passed) == 2
    assert len(r.failed) == 1


def test_min_validators_passed_3_rejects_one_failure():
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(volume_above_avg=False),     # 2 of 3 pass
        config=_config(require_all=False, min_validators_passed=3),
    )
    assert r.decision == PAValidationDecision.REJECT


# ─── rush_fall guards (hard reject independent of validators) ───────────


def test_buy_60min_drop_beyond_threshold_rejects():
    """Even if all 3 validators pass, a >5% adverse move in 60min
    triggers the 'don't catch falling knife' guard."""
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(pct_chg_buy=-6.0),     # 6% drop in 60min
        config=_config(reject_buy_drop=5.0),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert r.rush_fall_triggered == "buy_falling"
    assert "falling knife" in r.reason.lower()


def test_buy_60min_drop_below_threshold_passes():
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(pct_chg_buy=-3.0),     # 3% drop, below 5% threshold
        config=_config(reject_buy_drop=5.0),
    )
    assert r.decision == PAValidationDecision.PASS
    assert r.rush_fall_triggered is None


def test_sell_60min_rise_beyond_threshold_rejects():
    r = evaluate_pa_validation(
        side="sell",
        price_ctx=_ctx(
            above_vwap=False, below_vwap=True,
            higher_highs_4h=False, lower_lows_4h=True,
            pct_chg_sell=6.0,
        ),
        config=_config(reject_sell_rise=5.0),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert r.rush_fall_triggered == "sell_rising"
    assert "rip" in r.reason.lower()


def test_rush_fall_disabled_lets_extreme_moves_pass():
    """If rush_fall_enabled=False, even a 10% adverse move passes
    through (validators still apply)."""
    r = evaluate_pa_validation(
        side="buy",
        price_ctx=_ctx(pct_chg_buy=-10.0),
        config=_config(rush_fall_enabled=False),
    )
    assert r.decision == PAValidationDecision.PASS
    assert r.rush_fall_triggered is None


# ─── invalid input ──────────────────────────────────────────────────────


def test_invalid_side_rejects():
    r = evaluate_pa_validation(
        side="long", price_ctx=_ctx(), config=_config(),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert "invalid side" in r.reason.lower()


def test_unknown_validator_name_treated_as_failed():
    """Unknown validator in YAML = config bug. Fail closed (count as
    failed) rather than silently skip — bad config shouldn't loosen
    the gate."""
    r = evaluate_pa_validation(
        side="buy", price_ctx=_ctx(),
        config=_config(validators=("vwap_alignment", "made_up_validator")),
    )
    assert r.decision == PAValidationDecision.REJECT
    assert "made_up_validator" in r.failed


# ─── from_dict YAML parse ───────────────────────────────────────────────


def test_from_dict_full_yaml_block():
    raw = {
        "pa_validation": {
            "enabled": True,
            "require_all": True,
            "validators": [
                "vwap_alignment", "volume_confirmation", "structure_alignment",
            ],
            "rush_fall_guards": {
                "enabled": True,
                "reject_buy_on_60m_drop_pct": 5.0,
                "reject_sell_on_60m_rise_pct": 5.0,
            },
        },
    }
    cfg = PAValidationConfig.from_dict(raw)
    assert cfg.enabled is True
    assert cfg.require_all is True
    assert len(cfg.validators) == 3
    assert cfg.reject_buy_on_60m_drop_pct == 5.0
    assert cfg.reject_sell_on_60m_rise_pct == 5.0


def test_from_dict_missing_block_yields_disabled_defaults():
    cfg = PAValidationConfig.from_dict({})
    assert cfg.enabled is False
    # Default validators present so `enabled=true` cutover is one-line
    assert "vwap_alignment" in cfg.validators
