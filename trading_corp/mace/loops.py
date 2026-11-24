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
from datetime import datetime, time as dtime, timezone

from trading_corp.mace import strategy as st
from trading_corp.utils.market_hours import default_calendar
from trading_corp.utils.time import now_et, now_utc

_LOG = logging.getLogger("mace.loops")

_DEFAULT_SLOTS = (((15, 40), "snapshot"), ((15, 45), "entry"), ((15, 50), "summary"))


# ── holiday / early-close awareness (2026-11-23, Option 2) ───────────────────
# Shared day-resolver for the clock-gated loops (daily-slots, manage, watchdog). When
# cfg.management.session_close_aware is False it returns today's FIXED behaviour verbatim
# (the regression guard); when True it uses the injected NYSE calendar to skip full closures
# and anchor the afternoon gates to that day's close. Reconcile + weekly-calendar loops are
# unchanged. The single calendar touch lives in strategy.resolve_close_hhmm.

def _close_aware(division) -> bool:
    mgmt = getattr(getattr(getattr(division, "manager", None), "cfg", None), "management", None)
    return bool(getattr(mgmt, "session_close_aware", False))


def _log_skip_once(logged: set, now, loop_name: str, reason: str, *, warn: bool = False) -> None:
    """One log line per (date, loop) explaining a full-day skip or a calendar fallback."""
    key = now.date().isoformat()
    if key in logged:
        return
    logged.add(key)
    (_LOG.warning if warn else _LOG.info)("MACE %s %s: %s", loop_name, key, reason)


def _resolve_day(division, cal, now):
    """Return (run_today, close_hhmm, skip_note, warn) for a clock-gated loop.
      - flag off -> (weekday<5, None, None, False): today's FIXED behaviour exactly
      - closed   -> (False, None, 'market closed...', False): skip the whole day
      - fallback -> (weekday<5, None, 'calendar unavailable...', True): fixed hours + one warn
      - open day -> (True, <close hh:mm>, None, False): close-relative gates
    close_hhmm None means 'use the fixed configured times'; a time means 'shift to this close'."""
    weekday = now.weekday() < 5
    if not _close_aware(division):
        return weekday, None, None, False
    res = st.resolve_close_hhmm(cal, now.date())
    if res == st.CLOSE_CLOSED:
        return False, None, "market closed (holiday/weekend) -> loop skipped for the day", False
    if res == st.CLOSE_FALLBACK:
        return weekday, None, "calendar unavailable -> fixed normal-hours behaviour", True
    return True, res, None, False


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


def _entry_window_closed(division, now: datetime, close_hhmm=None) -> bool:
    """True iff `now` (ET) is at/after the configured entry cutoff — i.e. the entry
    window for `now`'s session has already closed, so an entry catch-up would be
    STALE (P1.5). The daily-slots `fired` set resets on every restart, so without
    this an off-hours restart re-runs the entry slot for a session whose 15:58
    window closed hours ago. Fail-OPEN (return False) if the cutoff is unreadable;
    run_entry's session-date-aware cutoff is the hard backstop either way.

    When `close_hhmm` is given (session-close-aware, early-close day) the cutoff is
    shifted by the same offset it sits before a 16:00 close (15:58 -> 12:58 on a 13:00
    close), so a post-close restart never re-fires an entry into the closed market."""
    entry = getattr(getattr(getattr(division, "manager", None), "cfg", None), "entry", None)
    raw = getattr(entry, "entry_cutoff_et", None)
    if not raw:
        return False
    try:
        cutoff = dtime.fromisoformat(raw)
        if close_hhmm is not None:
            cutoff = st.shift_gate_to_close(cutoff, close_hhmm)
        return now.time() >= cutoff
    except Exception:  # noqa: BLE001 — a config hiccup must never break the loop
        return False


