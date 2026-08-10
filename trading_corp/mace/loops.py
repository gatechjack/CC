"""MACE scheduled loops (Phase 4).

Four thin async schedulers that fire MaceManager operations on their cadence.
ALL FOUR gate on `division.active` (enabled + not standby + manager attached), so
they LOG ONLINE at boot and then no-op until go-live lifts standby (the PEAD
pattern). The broker/port live inside the manager — these loops hold no broker
and import no `trading_corp.brokers.*` (the AST boundary test covers this file).

  daily-slots : 15:40 snapshot -> 15:45 entry -> 15:50 summary (ET weekdays,
                deduped per (date, slot))
  manage      : every interval within 09:35-15:55 ET -> manager.manage_tick
  reconcile   : every interval -> manager.reconcile_tick (PT poll + submitting drain)
  calendar    : weekly (default Sunday), idempotent re-seed, deduped per ISO week

The manage-loop's exit EXECUTION is fail-safe (the fake-cancel guard) and is a
no-op in standby; the PT MECHANISM (GTC-resting vs T9) is deferred pending the
cancel-path fix, so nothing here depends on it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime

from trading_corp.utils.time import now_et

_LOG = logging.getLogger("mace.loops")

_DEFAULT_SLOTS = (((15, 40), "snapshot"), ((15, 45), "entry"), ((15, 50), "summary"))


def _log_event(logger_agent, kind: str, payload: dict) -> None:
    if logger_agent is None:
        return
    try:
        logger_agent.log_event("scheduler", kind, payload)
    except Exception:  # noqa: BLE001 — telemetry must never break a loop
        _LOG.exception("mace loop log_event failed: %s", kind)


async def _fire_slot(division, name: str, session_date, logger_agent) -> None:
    mgr = division.manager
    if mgr is None:
        return
    if name == "snapshot":
        await mgr.snapshot_equity(session_date)
        _log_event(logger_agent, "mace_snapshot", {"date": session_date.isoformat()})
    elif name == "entry":
        res = await mgr.evaluate_and_enter(session_date)
        entered = sum(1 for r in (list(res.primary) + list(res.overflow)) if r.entered)
        _log_event(logger_agent, "mace_entry_round",
                   {"date": session_date.isoformat(), "entered": entered,
                    "placed": len(res.outcomes), "auto_execute": res.auto_execute})
    elif name == "summary":
        await mgr.daily_summary(session_date)
        _log_event(logger_agent, "mace_daily_summary", {"date": session_date.isoformat()})


async def mace_daily_slots_loop(division, logger_agent, *, now_et_fn=now_et,
                                slots=_DEFAULT_SLOTS, poll_interval_sec: int = 30) -> None:
    """15:40 snapshot -> 15:45 entry -> 15:50 summary, ET weekdays, deduped per
    (date, slot). Fires a slot's op once `now >= slot time` on that date."""
    fired: set = set()
    _LOG.info("MACE daily-slots scheduler online.")
    while True:
        try:
            if division.active:
                now = now_et_fn()
                if now.weekday() < 5:
                    for (h, m), name in slots:
                        key = (now.date().isoformat(), name)
                        if now.time() >= dtime(h, m) and key not in fired:
                            fired.add(key)
                            await _fire_slot(division, name, now.date(), logger_agent)
            await asyncio.sleep(poll_interval_sec)
        except asyncio.CancelledError:
            _LOG.info("MACE daily-slots scheduler cancelled.")
            return
        except Exception as e:  # noqa: BLE001
            _LOG.exception("MACE daily-slots loop error (continuing): %s", e)
            await asyncio.sleep(poll_interval_sec)


async def mace_manage_loop(division, logger_agent, *, now_et_fn=now_et,
                           interval_sec: int = 300,
                           window=((9, 35), (15, 55))) -> None:
    """5-min management ticks inside the ET window. manager.manage_tick computes
    the pure precedence and drives exits (fail-safe under the cancel-path block)."""
    (ws_h, ws_m), (we_h, we_m) = window
    _LOG.info("MACE manage scheduler online.")
    while True:
        try:
            if division.active:
                now = now_et_fn()
                if now.weekday() < 5 and dtime(ws_h, ws_m) <= now.time() <= dtime(we_h, we_m):
                    mgr = division.manager
                    if mgr is not None:
                        outs = await mgr.manage_tick(now)
                        if outs:
                            _log_event(logger_agent, "mace_manage_exits", {"exits": len(outs)})
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            _LOG.info("MACE manage scheduler cancelled.")
            return
        except Exception as e:  # noqa: BLE001
            _LOG.exception("MACE manage loop error (continuing): %s", e)
            await asyncio.sleep(interval_sec)


async def mace_reconcile_loop(division, logger_agent, *, now_et_fn=now_et,
                              interval_sec: int = 300) -> None:
    """Poll resting PTs + drain `submitting` rungs by combo_id (fake-fill guard)."""
    _LOG.info("MACE reconcile scheduler online.")
    while True:
        try:
            if division.active:
                mgr = division.manager
                if mgr is not None:
                    await mgr.reconcile_tick(now_et_fn().date())
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            _LOG.info("MACE reconcile scheduler cancelled.")
            return
        except Exception as e:  # noqa: BLE001
            _LOG.exception("MACE reconcile loop error (continuing): %s", e)
            await asyncio.sleep(interval_sec)


async def mace_calendar_loop(division, logger_agent, *, now_et_fn=now_et,
                             refresh_weekday: int = 6,
                             poll_interval_sec: int = 3600) -> None:
    """Weekly idempotent calendar re-seed (default Sunday=6 in Python weekday()),
    deduped per ISO (year, week)."""
    last_week = None
    _LOG.info("MACE weekly-calendar scheduler online.")
    while True:
        try:
            if division.active:
                now = now_et_fn()
                wk = tuple(now.isocalendar()[:2])
                if now.weekday() == refresh_weekday and wk != last_week:
                    last_week = wk
                    mgr = division.manager
                    if mgr is not None:
                        await mgr.refresh_calendar()
                        _log_event(logger_agent, "mace_calendar_refresh", {"week": str(wk)})
            await asyncio.sleep(poll_interval_sec)
        except asyncio.CancelledError:
            _LOG.info("MACE weekly-calendar scheduler cancelled.")
            return
        except Exception as e:  # noqa: BLE001
            _LOG.exception("MACE weekly-calendar loop error (continuing): %s", e)
            await asyncio.sleep(poll_interval_sec)
