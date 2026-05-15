"""Tests for HTF S/R helpers used by adaptive TP placement."""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies.levels import (
    get_htf_levels,
    resample_3m_to_htf,
)
from trading_corp.data.live_bar_cache import Bar


_MS_PER_MIN = 60 * 1000
_BAR_3M_MS = 3 * _MS_PER_MIN


def _bars_3m(
    closes: list[float],
    *,
    start_ts_ms: int = 0,
    high_offset: float = 0.5,
    low_offset: float = 0.5,
) -> list[Bar]:
    """Build aligned 3m bars from a closes list. High/low straddle close."""
    out: list[Bar] = []
    for i, c in enumerate(closes):
        out.append(Bar(
            ts_ms=start_ts_ms + i * _BAR_3M_MS,
            open=c,
            high=c + high_offset,
            low=c - low_offset,
            close=c,
            volume=1.0,
        ))
    return out


def test_resample_15m_groups_five_3m_bars_into_one_htf_bar():
    # 10 aligned 3m bars = 2 complete 15m buckets; last one dropped → 1 HTF bar.
    bars = _bars_3m([100.0] * 10, start_ts_ms=0)
    htf = resample_3m_to_htf(bars, htf_minutes=15)
    assert len(htf) == 1
    assert htf[0].ts_ms == 0
    assert htf[0].volume == pytest.approx(5.0)


def test_resample_drops_in_progress_bucket():
    # 7 3m bars: bucket 0 has 5 bars (complete), bucket 1 has 2 bars
    # (in-progress). After dropping last bucket → 1 HTF bar.
    bars = _bars_3m([100.0] * 7, start_ts_ms=0)
    htf = resample_3m_to_htf(bars, htf_minutes=15)
    assert len(htf) == 1
    assert htf[0].ts_ms == 0


def test_resample_empty_inputs():
    assert resample_3m_to_htf([], htf_minutes=15) == []
    bars = _bars_3m([100.0] * 3, start_ts_ms=0)
    assert resample_3m_to_htf(bars, htf_minutes=15) == []


def test_resample_15m_open_high_low_close_correct():
    # Build 5 3m bars with varying highs/lows; check OHLCV aggregation.
    bars = [
        Bar(ts_ms=0,        open=100.0, high=101.0, low=99.0,  close=100.5, volume=1.0),
        Bar(ts_ms=_BAR_3M_MS,    open=100.5, high=102.0, low=99.5,  close=101.5, volume=2.0),
        Bar(ts_ms=2*_BAR_3M_MS,  open=101.5, high=103.0, low=100.0, close=102.0, volume=3.0),
        Bar(ts_ms=3*_BAR_3M_MS,  open=102.0, high=102.5, low=98.0,  close=99.0,  volume=4.0),
        Bar(ts_ms=4*_BAR_3M_MS,  open=99.0,  high=100.0, low=97.0,  close=98.0,  volume=5.0),
        # Bar in next bucket (will be dropped as in-progress)
        Bar(ts_ms=5*_BAR_3M_MS,  open=98.0,  high=99.0,  low=97.5,  close=98.5,  volume=1.0),
    ]
    htf = resample_3m_to_htf(bars, htf_minutes=15)
    assert len(htf) == 1
    h = htf[0]
    assert h.open == pytest.approx(100.0)
    assert h.high == pytest.approx(103.0)
    assert h.low == pytest.approx(97.0)
    assert h.close == pytest.approx(98.0)
    assert h.volume == pytest.approx(15.0)


def test_get_htf_levels_finds_resistance_above():
    # 30 3m bars = 6 complete 15m buckets after dropping last. Inject a peak
    # in bucket 2 (3m indices 10-14): high jumps to 110 at index 12.
    closes = [100.0] * 30
    bars = _bars_3m(closes, start_ts_ms=0, high_offset=0.5, low_offset=0.5)
    bars[12] = Bar(ts_ms=bars[12].ts_ms, open=100, high=110.0, low=99.5, close=100, volume=1.0)
    res, sup = get_htf_levels(bars, current_idx=29, htf_minutes=15, n=2)
    assert res == pytest.approx(110.0)
    # No support below 100 in this fixture (all lows ~99.5)
    assert sup is None


def test_get_htf_levels_finds_support_below():
    closes = [100.0] * 30
    bars = _bars_3m(closes, start_ts_ms=0)
    bars[12] = Bar(ts_ms=bars[12].ts_ms, open=100, high=100.5, low=90.0, close=100, volume=1.0)
    res, sup = get_htf_levels(bars, current_idx=29, htf_minutes=15, n=2)
    assert sup == pytest.approx(90.0)
    assert res is None


def test_get_htf_levels_returns_nearest_in_price_not_most_recent():
    # 50 3m bars → 10 buckets / 9 HTF after drop; n=2 inspects HTF indices
    # 2-6. Peaks at HTF bucket 2 (3m idx 10-14) high=105 and HTF bucket 5
    # (3m idx 25-29) high=115. Both above current_price=100; nearest in
    # price (105) wins, not most recent (115).
    closes = [100.0] * 50
    bars = _bars_3m(closes, start_ts_ms=0)
    bars[12] = Bar(ts_ms=bars[12].ts_ms, open=100, high=105.0, low=99.5, close=100, volume=1.0)
    bars[27] = Bar(ts_ms=bars[27].ts_ms, open=100, high=115.0, low=99.5, close=100, volume=1.0)
    res, _ = get_htf_levels(bars, current_idx=49, htf_minutes=15, n=2)
    assert res == pytest.approx(105.0)


def test_get_htf_levels_no_lookahead_invariant():
    # Lookahead-bug test: trailing peak (3m idx 32, HTF bucket 6) at 105 is
    # CLOSER in price than the in-slice peak (3m idx 12, HTF bucket 2) at
    # 110. If the function failed to slice, the trailing peak would change
    # the nearest-above result.
    closes = [100.0] * 50
    bars_full = _bars_3m(closes, start_ts_ms=0)
    bars_full[12] = Bar(ts_ms=bars_full[12].ts_ms, open=100, high=110.0, low=99.5, close=100, volume=1.0)
    bars_full[32] = Bar(ts_ms=bars_full[32].ts_ms, open=100, high=105.0, low=99.5, close=100, volume=1.0)

    res_a, _ = get_htf_levels(bars_full, current_idx=29, htf_minutes=15, n=2)
    res_b, _ = get_htf_levels(bars_full[:30], current_idx=29, htf_minutes=15, n=2)
    assert res_a == res_b
    assert res_a == pytest.approx(110.0)


def test_get_htf_levels_returns_none_on_insufficient_data():
    bars = _bars_3m([100.0] * 10, start_ts_ms=0)
    # Only 1 HTF bar after drop; needs at least 2n+1=5 for swing detection.
    res, sup = get_htf_levels(bars, current_idx=9, htf_minutes=15, n=2)
    assert res is None
    assert sup is None


def test_get_htf_levels_uses_explicit_current_price_when_given():
    closes = [100.0] * 30
    bars = _bars_3m(closes, start_ts_ms=0)
    bars[12] = Bar(ts_ms=bars[12].ts_ms, open=100, high=110.0, low=99.5, close=100, volume=1.0)
    # Pass current_price=108 — 110 is still above so should still be resistance.
    res, _ = get_htf_levels(bars, current_idx=29, htf_minutes=15, n=2, current_price=108.0)
    assert res == pytest.approx(110.0)
    # Pass current_price=112 — 110 is below now, should not be resistance.
    res, _ = get_htf_levels(bars, current_idx=29, htf_minutes=15, n=2, current_price=112.0)
    assert res is None
