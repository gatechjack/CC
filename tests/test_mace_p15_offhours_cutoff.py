"""P1.5 — off-hours catch-up fix (session-date-aware entry cutoff).

BUG (proven live 2026-08-21): the daily-slots `fired` set resets on every restart,
so a restart AFTER 15:45 ET re-fires the entry catch-up; the 15:58 cutoff was
checked on wall-clock TIME-OF-DAY only, so when a slow off-hours boot pushed the
ladder past midnight (00:06 < 15:58) the cutoff wrongly PASSED and the engine
tried to place a STALE-session entry with real capital (only a DB-lock race
stopped the fill).

FIX (two layers, both entry-path only; manage/exit untouched):
  - loops.mace_daily_slots_loop: don't fire the ENTRY slot's catch-up once the
    session's cutoff has passed (snapshot/summary catch-up is harmless).
  - execution.run_entry: the 15:58 cutoff compares the full ET (date, time)
    against THIS session's cutoff, so a prior-session ladder run after midnight
    stands down `cutoff` instead of placing.

The four scenarios the fix must satisfy (a/b/c/d) + a same-day legit-path guard.
"""
from __future__ import annotations

import asyncio
import collections
from datetime import datetime
from types import SimpleNamespace

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace import loops as mace_loops
from trading_corp.utils.time import ET

# Reuse the adversarial executor harness (scriptable FakePort + in-memory rung DB).
from tests.test_mace_execution import (
    FakePort, RecChannel, SESSION, RUNG_ID, _conn, _entry_quotes, _ev, _executor, _res,
)

_CUTOFF = "15:58"


def _et(h, m, d=(2026, 8, 10)):        # 2026-08-10 = Monday (weekday 0)
    return datetime(d[0], d[1], d[2], h, m, tzinfo=ET)


class _Mgr:
    """Minimal manager for the loop: counts the ops + carries the entry cutoff."""

    def __init__(self):
        self.calls = collections.Counter()
        self.cfg = SimpleNamespace(entry=SimpleNamespace(entry_cutoff_et=_CUTOFF))

    async def snapshot_equity(self, d):
        self.calls["snapshot_equity"] += 1

    async def evaluate_and_enter(self, d):
        self.calls["evaluate_and_enter"] += 1
        return SimpleNamespace(primary=[], overflow=[], outcomes=[], auto_execute=False)

    async def daily_summary(self, d):
        self.calls["daily_summary"] += 1


class _Div:
    def __init__(self, active, manager):
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


# ── (a) loop: restart at 23:45 ET same-day -> NO entry catch-up ───────────────

@pytest.mark.asyncio
async def test_p15_loop_no_entry_catchup_after_cutoff_same_day():
    mgr = _Mgr(); div = _Div(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=lambda: _et(23, 45), poll_interval_sec=0))
    assert mgr.calls["evaluate_and_enter"] == 0    # stale entry catch-up gated
    assert mgr.calls["snapshot_equity"] == 1       # non-entry slots still catch up
    assert mgr.calls["daily_summary"] == 1


# ── (c) loop: in-window restart -> entry catch-up STILL works ─────────────────

@pytest.mark.asyncio
async def test_p15_loop_entry_fires_in_window():
    mgr = _Mgr(); div = _Div(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=lambda: _et(15, 50), poll_interval_sec=0))
    assert mgr.calls["evaluate_and_enter"] == 1    # 15:50 < 15:58 cutoff -> fires


@pytest.mark.asyncio
async def test_p15_loop_entry_gate_fails_open_without_cfg():
    # Belt-and-suspenders: if the cutoff is unreadable the gate must FAIL-OPEN
    # (fire as before) — run_entry is the hard backstop. Manager w/o cfg.
    mgr = _Mgr(); mgr.cfg = None
    div = _Div(active=True, manager=mgr)
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=lambda: _et(23, 45), poll_interval_sec=0))
    assert mgr.calls["evaluate_and_enter"] == 1    # fail-open: still fires (backstopped)


# ── (b) run_entry: prior session, after midnight -> stand down "cutoff" ───────

@pytest.mark.asyncio
async def test_p15_run_entry_prior_session_post_midnight_standsdown():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_FILLED, "O1")]    # WOULD fill if it placed
    exr = _executor(port, store, chan,
                    now_et_fn=lambda: datetime(2026, 8, 11, 6, 0, tzinfo=ET))  # next day 06:00
    out = await exr.run_entry(_ev(), SESSION)            # SESSION = 2026-08-10 (prior day)
    assert not out.filled and out.standdown_reason == "cutoff"
    assert port.place_calls == []                        # NO order reached the broker


# ── (d) run_entry: the exact 8/21 replay -> stand down, nothing placed ────────

@pytest.mark.asyncio
async def test_p15_run_entry_regression_8_21_offhours_no_place():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_FILLED, "O1")]
    # Restart ~23:45 whose ladder executes at 00:06 next day for the prior session.
    exr = _executor(port, store, chan,
                    now_et_fn=lambda: datetime(2026, 8, 11, 0, 6, tzinfo=ET))
    out = await exr.run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "cutoff"
    assert len(port.place_calls) == 0                    # OLD code placed here; now gone
    assert not chan.any("MACE ENTRY")                    # no fill notification


# ── legit same-day in-window entry is UNAFFECTED (fix doesn't break real path) ─

@pytest.mark.asyncio
async def test_p15_run_entry_in_window_same_day_still_places():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_FILLED, "O1")]
    exr = _executor(port, store, chan,
                    now_et_fn=lambda: datetime(2026, 8, 10, 15, 46, tzinfo=ET))  # in-window
    out = await exr.run_entry(_ev(), SESSION)            # same-day 15:46 < 15:58
    assert out.filled and out.standdown_reason is None
    assert len(port.place_calls) == 1
