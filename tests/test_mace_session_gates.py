"""Holiday / early-close awareness (2026-11-23, Option 2).

MACE anchors its afternoon clock gates to each day's ACTUAL close and skips full
market closures. A normal 16:00 close reproduces the fixed ET times EXACTLY
(15:30/15:40/15:45/15:50, window end 15:55); a 13:00 half-day shifts them by the
same offset (12:30/12:40/12:45/12:50, window end 12:55); a full closure skips every
clock-gated loop with one log line and no orders/alerts. Gated by
cfg.management.session_close_aware (code default True); False reproduces the
pre-2026-11-23 fixed-wall-clock behaviour on every day type. Calendar source is the
injected utils.market_hours calendar; if it is unavailable/raises the loops fall
back to today's fixed behaviour.

Manager-integration style mirrors tests/test_mace_time_force.py (real constructor,
RungStore on :memory:, fake executor/port, injected calendar + clock)."""
from __future__ import annotations

import asyncio
import collections
import sqlite3
from dataclasses import replace
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from trading_corp.mace import execution as ex
from trading_corp.mace import loops as mace_loops
from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec, iso_week
from trading_corp.mace.manager import MaceManager
from trading_corp.persistence import db as dbmod

from tests.test_mace_manager_window import _cfg
from tests.test_mace_time_force import _Exec, _Notif, _Port, _Snap, _store

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

# Real-world anchors: 2026-11-24 Tue (normal 16:00), 11-26 Thu (holiday), 11-27 Fri (13:00 half).
NORMAL = date(2026, 11, 24)
HOLIDAY = date(2026, 11, 26)
HALF = date(2026, 11, 27)


class FakeCal:
    """market_hours-shaped: close_time_et(date|datetime)->datetime|None. Configured per date;
    default = weekday 16:00 ET, weekend None. raise_=True simulates a broken calendar."""
    def __init__(self, closes=None, raise_=False):
        self.closes = closes or {}
        self.raise_ = raise_

    def close_time_et(self, when):
        if self.raise_:
            raise RuntimeError("calendar down")
        d = when.date() if hasattr(when, "date") else when
        if d in self.closes:
            return self.closes[d]
        if d.weekday() >= 5:
            return None
        return datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)


CAL = FakeCal({HALF: datetime(2026, 11, 27, 13, 0, tzinfo=ET), HOLIDAY: None})


# ── 1. pure helpers ──────────────────────────────────────────────────────────

def test_shift_gate_identity_on_1600_and_shift_on_1300():
    for conf, exp16, exp13 in [
        (dtime(15, 30), dtime(15, 30), dtime(12, 30)),   # time exit  (close-30)
        (dtime(15, 40), dtime(15, 40), dtime(12, 40)),   # snapshot   (close-20)
        (dtime(15, 45), dtime(15, 45), dtime(12, 45)),   # entry      (close-15)
        (dtime(15, 50), dtime(15, 50), dtime(12, 50)),   # summary    (close-10)
        (dtime(15, 55), dtime(15, 55), dtime(12, 55)),   # window end (close-5)
        (dtime(15, 58), dtime(15, 58), dtime(12, 58)),   # entry cutoff (close-2)
    ]:
        assert st.shift_gate_to_close(conf, dtime(16, 0)) == exp16
        assert st.shift_gate_to_close(conf, dtime(13, 0)) == exp13


def test_resolve_close_hhmm_sentinels():
    assert st.resolve_close_hhmm(CAL, HALF) == dtime(13, 0)
    assert st.resolve_close_hhmm(CAL, NORMAL) == dtime(16, 0)
    assert st.resolve_close_hhmm(CAL, HOLIDAY) == st.CLOSE_CLOSED
    assert st.resolve_close_hhmm(FakeCal(raise_=True), HALF) == st.CLOSE_FALLBACK
    assert st.resolve_close_hhmm(None, HALF) == st.CLOSE_FALLBACK


# ── 2. _resolve_day (loop day-gate) ──────────────────────────────────────────

def _div(sca):
    mgmt = SimpleNamespace(session_close_aware=sca)
    entry = SimpleNamespace(entry_cutoff_et="15:58")
    return SimpleNamespace(active=True,
                           manager=SimpleNamespace(cfg=SimpleNamespace(management=mgmt, entry=entry)))


def test_resolve_day_flag_off_ignores_calendar():
    # flag off on a HOLIDAY date -> today's fixed behaviour (weekday True, no shift, no skip)
    now = datetime(2026, 11, 26, 11, 0, tzinfo=ET)
    assert mace_loops._resolve_day(_div(False), CAL, now) == (True, None, None, False)


