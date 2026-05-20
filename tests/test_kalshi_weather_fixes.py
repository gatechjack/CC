"""Tests for the 2026-05-16 kalshi_weather_arb bug fixes.

Two bug-fix surfaces:

  1. `_parse_target_time` no longer trusts `expected_expiration_time`
     for daily HIGH/LOW markets. Kalshi's expiration is the day AFTER
     the weather target — using it caused systematic 1-day-off forecast
     lookups (~$300 net loss observed in production).

  2. `apply_bucket_guard` (new pure function in `_weather_math.py`)
     refuses to bet NO when our own forecast is inside/on-the-right-side
     of the bucket. Mirrors: refuses YES when our forecast is outside.
     Closes the σ-vs-bucket-width mismatch that produced 16 losing
     trades for ~$117 in production.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies._weather_math import (
    BucketGuardResult,
    apply_bucket_guard,
    apply_entry_price_floor,
)
from trading_corp.agents.strategies.kalshi_weather_arb import _parse_target_time


# ─── _parse_target_time tests ───────────────────────────────────────────


class _FakeMarket:
    """Minimal stand-in for the Kalshi MarketModel surface the parser uses."""
    def __init__(
        self,
        expected_expiration_time: str | None = "2026-05-16T14:00:00Z",
        expiration_time: str | None = None,
        close_time: str | None = "2026-05-16T06:59:00Z",
    ) -> None:
        self.expected_expiration_time = expected_expiration_time
        self.expiration_time = expiration_time
        self.close_time = close_time


@pytest.mark.parametrize(
    "ticker,expected_date",
    [
        # Daily HIGH variants. ALL must parse to the TICKER date, NOT the
        # expiration date (the off-by-one bug fix).
        ("KXHIGHDEN-26MAY15-B82.5", "2026-05-15"),
        ("KXHIGHCHI-26MAY15-B77.5", "2026-05-15"),
        ("KXHIGHLAX-26MAY15-T87", "2026-05-15"),
        # T-prefix city codes (KXHIGHTBOS, KXHIGHTMIN, KXLOWT...) — same
        # daily date format, must parse identically.
        ("KXHIGHTBOS-26MAY15-T56", "2026-05-15"),
        ("KXHIGHTMIN-26MAY15-T90", "2026-05-15"),
        ("KXLOWTSEA-26MAY15-T41", "2026-05-15"),
        ("KXLOWTATL-26MAY15-T53", "2026-05-15"),
        # Different daily dates.
        ("KXHIGHDEN-26JAN01-B30.5", "2026-01-01"),
        ("KXHIGHDEN-26DEC31-B45.5", "2026-12-31"),
    ],
)
def test_parse_target_time_daily_uses_ticker_date(ticker, expected_date):
    """Bug B regression: parser must NOT return expected_expiration_time
    (2026-05-16) when the ticker carries a different date."""
    fake = _FakeMarket(expected_expiration_time="2026-05-16T14:00:00Z")
    result = _parse_target_time("rules", ticker, fake)
    assert result is not None
    assert expected_date in result, (
        f"Ticker {ticker} expected date {expected_date}, got {result}. "
        "Off-by-one-day bug regression."
    )
    # Also assert we're NOT just echoing the expiration:
    assert "2026-05-16T14:00:00Z" not in result


def test_parse_target_time_hourly_uses_ticker_hour():
    """Hourly TEMP markets carry YYMMMDDhh — must parse hour from ticker,
    converting Eastern Time to UTC. MAY is EDT (UTC-4), so hour 13 ET → 17 UTC."""
    fake = _FakeMarket(expected_expiration_time="2026-05-16T14:00:00Z")
    result = _parse_target_time(
        "rules", "KXTEMPNYCH-26MAY1513-T70", fake,
    )
    assert result is not None
    assert "2026-05-15T17:00" in result
    # And NOT the expiration time:
    assert "2026-05-16T14:00:00Z" not in result


def test_parse_target_time_winter_uses_est():
    """November-February tickers use EST offset (UTC-5)."""
    fake = _FakeMarket()
    result = _parse_target_time(
        "rules", "KXTEMPNYCH-26JAN1513-T20", fake,
    )
    assert result is not None
    # Hour 13 EST → 18 UTC
    assert "2026-01-15T18:00" in result


def test_parse_target_time_fallback_when_ticker_unparseable():
    """Unrecognized ticker shape falls back to expiration with a warning.
    Caller should treat that as 'wrong day possible'."""
    fake = _FakeMarket(expected_expiration_time="2026-05-16T14:00:00Z")
    result = _parse_target_time("rules", "NO-DATE-HERE", fake)
    assert result == "2026-05-16T14:00:00Z"


def test_parse_target_time_returns_none_when_no_source():
    """Unrecognized ticker AND missing expiration → None (caller skips)."""
    fake = _FakeMarket(
        expected_expiration_time=None, expiration_time=None, close_time=None,
    )
    result = _parse_target_time("rules", "NO-DATE-HERE", fake)
    assert result is None


# ─── apply_bucket_guard tests ───────────────────────────────────────────


@pytest.mark.parametrize(
    "direction,forecast,threshold,threshold_high,proposed,implied,"
    "expect_outcome,expect_action",
    [
        # ─── between markets ─────────────────────────────────────────
        # Forecast in bucket, model proposed NO → must flip to YES.
        (
            "between", 82.0, 82.0, 83.0, "no", 0.51,
            "yes", "flipped_no_to_yes",
        ),
        # Forecast in bucket, but implied is too expensive → skip.
        (
            "between", 82.0, 82.0, 83.0, "no", 0.85,
            None, "block_no_yes_too_expensive",
        ),
        # Forecast OUTSIDE bucket, model proposed YES → skip (smearing).
        (
            "between", 75.0, 82.0, 83.0, "yes", 0.05,
            None, "block_yes_forecast_outside",
        ),
        # Forecast outside bucket, model proposed NO → natural path.
        (
            "between", 75.0, 82.0, 83.0, "no", 0.51,
            "no", None,
        ),
        # Forecast in bucket, model proposed YES → natural path.
        (
            "between", 82.5, 82.0, 83.0, "yes", 0.20,
            "yes", None,
        ),
        # Edge: forecast EXACTLY on bucket boundary (counts as inside).
        (
            "between", 82.0, 82.0, 83.0, "no", 0.51,
            "yes", "flipped_no_to_yes",
        ),
        # ─── greater (T-ticker) ──────────────────────────────────────
        # Forecast > threshold (predicts YES), model said NO → flip.
        (
            "greater", 95.0, 90.0, None, "no", 0.40,
            "yes", "flipped_no_to_yes",
        ),
        # Forecast < threshold (predicts NO), model said YES → skip.
        (
            "greater", 86.0, 90.0, None, "yes", 0.04,
            None, "block_yes_forecast_outside",
        ),
        # Forecast < threshold (predicts NO), model said NO → natural.
        (
            "greater", 86.0, 90.0, None, "no", 0.04,
            "no", None,
        ),
        # Forecast == threshold (NOT strictly greater → predicts NO).
        (
            "greater", 90.0, 90.0, None, "yes", 0.05,
            None, "block_yes_forecast_outside",
        ),
        # ─── less (T-ticker) ─────────────────────────────────────────
        # Forecast < threshold (predicts YES), model said NO → flip.
        (
            "less", 38.0, 41.0, None, "no", 0.30,
            "yes", "flipped_no_to_yes",
        ),
        # Forecast > threshold (predicts NO), model said YES → skip.
        # This is the KXLOWTSEA-T41 fail from prod.
        (
            "less", 44.0, 41.0, None, "yes", 0.04,
            None, "block_yes_forecast_outside",
        ),
        # Forecast > threshold (predicts NO), model said NO → natural.
        (
            "less", 44.0, 41.0, None, "no", 0.04,
            "no", None,
        ),
    ],
)
def test_apply_bucket_guard(
    direction, forecast, threshold, threshold_high, proposed, implied,
    expect_outcome, expect_action,
):
    result = apply_bucket_guard(
        direction=direction,
        forecast_temp_f=forecast,
        threshold_f=threshold,
        threshold_high_f=threshold_high,
        proposed_outcome=proposed,
        implied_yes=implied,
    )
    assert result.outcome == expect_outcome
    assert result.action == expect_action
    if expect_outcome is None:
        assert result.skip_reason is not None and len(result.skip_reason) > 0
    else:
        assert result.skip_reason is None


def test_apply_bucket_guard_unknown_direction_passes_through():
    """Unknown direction string → natural path, no guard intervention."""
    result = apply_bucket_guard(
        direction="weird",
        forecast_temp_f=80.0,
        threshold_f=82.0,
        threshold_high_f=None,
        proposed_outcome="no",
        implied_yes=0.5,
    )
    assert result.outcome == "no"
    assert result.action is None
    assert result.skip_reason is None


def test_apply_bucket_guard_custom_flip_ceiling():
    """Ceiling controls when we flip vs block. At ceiling=0.30, an
    implied of 0.40 should block; at ceiling=0.50, the same should flip."""
    args = dict(
        direction="between",
        forecast_temp_f=82.0,
        threshold_f=82.0,
        threshold_high_f=83.0,
        proposed_outcome="no",
        implied_yes=0.40,
    )
    # With low ceiling — too expensive to flip, must block.
    res_tight = apply_bucket_guard(**args, flip_yes_implied_ceiling=0.30)
    assert res_tight.outcome is None
    assert res_tight.action == "block_no_yes_too_expensive"

    # With higher ceiling — flip allowed.
    res_loose = apply_bucket_guard(**args, flip_yes_implied_ceiling=0.50)
    assert res_loose.outcome == "yes"
    assert res_loose.action == "flipped_no_to_yes"


# ─── Regression: prod-observed failures must now be saved ──────────────


def test_regression_denver_b82_5_loss_now_saved():
    """The losing Denver bet from 2026-05-15: forecast=82, bucket [82,83],
    model proposed NO, implied=0.51. Pre-fix: bet NO and lost $5.67.
    Post-fix: flip to YES at $0.49 (1 - 0.51). The trade still might
    lose, but at least we'd be on the right side of our own forecast."""
    result = apply_bucket_guard(
        direction="between",
        forecast_temp_f=82.0,
        threshold_f=82.0,
        threshold_high_f=83.0,
        proposed_outcome="no",
        implied_yes=0.51,
    )
    assert result.outcome == "yes"
    assert result.action == "flipped_no_to_yes"


