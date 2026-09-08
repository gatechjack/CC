"""Perf guard (2026-09-08 latency isolation).

`build_command_center` fans out a LIVE `broker.snapshot()` for every division on
every render, gathered in parallel — so the render blocks for the SLOWEST single
broker's live latency (measured 6-10s, 28s at the AM peak). `_hydrate_division_metrics`
now (a) bounds each snapshot with `asyncio.wait_for(_SNAPSHOT_TIMEOUT_SEC)` and
(b) serves a last-known-good snapshot on timeout/failure so a briefly-slow broker
keeps its prior equity instead of flapping to not_wired/$0.

These tests pin BOTH properties and demonstrate the before/after latency.
"""
from __future__ import annotations

import asyncio
import time
import types

import pytest

from trading_corp.web import data as data_mod
from trading_corp.utils.divisions import Division


def _div(slug: str) -> Division:
    return Division(
        slug=slug, name=slug, broker="paper", account_filter="",
        intent="balanced", benchmark="SPY",
    )


class _FastBroker:
    def __init__(self, equity: float) -> None:
        self._e = equity
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        return types.SimpleNamespace(equity=self._e, positions=[])


class _SlowBroker:
    def __init__(self, delay: float, equity: float = 500.0) -> None:
        self.delay = delay
        self._e = equity
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return types.SimpleNamespace(equity=self._e, positions=[])


def _deps(brokers: dict):
    return types.SimpleNamespace(data_exec=types.SimpleNamespace(brokers=brokers))


@pytest.mark.asyncio
async def test_slow_broker_does_not_block_render(monkeypatch):
    """One 3s-slow broker must NOT make the whole hydrate take 3s.

    BEFORE (no timeout) it would; AFTER (timeout=0.5s) it is bounded.
    The fast broker is unaffected either way.
    """
    data_mod._SNAPSHOT_CACHE.clear()
    fast, slow = _FastBroker(100.0), _SlowBroker(delay=3.0, equity=500.0)
    dfast, dslow = _div("fast"), _div("slow")
    deps = _deps({"fast": fast, "slow": slow})

    # BEFORE-fix behaviour (simulate "no timeout" with a huge one).
    monkeypatch.setattr(data_mod, "_SNAPSHOT_TIMEOUT_SEC", 30.0)
    t0 = time.monotonic()
    await data_mod._hydrate_division_metrics([_div("fast"), _div("slow")], deps)
    before = time.monotonic() - t0

    # AFTER-fix behaviour: bounded by the timeout.
    data_mod._SNAPSHOT_CACHE.clear()
    monkeypatch.setattr(data_mod, "_SNAPSHOT_TIMEOUT_SEC", 0.5)
    t0 = time.monotonic()
    await data_mod._hydrate_division_metrics([dfast, dslow], deps)
    after = time.monotonic() - t0

    print(f"\n[before/after] slow-broker delay=3.0s  BEFORE(no-timeout)={before:.2f}s  "
          f"AFTER(timeout=0.5s)={after:.2f}s  speedup={before/after:.1f}x")

    assert before >= 2.8, f"control should block ~3s, got {before:.2f}s"
    assert after < 1.5, f"fixed render must be bounded ~0.5s, got {after:.2f}s"
    # Fast broker always fresh + online.
    assert dfast.equity == 100.0 and dfast.status == "online"
    # Slow broker had no prior cache -> degrades to not_wired (not a hang, not $0-online).
    assert dslow.status == "not_wired" and dslow.equity is None


@pytest.mark.asyncio
async def test_timeout_serves_last_known_good(monkeypatch):
    """After one successful snapshot, a later timeout serves the cached equity
    instead of flapping to not_wired."""
    data_mod._SNAPSHOT_CACHE.clear()
    slow = _SlowBroker(delay=0.0, equity=777.0)   # first call is fast -> caches
    deps = _deps({"slow": slow})
    monkeypatch.setattr(data_mod, "_SNAPSHOT_TIMEOUT_SEC", 0.5)

    d1 = _div("slow")
    await data_mod._hydrate_division_metrics([d1], deps)
    assert d1.equity == 777.0 and d1.status == "online"       # fresh, cached now

    slow.delay = 3.0                                           # now it goes slow
    d2 = _div("slow")
    t0 = time.monotonic()
    await data_mod._hydrate_division_metrics([d2], deps)
    elapsed = time.monotonic() - t0
    print(f"\n[last-known] timeout hit in {elapsed:.2f}s; served cached equity={d2.equity}")

    assert elapsed < 1.5                                       # bounded
    assert d2.equity == 777.0 and d2.status == "online"        # last-known served
    assert slow.calls == 2


@pytest.mark.asyncio
async def test_no_data_exec_marks_not_wired():
    """Preserved behaviour: deps.data_exec is None -> all not_wired, no crash."""
    d = _div("x")
    await data_mod._hydrate_division_metrics([d], types.SimpleNamespace(data_exec=None))
    assert d.status == "not_wired"