async def mace_daily_slots_loop(division, logger_agent, *, now_et_fn=now_et,
                                slots=_DEFAULT_SLOTS, poll_interval_sec: int = 5,
                                calendar=None) -> None:
    """15:40 snapshot -> 15:45 entry -> 15:50 summary, ET weekdays, deduped per
    (date, slot). Fires a slot's op once `now >= slot time` on that date. When
    session_close_aware, full closures skip (one log line) and the slot times shift to
    that day's close (12:40/12:45/12:50 on a 13:00 half-day)."""
    fired: set = set()
    skip_logged: set = set()
    cal = calendar if calendar is not None else default_calendar()
    _LOG.info("MACE daily-slots scheduler online.")
    while True:
        try:
            if division.active:
                now = now_et_fn()
                run_today, close_hhmm, note, warn = _resolve_day(division, cal, now)
                if note:
                    _log_skip_once(skip_logged, now, "daily-slots", note, warn=warn)
                if run_today:
                    for (h, m), name in slots:
                        key = (now.date().isoformat(), name)
                        eff = (dtime(h, m) if close_hhmm is None
                               else st.shift_gate_to_close(dtime(h, m), close_hhmm))
                        if now.time() >= eff and key not in fired:
                            # P1.5: never re-fire a STALE entry catch-up after the
                            # cutoff (an off-hours restart resets `fired`). The entry
                            # slot places real capital; snapshot/summary catch-up is safe.
                            if name == "entry" and _entry_window_closed(division, now, close_hhmm):
                                fired.add(key)          # mark so we don't re-check
                                continue
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
                           window=((9, 35), (15, 55)), calendar=None) -> None:
    """5-min management ticks inside the ET window. manager.manage_tick computes
    the pure precedence and drives exits (fail-safe under the cancel-path block). When
    session_close_aware, full closures skip (one log line) and the window END shifts to
    that day's close-5m (12:55 on a 13:00 half-day); the window START stays 09:35."""
    (ws_h, ws_m), (we_h, we_m) = window
    cal = calendar if calendar is not None else default_calendar()
    skip_logged: set = set()
    _LOG.info("MACE manage scheduler online.")
    while True:
        try:
            if division.active:
                now = now_et_fn()
                run_today, close_hhmm, note, warn = _resolve_day(division, cal, now)
                if note:
                    _log_skip_once(skip_logged, now, "manage", note, warn=warn)
                we_eff = (dtime(we_h, we_m) if close_hhmm is None
                          else st.shift_gate_to_close(dtime(we_h, we_m), close_hhmm))
                if run_today and dtime(ws_h, ws_m) <= now.time() <= we_eff:
                    mgr = division.manager
                    if mgr is not None:
                        # Durability fix (2026-10-23): BOUND the whole tick. The 2026-10-13 9-day
                        # outage was an un-timed-out broker await inside manage_tick that froze this
                        # loop. asyncio.wait_for CANCELS a tick that overruns -- safe because an
                        # interrupted mid-exit is equivalent to a crash the exit-redesign already
                        # recovers (set_exit_order persist-before-cancel + CLOSING crash-recovery
                        # preamble + combo_id working-order sweep; proven idempotent). We alert loudly
                        # + audit mace_manage_timeout, and the NEXT scheduled tick runs normally. The
                        # per-leg leg_quote timeout (execution._fresh_quotes) catches the actual quote
                        # hang much sooner; this outer cap is the backstop for any OTHER tick await.
                        t = mgr.cfg.management.manage_tick_timeout_sec
                        try:
                            outs = await asyncio.wait_for(mgr.manage_tick(now), timeout=t)
                        except asyncio.TimeoutError:
                            mgr._audit("mace_manage_timeout", timeout_sec=t,
                                       detail="manage_tick exceeded timeout -> cancelled; next tick will run")
                            try:
                                mgr.notifier.breaker(
                                    condition="MACE manage tick TIMED OUT -- cancelled, self-healing next tick",
                                    lines=[f"timeout {t}s", "a broker await overran; the tick was cancelled"],
                                    suggested_action="watch for repeats; a persistent timeout = broker/session down")
                            except Exception:  # noqa: BLE001 — an alert must never break the loop
                                pass
                            outs = None
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


def _alert_watchdog(mgr, logger_agent, kind: str, condition: str, action: str, **payload) -> None:
    """Fire a watchdog alert through the EXISTING plumbing (no new channels): the manager audit hook
    (Activity Pulse + audit_event) + the Telegram breaker + the scheduler telemetry log. Each guarded
    so the watchdog can never be sunk by an alert failure."""
    try:
        mgr._audit(kind, detail=condition, **payload)
    except Exception:  # noqa: BLE001
        _LOG.exception("watchdog audit failed: %s", kind)
    try:
        mgr.notifier.breaker(condition=condition, lines=[condition], suggested_action=action)
    except Exception:  # noqa: BLE001
        pass
    _log_event(logger_agent, kind, dict(payload, detail=condition))