def test_regression_seattle_low_t41_loss_now_blocked():
    """The losing Seattle low bet: forecast=44 (predicts >41), market
    "Will low be <41?", we bet YES (which means '<41'). Forecast
    contradicts the YES side. Pre-fix: bet YES anyway, lost.
    Post-fix: block as σ-smearing artifact."""
    result = apply_bucket_guard(
        direction="less",
        forecast_temp_f=44.0,
        threshold_f=41.0,
        threshold_high_f=None,
        proposed_outcome="yes",
        implied_yes=0.04,
    )
    assert result.outcome is None
    assert result.action == "block_yes_forecast_outside"


def test_regression_minneapolis_high_t90_loss_now_blocked():
    """KXHIGHTMIN-T90: forecast=86 (predicts ≤86, NOT >90), market
    "Will high be >90?", we bet YES. Forecast contradicts YES side.
    Pre-fix: bet YES, lost. Post-fix: block."""
    result = apply_bucket_guard(
        direction="greater",
        forecast_temp_f=86.0,
        threshold_f=90.0,
        threshold_high_f=None,
        proposed_outcome="yes",
        implied_yes=0.04,
    )
    assert result.outcome is None
    assert result.action == "block_yes_forecast_outside"


# ─── apply_entry_price_floor tests ─────────────────────────────────────


