"""Commit 3 (2026-10-09 exit-redesign): the RIDE-TO-EXPIRY disposition + the expiry settlement
sweep.

A dead-wing OTM winner whose close is not executable RIDES to expiry (Jack's ruling) instead of
looping an unfillable close: it enters `riding` ONCE (one alert), suppresses PT/TIME silently while
STOP/EXDIV still evaluate, escapes if a short is threatened, auto-revives when the wing market
returns, and is finally booked by the reconcile expiry sweep (expired worthless -> realized = full
credit). The capstone test walks the EXACT wedged-XLE shape end to end: closing+NULL -> reopen ->
gate-fail -> ride -> expiry books.
"""
from __future__ import annotations

from datetime import date

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.broker_port import OpenOptionPosition
from trading_corp.mace.domain import EXIT_EXPIRED, RUNG_CLOSED, RUNG_OPEN

# Manager-side ride harness (expiry/strike-keyed counting port + builder).
from tests.test_mace_closeability import (
    _CountPort, _build, _seed, _put, EXP_TIME, NOW, NOW_LATE,
)
# Executor-side expiry harness.
from tests.test_mace_execution import (
    FakePort as ExFakePort, RecChannel as ExChan, _conn as _ex_conn,
    _open_rung, _executor as _ex_executor, SPEC, RUNG_ID, EXPIRY,
)


# ── RIDE: enter once, then suppress silently ──

@pytest.mark.asyncio
async def test_ride_enters_once_then_suppresses():
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)
    _put(port, 60.0, "put", 0.07, 0.09, EXP_TIME)
    _put(port, 59.0, "put", 0.02, 0.04, EXP_TIME)
    _put(port, 70.0, "call", 0.07, 0.09, EXP_TIME)
    _put(port, 71.0, "call", None, 0.41, EXP_TIME)        # dead wing -> TIME uncloseable -> ride
    await mgr.manage_tick(NOW_LATE)                        # tick 1: enters ride
    await mgr.manage_tick(NOW_LATE)                        # tick 2: PT/TIME suppressed silently
    enters = [a for a in audits if a[0] == "mace_ride_enter"]
    assert len(enters) == 1                               # alerted exactly once
    assert len([m for m in chan.msgs if "RIDING to expiry" in m]) == 1
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # never attempts a close
    assert (store.get(rid).extra or {}).get("disposition") == "riding"


# ── RIDE: a threatened short drops the ride and re-manages (risk never sleeps) ──

@pytest.mark.asyncio
async def test_ride_escapes_when_short_threatened():
    # A riding condor is OTM; if spot moves within ride_shorts_buffer_pct of a short (pin/assignment
    # risk) the ride is DROPPED and the rung re-managed that tick (near-money legs are liquid, so the
    # gate would pass / a stop can fire) -- risk never sleeps under a disposition.
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)                       # EXP (no time)
    store.set_riding(rid, why="wings_one_sided", ts="t")  # already riding
    port.spot = 69.9                                      # within 2% of the 70 short call -> threatened
    _put(port, 60.0, "put", 0.02, 0.04)
    _put(port, 59.0, "put", 0.01, 0.03)
    _put(port, 70.0, "call", 0.80, 0.84)                  # near-money short call (liquid again)
    _put(port, 71.0, "call", 0.78, 0.82)
    await mgr.manage_tick(NOW)
    assert any(a[0] == "mace_ride_escape" for a in audits)                # dropped the ride
    assert (store.get(rid).extra or {}).get("disposition") != "riding"    # re-managed, not riding


@pytest.mark.asyncio
async def test_ride_resolves_by_closing_when_gate_passes_again():
    # The ride is sticky: it clears only by RESOLVING the position. When the wing market returns and
    # a TIME winner is now executable (gate passes), the close proceeds and the ride is cleared
    # (mace_ride_cleared) -- no intermediate liveness-"revive" (which would flap on a tiny-bid wing).
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)      # dte 18 -> TIME due at 15:45
    store.set_riding(rid, why="natural_above_cap", ts="t")
    _put(port, 60.0, "put", 0.20, 0.22, EXP_TIME)         # liquid, two-sided; mark 0.34 (no PT, no stop)
    _put(port, 59.0, "put", 0.03, 0.05, EXP_TIME)         # natural 0.38 <= cap mark0.34+0.10+0.05=0.49
    _put(port, 70.0, "call", 0.20, 0.22, EXP_TIME)
    _put(port, 71.0, "call", 0.03, 0.05, EXP_TIME)
    await mgr.manage_tick(NOW_LATE)
    assert any(a[0] == "mace_ride_cleared" for a in audits)               # ride resolved
    assert any(pc.direction == bp.DIR_DEBIT for pc in port.place_calls)   # by CLOSING the winner
    assert (store.get(rid).extra or {}).get("disposition") != "riding"