async def mace_watchdog_loop(division, logger_agent, *, now_et_fn=now_et, now_utc_fn=now_utc,
                             window=((9, 35), (15, 55)), calendar=None) -> None:
    """Manage-loop liveness WATCHDOG (2026-10-23 durability fix). A SIBLING, BROKER-FREE scheduler --
    it must NOT live inside the loop it watches (a hung loop cannot page about itself) and it makes NO
    broker calls (only a DB heartbeat read + the clock), so it can never hang the way the manage loop
    did on 2026-10-13. During market hours it alerts ONCE PER EPISODE (via _alert_watchdog -> existing
    Pulse + Telegram) on:
      STALL        -- no COMPLETED manage tick within watchdog.stall_alert_sec (the silent 9-day hang
                      -- also fires if there is NO heartbeat at all while in-window), or
      SESSION DEAD -- ticks ARE completing but 0 open rungs have priced within marks_dead_alert_sec
                      while open rungs exist (the 10-13 ROOT: a dead RH session -> all-None marks).
    Market-closed silence is EXPECTED -> no alert, and the episode latch resets so the next session
    re-arms cleanly. The manage loop writes its heartbeat only on a COMPLETED tick, so a hung/cancelled
    tick leaves last_tick_ts stale and this watchdog catches it."""
    (ws_h, ws_m), (we_h, we_m) = window
    cal = calendar if calendar is not None else default_calendar()
    skip_logged: set = set()
    _LOG.info("MACE watchdog scheduler online.")
    active = None   # None | "stall" | "marks_dead" -- in-memory de-dupe (one alert per episode)

    def _age_sec(ts_iso):
        if not ts_iso:
            return None
        try:
            t = datetime.fromisoformat(str(ts_iso))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return (now_utc_fn() - t).total_seconds()
        except Exception:  # noqa: BLE001
            return None

    while True:
        poll = 60
        try:
            mgr = division.manager if division.active else None
            wd = mgr.cfg.management.watchdog if mgr is not None else None
            if wd is not None:
                poll = wd.poll_interval_sec
            if mgr is not None and wd is not None and wd.enabled:
                now = now_et_fn()
                run_today, close_hhmm, note, warn = _resolve_day(division, cal, now)
                if note:
                    _log_skip_once(skip_logged, now, "watchdog", note, warn=warn)
                we_eff = (dtime(we_h, we_m) if close_hhmm is None
                          else st.shift_gate_to_close(dtime(we_h, we_m), close_hhmm))
                in_window = (run_today
                             and dtime(ws_h, ws_m) <= now.time() <= we_eff)
                if not in_window:
                    active = None   # market closed / after the early close -> silence expected; re-arm next session
                else:
                    # Warmup grace: at market open the heartbeat is ~overnight-stale and the first tick
                    # takes minutes, so don't judge a stall until a full stall-window has elapsed SINCE
                    # today's open (the overnight gap is not a stall). A mid-session hang is still caught
                    # (tick_age exceeds the window well after open).
                    open_dt = now.replace(hour=ws_h, minute=ws_m, second=0, microsecond=0)
                    secs_since_open = (now - open_dt).total_seconds()
                    hb = mgr.store.get_manage_heartbeat() or {}
                    tick_age = _age_sec(hb.get("last_tick_ts"))
                    marks_age = _age_sec(hb.get("last_marked_ok_ts"))
                    open_count = int(hb.get("open_count") or 0)
                    stalled = (secs_since_open >= wd.stall_alert_sec
                               and ((tick_age is None) or (tick_age > wd.stall_alert_sec)))
                    marks_dead = (not stalled and secs_since_open >= wd.marks_dead_alert_sec
                                  and open_count > 0
                                  and (marks_age is None or marks_age > wd.marks_dead_alert_sec))
                    if stalled and active != "stall":
                        active = "stall"
                        _alert_watchdog(
                            mgr, logger_agent, "mace_manage_stall",
                            "MACE manage loop STALLED -- no completed tick in "
                            f"{int(tick_age) if tick_age is not None else '>' + str(wd.stall_alert_sec)}s "
                            "during market hours",
                            "manage loop hung/dead; a RESTART revives it (2026-10-13 incident class)",
                            tick_age_sec=(round(tick_age) if tick_age is not None else None))
                    elif marks_dead and active != "marks_dead":
                        active = "marks_dead"
                        _alert_watchdog(
                            mgr, logger_agent, "mace_marks_dead",
                            f"MACE ticking but 0 of {open_count} rungs priced in "
                            f"{int(marks_age) if marks_age is not None else '>' + str(wd.marks_dead_alert_sec)}s "
                            "-- broker session likely DEAD",
                            "quotes failing on a live loop; check the RH session/pickle (2026-10-13 root)",
                            marks_age_sec=(round(marks_age) if marks_age is not None else None),
                            open_count=open_count)
                    elif not stalled and not marks_dead:
                        active = None   # healthy -> clear the latch so a future episode re-alerts
            await asyncio.sleep(poll)
        except asyncio.CancelledError:
            _LOG.info("MACE watchdog scheduler cancelled.")
            return
        except Exception as e:  # noqa: BLE001
            _LOG.exception("MACE watchdog loop error (continuing): %s", e)
            await asyncio.sleep(60)
