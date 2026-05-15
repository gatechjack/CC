"""Tests for the BitUnix HTF context provider (PR 2 impure boundary).

Pin the conversion from `LiveBarCache` → `TimeframeBars`, the staleness
gate that drops a stale TF to None, the funding-rate cache + freshness
window, and the synchronous `regime_snapshot()` end-to-end path.

What we DON'T test here:
  - The pure classifier itself (covered in test_bitunix_htf_regime.py)
  - Live BitUnix HTTP calls (no network in tests)
  - The dashboard partial template render (visual)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.strategies.bitunix_htf_regime import (
    HTFRegimeConfig,
    Regime,
)
from trading_corp.data.bitunix_htf_context import BitUnixHTFContextProvider
from trading_corp.data.live_bar_cache import Bar, LiveBarCache


# ─── helpers ────────────────────────────────────────────────────────────


def _filled_cache(timeframe: str, n: int = 250, base: float = 100.0) -> LiveBarCache:
    """Build a LiveBarCache pre-populated with `n` bars whose timestamps
    are aligned to `timeframe` and end at "now". Mirrors what a real
    refresh() would produce after a live poll."""
    cache = LiveBarCache(symbol="BTCUSDT", timeframe=timeframe, max_bars=n)
    tf_seconds = cache.timeframe_seconds
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # Last closed bar ends at the most recent multiple of tf below now
    last_close_ms = (now_ms // (tf_seconds * 1000)) * (tf_seconds * 1000)
    bars = []
    for i in range(n):
        ts_ms = last_close_ms - (n - 1 - i) * tf_seconds * 1000 - tf_seconds * 1000
        # Slight uptrend so EMA200 has actual signal
        c = base + i * 0.4
        bars.append(Bar(
            ts_ms=ts_ms, open=c, high=c * 1.005, low=c * 0.995,
            close=c, volume=1000.0,
        ))
    cache.bars = bars
    return cache


def _empty_cache(timeframe: str) -> LiveBarCache:
    return LiveBarCache(symbol="BTCUSDT", timeframe=timeframe, max_bars=250)


def _stale_cache(timeframe: str, age_hours: float = 100.0) -> LiveBarCache:
    """Cache with one ancient bar — should be dropped by staleness check."""
    cache = LiveBarCache(symbol="BTCUSDT", timeframe=timeframe, max_bars=250)
    tf_seconds = cache.timeframe_seconds
    ancient_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
        - int(age_hours * 3600 * 1000)
    )
    cache.bars = [Bar(
        ts_ms=ancient_ms, open=100.0, high=101.0, low=99.0,
        close=100.0, volume=1000.0,
    )]
    return cache


def _provider(
    h1: LiveBarCache, h4: LiveBarCache, d1: LiveBarCache,
    *, broker: object | None = None,
) -> BitUnixHTFContextProvider:
    if broker is None:
        broker = MagicMock()
        broker.get_funding_rate = AsyncMock(return_value=None)
    return BitUnixHTFContextProvider(
        h1_cache=h1, h4_cache=h4, d1_cache=d1, broker=broker,
        symbol="BTCUSDT",
    )


# ─── snapshot construction ──────────────────────────────────────────────


def test_snapshot_with_all_caches_filled_returns_all_three_tfs():
    p = _provider(
        _filled_cache("1h"), _filled_cache("4h"), _filled_cache("1d"),
    )
    ctx = p.snapshot()
    assert ctx.h1 is not None
    assert ctx.h4 is not None
    assert ctx.d1 is not None
    assert ctx.h1.timeframe == "1h"
    assert ctx.h4.timeframe == "4h"
    assert ctx.d1.timeframe == "1d"
    assert len(ctx.h1.closes) == 250


def test_snapshot_drops_stale_cache_to_none():
    """A cache whose newest bar is older than the staleness threshold
    should be treated as missing — the classifier sees None and that
    TF contributes 0 to composite (without forcing SAFE_MODE on its own).
    """
    p = _provider(
        _stale_cache("1h", age_hours=10),    # > 2h threshold
        _filled_cache("4h"),
        _filled_cache("1d"),
    )
    ctx = p.snapshot()
    assert ctx.h1 is None
    assert ctx.h4 is not None
    assert ctx.d1 is not None


def test_snapshot_with_all_caches_empty_returns_all_none_bars():
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), _empty_cache("1d"))
    ctx = p.snapshot()
    assert ctx.h1 is None
    assert ctx.h4 is None
    assert ctx.d1 is None
    # current_price falls through to 0.0 when no caches have bars
    assert ctx.current_price == 0.0


def test_snapshot_current_price_defaults_to_h1_latest_close():
    h1 = _filled_cache("1h", n=10, base=200.0)
    p = _provider(h1, _empty_cache("4h"), _empty_cache("1d"))
    ctx = p.snapshot()
    # h1 latest close = 200.0 + 9 * 0.4 = 203.6
    assert ctx.current_price == pytest.approx(203.6, abs=0.01)


def test_snapshot_current_price_explicit_override():
    p = _provider(_filled_cache("1h"), _filled_cache("4h"), _filled_cache("1d"))
    ctx = p.snapshot(current_price=70_000.0)
    assert ctx.current_price == 70_000.0


def test_snapshot_prior_day_high_low_from_d1_last_bar():
    d1 = _filled_cache("1d", n=10, base=100.0)
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), d1)
    ctx = p.snapshot()
    last = d1.bars[-1]
    assert ctx.prior_day_high == last.high
    assert ctx.prior_day_low == last.low


# ─── funding-rate cache ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_funding_rate_caches_value():
    broker = MagicMock()
    broker.get_funding_rate = AsyncMock(return_value=0.0003)
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), _empty_cache("1d"),
                  broker=broker)
    rate = await p.refresh_funding_rate()
    assert rate == 0.0003
    assert p._last_funding_rate == 0.0003
    # Cached value flows through to snapshot
    assert p.snapshot().funding_rate == 0.0003


@pytest.mark.asyncio
async def test_refresh_funding_rate_keeps_last_value_on_failure():
    broker = MagicMock()
    broker.get_funding_rate = AsyncMock(side_effect=[0.0002, None])
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), _empty_cache("1d"),
                  broker=broker)
    await p.refresh_funding_rate()
    assert p._last_funding_rate == 0.0002
    # Second call returns None (broker failure); cached value preserved
    await p.refresh_funding_rate()
    assert p._last_funding_rate == 0.0002


def test_funding_rate_dropped_to_none_when_stale():
    """If no successful fetch in `funding_max_age_seconds`, snapshot
    surfaces None so the HTF gate sees 'unknown funding' and skips
    the funding-extreme override."""
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), _empty_cache("1d"))
    # Force a stale cached value
    p._last_funding_rate = 0.0001
    p._last_funding_fetch_monotonic = time.monotonic() - 100_000   # ~28h
    p.funding_max_age_seconds = 43_200                              # 12h
    ctx = p.snapshot()
    assert ctx.funding_rate is None


# ─── regime_snapshot end-to-end ─────────────────────────────────────────


def test_regime_snapshot_end_to_end_with_filled_caches():
    """All three caches filled with rising bars → composite at least
    pushes off NEUTRAL. Pure classifier handles the actual logic; this
    test just validates the wire-up doesn't throw and produces a
    well-formed verdict."""
    p = _provider(
        _filled_cache("1h"), _filled_cache("4h"), _filled_cache("1d"),
    )
    config = HTFRegimeConfig.defaults()
    verdict = p.regime_snapshot(config)
    assert verdict.regime in (
        Regime.STRONG_BULL, Regime.BULL, Regime.NEUTRAL,
        Regime.BEAR, Regime.STRONG_BEAR,
    )
    assert -1.01 <= verdict.score <= 1.01
    # All three classifications populated
    assert verdict.h1 is not None
    assert verdict.h4 is not None
    assert verdict.d1 is not None


def test_regime_snapshot_safe_mode_when_all_caches_empty():
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), _empty_cache("1d"))
    config = HTFRegimeConfig.defaults()
    verdict = p.regime_snapshot(config)
    assert verdict.regime == Regime.SAFE_MODE
    assert verdict.safe_mode_reason is not None


def test_regime_snapshot_handles_partial_data():
    """1H + 4H empty, 1D filled — should NOT trip SAFE_MODE; 1D's
    contribution governs alone."""
    p = _provider(_empty_cache("1h"), _empty_cache("4h"), _filled_cache("1d"))
    config = HTFRegimeConfig.defaults()
    verdict = p.regime_snapshot(config)
    assert verdict.regime != Regime.SAFE_MODE


# ─── view-builder smoke (catches rename breakage) ───────────────────────


def test_build_bitunix_htf_view_returns_none_when_provider_missing():
    from trading_corp.web.data import build_bitunix_htf_view
    deps = MagicMock()
    deps.bitunix_htf_provider = None
    assert build_bitunix_htf_view(deps) is None


def test_build_bitunix_htf_view_shape():
    """End-to-end: provider with filled caches → well-formed view dict.
    Pin every key the template reads — a rename on RegimeVerdict that
    forgets to update build_bitunix_htf_view will break the template
    silently, this catches it loud."""
    from trading_corp.web.data import build_bitunix_htf_view
    p = _provider(
        _filled_cache("1h"), _filled_cache("4h"), _filled_cache("1d"),
    )
    deps = MagicMock()
    deps.bitunix_htf_provider = p

    view = build_bitunix_htf_view(deps)
    assert view is not None

    # Top-level keys the template reads
    expected_keys = {
        "gate_mode", "regime", "composite_score",
        "h1", "h4", "d1",
        "volatility_tier", "atr_pct_d1",
        "nearest_support", "nearest_resistance",
        "distance_to_support_pct", "distance_to_resistance_pct",
        "session", "funding_rate", "funding_extreme",
        "safe_mode_reason", "cache_health",
    }
    assert expected_keys <= set(view.keys())

    # Per-TF block keys
    tf_keys = {"regime", "ema_alignment", "structure",
               "ema20", "ema50", "ema200",
               "adx", "macd_hist", "reason"}
    for tf in ("h1", "h4", "d1"):
        assert tf_keys <= set(view[tf].keys())

    # Cache-health block keys
    for tf in ("h1", "h4", "d1"):
        assert {"bars", "last_close", "last_refresh_error"} <= \
               set(view["cache_health"][tf].keys())

    # PR 2 always ships gate_mode='off' (PR 3 reads from yaml config)
    assert view["gate_mode"] == "off"
