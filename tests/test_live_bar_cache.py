"""Tests for the LiveBarCache used by BitUnix Phase 3.2a.

ATR(14) computation correctness, status snapshot, and basic cache
shape. Network-dependent `refresh()` is NOT tested here (would require
live network or a mocked ccxt) — covered by manual prod verification
in the deploy step.
"""
from __future__ import annotations

import pytest

from trading_corp.data.live_bar_cache import Bar, LiveBarCache


# ─── ATR computation ────────────────────────────────────────────────────


def _make_cache_with_bars(bars: list[Bar]) -> LiveBarCache:
    c = LiveBarCache(symbol="BTC/USD", timeframe="3m", venue="coinbase", max_bars=60)
    c.bars = bars
    return c


def test_atr_returns_none_when_too_few_bars():
    c = _make_cache_with_bars([
        Bar(ts_ms=i * 180_000, open=100, high=110, low=90, close=105, volume=1.0)
        for i in range(5)
    ])
    # Need period+1 = 15 bars for ATR(14); only 5 → None
    assert c.get_atr(period=14) is None


def test_atr_simple_constant_range():
    """When every bar has the same true range R, ATR = R."""
    bars = []
    prev_close = 100.0
    for i in range(20):
        # Each bar: high=prev_close+5, low=prev_close-5, so TR = max(10, 5, 5) = 10
        bars.append(Bar(
            ts_ms=i * 180_000,
            open=prev_close, high=prev_close + 5, low=prev_close - 5,
            close=prev_close, volume=1.0,
        ))
    c = _make_cache_with_bars(bars)
    atr = c.get_atr(period=14)
    assert atr == pytest.approx(10.0)


def test_atr_handles_gap_open():
    """TR includes |high - prev_close| and |low - prev_close|, capturing gap moves."""
    bars = [
        Bar(ts_ms=0, open=100, high=101, low=99, close=100, volume=1.0),
        # Gap up: open 110, range stays small but TR should be max(101-99, 110-100, ...)
        Bar(ts_ms=180_000, open=110, high=111, low=109, close=110, volume=1.0),
    ]
    # 2 bars only — not enough for ATR(14), but for the math contract:
    # TR(1) = max(111-109, |111-100|, |109-100|) = max(2, 11, 9) = 11
    # We can't compute ATR with these few bars; test ATR(1) instead — wait,
    # period=1 still needs period+1=2 bars. So ATR(1) on 2 bars works:
    c = _make_cache_with_bars(bars)
    atr1 = c.get_atr(period=1)
    assert atr1 == pytest.approx(11.0)


def test_atr_decays_after_volatile_period():
    """Wilder's smoothing: ATR adapts but doesn't whip on a single bar."""
    bars = []
    # 14 quiet bars: TR=2 each
    for i in range(15):
        bars.append(Bar(
            ts_ms=i * 180_000,
            open=100, high=101, low=99, close=100, volume=1.0,
        ))
    c = _make_cache_with_bars(bars)
    atr_quiet = c.get_atr(period=14)
    # ATR after 15 bars of TR=2 should be 2.0
    assert atr_quiet == pytest.approx(2.0)

    # Add 1 bar with TR=20 (10x normal). Wilder's: new ATR = (old*13 + 20)/14 ≈ 3.29
    # New True Range = max(110-90, |110-100|, |90-100|) = max(20, 10, 10) = 20
    bars.append(Bar(
        ts_ms=15 * 180_000,
        open=100, high=110, low=90, close=100, volume=1.0,
    ))
    c.bars = bars
    atr_after_spike = c.get_atr(period=14)
    expected = (2.0 * 13 + 20.0) / 14
    assert atr_after_spike == pytest.approx(expected)


def test_last_close_returns_most_recent():
    bars = [
        Bar(ts_ms=0, open=100, high=101, low=99, close=99.5, volume=1.0),
        Bar(ts_ms=180_000, open=99.5, high=100.5, low=99, close=100.0, volume=1.0),
        Bar(ts_ms=360_000, open=100, high=102, low=99.5, close=101.5, volume=1.0),
    ]
    c = _make_cache_with_bars(bars)
    assert c.last_close() == 101.5


def test_last_close_returns_none_when_empty():
    c = LiveBarCache()
    assert c.last_close() is None


def test_status_snapshot():
    bars = [
        Bar(ts_ms=i * 180_000, open=100, high=101, low=99, close=100, volume=1.0)
        for i in range(15)
    ]
    c = _make_cache_with_bars(bars)
    s = c.status()
    assert s["bars_cached"] == 15
    assert s["last_close"] == 100
    assert s["atr_14"] == pytest.approx(2.0)
    assert s["symbol"] == "BTC/USD"
    assert s["timeframe"] == "3m"


def test_timeframe_seconds():
    assert LiveBarCache(timeframe="3m").timeframe_seconds == 180
    assert LiveBarCache(timeframe="1h").timeframe_seconds == 3600
    assert LiveBarCache(timeframe="4h").timeframe_seconds == 14400
    assert LiveBarCache(timeframe="unknown").timeframe_seconds == 180  # default
