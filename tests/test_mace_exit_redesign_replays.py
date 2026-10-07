"""Commit 4 (2026-10-09 exit-redesign): THE THREE-INCIDENT REPLAY + BYTE-PARITY artifact.

This is the Friday deploy gate in one file: each historical incident is replayed against the
redesigned pipeline and proven to no longer wedge, and a liquid winner close is proven byte-identical
to the deployed winner ladder (normal closes undisturbed).

  2026-09-18  stale/FROZEN mark          -> the mark-trust guard HOLDS (frozen); no close.
  2026-10-02  inverted wing, no sibling  -> the guard HOLDS (leg_inversion); no CLOSING.
  2026-10-05  wiggle slips the guard     -> the closeability GATE blocks -> RIDE at tick 1; ticks
                                            2-5 produce zero alerts / zero places (no loop).
              + un-latch alone           -> gate DISABLED + MaceOrderRejected x5 -> DEFER, never CLOSING
                                            (proves Commit 1 kills the latch without the gate).
  byte-parity liquid winner              -> the winner ladder limit walk == the deployed formula.
"""
from __future__ import annotations

import dataclasses
import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.domain import EXIT_PT, RUNG_CLOSING, RUNG_OPEN

from tests.test_mace_closeability import _CountPort, _build, _seed, _put, EXP_TIME, NOW_LATE
from tests.test_mace_execution import (
    CFG as EXCFG, FakePort as ExFakePort, RecChannel as ExChan,
    _conn as _ex_conn, _res, _open_rung, _executor as _ex_executor, RUNG_ID,
)


# ── 2026-09-18 — frozen/stale mark: the mark-trust guard holds ──

@pytest.mark.asyncio
async def test_incident_2026_09_18_frozen_mark_holds():
    # A PT-eligible mark that is bit-identical across >= frozen_cycles ticks is a frozen upstream
    # quote -> the mark-trust guard HOLDS (never fires the PT). Two identical ticks: tick 1 is the
    # silent no-baseline seed, tick 2 trips frozen -> reject (reason 'frozen'), no close, OPEN.
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)      # pt_target 0.15
    _put(port, 60.0, "put", 0.10, 0.12, EXP_TIME)         # mark = (0.11-0.04)+(0.10-0.04) = 0.14 <= 0.15
    _put(port, 59.0, "put", 0.03, 0.05, EXP_TIME)
    _put(port, 70.0, "call", 0.09, 0.11, EXP_TIME)
    _put(port, 71.0, "call", 0.03, 0.05, EXP_TIME)        # two-sided + closeable (so the GUARD, not the gate, holds)
    await mgr.manage_tick(NOW_LATE)                        # tick 1: no_baseline seed (silent hold)
    await mgr.manage_tick(NOW_LATE)                        # tick 2: identical mark -> frozen hold
    assert any(k == "mace_pt_mark_reject" and p.get("reason") == "frozen" for k, p in audits)
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # no close fired
    assert store.get(rid).status == RUNG_OPEN


# ── 2026-10-02 — inverted wing, no sibling: the guard holds (leg_inversion) ──

@pytest.mark.asyncio
async def test_incident_2026_10_02_inverted_wing_no_sibling_holds():
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)
    _put(port, 60.0, "put", 0.665, 0.685, EXP_TIME)       # sp mid 0.675
    _put(port, 59.0, "put", 0.46, 0.48, EXP_TIME)         # lp mid 0.47
    _put(port, 70.0, "call", 0.14, 0.16, EXP_TIME)        # sc mid 0.15
    _put(port, 71.0, "call", 0.21, 0.23, EXP_TIME)        # lc mid 0.22 > sc 0.15 -> INVERTED
    # mark = (0.675-0.47)+(0.15-0.22) = 0.135 <= 0.15 (PT-eligible); NO sibling seeded.
    await mgr.manage_tick(NOW_LATE)
    assert any(k == "mace_pt_mark_reject" and p.get("reason") == "leg_inversion" for k, p in audits)
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)
    assert store.get(rid).status == RUNG_OPEN             # held, never CLOSING


# ── 2026-10-05 — the wiggle that slipped the guard: the GATE blocks -> ride, no loop ──

