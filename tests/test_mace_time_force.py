"""21-DTE forced time exit (2026-11-20).

On 11-20 the TIME exit fired on two fully-ITM SPY 12-11 put spreads, the
closeability gate failed natural_above_cap, and both entered ride -- the winner
cap (built to stop overpaying on a near-worthless OTM condor) deferred Jack's
21-DTE exit on rungs that were threatened AND losing. New behaviour
(management.closeability.time_force_when_threatened, code default True):

  TIME + natural_above_cap + (THREATENED or LOSING) -> the SAME forced
  marketable path the DTE floor uses (liveness-only gate, pricing "marketable",
  no defer), audited mace_time_exit_forced. THREATENED = spot within
  ride_shorts_buffer_pct of a short strike or through it; LOSING = this tick's
  mark >= credit_actual. Everything else (true OTM winners, spot-blind and
  not-losing rungs, PT, the 2x stop, the DTE floor) is unchanged, and the
  RIDING breaker push is capped at one per rung per session.

Integration style mirrors tests/test_mace_spot_source.py (real constructor,
RungStore on :memory:, fake executor/port).
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from trading_corp.mace import execution as ex
from trading_corp.mace.domain import CondorSpec, iso_week
from trading_corp.mace.manager import MaceManager
from trading_corp.persistence import db as dbmod

from tests.test_mace_manager_window import _cfg

UTC = timezone.utc
NOW = datetime(2026, 8, 12, 15, 50, tzinfo=UTC)   # past time_exit_at_et 15:30
EXPIRY = date(2026, 8, 28)                         # DTE 16: <= 21 (TIME), > 14 (above floor)
ENTRY_DAY = date(2026, 8, 1)
SPEC = CondorSpec(symbol="SPY", expiry=EXPIRY, short_put=715.0, long_put=710.0,
                  short_call=786.0, long_call=791.0, width_dollars=5.0)
CREDIT = 1.59                                      # stop 3.18; PT target 0.795


class _Port:
    """Direct spot quote; px None = broker down (no candle rows -> spot None)."""
    def __init__(self, px):
        self.px = px

    async def quote(self, sym):
        if self.px is None:
            raise RuntimeError("quote down")
        return self.px

    async def chain(self, sym):
        raise AssertionError("chain fetched")


class _Snap:
    def __init__(self, mark, natural, two_sided=True):
        self.mark = mark
        self.stop_mark = mark
        self.natural_debit = natural
        self.wings_two_sided = two_sided
        self.leg_mids = {"sp": None, "lp": None, "sc": None, "lc": None}


class _Exec:
    def __init__(self, snap):
        self.snap = snap
        self.closes = []

    async def mark(self, spec):
        return self.snap.mark

    async def quote_snapshot(self, spec):
        return self.snap

    async def close_rung(self, rung, reason, **kw):
        self.closes.append((rung.rung_id, reason, kw))
        return ex.ExitOutcome(rung.rung_id, True, reason=reason)


class _Notif:
    def __init__(self):
        self.breakers = []

    def breaker(self, **kw):
        self.breakers.append(kw)

    def error(self, **kw):
        pass

    def exit(self, **kw):
        pass

    def reject(self, **kw):
        pass


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    return ex.RungStore(conn)


def _mk(tmp_path, *, mark, natural, spot, two_sided=True, riding=False, cfg=None):
    cfg = cfg if cfg is not None else _cfg(tmp_path, ["SPY"], ["SPY"])
    store = _store()
    rid = SPEC.rung_id(ENTRY_DAY)
    store.insert_submitting(rid, SPEC, 2, entry_ts="2026-08-01T13:31:00+00:00",
                            entry_iso_week=iso_week(ENTRY_DAY), max_risk_usd=682.0)
    store.promote_open(rid, credit_actual=CREDIT, entry_order_id="O1",
                       entry_ts="2026-08-01T13:31:00+00:00")
    if riding:
        store.set_riding(rid, why="natural_above_cap",
                         ts="2026-08-11T20:36:00+00:00", detail="seeded riding")
    audits = []
    notif = _Notif()
    exec_ = _Exec(_Snap(mark, natural, two_sided))
    clock = {"now": NOW}
    mgr = MaceManager(cfg, port=_Port(spot), store=store, executor=exec_,
                      notifier=notif, fetch_metrics=None,
                      auto_execute_fn=lambda: True,
                      audit=lambda k, **p: audits.append((k, p)),
                      now_utc_fn=lambda: clock["now"],
                      now_et_fn=lambda: clock["now"])
    return mgr, store, exec_, notif, audits, rid, clock


def _run(mgr, when=NOW):
    asyncio.run(mgr.manage_tick(when))


def _disposition(store, rid):
    rung = [r for r in store.load_all() if r.rung_id == rid][0]
    return (rung.extra or {}).get("disposition")


def _rows(audits, kind):
    return [p for k, p in audits if k == kind]


def _assert_forced(exec_, rid):
    assert len(exec_.closes) == 1
    crid, reason, kw = exec_.closes[0]
    assert crid == rid and reason == "time"
    assert kw["pricing"] == "marketable"
    assert kw["defer_on_unfilled"] is False


# ── the force fires ─────────────────────────────────────────────────────────

def test_time_force_short_in_the_money(tmp_path):
    # Spot through the short put (690 < 715): threatened; mark 1.99 >= credit: losing.
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.99, natural=4.76,
                                                 spot=690.0)
    _run(mgr)
    _assert_forced(exec_, rid)
    assert _disposition(store, rid) is None            # never rode
    assert not _rows(audits, "mace_ride_enter")
    rows = _rows(audits, "mace_time_exit_forced")
    assert len(rows) == 1
    p = rows[0]
    assert p["condition"] == "both"
    assert p["spot"] == 690.0
    assert p["short_put"] == 715.0 and p["short_call"] == 786.0
    assert p["mark"] == 1.99 and p["credit"] == CREDIT
    assert p["natural"] == 4.76
    cap = (1.99 + mgr.cfg.management.exit_winner_band
           + mgr.cfg.management.closeability.slack_usd)
    assert p["cap"] == pytest.approx(round(cap, 4))


def test_time_force_spot_within_buffer_mark_below_credit(tmp_path):
    # 718 <= 715 + 2% buffer -> threatened alone forces (mark 1.40 < credit 1.59).
    mgr, _s, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.40, natural=2.50,
                                              spot=718.0)
    _run(mgr)
    _assert_forced(exec_, rid)
    assert _rows(audits, "mace_time_exit_forced")[0]["condition"] == "threatened"


def test_time_force_spot_unavailable_mark_at_credit(tmp_path):
    # Broker spot down (no candle either) but the rung is LOSING -> still forced.
    mgr, _s, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.99, natural=4.76,
                                              spot=None)
    _run(mgr)
    _assert_forced(exec_, rid)
    p = _rows(audits, "mace_time_exit_forced")[0]
    assert p["condition"] == "losing" and p["spot"] is None


# ── unchanged behaviour ─────────────────────────────────────────────────────

def test_time_rides_spot_unavailable_mark_below_credit(tmp_path):
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.40, natural=2.50,
                                                 spot=None)
    _run(mgr)
    assert exec_.closes == []
    assert _disposition(store, rid) == "riding"
    assert _rows(audits, "mace_ride_enter")[0]["reason"] == "time"
    assert not _rows(audits, "mace_time_exit_forced")


def test_time_rides_otm_winner_with_spot(tmp_path):
    # Spot 750: > 715 + buffer, < 786 - buffer -> genuine OTM winner keeps riding.
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.40, natural=2.50,
                                                 spot=750.0)
    _run(mgr)
    assert exec_.closes == []
    assert _disposition(store, rid) == "riding"
    assert not _rows(audits, "mace_time_exit_forced")


def test_already_riding_threatened_takes_forced_path(tmp_path):
    # The two live SPY 12-11 rungs' shape: disposition="riding", threatened + losing.
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.99, natural=4.76,
                                                 spot=690.0, riding=True)
    _run(mgr)
    _assert_forced(exec_, rid)
    assert _rows(audits, "mace_ride_escape")           # escape dropped the ride first
    assert _rows(audits, "mace_time_exit_forced")
    assert _disposition(store, rid) is None
    assert not _rows(audits, "mace_ride_enter")        # no re-entry churn


def test_pt_above_cap_still_rides(tmp_path):
    # PT (a win) is never forced. Tick 1: PT guard holds (no baseline, silent seed).
    # Tick 2 (mark moved a cent -> not frozen): PT trusted -> gate nac -> ride.
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=0.70, natural=2.00,
                                                 spot=690.0)
    _run(mgr)
    assert exec_.closes == [] and _disposition(store, rid) is None   # held, not riding
    mgr.executor.snap.mark = 0.72
    mgr.executor.snap.stop_mark = 0.72
    _run(mgr)
    assert exec_.closes == []
    assert _disposition(store, rid) == "riding"
    assert _rows(audits, "mace_ride_enter")[0]["reason"] == "pt"
    assert not _rows(audits, "mace_time_exit_forced")


def test_dead_wing_no_close_placed(tmp_path):
    # wings_one_sided (not natural_above_cap): existing handling -> ride, no order.
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.99, natural=4.76,
                                                 spot=690.0, two_sided=False)
    _run(mgr)
    assert exec_.closes == []
    assert _disposition(store, rid) == "riding"
    assert _rows(audits, "mace_ride_enter")[0]["gate"] == "wings_one_sided"
    assert not _rows(audits, "mace_time_exit_forced")


def test_flag_false_restores_old_behaviour(tmp_path):
    base = _cfg(tmp_path, ["SPY"], ["SPY"])
    assert base.management.closeability.time_force_when_threatened is True  # parse default
    cc = replace(base.management.closeability, time_force_when_threatened=False)
    cfg = replace(base, management=replace(base.management, closeability=cc))
    mgr, store, exec_, _n, audits, rid, _c = _mk(tmp_path, mark=1.99, natural=4.76,
                                                 spot=690.0, cfg=cfg)
    _run(mgr)
    assert exec_.closes == []
    assert _disposition(store, rid) == "riding"
    assert not _rows(audits, "mace_time_exit_forced")


# ── alert cap ───────────────────────────────────────────────────────────────

def test_escape_realert_once_per_rung_per_session(tmp_path):
    # Dead wing + riding + threatened: escape -> gate fail -> re-enter ride each
    # tick. Audits record every transition; the breaker push fires once per session.
    mgr, store, exec_, notif, audits, rid, clock = _mk(tmp_path, mark=1.99,
                                                       natural=4.76, spot=690.0,
                                                       two_sided=False, riding=True)
    _run(mgr)                                          # escape -> re-enter: push 1
    _run(mgr)                                          # escape -> re-enter: capped
    assert len(_rows(audits, "mace_ride_escape")) == 2
    assert len(_rows(audits, "mace_ride_enter")) == 2
    assert len(notif.breakers) == 1
    nxt = NOW + timedelta(days=1)                      # new session -> one more push
    clock["now"] = nxt
    _run(mgr, when=nxt)
    assert len(_rows(audits, "mace_ride_enter")) == 3
    assert len(notif.breakers) == 2