@pytest.mark.asyncio
async def test_ride_is_sticky_on_a_hold_tick():
    # A riding rung on a BENIGN (no PT/stop/time) tick stays riding silently -- it does NOT un-ride
    # just because the wing looks two-sided (no liveness-revive). It clears only by close/escape/expiry.
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)                       # EXP (dte 42, no time)
    store.set_riding(rid, why="wings_one_sided", ts="t")
    _put(port, 60.0, "put", 0.20, 0.22)                   # mark 0.34: 0.15 < .. < 0.60 -> HOLD
    _put(port, 59.0, "put", 0.03, 0.05)
    _put(port, 70.0, "call", 0.20, 0.22)
    _put(port, 71.0, "call", 0.03, 0.05)
    await mgr.manage_tick(NOW)
    assert not any(a[0] in ("mace_ride_cleared", "mace_ride_escape") for a in audits)
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # no close
    assert (store.get(rid).extra or {}).get("disposition") == "riding"    # still riding (sticky)


# ── EXPIRY SWEEP (executor-level) ──

def _past_session():
    return date.fromordinal(EXPIRY.toordinal() + 2)   # 2 sessions past the rung's expiry


@pytest.mark.asyncio
async def test_expiry_sweep_books_worthless_otm():
    conn = _ex_conn(); store = ex.RungStore(conn); port = ExFakePort(); chan = ExChan()
    rung = _open_rung(store, credit=1.18, pt=None)        # past expiry at session+2; no same-exp legs
    port.positions_ret = []
    await _ex_executor(port, store, chan, resting_pt=False).reconcile(_past_session())
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSED and r.exit_reason == EXIT_EXPIRED
    assert r.exit_debit == pytest.approx(0.0)
    assert r.realized_pnl == pytest.approx(1.18 * 100.0 * 1)   # full credit kept
    assert chan.any("MACE EXIT") and chan.any("EXPIRED")


@pytest.mark.asyncio
async def test_expiry_sweep_warns_once_when_legs_remain():
    conn = _ex_conn(); store = ex.RungStore(conn); port = ExFakePort(); chan = ExChan()
    rung = _open_rung(store, credit=1.18, pt=None)
    port.positions_ret = [OpenOptionPosition(symbol="SPY", option_id="o1", quantity=-1.0,
                                             raw={"expiration_date": EXPIRY.isoformat()})]
    execu = _ex_executor(port, store, chan, resting_pt=False)
    await execu.reconcile(_past_session())
    await execu.reconcile(_past_session())                # second sweep: must NOT re-alert
    assert store.get(RUNG_ID).status == RUNG_OPEN         # left open (possible assignment)
    assert len([m for m in chan.msgs if "possible assignment" in m]) == 1


# ── CAPSTONE: the wedged-XLE shape, end to end ──

@pytest.mark.asyncio
async def test_wedged_xle_closing_reopens_rides_then_expiry_books():
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)
    # 1) The exact wedge: status=closing, exit_reason NULL (pre-d1), exit fields NULL, no working order.
    store.mark_closing(rid)
    assert store.get(rid).status == "closing" and store.get(rid).exit_reason is None
    # Dead wing (the persistent XLE condition).
    _put(port, 60.0, "put", 0.07, 0.09, EXP_TIME)
    _put(port, 59.0, "put", 0.02, 0.04, EXP_TIME)
    _put(port, 70.0, "call", 0.07, 0.09, EXP_TIME)
    _put(port, 71.0, "call", None, 0.41, EXP_TIME)
    port.open_orders_ret = []

    # 2) First manage tick: _drive_closing REOPENS the wedged rung (no fill, no working order).
    await mgr.manage_tick(NOW_LATE)
    assert any(a[0] == "mace_closing_reopen" for a in audits)
    assert store.get(rid).status == "open"

    # 3) Next tick (now OPEN, still dead wing): TIME fires -> gate fails -> RIDE.
    await mgr.manage_tick(NOW_LATE)
    assert any(a[0] == "mace_ride_enter" for a in audits)
    assert (store.get(rid).extra or {}).get("disposition") == "riding"
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # never looped a close

    # 4) Reconcile past expiry: the ride is booked expired-worthless (full credit kept).
    past = EXP_TIME.fromordinal(EXP_TIME.toordinal() + 1)
    port.positions_ret = []                                # no same-expiry legs remain
    # _CountPort.open_positions returns [] by default; drive the executor reconcile directly.
    await mgr.executor.reconcile(past)
    r = store.get(rid)
    assert r.status == RUNG_CLOSED and r.exit_reason == EXIT_EXPIRED
    assert r.realized_pnl == pytest.approx(0.30 * 100.0 * 1)
