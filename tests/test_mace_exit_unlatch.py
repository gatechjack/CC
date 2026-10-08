"""Commit 1 (2026-10-09 exit-redesign): the LOOP-STOPPER.

Exit-ladder exception taxonomy (MaceOrderRejected = definitive-no-order -> continue,
not latch), CLOSING un-latch (reason persistence, re-drive cap + park, reopen-from-closing
guarded like mace_xle_reset.py), and exit-order-id crash-recovery. The 2026-10-05 wedge --
a false winner close that RH "empty response"-rejects latching status=closing forever -- dies
HERE even before the Commit 2 closeability gate: the reject no longer latches a winner-defer.

Executor-level tests reuse the scriptable FakePort from test_mace_execution; manager-level
tests (_drive_closing) build a MaceManager over the same fake.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.broker_port import OpenOrder
from trading_corp.mace.domain import (
    EXIT_PT, EXIT_STOP, EXIT_EXDIV, OptionQuote, RUNG_CLOSING, RUNG_OPEN, RUNG_CLOSED,
)
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.utils.time import ET, UTC

# Reuse the proven scriptable harness (FakePort: place_script/status_script/open_orders_ret).
from tests.test_mace_execution import (
    CFG, FakePort, RecChannel, SPEC, RUNG_ID, ISO_WK, EXPIRY,
    _conn, _res, _exit_quotes, _open_rung, _executor,
)

NOW_ET = datetime(2026, 8, 10, 15, 45, tzinfo=ET)
NOW_UTC = datetime(2026, 8, 10, 19, 45, tzinfo=UTC)


def _dead_wing_exit_quotes(port: FakePort) -> None:
    """A dead long CALL wing (bid 0 -> None): natural_debit is None (can't sell the wing),
    so the marketable ladder skips every attempt (mace_exit_unpriceable) and never places."""
    from trading_corp.mace.domain import OptionQuote
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", SPEC.expiry, 585.0, "put", 1.90, 2.00, -0.55),
        ("put", 582.0): OptionQuote("SPY", SPEC.expiry, 582.0, "put", 0.05, 0.07, -0.30),
        ("call", 615.0): OptionQuote("SPY", SPEC.expiry, 615.0, "call", 0.06, 0.10, 0.10),
        ("call", 618.0): OptionQuote("SPY", SPEC.expiry, 618.0, "call", None, 0.41, 0.05),
    }


def _mgr(port, store, chan):
    audits: list = []
    notifier = MaceNotifier(channel=chan, enabled=True)
    execu = _executor(port, store, chan, resting_pt=False,
                      now_et_fn=lambda: NOW_ET)
    mgr = MaceManager(CFG, port, store, execu, notifier,
                      audit=lambda kind, **p: audits.append((kind, p)),
                      now_utc_fn=lambda: NOW_UTC, now_et_fn=lambda: NOW_ET)
    return mgr, audits


# ── 1. THE UN-LATCH: a winner close that only ever REJECTS defers, never latches ──

@pytest.mark.asyncio
async def test_winner_reject_x5_defers_never_latches_closing():
    # The 2026-10-05 chain WITHOUT the Commit 2 gate: a (false) PT winner close whose every
    # attempt comes back MaceOrderRejected ("empty response"). PRE-FIX: the generic except ->
    # _exit_exhausted -> mark_closing latched it CLOSING forever. POST-FIX: the reject arm spends
    # the attempt + continues; on exhaustion the winner path DEFERS -> rung stays OPEN.
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)                                   # two-sided -> priceable (reaches _place)
    rung = _open_rung(store, pt=None)                    # T9 synthetic: no resting PT to cancel
    port.place_script = [bp.MaceOrderRejected("empty response")] * 5
    port.open_orders_ret = []                            # clean sweep -> safe to defer
    out = await _executor(port, store, chan, resting_pt=False).close_rung(
        rung, EXIT_PT, pricing="winner", defer_on_unfilled=True, trigger_mid=1.95)
    assert out.deferred and not out.exhausted and not out.closed
    assert store.get(RUNG_ID).status == RUNG_OPEN        # NOT latched CLOSING -- the loop is dead
    assert len(port.place_calls) == 5                    # every attempt tried, none latched


@pytest.mark.asyncio
async def test_winner_reject_with_secret_live_order_does_not_leave_open():
    # Guard the rare "empty response that WAS actually placed": if a working {rid}-x* order is
    # resting after a reject-laden winner ladder, do NOT leave the rung OPEN (PT could re-fire and
    # double-close) -> persist it + exhaust->CLOSING so the preamble/reconcile owns the live order.
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store, pt=None)
    port.place_script = [bp.MaceOrderRejected("empty response")] * 5
    port.open_orders_ret = [OpenOrder(order_id="GHOST", state="queued", ref_id=f"{RUNG_ID}-x3")]
    out = await _executor(port, store, chan, resting_pt=False).close_rung(
        rung, EXIT_PT, pricing="winner", defer_on_unfilled=True, trigger_mid=1.95)
    assert out.exhausted and not out.deferred
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSING
    assert (r.extra or {}).get("exit_order_id") == "GHOST"   # persisted -> preamble cancels it


# ── 2. REASON PERSISTENCE (reason-amnesia fix) ──

@pytest.mark.asyncio
async def test_marketable_reject_stays_closing_and_persists_reason():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store, pt=None)
    port.place_script = [bp.MaceOrderRejected("empty response")] * 5
    out = await _executor(port, store, chan, resting_pt=False).close_rung(rung, EXIT_STOP)
    assert out.exhausted
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSING
    assert r.exit_reason == "stop"                        # persisted (was NULL->"manual" pre-fix)
    blk = (r.extra or {}).get("closing") or {}
    assert blk.get("redrives") == 0 and "since" in blk    # bookkeeping block stamped once


# ── 3. EXIT-ORDER-ID CRASH-RECOVERY ──

@pytest.mark.asyncio
async def test_exit_order_persisted_on_unconfirmed():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store, pt=None)
    port.place_script = [_res(bp.STATE_QUEUED, "X1")]
    port.status_script = {"X1": _res(bp.STATE_QUEUED, "X1")}   # never terminal -> unconfirmed
    out = await _executor(port, store, chan, resting_pt=False).close_rung(rung, EXIT_STOP)
    assert out.exhausted
    assert (store.get(RUNG_ID).extra or {}).get("exit_order_id") == "X1"  # durable pointer left


@pytest.mark.asyncio
async def test_preamble_books_a_recovered_filled_exit_order():
    # Next tick after the crash above: the persisted X1 is polled in the preamble and comes back
    # FILLED -> book it (at the persisted limit), clear the pointer, CLOSED. No fresh ladder.
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID, exit_reason=EXIT_STOP, ts="2026-08-10T19:40:00+00:00")
    store.set_exit_order(RUNG_ID, "X1", 2.04, EXIT_STOP)
    reloaded = store.get(RUNG_ID)
    port.status_script = {"X1": _res(bp.STATE_FILLED, "X1")}
    port.place_script = []                                # must NOT ladder
    out = await _executor(port, store, chan, resting_pt=False).close_rung(reloaded, EXIT_STOP)
    assert out.closed and out.exit_debit == pytest.approx(2.04)
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSED and (r.extra or {}).get("exit_order_id") is None
    assert len(port.place_calls) == 0                     # booked from the recovered order only


@pytest.mark.asyncio
async def test_preamble_aborts_when_recovered_order_unconfirmed():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID, exit_reason=EXIT_STOP, ts="2026-08-10T19:40:00+00:00")
    store.set_exit_order(RUNG_ID, "X1", 2.04, EXIT_STOP)
    reloaded = store.get(RUNG_ID)
    port.status_script = {"X1": _res(bp.STATE_QUEUED, "X1")}   # not provably dead
    out = await _executor(port, store, chan, resting_pt=False).close_rung(reloaded, EXIT_STOP)
    assert out.aborted and not out.closed
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSING and (r.extra or {}).get("exit_order_id") == "X1"
    assert len(port.place_calls) == 0                     # never laddered under a live order


# ── 4. REDRIVE CAP -> PARK (manager _drive_closing) ──

@pytest.mark.asyncio
async def test_committed_redrive_caps_and_parks_once():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    # A SHORT ask is missing -> _natural_debit_floored None -> every ladder attempt skips (no place,
    # no place_script needed) -> exhaust. reason=EXDIV (stop-class cap/park path, but NOT the
    # EXIT_STOP-only spurious-stop self-heal) so the rung genuinely re-drives toward the cap.
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 1.90, 2.00, -0.55),
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.05, 0.07, -0.30),
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 0.06, None, 0.10),  # short ask None
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.01, 0.03, 0.05),
    }
    _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID, exit_reason=EXIT_EXDIV, ts="2026-08-10T19:40:00+00:00")
    mgr, audits = _mgr(port, store, chan)
    for _ in range(_park_cap() + 2):                      # drive past the cap
        await mgr._drive_closing(store.get(RUNG_ID), NOW_ET)
    parked = [a for a in audits if a[0] == "mace_close_parked"]
    assert len(parked) == 1                               # parked exactly once
    assert sum(chan.msgs.count(m) for m in chan.msgs if "CLOSE BLOCKED" in m) >= 1
    assert len([m for m in chan.msgs if "CLOSE BLOCKED" in m]) == 1   # ONE urgent alert, not a flood
    assert (store.get(RUNG_ID).extra or {}).get("closing", {}).get("parked")
    assert store.get(RUNG_ID).status == RUNG_CLOSING      # still committed, just parked


def _park_cap() -> int:
    return CFG.management.closeability.max_closing_redrives


# ── 5. REOPEN SELF-HEAL (the wedged-XLE shape) ──

@pytest.mark.asyncio
async def test_legacy_null_reason_closing_self_heals_to_open():
    # The EXACT wedged-XLE shape: status=closing, exit_reason NULL (pre-d1), exit fields NULL, no
    # working order. _drive_closing REOPENS it to open (the manual mace_xle_reset.py becomes moot).
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID)                           # legacy: no reason, no closing block
    assert store.get(RUNG_ID).exit_reason is None
    port.open_orders_ret = []                             # no working close order
    mgr, audits = _mgr(port, store, chan)
    out = await mgr._drive_closing(store.get(RUNG_ID), NOW_ET)
    assert out is None
    r = store.get(RUNG_ID)
    assert r.status == RUNG_OPEN                           # self-healed
    assert any(a[0] == "mace_closing_reopen" for a in audits)
    assert len(port.place_calls) == 0                     # reopened, not re-driven


@pytest.mark.asyncio
async def test_closing_with_working_order_is_not_reopened():
    # Negative: a winner-class/legacy CLOSING rung with a working {rid}-x* order must NOT reopen
    # underneath a live close -> drive once (preamble owns it), stay CLOSING.
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID)                           # legacy NULL reason
    port.open_orders_ret = [OpenOrder(order_id="LIVE", state="queued", ref_id=f"{RUNG_ID}-x2")]
    port.place_script = [_res(bp.STATE_QUEUED, "X9")]
    port.status_script = {"X9": _res(bp.STATE_CANCELLED, "X9")}
    mgr, audits = _mgr(port, store, chan)
    await mgr._drive_closing(store.get(RUNG_ID), NOW_ET)
    assert store.get(RUNG_ID).status == RUNG_CLOSING      # NOT reopened
    assert not any(a[0] == "mace_closing_reopen" for a in audits)


# ── 6. REOPEN GUARD (invariant) ──

def test_reopen_from_closing_refuses_when_exit_fields_set():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort()
    _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID, exit_reason=EXIT_STOP, ts="t")
    # Simulate a booked close that mislabeled status (exit_debit set) -> reopen MUST refuse.
    conn.execute("UPDATE mace_rung SET exit_debit=1.5 WHERE rung_id=?", (RUNG_ID,))
    assert store.reopen_from_closing(RUNG_ID) is False
    assert store.get(RUNG_ID).status == RUNG_CLOSING


def test_reopen_from_closing_succeeds_on_clean_closing():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort()
    _open_rung(store, pt=None)
    store.mark_closing(RUNG_ID, exit_reason=EXIT_STOP, ts="t")
    assert store.reopen_from_closing(RUNG_ID) is True
    r = store.get(RUNG_ID)
    assert r.status == RUNG_OPEN and r.exit_reason is None
    assert (r.extra or {}).get("closing") is None          # bookkeeping cleared