def test_resolve_day_open_closed_fallback():
    assert mace_loops._resolve_day(_div(True), CAL, datetime(2026, 11, 24, 11, 0, tzinfo=ET)) \
        == (True, dtime(16, 0), None, False)
    assert mace_loops._resolve_day(_div(True), CAL, datetime(2026, 11, 27, 11, 0, tzinfo=ET)) \
        == (True, dtime(13, 0), None, False)
    run, ch, note, warn = mace_loops._resolve_day(_div(True), CAL, datetime(2026, 11, 26, 11, 0, tzinfo=ET))
    assert run is False and ch is None and note and warn is False
    run, ch, note, warn = mace_loops._resolve_day(_div(True), FakeCal(raise_=True),
                                                  datetime(2026, 11, 27, 11, 0, tzinfo=ET))
    assert run is True and ch is None and note and warn is True   # fallback = fixed hours + warn


def test_entry_window_closed_shifts_with_close():
    div = _div(True)
    # cutoff 15:58 -> 12:58 on a 13:00 close: 12:50 open, 13:10 closed
    assert mace_loops._entry_window_closed(div, datetime(2026, 11, 27, 12, 50, tzinfo=ET), dtime(13, 0)) is False
    assert mace_loops._entry_window_closed(div, datetime(2026, 11, 27, 13, 10, tzinfo=ET), dtime(13, 0)) is True
    # fixed (no close) stays 15:58
    assert mace_loops._entry_window_closed(div, datetime(2026, 11, 27, 13, 10, tzinfo=ET), None) is False


# ── 3. time-exit gate via the real manager (incl. the 11-20 forced path) ─────

def _mgr(tmp_path, *, now_et, spot, mark, natural, credit=1.59, dte=21,
         sca=True, cal=CAL, two_sided=True):
    cfg = _cfg(tmp_path, ["SPY"], ["SPY"])
    if not sca:
        cfg = replace(cfg, management=replace(cfg.management, session_close_aware=False))
    store = _store()
    expiry = now_et.date() + timedelta(days=dte)
    spec = CondorSpec("SPY", expiry, 715.0, 710.0, 786.0, 791.0, 5.0)
    rid = spec.rung_id(date(2026, 11, 2))
    store.insert_submitting(rid, spec, 2, entry_ts="2026-11-02T20:47:00+00:00",
                            entry_iso_week=iso_week(date(2026, 11, 2)), max_risk_usd=682.0)
    store.promote_open(rid, credit_actual=credit, entry_order_id="O1",
                       entry_ts="2026-11-02T20:47:00+00:00")
    audits = []
    exec_ = _Exec(_Snap(mark, natural, two_sided))
    mgr = MaceManager(cfg, port=_Port(spot), store=store, executor=exec_, notifier=_Notif(),
                      auto_execute_fn=lambda: True,
                      audit=lambda k, **p: audits.append((k, p)),
                      now_utc_fn=lambda: now_et, now_et_fn=lambda: now_et,
                      session_calendar=cal)
    return mgr, exec_, audits, rid


def _closes(exec_):
    return exec_.closes


def _rows(audits, kind):
    return [p for k, p in audits if k == kind]


def test_time_exit_fires_before_early_close_and_forces(tmp_path):
    # 13:00 half-day, 12:30 ET: gate opens (close-30); threatened+losing -> forced marketable.
    now = datetime(2026, 11, 27, 12, 30, tzinfo=ET)
    mgr, exec_, audits, rid = _mgr(tmp_path, now_et=now, spot=690.0, mark=1.99, natural=4.76)
    asyncio.run(mgr.manage_tick(now))
    assert mgr._time_exit_override(now) == dtime(12, 30)
    assert len(_closes(exec_)) == 1 and _closes(exec_)[0][1] == "time"
    assert _closes(exec_)[0][2]["pricing"] == "marketable"      # the 11-20 forced path, intact
    assert _rows(audits, "mace_time_exit_forced")


def test_time_exit_holds_before_shifted_gate_on_halfday(tmp_path):
    now = datetime(2026, 11, 27, 12, 29, tzinfo=ET)            # one minute before 12:30 gate
    mgr, exec_, audits, rid = _mgr(tmp_path, now_et=now, spot=690.0, mark=1.99, natural=4.76)
    asyncio.run(mgr.manage_tick(now))
    assert _closes(exec_) == []                                 # time gate not open yet
    assert not _rows(audits, "mace_time_exit_forced")