@pytest.mark.parametrize(
    "outcome,share_price,expect_skip",
    [
        # YES side, default min_yes_entry = 0.10, inclusive comparator
        ("yes", 0.05, True),   # cheap YES -> skip
        ("yes", 0.10, True),   # boundary inclusive -> skip
        ("yes", 0.12, False),  # just above floor -> pass
        ("yes", 0.50, False),  # well above -> pass
        # NO side, default min_no_entry = 0.50, STRICT comparator
        # (boundary stays in observed [0.50, 0.60) band rather than skip)
        ("no",  0.40, True),   # cheap NO -> skip
        ("no",  0.50, False),  # boundary strict -> pass
        ("no",  0.55, False),  # just above floor -> pass
        ("no",  0.85, False),  # well above -> pass
    ],
)
def test_apply_entry_price_floor_defaults(outcome, share_price, expect_skip):
    skip_reason = apply_entry_price_floor(
        outcome=outcome, share_price=share_price,
    )
    if expect_skip:
        assert skip_reason is not None
        assert "entry_below_floor" in skip_reason
        assert outcome in skip_reason
    else:
        assert skip_reason is None


def test_apply_entry_price_floor_custom_thresholds():
    """Caller-supplied thresholds override defaults on either side."""
    # Tighter YES floor — bet at $0.05 now passes.
    assert apply_entry_price_floor(
        outcome="yes", share_price=0.05,
        min_yes_entry=0.02, min_no_entry=0.50,
    ) is None
    # Looser NO floor — bet at $0.30 now passes.
    assert apply_entry_price_floor(
        outcome="no", share_price=0.30,
        min_yes_entry=0.10, min_no_entry=0.20,
    ) is None
    # Tighter NO floor — bet at $0.55 now skips.
    res = apply_entry_price_floor(
        outcome="no", share_price=0.55,
        min_yes_entry=0.10, min_no_entry=0.70,
    )
    assert res is not None
    assert "entry_below_floor" in res
