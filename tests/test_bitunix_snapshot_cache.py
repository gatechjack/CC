"""Tests for the BitUnix account-snapshot poll-cache + single-flight
(10006 "request too frequently" rate-limit mitigation, 2026-06-15).

The bitunix observer calls `snapshot()` on every TradingView alert (for
tier-sizing AND the drawdown-breaker equity), and alerts cluster at candle
closes across concurrent webhook-handler tasks. Each `snapshot()` fires two
signed `/account` calls (USDT+USDC), so a cluster produced ~12 calls in 2 s →
BitUnix 10006. The fix adds:

  * single-flight  — concurrent callers share ONE in-flight fetch;
  * a short TTL    — rapid sequential callers reuse a COMPLETE snapshot;
  * cache invalidation on every state mutation — so the drawdown breaker's
    post-flatten verification (data_exec.flatten_division) always reads fresh
    broker truth;
  * never caching a PARTIAL fetch — a 10006 on one stablecoin under-reports
    equity; caching it would feed the breaker an under-count for the TTL.

Mocked + fundless: `_client.get` is faked, no network, no real keys.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.brokers import bitunix as bx
from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.bitunix import BitunixBroker

_ACCOUNT_URL = "/api/v1/futures/account"
_POSITION_URL = "/api/v1/futures/position/get_pending_positions"

# Realistic per-coin balances (2026-05-10 live evidence): equity = 3381.97.
_COIN_AVAILABLE = {"USDT": 25.27, "USDC": 3356.70}
_EXPECTED_EQUITY = _COIN_AVAILABLE["USDT"] + _COIN_AVAILABLE["USDC"]


def _resp(payload: dict):
    r = MagicMock()
    r.raise_for_status = MagicMock(return_value=None)
    r.json = MagicMock(return_value=payload)
    return r


def _install_fake_client(
    broker: BitunixBroker,
    *,
    counter: dict,
    gate: "asyncio.Event | None" = None,
    codes: "dict | None" = None,
    positions: "list | None" = None,
) -> None:
    """Wire a fake `_client.get` onto `broker`. `counter` tallies calls per URL.

    `codes` overrides the BitUnix envelope code per key ("USDT"/"USDC"/
    "position") to simulate a 10006. `gate`, when set, blocks every call until
    released (used to pile up concurrent callers for the single-flight test).
    """
    codes = codes or {}
    positions = positions if positions is not None else []

    async def fake_get(url, params=None, headers=None):
        if gate is not None:
            await gate.wait()
        counter[url] = counter.get(url, 0) + 1
        if url == _ACCOUNT_URL:
            coin = (params or {}).get("marginCoin")
            code = codes.get(coin, 0)
            data = {"available": _COIN_AVAILABLE.get(coin, 0.0)} if code == 0 else {}
            return _resp({"code": code, "data": data, "msg": "ok"})
        # position endpoint
        code = codes.get("position", 0)
        return _resp({"code": code, "data": positions if code == 0 else None})

    client = MagicMock()
    client.get = fake_get
    broker._client = client


def _make_broker(*, ttl: float = 3.0) -> BitunixBroker:
    return BitunixBroker(api_key="k", api_secret="s", snapshot_cache_ttl_s=ttl)


def _acct_calls(counter: dict) -> int:
    return counter.get(_ACCOUNT_URL, 0)


# ── correctness: a complete fetch returns the summed equity ────────────────

@pytest.mark.asyncio
async def test_complete_snapshot_returns_correct_equity():
    broker = _make_broker()
    counter: dict = {}
    _install_fake_client(broker, counter=counter)
    snap = await broker.snapshot()
    assert snap.equity == pytest.approx(_EXPECTED_EQUITY)
    assert snap.cash == pytest.approx(_EXPECTED_EQUITY)
    assert _acct_calls(counter) == 2  # one fetch = USDT + USDC


# ── TTL: rapid sequential within TTL → 1 fetch; past TTL → refetch ─────────

@pytest.mark.asyncio
async def test_ttl_cache_collapses_rapid_sequential_calls(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(bx.time, "monotonic", lambda: clock["t"])
    broker = _make_broker(ttl=3.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter)

    s1 = await broker.snapshot()
    assert _acct_calls(counter) == 2

    clock["t"] = 1002.0  # within TTL → cached, no new account calls
    s2 = await broker.snapshot()
    assert _acct_calls(counter) == 2
    assert s2.equity == pytest.approx(s1.equity)  # correctness preserved

    clock["t"] = 1004.0  # past TTL (4 s > 3 s) → refetch
    await broker.snapshot()
    assert _acct_calls(counter) == 4


# ── single-flight: the 12-in-2s burst collapses to ONE fetch ───────────────

@pytest.mark.asyncio
async def test_single_flight_concurrent_burst_one_fetch():
    """6 concurrent snapshot() callers (the ~12-account-calls-in-2s burst)
    collapse to ONE underlying fetch (2 account calls)."""
    broker = _make_broker(ttl=3.0)
    counter: dict = {}
    gate = asyncio.Event()
    _install_fake_client(broker, counter=counter, gate=gate)

    tasks = [asyncio.create_task(broker.snapshot()) for _ in range(6)]
    # Let all 6 callers reach the shared in-flight await before it resolves.
    for _ in range(30):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)

    assert _acct_calls(counter) == 2  # ONE fetch despite 6 concurrent callers
    for snap in results:
        assert snap.equity == pytest.approx(_EXPECTED_EQUITY)


# ── force_refresh bypasses a valid cache ───────────────────────────────────

@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(bx.time, "monotonic", lambda: clock["t"])
    broker = _make_broker(ttl=30.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter)

    await broker.snapshot()
    assert _acct_calls(counter) == 2
    clock["t"] = 1001.0  # well within TTL, but force_refresh must refetch
    await broker.snapshot(force_refresh=True)
    assert _acct_calls(counter) == 4


# ── safety: a PARTIAL (10006) fetch is never cached ────────────────────────

@pytest.mark.asyncio
async def test_partial_balance_fetch_not_cached(monkeypatch):
    """A 10006 on one stablecoin → under-reported equity → must NOT be cached,
    so the drawdown breaker is never fed a stale under-count for the TTL."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(bx.time, "monotonic", lambda: clock["t"])
    broker = _make_broker(ttl=30.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter, codes={"USDC": 10006})

    s1 = await broker.snapshot()
    assert _acct_calls(counter) == 2
    # Pre-existing read-side behavior: dropped coin → equity under-counts.
    assert s1.equity == pytest.approx(_COIN_AVAILABLE["USDT"])
    assert broker._snapshot_cache is None  # partial → not cached

    clock["t"] = 1001.0  # within TTL, but nothing cached → refetch
    await broker.snapshot()
    assert _acct_calls(counter) == 4


