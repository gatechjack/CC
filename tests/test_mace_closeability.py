"""Commit 2 (2026-10-09 exit-redesign): QuoteSnapshot + the positive closeability gate +
the stop-basis fix.

The gate prices a PT/TIME close on EXECUTABLE values (natural = buy shorts @ ask, sell wings @
bid) and asks "can this ACTUALLY fill?". A dead long wing (bid 0 -> None) fails `wings_two_sided`
DETERMINISTICALLY every tick -- the wiggling-quote slip-through that defeated the point-in-time
mid-inversion guard is impossible against a positive liveness check. Pure tests cover the gate
truth table + the snapshot derivations; manager tests prove the dead-wing false-PT is blocked at
tick 1 (no close, no loop) while a liquid PT still fires and a dead-wing STOP still fires.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace import strategy as st
from trading_corp.mace.broker_port import OptionsBrokerPort, OrderResult
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import (
    CondorSpec, OptionQuote, QuoteSnapshot, EXIT_PT, EXIT_STOP, EXIT_TIME,
)
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod
from trading_corp.utils.time import ET, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")
CC = CFG.management.closeability
BAND = CFG.management.exit_winner_band    # 0.10

EXP = date(2026, 10, 30)                                 # dte 42 from NOW (no time exit)
EXP_TIME = date(2026, 10, 6)                             # dte 18 from NOW (<=21, >floor 14 -> winner)
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=ET)            # 12:00 ET (before the 15:30 time-exit slot)
NOW_LATE = datetime(2026, 9, 18, 15, 45, tzinfo=ET)      # 15:45 ET (>= time_exit_at_et -> TIME fires)
NOW_UTC = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)


def _q(strike, opt, bid, ask):
    return OptionQuote("XLE", EXP, strike, opt, bid, ask)


def _snap(*, sp=(0.14, 0.16), lp=(0.03, 0.05), sc=(0.14, 0.16), lc=(0.03, 0.05)):
    """Default: a liquid, two-sided condor. Pass a (bid, ask) with bid=None for a dead wing."""
    return QuoteSnapshot(
        sp=_q(60.0, "put", *sp), lp=_q(59.0, "put", *lp),
        sc=_q(70.0, "call", *sc), lc=_q(71.0, "call", *lc))


def _rung(credit=0.30, pt_debit=0.15, width=1.0):
    spec = CondorSpec("XLE", EXP, 60.0, 59.0, 70.0, 71.0, width)
    from trading_corp.mace.domain import RungState
    return RungState(rung_id="r", symbol="XLE", status="open", expiry=EXP, spec=spec,
                     width_dollars=width, contracts=1, credit_actual=credit, pt_debit=pt_debit)


# ══ QuoteSnapshot derivations ══

def test_snapshot_mark_and_natural_liquid():
    s = _snap()
    # mark = (0.15-0.04)+(0.15-0.04) = 0.22; natural = (0.16+0.16)-(0.03+0.03) = 0.26
    assert s.mark == pytest.approx(0.22)
    assert s.natural_debit == pytest.approx(0.26)
    assert s.wings_two_sided is True


def test_snapshot_dead_wing_mark_and_natural_none():
    # dead long call (no bid) -> mid None -> mark None; natural None (can't sell the wing).
    s = _snap(lc=(None, 0.41))
    assert s.mark is None
    assert s.natural_debit is None
    assert s.wings_two_sided is False


def test_snapshot_stop_basis_floors_dead_wing_to_zero():
    # Dead long call (no bid): plain mark is None (blind), but stop_mark/stop_natural floor ONLY the
    # dead wing to 0 (the live put wing keeps its real 0.03 bid) so a STOP can still evaluate + price.
    # stop_mark = (sp.mid - lp.bid) + (sc.mid - 0) = (0.15-0.03) + (0.15-0) = 0.27.
    s = _snap(lc=(None, 0.41))
    assert s.stop_mark == pytest.approx(0.27)
    # stop_natural = (sp.ask+sc.ask) - (lp.bid + wing0) = (0.16+0.16) - (0.03 + 0) = 0.29
    assert s.stop_natural == pytest.approx(0.29)


def test_snapshot_stop_mark_ge_mark_always():
    s = _snap()
    assert s.stop_mark >= s.mark           # wings at bid <= wings at mid -> stop fires no later


def test_snapshot_outage_all_none():
    s = QuoteSnapshot(None, None, None, None)
    assert s.mark is None and s.natural_debit is None and s.stop_mark is None
    assert s.wings_two_sided is False
    assert s.leg_mids == {"sp": None, "lp": None, "sc": None, "lc": None}


# ══ assess_closeability truth table ══

def test_gate_ok_on_liquid_winner_within_cap():
    # natural 0.26 <= target 0.15 + band 0.10 + slack 0.05 = 0.30 -> closeable.
    g = st.assess_closeability(_rung(), _snap(), CFG, exit_reason=EXIT_PT, target_debit=0.15)
    assert g.closeable and g.reason == "ok" and g.natural == pytest.approx(0.26)


def test_gate_blocks_dead_wing_wings_one_sided():
    g = st.assess_closeability(_rung(), _snap(lc=(None, 0.41)), CFG,
                               exit_reason=EXIT_PT, target_debit=0.15)
    assert not g.closeable and g.reason == "wings_one_sided"


def test_gate_blocks_short_unpriceable():
    # short call has no ask -> natural uncomputable (wings still two-sided).
    g = st.assess_closeability(_rung(), _snap(sc=(0.14, None)), CFG,
                               exit_reason=EXIT_PT, target_debit=0.15)
    assert not g.closeable and g.reason == "shorts_unpriceable"


def test_gate_blocks_natural_above_cap_the_false_pt():
    # The dead-wing false-PT NUMERICALLY: mid looks cheap (would satisfy PT) but the executable
    # natural is far above the winner cap -> the ladder can never fill -> blocked.
    # natural = (2.00+0.16)-(0.03+0.03)=2.10 >> cap 0.15+0.10+0.05=0.30.
    s = _snap(sp=(1.98, 2.00))
    g = st.assess_closeability(_rung(), s, CFG, exit_reason=EXIT_PT, target_debit=0.15)
    assert not g.closeable and g.reason == "natural_above_cap" and g.natural == pytest.approx(2.10)


def test_gate_liveness_only_when_target_none():
    # Forced-marketable / re-drive (target None): a high natural is fine as long as wings are
    # two-sided + natural computable (the marketable ladder may pay up to width).
    s = _snap(sp=(1.98, 2.00))
    g = st.assess_closeability(_rung(), s, CFG, exit_reason=EXIT_TIME, target_debit=None)
    assert g.closeable and g.reason == "ok"


def test_gate_disabled_is_passthrough():
    import dataclasses
    cfg2 = dataclasses.replace(CFG, management=dataclasses.replace(
        CFG.management, closeability=dataclasses.replace(CC, enabled=False)))
    g = st.assess_closeability(_rung(), _snap(lc=(None, 0.41)), cfg2,
                               exit_reason=EXIT_PT, target_debit=0.15)
    assert g.closeable and g.reason == "disabled"


# ══ evaluate_management stop semantics (2026-10-08 hotfix: plain mark + dead-wing short-ITM
#     fallback; REPLACES the stop_mark flooring that fired spurious stops) ══

def test_stop_fires_on_plain_mark():
    # All-liquid: plain mark 0.70 >= 2x credit 0.60 -> STOP (unchanged behaviour).
    rung = _rung(credit=0.30)                       # stop threshold 0.60; shorts 60/70
    d = st.evaluate_management(rung, 0.70, 64.0, NOW, CFG, CFG.symbols["XLE"], exdiv_within=False)
    assert d.exit_reason == EXIT_STOP


def test_no_stop_on_healthy_priceable_mark():
    rung = _rung(credit=0.30)
    d = st.evaluate_management(rung, 0.50, 64.0, NOW, CFG, CFG.symbols["XLE"], exdiv_within=False)
    assert d.exit_reason != EXIT_STOP               # 0.50 < 0.60 -> no stop


def test_SPURIOUS_STOP_REPLAY_dead_wing_otm_no_fire():
    # THE 2026-10-07 regression, exactly: plain mark None (a wing has no two-sided market) and spot
    # SAFELY BETWEEN the shorts (60 < 64.77 < 70) -> a healthy OTM condor. Must NOT stop. (Pre-hotfix,
    # stop_mark floored the dead wing to 0 and fabricated >= 0.60 -> spurious stop.)
    rung = _rung(credit=0.30)
    d = st.evaluate_management(rung, None, 64.77, NOW, CFG, CFG.symbols["XLE"], exdiv_within=False)
    assert d.exit_reason != EXIT_STOP
    assert st.stop_triggered(rung, None, 64.77, CFG.management) is False


def test_dead_wing_short_call_itm_still_fires():
    # Genuine dead-wing stop: plain mark None but spot 71 >= short_call 70 (ITM) -> real risk -> STOP.
    rung = _rung(credit=0.30)
    d = st.evaluate_management(rung, None, 71.0, NOW, CFG, CFG.symbols["XLE"], exdiv_within=False)
    assert d.exit_reason == EXIT_STOP


def test_dead_wing_short_put_itm_still_fires():
    rung = _rung(credit=0.30)
    d = st.evaluate_management(rung, None, 59.0, NOW, CFG, CFG.symbols["XLE"], exdiv_within=False)
    assert d.exit_reason == EXIT_STOP               # spot 59 <= short_put 60 (ITM)


def test_stop_triggered_credit_none_no_stop():
    import dataclasses
    rung = dataclasses.replace(_rung(credit=0.30), credit_actual=None)
    assert st.stop_triggered(rung, 0.99, 64.0, CFG.management) is False
    assert st.stop_triggered(rung, None, 71.0, CFG.management) is False


# ══ manager-level: dead-wing false-PT blocked, liquid PT fires, dead-wing stop fires ══

class _CountPort(OptionsBrokerPort):
    """Expiry/strike-keyed fake that COUNTS leg_quote calls (to prove one snapshot/tick)."""
    def __init__(self):
        self.quotes: dict = {}
        self.place_calls: list = []
        self.leg_quote_calls = 0
        self.spot = 64.0

    async def chain(self, s): raise NotImplementedError
    async def quote(self, s): return self.spot
    async def leg_quote(self, symbol, expiry, opt_type, strike):
        self.leg_quote_calls += 1
        return self.quotes.get((expiry, opt_type, round(float(strike), 4)))
    async def place_condor(self, spec, contracts, net_limit, combo_id, *, direction,
                           time_in_force, fill_timeout_s):
        self.place_calls.append(SimpleNamespace(direction=direction, combo_id=combo_id))
        return OrderResult(order_id="X1", state=bp.STATE_FILLED, processed_quantity=1.0)
    async def place_resting_close(self, *a, **k): raise NotImplementedError
    async def cancel(self, oid): pass
    async def order_status(self, oid): return OrderResult(order_id=oid, state=bp.STATE_FILLED, processed_quantity=1.0)
    async def open_orders(self): return []
    async def open_positions(self): return []
    async def snapshot(self): return bp.PortSnapshot(equity=10000.0)
    async def account_assertions(self):
        return bp.AccountInfo(account_number="116637293063", option_level=3, account_type="joint", margin=True)


class _Chan:
    def __init__(self): self.msgs = []
    def push(self, t): self.msgs.append(t)
    def push_split(self, t): self.msgs.append(t)
    def any(self, n): return any(n in m for m in self.msgs)


def _build(port):
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    store = ex.RungStore(conn)
    chan = _Chan(); audits = []
    notifier = MaceNotifier(channel=chan, enabled=True)
    execu = ex.MaceExecutor(CFG, port, store, notifier, risk_gate=lambda s, c, d: True,
                            resting_pt=False, now_utc_fn=lambda: NOW_UTC, now_et_fn=lambda: NOW,
                            poll_interval_s=0.001, poll_timeout_s=0.01)
    mgr = MaceManager(CFG, port, store, execu, notifier,
                      audit=lambda k, **p: audits.append((k, p)),
                      now_utc_fn=lambda: NOW_UTC, now_et_fn=lambda: NOW)
    return store, mgr, audits, chan


def _seed(store, credit=0.30, expiry=EXP):
    spec = CondorSpec("XLE", expiry, 60.0, 59.0, 70.0, 71.0, 1.0)
    rid = spec.rung_id(date(2026, 9, 17))
    store.insert_submitting(rid, spec, 1, entry_ts="2026-09-17T19:46:00+00:00",
                            entry_iso_week="2026-W38", max_risk_usd=70.0)
    store.promote_open(rid, credit_actual=credit, entry_order_id="E1",
                       entry_ts="2026-09-17T19:46:00+00:00")
    return rid


def _put(port, strike, opt, bid, ask, expiry=EXP):
    port.quotes[(expiry, opt, round(strike, 4))] = OptionQuote("XLE", expiry, strike, opt, bid, ask)


# ══ manager-level: gate exercised via TIME (the PT mark-trust guard is PT-only; the gate is the
# second, positive line for PT AND the sole gate for TIME). A dead/illiquid-wing TIME close is
# BLOCKED -> ride (Commit 3); a liquid TIME close fires; a STOP is NEVER gated. ══

@pytest.mark.asyncio
async def test_manager_time_blocked_on_nobid_wing():
    # No-bid long wing -> mark None, but TIME is mark-INDEPENDENT so the decision still fires; the
    # gate blocks on wings_one_sided -> no close, rung OPEN (Commit 3 turns this hold into a ride).
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)
    _put(port, 60.0, "put", 0.07, 0.09, EXP_TIME)
    _put(port, 59.0, "put", 0.02, 0.04, EXP_TIME)
    _put(port, 70.0, "call", 0.07, 0.09, EXP_TIME)
    _put(port, 71.0, "call", None, 0.41, EXP_TIME)        # DEAD wing (no bid)
    await mgr.manage_tick(NOW_LATE)
    assert any(k == "mace_ride_enter" and p.get("gate") == "wings_one_sided" for k, p in audits)
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # no close attempted
    r = store.get(rid)
    assert r.status == "open" and (r.extra or {}).get("disposition") == "riding"  # rides to expiry


@pytest.mark.asyncio
async def test_manager_pt_blocked_natural_above_cap_the_10_5_shape():
    # THE 10/5 shape, exactly: a tiny-bid (0.01) garbage wing makes the condor MID look cheap
    # (PT-eligible) AND non-inverted (so the mark-trust guard PASSES -- the wiggle), but the
    # EXECUTABLE natural is above the winner cap. A wing-bid-EXISTS check alone would miss this
    # (bid 0.01 != None); the gate's natural_above_cap catches it -> block, no close, OPEN (ride).
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)      # pt_target 0.15; dte 18 (fallback no-reject)
    # Prime the guard baseline so tick-1 is not the silent no_baseline hold (seed+fire-next).
    mgr._pt_mark_trust[rid] = {"last_mark": 0.10, "repeat": 1, "last_trusted": 0.10}
    _put(port, 60.0, "put", 0.08, 0.10, EXP_TIME)         # sp mid 0.09
    _put(port, 59.0, "put", 0.01, 0.03, EXP_TIME)         # lp mid 0.02
    _put(port, 70.0, "call", 0.20, 0.24, EXP_TIME)        # sc mid 0.22
    _put(port, 71.0, "call", 0.01, 0.41, EXP_TIME)        # lc tiny-bid garbage (mid 0.21, NOT inverted)
    # mark = (0.09-0.02)+(0.22-0.21) = 0.08 <= 0.15 (PT) ; stop_mark 0.29 < 0.60 ; not inverted.
    # natural = (0.10+0.24)-(0.01+0.01) = 0.32 > cap 0.15+0.10+0.05 = 0.30 -> natural_above_cap.
    await mgr.manage_tick(NOW_LATE)
    assert any(k == "mace_ride_enter" and p.get("gate") == "natural_above_cap" for k, p in audits)
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)
    r = store.get(rid)
    assert r.status == "open" and (r.extra or {}).get("disposition") == "riding"


@pytest.mark.asyncio
async def test_manager_time_fires_when_closeable():
    # Control: a liquid, closeable TIME winner fires normally (gate is a no-op pass) -> close placed.
    # mark kept ABOVE pt_target so TIME (not PT) is the decision (precedence stop>PT>time).
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30, expiry=EXP_TIME)      # pt_target 0.15, stop 0.60
    _put(port, 60.0, "put", 0.20, 0.22, EXP_TIME)         # sp mid 0.21
    _put(port, 59.0, "put", 0.03, 0.05, EXP_TIME)         # lp mid 0.04
    _put(port, 70.0, "call", 0.20, 0.22, EXP_TIME)        # sc mid 0.21
    _put(port, 71.0, "call", 0.03, 0.05, EXP_TIME)        # lc mid 0.04
    # mark = (0.21-0.04)+(0.21-0.04) = 0.34 (>0.15 no PT, <0.60 no stop); dte 18 + 15:45 -> TIME.
    # natural = (0.22+0.22)-(0.03+0.03) = 0.38 <= cap mark0.34+0.10+0.05 = 0.49 -> closeable.
    await mgr.manage_tick(NOW_LATE)
    assert any(pc.direction == bp.DIR_DEBIT for pc in port.place_calls)   # TIME close placed
    assert not any(k == "mace_close_blocked" for k, _ in audits)


@pytest.mark.asyncio
async def test_manager_dead_wing_stop_still_fires():
    # A dead wing must NEVER block a GENUINE STOP. 2026-10-08: "genuine" = a SHORT is ITM (real
    # risk), not merely illiquid wings on an OTM condor (that was the spurious-stop regression).
    # Here spot 71 >= short_call 70 (ITM) with the wings dead + plain mark None -> the dead-wing
    # fallback fires the stop (ungated) and a close is placed via stop_natural (wings given away).
    port = _CountPort(); port.spot = 71.0                 # short call 70 ITM -> genuine risk
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)                       # stop threshold 0.60; EXP (dte 42, no time)
    _put(port, 60.0, "put", 0.02, 0.04)                   # sp OTM
    _put(port, 59.0, "put", None, 0.03)                   # lp DEAD (no bid)
    _put(port, 70.0, "call", 1.30, 1.34)                  # sc ITM (spot 71)
    _put(port, 71.0, "call", None, 0.41)                  # lc DEAD -> plain mark None
    # plain mark None (dead wings); spot 71 >= short_call 70 -> stop_triggered fallback -> STOP.
    await mgr.manage_tick(NOW)
    assert any(pc.direction == bp.DIR_DEBIT for pc in port.place_calls)   # stop close placed
    assert not any(k == "mace_close_blocked" for k, _ in audits)         # stop is never gated


@pytest.mark.asyncio
async def test_manager_spurious_stop_closing_rung_self_heals():
    # THE 2026-10-08 live wedge (XLE 57.5/56.5/67/68 @ spot 64.77): a dead-wing OTM condor was
    # SPURIOUSLY stopped (status=closing, exit_reason=stop) by the old stop_mark flooring. On a tick
    # where the stop is no longer valid (plain mark None + both shorts OTM), _drive_closing REOPENS
    # it to open (clears the wedge automatically) instead of re-driving an unwarranted/unfillable close.
    port = _CountPort(); port.spot = 64.77                # between shorts 60/70 -> healthy OTM
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)                       # shorts 60/70; stop thresh 0.60
    _put(port, 60.0, "put", 0.07, 0.09)                   # sp OTM
    _put(port, 59.0, "put", None, 0.03)                   # lp DEAD -> plain mark None
    _put(port, 70.0, "call", 0.07, 0.09)                  # sc OTM
    _put(port, 71.0, "call", None, 0.41)                  # lc DEAD
    store.mark_closing(rid, exit_reason=EXIT_STOP, ts="2026-10-08T13:46:00+00:00")  # wedged
    await mgr.manage_tick(NOW)                            # NOW 12:00 ET -> no time exit
    r = store.get(rid)
    assert r.status == "open"                             # self-healed (reopened)
    assert any(k == "mace_closing_reopen" for k, _ in audits)
    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # no close attempted


@pytest.mark.asyncio
async def test_manager_genuine_stop_closing_rung_keeps_driving_not_reopened():
    # Negative: a GENUINE stop (short ITM) in CLOSING must NOT self-heal -- it keeps re-driving.
    port = _CountPort(); port.spot = 71.0                 # short call 70 ITM -> stop still valid
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    _put(port, 60.0, "put", 0.02, 0.04); _put(port, 59.0, "put", None, 0.03)
    _put(port, 70.0, "call", 1.30, None)                  # short ask None -> close unpriceable (no place)
    _put(port, 71.0, "call", None, 0.41)
    store.mark_closing(rid, exit_reason=EXIT_STOP, ts="2026-10-08T13:46:00+00:00")
    await mgr.manage_tick(NOW)
    assert store.get(rid).status == "closing"             # still committed (stop valid, short ITM)
    assert not any(k == "mace_closing_reopen" for k, _ in audits)


@pytest.mark.asyncio
async def test_single_snapshot_four_leg_quotes_on_benign_tick():
    # One snapshot/tick: a benign (no-exit) tick fetches the 4 legs EXACTLY once (was 8 on a
    # PT-eligible tick: mark() + leg_mids()). No sibling, no close ladder -> exactly 4.
    port = _CountPort()
    store, mgr, audits, chan = _build(port)
    _seed(store, credit=0.30)                             # pt_target 0.15, stop 0.60
    _put(port, 60.0, "put", 0.20, 0.22)                   # mark = (0.21-0.04)+(0.21-0.04)=0.34:
    _put(port, 59.0, "put", 0.03, 0.05)                   #   0.15 < 0.34 < 0.60 -> HOLD (no exit)
    _put(port, 70.0, "call", 0.20, 0.22)
    _put(port, 71.0, "call", 0.03, 0.05)
    port.leg_quote_calls = 0
    outs = await mgr.manage_tick(NOW)
    assert outs == []                                     # benign -> no exit
    assert port.leg_quote_calls == 4                      # ONE snapshot, not two fetches
