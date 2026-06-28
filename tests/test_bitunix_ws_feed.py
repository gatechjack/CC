"""Piece 3 (2026-06-27) — Bitunix ws-primary / REST-fallback hybrid.

Covers the ws feed's channel mapping + bucket-rollover closed-bar emission, and
the LiveBarCache hybrid refresh() (short-circuit while ws fresh, REST fallback
when stale). Protocol fixtures mirror the shapes empirically captured from prod.
"""
from __future__ import annotations

import json

import pytest

from trading_corp.data.live_bar_cache import Bar, LiveBarCache
from trading_corp.data.bitunix_ws_feed import BitunixWsFeed


def _mk(symbol="BTCUSDT", tf="3m", max_bars=60) -> LiveBarCache:
    return LiveBarCache(symbol=symbol, timeframe=tf, venue="bitunix", max_bars=max_bars)


def _kline(ch, symbol, ts, o, h, low, c, b="1.0") -> str:
    return json.dumps({
        "ch": ch, "symbol": symbol, "ts": ts,
        "data": {"o": str(o), "h": str(h), "l": str(low), "c": str(c), "b": b},
    })


# ── subscription building ───────────────────────────────────────────────


def test_feed_builds_correct_channels():
    caches = [_mk(tf="3m"), _mk("ETHUSDT", "15m"), _mk(tf="1h"), _mk(tf="4h"), _mk(tf="1d")]
    f = BitunixWsFeed(caches)
    assert sorted(s["ch"] for s in f._subs) == sorted([
        "market_kline_3min", "market_kline_15min", "market_kline_60min",
        "market_kline_4h", "market_kline_1day",
    ])
    assert f.sub_count == 5


def test_feed_skips_unsupported_tf_and_nonbitunix_venue():
    c_bad_tf = LiveBarCache(symbol="BTCUSDT", timeframe="2m", venue="bitunix")
    c_ccxt = LiveBarCache(symbol="BTC/USD", timeframe="3m", venue="coinbase")
    f = BitunixWsFeed([c_bad_tf, c_ccxt])
    assert f.sub_count == 0


# ── message handling ────────────────────────────────────────────────────


def test_on_message_ignores_control_frames():
    c = _mk()
    f = BitunixWsFeed([c])
    f._on_message(json.dumps({"op": "connect", "data": {"result": True}}))
    f._on_message(json.dumps({"op": "ping", "pong": 123, "ping": 123}))
    f._on_message("not json")
    assert c.bars == []


def test_bucket_rollover_emits_closed_bar():
    c = _mk(tf="3m")
    f = BitunixWsFeed([c])
    ch = "market_kline_3min"
    B0 = 1782545400000  # 3m-aligned bucket start (divisible by 180000)
    # first push in B0 -> start accumulator, NO bar yet
    f._on_message(_kline(ch, "BTCUSDT", B0 + 1000, 100, 100, 100, 100))
    assert c.bars == []
    # running update within B0 (new high/low/close)
    f._on_message(_kline(ch, "BTCUSDT", B0 + 2000, 100, 110, 95, 105))
    assert c.bars == []
    # push lands in B1 -> previous bucket B0 is CLOSED and emitted
    f._on_message(_kline(ch, "BTCUSDT", B0 + 180000 + 1000, 105, 106, 104, 105))
    assert len(c.bars) == 1
    bar = c.bars[0]
    assert bar.ts_ms == B0                       # bucket-aligned, like REST bars
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 110.0, 95.0, 105.0)
    assert f.status()["bars_emitted"] == 1


def test_rollover_routes_per_channel_independently():
    c3 = _mk(tf="3m")
    c15 = _mk(tf="15m")
    f = BitunixWsFeed([c3, c15])
    B = 1782545400000  # divisible by both 180000 and 900000
    f._on_message(_kline("market_kline_3min", "BTCUSDT", B + 1000, 1, 1, 1, 1))
    f._on_message(_kline("market_kline_3min", "BTCUSDT", B + 180000 + 1, 2, 2, 2, 2))
    # 3m rolled once; 15m never rolled
    assert len(c3.bars) == 1 and c15.bars == []


# ── LiveBarCache hybrid refresh() ───────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_shortcircuits_when_ws_fresh(monkeypatch):
    import time
    c = _mk()
    c.bars = [Bar(1, 1, 1, 1, 1, 0)]
    c.ws_enabled = True
    c.ws_last_msg_ts = time.monotonic()         # fresh
    called = []

    async def _fake_rest():
        called.append(1)
        return 0
    monkeypatch.setattr(c, "_refresh_bitunix", _fake_rest)
    n = await c.refresh()
    assert n == 1 and called == []               # REST NOT called


@pytest.mark.asyncio
async def test_refresh_rest_fallback_when_ws_stale(monkeypatch):
    import time
    c = _mk()
    c.bars = [Bar(1, 1, 1, 1, 1, 0)]
    c.ws_enabled = True
    c.ws_last_msg_ts = time.monotonic() - 9999   # stale
    called = []

    async def _fake_rest():
        called.append(1)
        return 5
    monkeypatch.setattr(c, "_refresh_bitunix", _fake_rest)
    n = await c.refresh()
    assert called == [1] and n == 5              # REST fallback fired


@pytest.mark.asyncio
async def test_refresh_does_rest_when_ws_disabled(monkeypatch):
    c = _mk()
    c.bars = [Bar(1, 1, 1, 1, 1, 0)]
    c.ws_enabled = False                          # default
    called = []

    async def _fake_rest():
        called.append(1)
        return 3
    monkeypatch.setattr(c, "_refresh_bitunix", _fake_rest)
    await c.refresh()
    assert called == [1]                          # back-compat: pure REST


# ── append + freshness helpers ──────────────────────────────────────────


def test_append_ws_bar_dedupe_and_bound():
    c = _mk(max_bars=3)
    for i in range(5):
        c.append_ws_bar(Bar(i * 1000, i, i, i, i, 0))
    assert [b.ts_ms for b in c.bars] == [2000, 3000, 4000]      # bounded to 3
    c.append_ws_bar(Bar(4000, 9, 9, 9, 9, 0))                   # same ts -> replace
    assert len(c.bars) == 3 and c.bars[-1].close == 9.0
    c.append_ws_bar(Bar(500, 0, 0, 0, 0, 0))                    # older -> ignored
    assert len(c.bars) == 3


def test_note_ws_alive_sets_monotonic():
    c = _mk()
    assert c.ws_last_msg_ts == 0.0
    c.note_ws_alive()
    assert c.ws_last_msg_ts > 0.0
