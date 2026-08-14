"""Phase-4 tests: MACE scheduled loops + manager daily_summary / refresh_calendar.

Loops are exercised with a mock division/manager + forced clocks; each is spun up
as a task, allowed to fire (dedup caps it), then cancelled. Confirms the four loops
gate on `division.active`, fire the right manager op at the right slot/window, and
de-dupe.
"""
from __future__ import annotations

import asyncio
import collections
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.mace import execution as ex
from trading_corp.mace import loops as mace_loops
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod
from trading_corp.utils.time import ET, UTC

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")
SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)


def _et(h, m, d=(2026, 8, 10)):        # 2026-08-10 = Monday (weekday 0)
    return datetime(d[0], d[1], d[2], h, m, tzinfo=ET)


class MockManager:
    def __init__(self):
        self.calls = collections.Counter()

    async def snapshot_equity(self, d):
        self.calls["snapshot_equity"] += 1

    async def evaluate_and_enter(self, d):
        self.calls["evaluate_and_enter"] += 1
        return SimpleNamespace(primary=[], overflow=[], outcomes=[], auto_execute=False)

    async def daily_summary(self, d):
        self.calls["daily_summary"] += 1

    async def manage_tick(self, now):
        self.calls["manage_tick"] += 1
        return []

    async def reconcile_tick(self, d):
        self.calls["reconcile_tick"] += 1

    async def refresh_calendar(self, *a, **k):
        self.calls["refresh_calendar"] += 1


class MockDivision:
    def __init__(self, *, active, manager):
        self._active = active
        self.manager = manager

    @property
    def active(self):
        return self._active


async def _run_briefly(coro_factory):
    task = asyncio.create_task(coro_factory())
    await asyncio.sleep(0.02)          # let it spin (poll=0) + de-dupe
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── daily-slots loop ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_slots_fires_all_three_deduped():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=lambda: _et(15, 50), poll_interval_sec=0))
    assert mgr.calls["snapshot_equity"] == 1   # deduped despite many iterations
    assert mgr.calls["evaluate_and_enter"] == 1
    assert mgr.calls["daily_summary"] == 1


@pytest.mark.asyncio
async def test_daily_slots_only_snapshot_before_1545():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=lambda: _et(15, 41), poll_interval_sec=0))
    assert mgr.calls["snapshot_equity"] == 1
    assert mgr.calls["evaluate_and_enter"] == 0   # 15:45 not reached
    assert mgr.calls["daily_summary"] == 0


@pytest.mark.asyncio
async def test_daily_slots_inactive_no_fires():
    mgr = MockManager()
    div = MockDivision(active=False, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=lambda: _et(15, 50), poll_interval_sec=0))
    assert sum(mgr.calls.values()) == 0


# ── manage loop ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manage_loop_fires_in_window():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div, None, now_et_fn=lambda: _et(12, 0), interval_sec=0))
    assert mgr.calls["manage_tick"] >= 1


@pytest.mark.asyncio
async def test_manage_loop_silent_outside_window():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div, None, now_et_fn=lambda: _et(8, 0), interval_sec=0))
    assert mgr.calls["manage_tick"] == 0


# ── reconcile loop ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_loop_fires_when_active():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_reconcile_loop(
        div, None, now_et_fn=lambda: _et(12, 0), interval_sec=0))
    assert mgr.calls["reconcile_tick"] >= 1


# ── weekly calendar loop ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_loop_fires_on_sunday_deduped():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    # 2026-08-09 = Sunday (weekday 6)
    await _run_briefly(lambda: mace_loops.mace_calendar_loop(
        div, None, now_et_fn=lambda: _et(10, 0, d=(2026, 8, 9)),
        refresh_weekday=6, poll_interval_sec=0))
    assert mgr.calls["refresh_calendar"] == 1   # deduped per ISO week


@pytest.mark.asyncio
async def test_calendar_loop_silent_on_weekday():
    mgr = MockManager()
    div = MockDivision(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_calendar_loop(
        div, None, now_et_fn=lambda: _et(10, 0), refresh_weekday=6, poll_interval_sec=0))
    assert mgr.calls["refresh_calendar"] == 0


# ── manager.daily_summary + refresh_calendar ────────────────────────────

class _RecChannel:
    def __init__(self):
        self.msgs = []

    def push(self, t):
        self.msgs.append(t)

    def push_split(self, t):
        self.msgs.append(t)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dbmod.SCHEMA)
    return c


def _manager(conn, chan):
    return MaceManager(
        CFG, port=None, store=ex.RungStore(conn), executor=None,
        notifier=MaceNotifier(channel=chan, enabled=True),
        now_utc_fn=lambda: datetime(2026, 8, 10, 19, 45, tzinfo=UTC),
        now_et_fn=lambda: datetime(2026, 8, 10, 15, 50, tzinfo=ET))


@pytest.mark.asyncio
async def test_manager_daily_summary_pushes_and_returns_breakers():
    conn = _conn(); chan = _RecChannel(); store = ex.RungStore(conn)
    store.insert_submitting(SPEC.rung_id(date(2026, 8, 10)), SPEC, 1,
                            entry_ts="2026-08-10T19:40:00+00:00",
                            entry_iso_week="2026-W33", max_risk_usd=182.0)
    store.promote_open(SPEC.rung_id(date(2026, 8, 10)), credit_actual=1.18,
                       entry_order_id="O1", entry_ts="2026-08-10T19:40:00+00:00")
    conn.execute("INSERT INTO mace_equity_snapshot (snap_date, equity, ts) VALUES (?,?,?)",
                 ("2026-08-10", 10000.0, "2026-08-10T19:40:00+00:00"))
    mgr = _manager(conn, chan)
    breakers = await mgr.daily_summary(date(2026, 8, 10))
    assert any("MACE daily summary" in m for m in chan.msgs)
    assert breakers is not None and not breakers.any_hit    # $0 realized, no breach


@pytest.mark.asyncio
async def test_manager_refresh_calendar_seeds_events():
    conn = _conn(); chan = _RecChannel()
    mgr = _manager(conn, chan)
    result = await mgr.refresh_calendar(macro_path=str(ROOT / "config" / "macro_calendar.yaml"))
    assert result is not None and "seed" in result
    n = conn.execute("SELECT COUNT(*) AS c FROM economic_event").fetchone()["c"]
    assert n > 0        # FOMC/CPI/NFP seeds + LPR rows landed
