"""Tests for swing detection helpers used by adaptive SL placement."""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies.swing import get_recent_swing
from trading_corp.data.live_bar_cache import Bar


def _bar(
    price: float,
    *,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    return Bar(
        ts_ms=0,
        open=price,
        high=high if high is not None else price,
        low=low if low is not None else price,
        close=price,
        volume=1.0,
    )


def test_flat_series_has_no_swings():
    bars = [_bar(100.0) for _ in range(20)]
    assert get_recent_swing(bars, 19, side="high") is None
    assert get_recent_swing(bars, 19, side="low") is None


def test_detects_obvious_swing_high():
    prices = [100.0] * 10 + [110.0] + [100.0] * 9
    bars = [_bar(p) for p in prices]
    assert get_recent_swing(bars, 15, side="high") == pytest.approx(110.0)


def test_unconfirmed_swing_excluded_until_right_buffer_satisfied():
    prices = [100.0] * 10 + [110.0] + [100.0] * 9
    bars = [_bar(p) for p in prices]
    assert get_recent_swing(bars, 10, side="high") is None
    assert get_recent_swing(bars, 11, side="high") is None
    assert get_recent_swing(bars, 12, side="high") == pytest.approx(110.0)


def test_max_lookback_excludes_old_swings():
    prices = [100.0] * 5 + [110.0] + [100.0] * 15
    bars = [_bar(p) for p in prices]
    assert get_recent_swing(bars, 20, side="high", max_lookback=10) is None
    assert get_recent_swing(bars, 20, side="high", max_lookback=20) == pytest.approx(110.0)


def test_no_lookahead_invariant():
    prices_with_future = [100.0] * 10 + [110.0] + [100.0] * 9 + [200.0]
    prices_without_future = [100.0] * 10 + [110.0] + [100.0] * 9
    bars_a = [_bar(p) for p in prices_with_future]
    bars_b = [_bar(p) for p in prices_without_future]
    assert get_recent_swing(bars_a, 15, side="high") == get_recent_swing(bars_b, 15, side="high")


def test_returns_most_recent_when_multiple_swings_in_window():
    prices = [100.0] * 5 + [108.0] + [100.0] * 5 + [115.0] + [100.0] * 5
    bars = [_bar(p) for p in prices]
    assert get_recent_swing(bars, 16, side="high") == pytest.approx(115.0)


def test_swing_low_side_mirrors_high():
    bars = [_bar(100.0, low=90.0 if i == 10 else 100.0) for i in range(20)]
    assert get_recent_swing(bars, 15, side="low") == pytest.approx(90.0)
    assert get_recent_swing(bars, 11, side="low") is None


def test_invalid_side_raises():
    bars = [_bar(100.0) for _ in range(10)]
    with pytest.raises(ValueError):
        get_recent_swing(bars, 5, side="sideways")  # type: ignore[arg-type]


def test_out_of_range_idx_returns_none():
    bars = [_bar(100.0) for _ in range(10)]
    assert get_recent_swing(bars, -1, side="high") is None
    assert get_recent_swing(bars, 100, side="high") is None
