"""Off-hours exit gate for PEAD manage() (fix: no more overnight GFD-sell -> 90s
cancel churn).

manage() now classifies `now` via PEADStrategy._exit_window_state against the
SHARED NYSE calendar (default_calendar / _session_open_et) and:
  - 'closed'   -> returns before the broker snapshot: evaluates nothing, places
                  and cancels NOTHING (overnight / weekend / holiday / after close);
  - 'pre_open' -> evaluates rules but DEFERS placement (RH rejects pre-market);
  - 'session'  -> evaluates AND places, as before.

These tests pin the pure timing decision. 2026-08-12 is a normal NYSE Wednesday
(the night of the incident); 2026-08-15 is a Saturday.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_corp.agents.strategies import pead_strategy as ps
from trading_corp.agents.strategies.pead_strategy import PEADStrategy
from trading_corp.utils.market_hours import ET


def _state(y, m, d, hh, mm, cfg=None):
    """_exit_window_state at a given ET wall-clock (fed as tz-aware UTC)."""
    now = datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(timezone.utc)
    return PEADStrategy._exit_window_state(now, cfg or {})


# ── (a) overnight / after-hours -> closed: no eval, no place, no cancel ────────
def test_overnight_is_closed():
    # 01:27 ET 2026-08-12 — the exact incident time.
    assert _state(2026, 8, 12, 1, 27) == ("closed", False)


def test_after_close_is_closed():
    assert _state(2026, 8, 12, 16, 30) == ("closed", False)


def test_just_before_eval_window_is_closed():
    # 08:59 ET is before open-30min (09:00) -> still closed.
    assert _state(2026, 8, 12, 8, 59) == ("closed", False)


# ── (b) 30 min before open -> evaluate, but DEFER placement ────────────────────
def test_pre_open_window_evaluates_but_defers():
    # 09:15 ET is inside [09:00, 09:31): evaluate, placement_open is False.
    assert _state(2026, 8, 12, 9, 15) == ("pre_open", False)


def test_eval_window_opens_at_30min_before():
    # 09:00 ET exactly = open - eval_lead -> inside the window (pre_open).
    assert _state(2026, 8, 12, 9, 0) == ("pre_open", False)


def test_still_pre_open_just_before_buffer():
    # 09:30:30 ET is after the 09:30 open but before open+60s buffer -> defer.
    now = datetime(2026, 8, 12, 9, 30, 30, tzinfo=ET).astimezone(timezone.utc)
    assert PEADStrategy._exit_window_state(now, {}) == ("pre_open", False)


# ── (c) mid-session -> evaluate AND place ─────────────────────────────────────
def test_session_places():
    assert _state(2026, 8, 12, 10, 0) == ("session", True)


def test_placement_opens_at_open_plus_buffer():
    # 09:31:00 ET = open+60s -> placement allowed.
    now = datetime(2026, 8, 12, 9, 31, 0, tzinfo=ET).astimezone(timezone.utc)
    assert PEADStrategy._exit_window_state(now, {}) == ("session", True)


# ── weekend / holiday -> closed (open_et is None) ─────────────────────────────
def test_weekend_is_closed():
    # 2026-08-15 is a Saturday.
    assert _state(2026, 8, 15, 10, 0) == ("closed", False)


# ── config knobs are honoured (defaults unchanged; both optional) ─────────────
def test_config_lead_and_buffer_overrides():
    cfg = {"manage_eval_lead_sec": 0, "manage_open_buffer_sec": 0}
    # lead=0 -> 09:15 is before the (now zero-lead) window still opens only at open;
    # 09:15 is after 09:30? no -> before open -> closed with lead=0.
    assert _state(2026, 8, 12, 9, 15, cfg) == ("closed", False)
    # at the open with buffer=0 -> session immediately.
    assert _state(2026, 8, 12, 9, 30, cfg) == ("session", True)


# ── half-day close shrinks the upper bound (calendar-driven) ──────────────────
def test_half_day_close_shrinks_window(monkeypatch):
    class _HalfDayCal:
        def close_time_et(self, when):
            d = when.date() if isinstance(when, datetime) else when
            return datetime(d.year, d.month, d.day, 13, 0, tzinfo=ET)  # 1:00 PM ET

    monkeypatch.setattr(ps, "default_calendar", lambda: _HalfDayCal())
    # 11:00 ET is before the 1pm half-day close -> session.
    assert _state(2026, 8, 12, 11, 0) == ("session", True)
    # 13:30 ET is after the 1pm half-day close -> closed (would be 'session' on a
    # normal 4pm-close day).
    assert _state(2026, 8, 12, 13, 30) == ("closed", False)