@pytest.mark.asyncio
async def test_incident_2026_10_05_wiggle_rides_no_loop():
    # The wing momentarily un-inverts (guard PASSES) and the mark looks PT-eligible, but the
    # EXECUTABLE natural is above the winner cap -> the gate blocks -> RIDE at tick 1. Across 5
    # subsequent ticks the rung neither alerts again nor attempts a single close: the ~15-min loop
    # is structurally gone.
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)
    mgr._pt_mark_trust[rid] = {"last_mark": 0.10, "repeat": 1, "last_trusted": 0.10}   # past no_baseline
    _put(port, 60.0, "put", 0.08, 0.10, EXP_TIME)         # sp mid 0.09
    _put(port, 59.0, "put", 0.01, 0.03, EXP_TIME)         # lp mid 0.02
    _put(port, 70.0, "call", 0.20, 0.24, EXP_TIME)        # sc mid 0.22
    _put(port, 71.0, "call", 0.01, 0.41, EXP_TIME)        # lc tiny-bid garbage (mid 0.21, NOT inverted)
    # mark 0.08 (PT, guard passes: not inverted/frozen, fallback no-reject at dte 18) ;
    # natural = (0.10+0.24)-(0.01+0.01) = 0.32 > cap 0.30 -> gate blocks -> ride.
    for _ in range(6):
        await mgr.manage_tick(NOW_LATE)
    assert len([a for a in audits if a[0] == "mace_ride_enter"]) == 1      # entered ride exactly once
    assert len(port.place_calls) == 0                                      # NEVER attempted a close
    r = store.get(rid)
    assert r.status == RUNG_OPEN and (r.extra or {}).get("disposition") == "riding"


@pytest.mark.asyncio
async def test_incident_2026_10_05_unlatch_alone_without_gate():
    # Proof that Commit 1's un-latch kills the latch EVEN with the closeability gate DISABLED: a
    # winner close whose every attempt is MaceOrderRejected ("empty response") DEFERS (rung stays
    # OPEN), where the pre-fix generic-except path latched it CLOSING forever.
    cfg = dataclasses.replace(EXCFG, management=dataclasses.replace(
        EXCFG.management, closeability=dataclasses.replace(
            EXCFG.management.closeability, enabled=False)))
    conn = _ex_conn(); store = ex.RungStore(conn); port = ExFakePort(); chan = ExChan()
    from trading_corp.mace.domain import OptionQuote
    from tests.test_mace_execution import EXPIRY
    # liquid two-sided quotes so the winner mid is priceable (reaches _place)
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 1.90, 2.00, -0.55),
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.05, 0.07, -0.30),
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 0.06, 0.10, 0.10),
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.01, 0.03, 0.05),
    }
    rung = _open_rung(store, pt=None)
    port.place_script = [bp.MaceOrderRejected("empty response")] * 5
    port.open_orders_ret = []
    execu = _ex_executor(port, store, chan, resting_pt=False)
    execu.cfg = cfg   # gate off (defensive; close_rung doesn't consult the gate, but prove parity)
    out = await execu.close_rung(rung, EXIT_PT, pricing="winner", defer_on_unfilled=True, trigger_mid=1.95)
    assert out.deferred and store.get(RUNG_ID).status == RUNG_OPEN       # NOT latched CLOSING


# ── byte-parity: the liquid winner ladder limit walk == the deployed formula ──

@pytest.mark.asyncio
async def test_byte_parity_winner_ladder_limit_walk():
    # A liquid winner close (nothing dead) must walk the EXACT deployed winner ladder: start at the
    # fresh mid, step by band/(attempts-1) toward the fixed trigger+band cap, rounded UP to tick.
    # anchor=mid=0.50, attempts 5, band 0.10 (step 0.025), tick 0.01 -> [0.50,0.53,0.55,0.58,0.60].
    conn = _ex_conn(); store = ex.RungStore(conn); port = ExFakePort(); chan = ExChan()
    from trading_corp.mace.domain import OptionQuote
    from tests.test_mace_execution import EXPIRY
    port.quotes = {   # _credit_mid = (0.50-0.05)+(0.10-0.05) = 0.50 every refetch
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 0.49, 0.51, -0.40),
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.04, 0.06, -0.20),
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 0.09, 0.11, 0.20),
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.04, 0.06, 0.10),
    }
    rung = _open_rung(store, pt=None)
    port.place_script = [_res(bp.STATE_QUEUED, f"X{k}") for k in range(1, 6)]
    port.status_script = {f"X{k}": _res(bp.STATE_CANCELLED, f"X{k}") for k in range(1, 6)}
    out = await _ex_executor(port, store, chan, resting_pt=False).close_rung(
        rung, EXIT_PT, pricing="winner", defer_on_unfilled=True, trigger_mid=0.50)
    limits = [round(pc.net_limit, 2) for pc in port.place_calls]
    assert limits == [0.50, 0.53, 0.55, 0.58, 0.60]       # deployed winner formula, byte-exact
    assert out.deferred                                   # clean no-fill winner -> DEFER (stays OPEN)
    assert store.get(RUNG_ID).status == RUNG_OPEN