@pytest.mark.asyncio
async def test_errored_position_read_not_cached(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(bx.time, "monotonic", lambda: clock["t"])
    broker = _make_broker(ttl=30.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter, codes={"position": 10006})

    await broker.snapshot()
    assert _acct_calls(counter) == 2
    assert broker._snapshot_cache is None  # incomplete position read → not cached
    clock["t"] = 1001.0
    await broker.snapshot()
    assert _acct_calls(counter) == 4


# ── safety: flatten() invalidates so the post-flatten verify is fresh ──────

@pytest.mark.asyncio
async def test_flatten_invalidates_cache_so_verification_is_fresh(monkeypatch):
    """The drawdown breaker's safety path: after flatten(), the post-flatten
    snapshot() (data_exec.flatten_division verification) MUST refetch — never
    serve the cached pre-flatten snapshot that still shows the open position."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(bx.time, "monotonic", lambda: clock["t"])
    broker = _make_broker(ttl=30.0)
    counter: dict = {}
    open_pos = [{"symbol": "BTCUSDT", "qty": "0.01", "side": "SHORT",
                 "avgOpenPrice": "65000", "ctime": "1717200000000"}]
    _install_fake_client(broker, counter=counter, positions=open_pos)

    snap_before = await broker.snapshot()
    assert len(snap_before.positions) == 1
    assert _acct_calls(counter) == 2
    assert broker._snapshot_cache is not None  # cached pre-flatten

    # flatten() → cancel_all_orders + close_all_position (mocked via _request);
    # the broker is flat afterwards.
    broker._request = AsyncMock(return_value={})
    _install_fake_client(broker, counter=counter, positions=[])
    await broker.flatten()
    assert broker._snapshot_cache is None  # invalidated by the mutation

    clock["t"] = 1001.0  # within TTL — but the verify read must refetch fresh
    snap_after = await broker.snapshot()
    assert _acct_calls(counter) == 4
    assert snap_after.positions == []  # proven flat from a FRESH read


# ── place_order wrapper invalidates (success and on raise) ─────────────────

@pytest.mark.asyncio
async def test_place_order_wrapper_invalidates_cache():
    broker = _make_broker(ttl=30.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter)
    await broker.snapshot()
    assert broker._snapshot_cache is not None

    broker._place_order_impl = AsyncMock(return_value="FILL")
    result = await broker.place_order(MagicMock())
    assert result == "FILL"
    assert broker._snapshot_cache is None  # post-entry → fresh next read


@pytest.mark.asyncio
async def test_place_order_wrapper_invalidates_even_on_raise():
    broker = _make_broker(ttl=30.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter)
    await broker.snapshot()
    assert broker._snapshot_cache is not None

    broker._place_order_impl = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await broker.place_order(MagicMock())
    assert broker._snapshot_cache is None  # finally: cleared even on raise


# ── config knobs / edge cases ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ttl_zero_disables_cache():
    broker = _make_broker(ttl=0.0)
    counter: dict = {}
    _install_fake_client(broker, counter=counter)
    await broker.snapshot()
    await broker.snapshot()
    assert _acct_calls(counter) == 4  # no caching → every call fetches


@pytest.mark.asyncio
async def test_stub_snapshot_returns_zero_accountsnapshot():
    broker = BitunixBroker(api_key=None, api_secret=None)  # stub mode
    snap = await broker.snapshot()
    assert isinstance(snap, AccountSnapshot)
    assert snap.equity == 0.0
    assert snap.positions == []


def test_default_ttl_below_breaker_freshness_ceiling():
    """Pin the safety contract: the default TTL stays well below any tolerable
    staleness for a 15%-account-drawdown breaker, so a future bump cannot
    silently let the breaker act on stale equity."""
    assert 0 < bx._SNAPSHOT_CACHE_TTL_S <= 5.0


@pytest.mark.asyncio
async def test_cache_never_serves_older_than_ttl(monkeypatch):
    """Max staleness handed to sizing / the breaker is strictly < TTL."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(bx.time, "monotonic", lambda: clock["t"])
    ttl = 3.0
    broker = _make_broker(ttl=ttl)
    counter: dict = {}
    _install_fake_client(broker, counter=counter)

    await broker.snapshot()                 # fetch at t=1000
    clock["t"] = 1000.0 + ttl - 0.001       # just inside TTL → cached
    await broker.snapshot()
    assert _acct_calls(counter) == 2
    clock["t"] = 1000.0 + ttl               # exactly TTL → boundary refetch
    await broker.snapshot()
    assert _acct_calls(counter) == 4
