"""Process-wide rollup single-flight (availability fix 2026-10-10).

The per-wallet _refresh_inflight guard does NOT bound DISTINCT wallets, so N whales refreshed at once spawned
N concurrent rollups. The new module-level asyncio.Lock must bound the rollup to AT MOST ONE in flight
process-wide. These drive the real app._refresh_whale with the network pull stubbed and stats.rollup replaced
by an instrumented stub that records peak concurrency.

★ NON-TAUTOLOGICAL: the expected value (peak == 1) follows from the lock's SEMANTICS, not from the code under
test's output. The red-first control NEUTRALISES the lock and asserts peak > 1 -- proving the test would FAIL
without the fix (i.e. that it actually exercises the lock).
"""
import asyncio
import contextlib
import os
import tempfile
import threading
import time

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import app


def _init_db_env():
    p = os.path.join(tempfile.mkdtemp(), "pm.db")
    db.init_db(p)
    os.environ["PM_DB_PATH"] = p      # app.connect() resolves PM_DB_PATH at call time (db.pm_db_path)
    return p


class _DummyClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


async def _stub_refresh_one(conn, wallet, client=None, now_ts=None):
    return {"verdict": "complete"}


def _install_stubs(monkeypatch, fake_rollup):
    import trading_corp.prediction_markets.search_run as sr
    import trading_corp.data.polymarket_data_api_client as pmc
    monkeypatch.setattr(sr, "refresh_one", _stub_refresh_one, raising=False)
    monkeypatch.setattr(pmc, "PolymarketDataAPIClient", _DummyClient, raising=False)
    monkeypatch.setattr(app.stats, "rollup", fake_rollup, raising=True)


def _concurrency_probe():
    """A fake rollup that records the PEAK number running at once (held like the real blocking aggregate)."""
    peak = {"cur": 0, "max": 0}
    lk = threading.Lock()

    def fake_rollup(conn, *, now_ts, wallet=None):
        with lk:
            peak["cur"] += 1
            peak["max"] = max(peak["max"], peak["cur"])
        time.sleep(0.05)          # hold a worker thread like the real scan+write
        with lk:
            peak["cur"] -= 1
    return peak, fake_rollup


async def test_rollup_lock_serialises_process_wide(monkeypatch):
    _init_db_env()
    peak, fake = _concurrency_probe()
    _install_stubs(monkeypatch, fake)
    await asyncio.gather(*[app._refresh_whale("w%d" % i, 1700) for i in range(8)])
    assert peak["max"] == 1, peak      # the lock held: at most ONE rollup ran at any instant


async def test_without_lock_rollups_run_concurrently_redfirst(monkeypatch):
    _init_db_env()
    peak, fake = _concurrency_probe()
    _install_stubs(monkeypatch, fake)
    monkeypatch.setattr(app, "_rollup_lock", contextlib.nullcontext(), raising=True)   # NEUTRALISE the lock
    await asyncio.gather(*[app._refresh_whale("w%d" % i, 1700) for i in range(8)])
    assert peak["max"] > 1, peak       # WITHOUT the lock the per-wallet guard does NOT bound distinct wallets