def test_normal_day_gate_is_exactly_1530(tmp_path):
    base = datetime(2026, 11, 24, 15, 29, tzinfo=ET)
    mgr, exec_, _a, _r = _mgr(tmp_path, now_et=base, spot=690.0, mark=1.99, natural=4.76)
    assert mgr._time_exit_override(base) == dtime(15, 30)       # 16:00 close -> identity
    asyncio.run(mgr.manage_tick(base))
    assert _closes(exec_) == []                                 # 15:29 < 15:30 -> holds
    fire = datetime(2026, 11, 24, 15, 30, tzinfo=ET)
    mgr2, exec2, _a2, _r2 = _mgr(tmp_path, now_et=fire, spot=690.0, mark=1.99, natural=4.76)
    asyncio.run(mgr2.manage_tick(fire))
    assert len(_closes(exec2)) == 1                             # 15:30 -> fires exactly as today


def test_flag_off_uses_fixed_1530_on_halfday(tmp_path):
    now = datetime(2026, 11, 27, 12, 30, tzinfo=ET)
    mgr, exec_, _a, _r = _mgr(tmp_path, now_et=now, spot=690.0, mark=1.99, natural=4.76, sca=False)
    assert mgr._time_exit_override(now) is None                 # flag off -> fixed
    asyncio.run(mgr.manage_tick(now))
    assert _closes(exec_) == []                                 # 12:30 < fixed 15:30 -> holds


def test_calendar_unavailable_falls_back_to_fixed(tmp_path):
    now = datetime(2026, 11, 27, 12, 30, tzinfo=ET)
    mgr, exec_, _a, _r = _mgr(tmp_path, now_et=now, spot=690.0, mark=1.99, natural=4.76,
                              cal=FakeCal(raise_=True))
    assert mgr._time_exit_override(now) is None                 # raised -> fixed normal hours
    asyncio.run(mgr.manage_tick(now))
    assert _closes(exec_) == []                                 # behaves exactly as today


class _StopSnap(_Snap):
    """_Snap + the leg quotes / anti_natural that the stop-evidence path reads when a stop fires."""
    def __init__(self, mark, natural, anti):
        super().__init__(mark, natural, True)
        self.anti_natural = anti
        q = SimpleNamespace(bid=0.0, ask=0.0, mid=0.0)
        self.sp = self.lp = self.sc = self.lc = q


def test_stop_still_fires_on_halfday_regardless_of_gate(tmp_path):
    # 2x stop is unchanged (not time-gated): mark 3.30 >= 3.18 at 10:00 on a half-day -> stop.
    now = datetime(2026, 11, 27, 10, 0, tzinfo=ET)
    mgr, exec_, audits, rid = _mgr(tmp_path, now_et=now, spot=690.0, mark=3.30, natural=3.40)
    mgr.executor.snap = _StopSnap(3.30, 3.40, 3.35)
    asyncio.run(mgr.manage_tick(now))
    assert len(_closes(exec_)) == 1 and _closes(exec_)[0][1] == "stop"


# ── 4. the three clock-gated loops (fake calendar + fixed clock) ─────────────

class GateMgr:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = collections.Counter()
        self.store = SimpleNamespace(get_manage_heartbeat=lambda: {})
        self.notifier = SimpleNamespace(breaker=lambda **k: self.calls.update(["breaker"]))

    async def snapshot_equity(self, d):
        self.calls["snapshot"] += 1

    async def evaluate_and_enter(self, d):
        self.calls["entry"] += 1
        return SimpleNamespace(primary=[], overflow=[], outcomes=[], auto_execute=False)

    async def daily_summary(self, d):
        self.calls["summary"] += 1

    async def manage_tick(self, now):
        self.calls["manage_tick"] += 1
        return []

    def _audit(self, kind, **p):
        self.calls["audit_" + kind] += 1


class GateDiv:
    def __init__(self, cfg, active=True):
        self.manager = GateMgr(cfg)
        self._active = active

    @property
    def active(self):
        return self._active


def _cfg_flag(on: bool):
    return CFG if on else replace(CFG, management=replace(CFG.management, session_close_aware=False))


async def _run_briefly(coro_factory):
    task = asyncio.create_task(coro_factory())
    await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _at(h, m, d):
    return lambda: datetime(d.year, d.month, d.day, h, m, tzinfo=ET)


# daily-slots
@pytest.mark.asyncio
async def test_slots_normal_day_fires_at_fixed_times():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=_at(15, 50, NORMAL), poll_interval_sec=0, calendar=CAL))
    assert div.manager.calls["snapshot"] == 1
    assert div.manager.calls["entry"] == 1
    assert div.manager.calls["summary"] == 1


@pytest.mark.asyncio
async def test_slots_halfday_shift_all_three_by_1250():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=_at(12, 50, HALF), poll_interval_sec=0, calendar=CAL))
    assert div.manager.calls["snapshot"] == 1      # 12:40
    assert div.manager.calls["entry"] == 1         # 12:45
    assert div.manager.calls["summary"] == 1       # 12:50


