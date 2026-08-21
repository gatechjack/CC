"""P1.4 - manage_tick None-tolerance (broker-outage resilience).

BUG (8/20): an RH session outage returned None broker responses; the leg-quote
parse `.get`s on None and RAISES, so the manage loop threw ~80x 'NoneType' .get
crashes (caught per-rung as mace_manage_error, but the rung went unmanaged and
the log filled with errors).

FIX (None-tolerance ONLY; manage/exit DECISION logic byte-unchanged):
executor.mark() catches the outage crash and returns a None mark. evaluate_management
is ALREADY None-mark-safe by design - it guards stop/PT on `mark is not None` and
still evaluates time (21-DTE, date-based) + exdiv (spot-based). So a None mark
degrades gracefully (no stop/PT, but a due time/exdiv exit STILL fires) instead of
crashing. This file proves: the crash is tolerated, normal marks are unchanged, the
loop survives an outage, and a due time exit is NOT suppressed by a None mark.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trading_corp.mace import execution as ex
from trading_corp.mace.domain import CondorSpec, EXIT_TIME, iso_week
from trading_corp.mace.notify import MaceNotifier

from tests.test_mace_execution import (
    FakePort, RecChannel, SPEC, _conn, _entry_quotes, _executor,
)
from tests.test_mace_manager_live_write import _FakePort, _MarkExec, _mk, _open_rung, _store
from tests.test_mace_manager_window import _cfg

UTC = timezone.utc
SESSION = date(2026, 8, 12)


class _RaisingPort(FakePort):
    """The 8/20 outage: leg_quote `.get`s on a None broker response and raises."""
    async def leg_quote(self, *a, **k):
        raise AttributeError("'NoneType' object has no attribute 'get'")


def _now_et():
    return datetime(2026, 8, 12, 15, 50, tzinfo=UTC)


def _now_utc():
    return datetime(2026, 8, 12, 19, 50, tzinfo=UTC)


# ── executor.mark: the None-tolerance guard ──────────────────────────────────

@pytest.mark.asyncio
async def test_p14_mark_tolerates_raising_leg_quote():
    # broker outage -> leg_quote raises -> mark degrades to None (not a crash)
    conn = _conn(); store = ex.RungStore(conn); port = _RaisingPort(); chan = RecChannel()
    audits = []
    exr = _executor(port, store, chan)
    exr._audit_fn = lambda kind, **p: audits.append((kind, p))
    m = await exr.mark(SPEC)
    assert m is None
    assert any(k == "mace_mark_unavailable" for k, _ in audits)   # benign degradation logged


@pytest.mark.asyncio
async def test_p14_mark_normal_unchanged():
    # valid quotes -> mark == the combo mid, byte-unchanged from before the guard
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)                       # credit_mid = (1.00-0.40)+(1.10-0.50) = 1.20
    m = await _executor(port, store, chan).mark(SPEC)
    assert m == pytest.approx(1.20)


@pytest.mark.asyncio
async def test_p14_mark_none_quotes_returns_none():
    # unpriceable legs (None quotes) already yield None via _credit_mid - unchanged
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    m = await _executor(port, store, chan).mark(SPEC)   # empty quotes -> all legs None
    assert m is None


# ── manage_tick: the 8/20 regression ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_p14_manage_tick_survives_broker_outage(tmp_path):
    # A real manager + real executor over an OUTAGE port: the tick must NOT crash,
    # must hold (mark None -> no stop/PT; DTE 44 -> no time exit), and must log the
    # benign degradation rather than a crash-and-catch mace_manage_error.
    cfg = _cfg(tmp_path, ["SPY"], ["SPY"])
    store = _store()
    _open_rung(store, "SPY", credit=0.93)     # EXPIRY 2026-09-25 -> DTE ~44, no time exit
    port = _RaisingPort()
    audits = []
    executor = ex.MaceExecutor(
        cfg, port, store, MaceNotifier(channel=None, enabled=False),
        risk_gate=lambda *a: True, audit=lambda k, **p: audits.append((k, p)),
        now_utc_fn=_now_utc, now_et_fn=_now_et)
    mgr = _mk(cfg, store, port=port, executor=executor, audits=audits)

    outs = await mgr.manage_tick(_now_et())               # must not raise
    assert outs == []                                     # graceful hold
    assert any(k == "mace_mark_unavailable" for k, _ in audits)
    assert not any(k == "mace_manage_error" for k, _ in audits)


@pytest.mark.asyncio
async def test_p14_manage_tick_time_exit_fires_despite_none_mark(tmp_path):
    # Decision-neutral proof: with mark=None (outage), a DUE 21-DTE time exit STILL
    # fires - the None mark suppresses only stop/PT, never time/exdiv.
    cfg = _cfg(tmp_path, ["SPY"], ["SPY"])
    store = _store()
    spec = CondorSpec("SPY", date(2026, 8, 25), 742.0, 739.0, 802.0, 805.0, 3.0)  # DTE 13
    rid = spec.rung_id(SESSION)
    store.insert_submitting(rid, spec, 1, entry_ts="2026-08-04T13:31:00+00:00",
                            entry_iso_week=iso_week(SESSION), max_risk_usd=207.0)
    store.promote_open(rid, credit_actual=0.93, entry_order_id="O1",
                       entry_ts="2026-08-04T13:31:00+00:00")
    execu = _MarkExec(mark_val=None)                      # broker outage: mark unavailable
    mgr = _mk(cfg, store, port=_FakePort(), executor=execu)

    outs = await mgr.manage_tick(_now_et())               # 15:50 >= time_exit_at 15:30
    assert [o.reason for o in outs] == [EXIT_TIME]        # 21-DTE exit fires with mark=None
    assert execu.closed and execu.closed[0][1] == EXIT_TIME
