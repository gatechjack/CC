"""Reproduce the refresh OUTAGE shape, then prove the fix (availability fix 2026-10-10).

The acceptance for an availability bug is a REPRODUCTION, not an assertion: concurrent refreshes on DISTINCT
wallets held every asyncio thread-pool worker with FULL rollups, so unrelated to_thread() reads (the real
_load_farm_category / _load_sizing) starved and the site stopped responding. This drives the real
app._refresh_whale with the network pull stubbed and stats.rollup made slow, and measures the latency of an
UNRELATED to_thread() -- the liveness signal -- while 8 distinct-wallet refreshes are in flight.

PRODUCTION NOTE: pm_web runs the DEFAULT asyncio ThreadPoolExecutor = min(32, cpu+4) = 6 threads on the
2-vCPU box. This test PINS a 4-thread executor as a deliberate, machine-independent simplification -- the
wedge mechanism (all pool threads held by concurrent rollups) is identical, just reached at a lower N.

★ RED-FIRST: with the lock NEUTRALISED the control probe wedges (BEFORE); with the real lock it stays
responsive (AFTER). The thresholds come from the thread-pool arithmetic, not from the code under test.
"""
import asyncio
import contextlib
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import app

_POOL = 4            # pinned pool size (deliberate simplification; prod is 6 on 2 vCPU)
_ROLLUP_SECS = 0.5   # emulated full-table scan+write duration (holds one pool thread)


def _init_db_env():
    p = os.path.join(tempfile.mkdtemp(), "pm.db")
    db.init_db(p)
    os.environ["PM_DB_PATH"] = p
    return p


class _DummyClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


async def _stub_refresh_one(conn, wallet, client=None, now_ts=None):
    return {"verdict": "complete"}


def _slow_rollup(conn, *, now_ts, wallet=None):
    time.sleep(_ROLLUP_SECS)          # a FULL rollup holds a pool thread for its whole scan+write


def _install(monkeypatch):
    import trading_corp.prediction_markets.search_run as sr
    import trading_corp.data.polymarket_data_api_client as pmc
    monkeypatch.setattr(sr, "refresh_one", _stub_refresh_one, raising=False)
    monkeypatch.setattr(pmc, "PolymarketDataAPIClient", _DummyClient, raising=False)
    monkeypatch.setattr(app.stats, "rollup", _slow_rollup, raising=True)


async def _control_latency():
    """An UNRELATED to_thread() read (what _load_farm_category/_load_sizing do) -- its latency = liveness."""
    t0 = time.monotonic()
    await asyncio.to_thread(lambda: None)
    return time.monotonic() - t0


async def _measure(monkeypatch, lock_obj):
    _init_db_env()
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=_POOL))
    _install(monkeypatch)
    monkeypatch.setattr(app, "_rollup_lock", lock_obj, raising=True)
    refreshes = asyncio.gather(*[app._refresh_whale("w%d" % i, 1700) for i in range(8)])
    await asyncio.sleep(0.05)              # let the rollups grab threads
    lat = await _control_latency()         # probe liveness WHILE the 8 refreshes are in flight
    await refreshes
    return lat


async def test_outage_reproduces_without_lock_and_is_fixed_with_it(monkeypatch):
    # BEFORE: 8 slow rollups saturate the 4-thread pool -> the unrelated probe queues behind them -> wedged
    lat_broken = await _measure(monkeypatch, contextlib.nullcontext())
    # AFTER: at most ONE rollup thread is held -> >=3 threads free -> the probe returns immediately
    lat_fixed = await _measure(monkeypatch, asyncio.Lock())
    assert lat_broken > 0.3, ("BEFORE should wedge", lat_broken)
    assert lat_fixed < 0.15, ("AFTER should stay live", lat_fixed)
    assert lat_broken > lat_fixed * 3, ("the fix is not marginal", lat_broken, lat_fixed)