@pytest.mark.asyncio
async def test_slots_halfday_only_snapshot_before_1245():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=_at(12, 44, HALF), poll_interval_sec=0, calendar=CAL))
    assert div.manager.calls["snapshot"] == 1      # 12:40 reached
    assert div.manager.calls["entry"] == 0         # 12:45 not reached
    assert div.manager.calls["summary"] == 0


@pytest.mark.asyncio
async def test_slots_holiday_skips_everything():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=_at(12, 0, HOLIDAY), poll_interval_sec=0, calendar=CAL))
    assert sum(div.manager.calls.values()) == 0    # full holiday -> no snapshot/entry/summary


@pytest.mark.asyncio
async def test_slots_halfday_no_entry_after_close():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=_at(13, 30, HALF), poll_interval_sec=0, calendar=CAL))
    assert div.manager.calls["entry"] == 0         # cutoff 12:58 passed -> no entry into closed mkt


@pytest.mark.asyncio
async def test_slots_flag_off_ignores_calendar_on_halfday():
    div = GateDiv(_cfg_flag(False))
    await _run_briefly(lambda: mace_loops.mace_daily_slots_loop(
        div, None, now_et_fn=_at(15, 50, HALF), poll_interval_sec=0, calendar=CAL))
    assert div.manager.calls["snapshot"] == 1      # fixed 15:40/15:45/15:50 -> today's behaviour
    assert div.manager.calls["entry"] == 1
    assert div.manager.calls["summary"] == 1


# manage
@pytest.mark.asyncio
async def test_manage_normal_day_ticks():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div, None, now_et_fn=_at(12, 0, NORMAL), interval_sec=0, calendar=CAL))
    assert div.manager.calls["manage_tick"] >= 1


@pytest.mark.asyncio
async def test_manage_halfday_ticks_before_1255_and_stops_after():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div, None, now_et_fn=_at(12, 50, HALF), interval_sec=0, calendar=CAL))
    assert div.manager.calls["manage_tick"] >= 1   # 12:50 <= window end 12:55
    div2 = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div2, None, now_et_fn=_at(12, 56, HALF), interval_sec=0, calendar=CAL))
    assert div2.manager.calls["manage_tick"] == 0  # after 12:55 -> no tick
    div3 = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div3, None, now_et_fn=_at(13, 30, HALF), interval_sec=0, calendar=CAL))
    assert div3.manager.calls["manage_tick"] == 0  # well after the early close


@pytest.mark.asyncio
async def test_manage_holiday_no_ticks():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div, None, now_et_fn=_at(12, 0, HOLIDAY), interval_sec=0, calendar=CAL))
    assert div.manager.calls["manage_tick"] == 0


@pytest.mark.asyncio
async def test_manage_flag_off_ignores_calendar():
    div = GateDiv(_cfg_flag(False))
    await _run_briefly(lambda: mace_loops.mace_manage_loop(
        div, None, now_et_fn=_at(13, 30, HALF), interval_sec=0, calendar=CAL))
    assert div.manager.calls["manage_tick"] >= 1   # fixed 09:35-15:55 -> 13:30 ticks (today)


# watchdog
@pytest.mark.asyncio
async def test_watchdog_silent_on_holiday():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_watchdog_loop(
        div, None, now_et_fn=_at(11, 0, HOLIDAY), now_utc_fn=lambda: datetime(2026, 11, 26, 16, 0, tzinfo=UTC),
        calendar=CAL))
    assert div.manager.calls["audit_mace_manage_stall"] == 0
    assert div.manager.calls["audit_mace_marks_dead"] == 0


@pytest.mark.asyncio
async def test_watchdog_still_alerts_on_normal_day_stall():
    # Open day, empty heartbeat, 11:00 ET (secs_since_open > 900) -> stall alert still fires.
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_watchdog_loop(
        div, None, now_et_fn=_at(11, 0, NORMAL), now_utc_fn=lambda: datetime(2026, 11, 24, 16, 0, tzinfo=UTC),
        calendar=CAL))
    assert div.manager.calls["audit_mace_manage_stall"] >= 1


@pytest.mark.asyncio
async def test_watchdog_halfday_silent_after_close():
    div = GateDiv(_cfg_flag(True))
    await _run_briefly(lambda: mace_loops.mace_watchdog_loop(
        div, None, now_et_fn=_at(13, 30, HALF), now_utc_fn=lambda: datetime(2026, 11, 27, 18, 30, tzinfo=UTC),
        calendar=CAL))
    assert div.manager.calls["audit_mace_manage_stall"] == 0   # after 12:55 window end -> silent
