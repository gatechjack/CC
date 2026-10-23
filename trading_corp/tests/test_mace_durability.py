"""MACE durability-fix tests (2026-10-23) — manage-tick/leg-quote timeouts + liveness watchdog.

Mirrors tests/test_pt_alert_dedupe.py's style: no fixtures/conftest, build objects with
object.__new__ / SimpleNamespace, drive async code with asyncio.run. pytest-asyncio
asyncio_mode="auto" is set in pyproject, so async def tests also run.

Proves:
  1. a hung manage_tick is TIMED OUT by the loop -> audit mace_manage_timeout + alert + next tick runs
  2. a hung leg_quote is TIMED OUT in _fresh_quotes -> that leg None, short-circuit, audit, bounded time
  3. the watchdog ALERTS on a stalled heartbeat (hang) during market hours, and is SILENT off-hours
  4. the watchdog ALERTS on a live-but-session-dead loop (ticking, 0 marks, open rungs)
  5. the manage heartbeat round-trips through agent_state
  6. an interrupted mid-exit re-drives idempotently (reopen_from_closing is guarded + idempotent)
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

from trading_corp.mace import loops as mace_loops
from trading_corp.mace.config import WatchdogConfig
from trading_corp.mace.domain import CondorSpec
from trading_corp.mace.execution import MaceExecutor, RungStore
from trading_corp.persistence.db import SCHEMA

SPEC = CondorSpec(symbol="XLE", expiry=date(2026, 11, 6), short_put=57.5,
                  long_put=56.5, short_call=67.0, long_call=68.0, width_dollars=1.0)
FRI_NOON = datetime(2026, 10, 23, 12, 0)          # weekday, inside 09:35-15:55 ET
SUN_NOON = datetime(2026, 10, 25, 12, 0)          # weekend -> market closed


# ── 1. manage-tick outer timeout self-heals ────────────────────────────────
def test_manage_tick_timeout_audits_alerts_and_continues():
    audits, breakers = [], []

    async def _hang_tick(now):
        await asyncio.sleep(100)                  # never returns within the tiny timeout

    mgr = SimpleNamespace(
        cfg=SimpleNamespace(management=SimpleNamespace(manage_tick_timeout_sec=0.05)),
        manage_tick=_hang_tick,
        _audit=lambda k, **p: audits.append((k, p)),
        notifier=SimpleNamespace(breaker=lambda **k: breakers.append(k)))
    division = SimpleNamespace(active=True, manager=mgr)

    async def run():
        task = asyncio.create_task(mace_loops.mace_manage_loop(
            division, None, interval_sec=0.01, now_et_fn=lambda: FRI_NOON))
        await asyncio.sleep(0.4)                   # several iterations at ~0.06s each
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert any(k == "mace_manage_timeout" for k, _ in audits), audits
    assert breakers, "a Telegram breaker alert must fire on a manage-tick timeout"
    # proof it SELF-HEALS: it timed out MORE THAN ONCE (the loop kept cycling, not stuck)
    assert sum(1 for k, _ in audits if k == "mace_manage_timeout") >= 2, audits


# ── 2. leg_quote per-leg timeout + short-circuit ───────────────────────────
def test_leg_quote_timeout_short_circuits_and_audits():
    audits = []

    async def _hang_quote(symbol, expiry, opt_type, strike):
        await asyncio.sleep(100)

    ex = object.__new__(MaceExecutor)
    ex.cfg = SimpleNamespace(management=SimpleNamespace(leg_quote_timeout_sec=0.05))
    ex.port = SimpleNamespace(leg_quote=_hang_quote)
    ex._audit_fn = lambda k, **p: audits.append((k, p))

    q = asyncio.run(ex._fresh_quotes(SPEC))
    assert q == {"sp": None, "lp": None, "sc": None, "lc": None}, q
    # short-circuit: only the FIRST leg is attempted (a timeout = session trouble, skip the rest)
    tos = [p for k, p in audits if k == "mace_leg_quote_timeout"]
    assert len(tos) == 1, audits
    assert tos[0]["timeout_sec"] == 0.05


# ── 3. watchdog: stall alert in-window, silent off-hours ───────────────────
def _watchdog_once(hb, now_et_dt, now_utc_dt, wd=None):
    audits, breakers = [], []
    wd = wd or WatchdogConfig(enabled=True, poll_interval_sec=1,
                              stall_alert_sec=900, marks_dead_alert_sec=900)
    mgr = SimpleNamespace(
        cfg=SimpleNamespace(management=SimpleNamespace(watchdog=wd)),
        store=SimpleNamespace(get_manage_heartbeat=lambda: hb),
        notifier=SimpleNamespace(breaker=lambda **k: breakers.append(k)),
        _audit=lambda k, **p: audits.append((k, p)))
    division = SimpleNamespace(active=True, manager=mgr)

    async def run():
        task = asyncio.create_task(mace_loops.mace_watchdog_loop(
            division, None, now_et_fn=lambda: now_et_dt, now_utc_fn=lambda: now_utc_dt))
        await asyncio.sleep(0.15)                  # first check runs immediately (before first sleep)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    return audits, breakers


def test_watchdog_alerts_on_stall_in_market_hours():
    hb = {"last_tick_ts": "2026-10-23T12:00:00+00:00",
          "last_marked_ok_ts": "2026-10-23T12:00:00+00:00", "open_count": 5, "marked_ok": 5}
    now_utc = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)   # 4h after last tick >> 900s
    audits, breakers = _watchdog_once(hb, FRI_NOON, now_utc)
    assert any(k == "mace_manage_stall" for k, _ in audits), audits
    assert breakers


def test_watchdog_silent_when_market_closed():
    hb = {"last_tick_ts": "2026-10-23T12:00:00+00:00",
          "last_marked_ok_ts": "2026-10-23T12:00:00+00:00", "open_count": 5, "marked_ok": 5}
    now_utc = datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)
    audits, breakers = _watchdog_once(hb, SUN_NOON, now_utc)   # weekend
    assert not any(k == "mace_manage_stall" for k, _ in audits), audits
    assert not breakers


def test_watchdog_no_heartbeat_in_window_is_stall():
    audits, breakers = _watchdog_once({}, FRI_NOON,
                                      datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc))
    assert any(k == "mace_manage_stall" for k, _ in audits), audits


def test_watchdog_warmup_grace_no_false_alarm_at_open():
    # 09:40 ET = only 5 min after the 09:35 open (< stall_alert_sec 900); an overnight-stale heartbeat
    # must NOT trip a stall before the first post-open tick has had time to complete.
    fri_0940 = datetime(2026, 10, 23, 9, 40)
    hb = {"last_tick_ts": "2026-10-22T19:55:00+00:00",         # yesterday's last tick (~14h old)
          "last_marked_ok_ts": "2026-10-22T19:55:00+00:00", "open_count": 5, "marked_ok": 5}
    audits, breakers = _watchdog_once(hb, fri_0940,
                                      datetime(2026, 10, 23, 13, 40, tzinfo=timezone.utc))
    assert not any(k in ("mace_manage_stall", "mace_marks_dead") for k, _ in audits), audits
    assert not breakers


# ── 4. watchdog: live-but-session-dead (ticking, 0 marks, open rungs) ──────
def test_watchdog_alerts_on_dead_session():
    hb = {"last_tick_ts": "2026-10-23T15:59:40+00:00",    # tick FRESH (20s old)
          "last_marked_ok_ts": "2026-10-23T12:00:00+00:00",  # marks STALE (4h)
          "open_count": 8, "marked_ok": 0}
    now_utc = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
    audits, breakers = _watchdog_once(hb, FRI_NOON, now_utc)
    assert any(k == "mace_marks_dead" for k, _ in audits), audits
    assert not any(k == "mace_manage_stall" for k, _ in audits), audits   # NOT a stall (tick is fresh)
    assert breakers


# ── 5. heartbeat round-trips through agent_state ───────────────────────────
def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return RungStore(conn)


def test_manage_heartbeat_roundtrip():
    st = _store()
    assert st.get_manage_heartbeat() is None
    payload = {"last_tick_ts": "2026-10-23T12:00:00+00:00",
               "last_marked_ok_ts": "2026-10-23T12:00:00+00:00", "open_count": 3, "marked_ok": 3}
    st.set_manage_heartbeat(payload, "2026-10-23T12:00:00+00:00")
    got = st.get_manage_heartbeat()
    assert got == payload, got
    # upsert (not duplicate)
    st.set_manage_heartbeat({**payload, "marked_ok": 0}, "2026-10-23T12:05:00+00:00")
    assert st.get_manage_heartbeat()["marked_ok"] == 0


# ── 6. interrupted mid-exit re-drives idempotently (reopen_from_closing) ───
def _insert_rung(st, rung_id, status, *, booked=False):
    legs = '[{"opt_type":"put","strike":57.5,"side":"sell","effect":"open"}]'
    st.conn.execute(
        "INSERT INTO mace_rung (rung_id,symbol,status,expiry,legs_json,width_dollars,contracts,"
        "credit_actual,exit_ts,exit_reason,exit_debit,realized_pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (rung_id, "XLE", status, "2026-11-06", legs, 1.0, 2, 0.32,
         ("2026-10-23T14:59:00Z" if booked else None), ("manual" if booked else "stop"),
         (0.75 if booked else None), (-86.0 if booked else None)))


def test_reopen_from_closing_is_idempotent_and_guarded():
    st = _store()
    # (a) a CLOSING rung with NO booked exit (interrupted mid-close) -> reopens ONCE, idempotent.
    _insert_rung(st, "mace-XLE-A", "closing", booked=False)
    assert st.reopen_from_closing("mace-XLE-A") is True
    assert st.get("mace-XLE-A").status == "open"
    assert st.reopen_from_closing("mace-XLE-A") is False    # already open -> no-op (idempotent re-drive)
    # (b) a CLOSING rung that WAS booked (exit fields set) -> NEVER reopened (no double-close).
    _insert_rung(st, "mace-XLE-B", "closing", booked=True)
    assert st.reopen_from_closing("mace-XLE-B") is False
    assert st.get("mace-XLE-B").status == "closing"
