"""Structural-stop one-tick confirmation (2026-11-17, Part B commit 2).

THE 11-17 SPY-732 false stop: a transient 30s leg-quote timeout on the LIQUID 729 long put
(3 pts OTM) made the condor mark None for ONE tick; the structural fallback then fired on a
short put 1 cent ITM (spot 731.99 vs 732.0) while the condor's market value sat at ~63% of the
2x stop level. Fix: the dead-wing structural stop fires only when (mark unpriceable + valid
spot + short ITM) held on two CONSECUTIVE evaluations of the rung. A transient quote failure
self-heals next tick (the mark prices again -> the 2x test resumes); a genuine dead-wing
breach persists and exits one tick later. The CLOSING re-eval keeps default
structural_confirmed=True (the stop already fired once; the armed test is the right re-check).
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
from trading_corp.mace.domain import CondorSpec, OptionQuote, RungState, EXIT_STOP
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod
from trading_corp.utils.time import ET, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

EXP = date(2026, 10, 30)                                 # dte 42 from NOW (no time exit)
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=ET)            # before the 15:30 time-exit slot
NOW_UTC = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)


# ── pure: the structural_confirmed flag ──────────────────────────────────

def _spy_rung(credit=0.93):
    # The real 11-17 shape: SPY 732/729P 798/801C, credit 0.93, 1 contract.
    spec = CondorSpec("SPY", date(2026, 12, 18), 732.0, 729.0, 798.0, 801.0, 3.0)
    return RungState(rung_id="r-spy", symbol="SPY", status="open",
                     expiry=date(2026, 12, 18), spec=spec, width_dollars=3.0,
                     contracts=1, credit_actual=credit)


def test_replay_2026_11_17_first_tick_structural_holds():
    # THE 11-17 inputs: mark None (729P quote timeout), spot 731.99 (1 cent ITM on the 732
    # short put). Unconfirmed (first tick) -> HOLD.
    rung = _spy_rung()
    assert st.stop_triggered(rung, None, 731.99, CFG.management,
                             structural_confirmed=False) is False
    d = st.evaluate_management(rung, None, 731.99, NOW, CFG, CFG.symbols["SPY"],
                               exdiv_within=False, structural_confirmed=False)
    assert d.exit_reason is None and not d.should_exit


def test_confirmed_second_tick_structural_fires():
    rung = _spy_rung()
    assert st.stop_triggered(rung, None, 731.99, CFG.management,
                             structural_confirmed=True) is True
    d = st.evaluate_management(rung, None, 731.99, NOW, CFG, CFG.symbols["SPY"],
                               exdiv_within=False, structural_confirmed=True)
    assert d.exit_reason == EXIT_STOP and "dead-wing" in d.detail


def test_default_is_confirmed_for_history_less_callers():
    # The CLOSING re-eval calls stop_triggered with no flag -> single-shot semantics kept.
    rung = _spy_rung()
    assert st.stop_triggered(rung, None, 731.99, CFG.management) is True


def test_mark_based_stop_ignores_confirmation_flag():
    rung = _spy_rung()
    assert st.stop_triggered(rung, 1.90, 760.0, CFG.management,
                             structural_confirmed=False) is True


# ── manager-level: arm -> self-heal -> re-arm; arm -> persist -> fire ────

class _CountPort(OptionsBrokerPort):
    def __init__(self):
        self.quotes: dict = {}
        self.place_calls: list = []
        self.spot = 64.0

    async def chain(self, s): raise NotImplementedError
    async def quote(self, s): return self.spot
    async def leg_quote(self, symbol, expiry, opt_type, strike):
        return self.quotes.get((expiry, opt_type, round(float(strike), 4)))
    async def place_condor(self, spec, contracts, net_limit, combo_id, *, direction,
                           time_in_force, fill_timeout_s):
        self.place_calls.append(SimpleNamespace(direction=direction, combo_id=combo_id))
        return OrderResult(order_id="X1", state=bp.STATE_FILLED, processed_quantity=1.0)
    async def place_resting_close(self, *a, **k): raise NotImplementedError
    async def cancel(self, oid): pass
    async def order_status(self, oid):
        return OrderResult(order_id=oid, state=bp.STATE_FILLED, processed_quantity=1.0)
    async def open_orders(self): return []
    async def open_positions(self): return []
    async def snapshot(self): return bp.PortSnapshot(equity=10000.0)
    async def account_assertions(self):
        return bp.AccountInfo(account_number="116637293063", option_level=3,
                              account_type="joint", margin=True)


class _Chan:
    def __init__(self): self.msgs = []
    def push(self, t): self.msgs.append(t)
    def push_split(self, t): self.msgs.append(t)


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


def _seed(store, credit=0.30):
    spec = CondorSpec("XLE", EXP, 60.0, 59.0, 70.0, 71.0, 1.0)
    rid = spec.rung_id(date(2026, 9, 17))
    store.insert_submitting(rid, spec, 1, entry_ts="2026-09-17T19:46:00+00:00",
                            entry_iso_week="2026-W38", max_risk_usd=70.0)
    store.promote_open(rid, credit_actual=credit, entry_order_id="E1",
                       entry_ts="2026-09-17T19:46:00+00:00")
    return rid


def _liquid_hold_quotes(port):
    """All four legs two-sided; mark 0.34 sits between PT 0.15 and stop 0.60 -> HOLD."""
    port.quotes[(EXP, "put", 60.0)] = OptionQuote("XLE", EXP, 60.0, "put", 0.20, 0.22)
    port.quotes[(EXP, "put", 59.0)] = OptionQuote("XLE", EXP, 59.0, "put", 0.03, 0.05)
    port.quotes[(EXP, "call", 70.0)] = OptionQuote("XLE", EXP, 70.0, "call", 0.20, 0.22)
    port.quotes[(EXP, "call", 71.0)] = OptionQuote("XLE", EXP, 71.0, "call", 0.03, 0.05)


@pytest.mark.asyncio
async def test_manager_transient_timeout_self_heals_no_stop():
    # Tick 1: no leg quotes (the timeout shape -> mark None) + spot ITM -> ARM + hold.
    # Tick 2: quotes restored, mark 0.34 < stop 0.60 -> plain hold, arm cleared.
    # Tick 3: transient again -> ARMS AGAIN (not fire). Net: zero exits, two pending audits.
    port = _CountPort(); port.spot = 71.0                 # short call 70 "ITM" by spot
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    await mgr.manage_tick(NOW)                            # tick 1: mark None -> arm
    _liquid_hold_quotes(port)
    await mgr.manage_tick(NOW)                            # tick 2: mark prices -> heal
    port.quotes.clear()
    await mgr.manage_tick(NOW)                            # tick 3: transient again -> re-arm
    assert store.get(rid).status == "open"
    assert not any(k == "mace_manage_exit" for k, _ in audits)
    assert port.place_calls == []
    assert sum(1 for k, _ in audits if k == "mace_structural_stop_pending") == 2


@pytest.mark.asyncio
async def test_manager_persistent_dead_wing_fires_on_second_tick():
    port = _CountPort(); port.spot = 59.0                 # short put 60 ITM (put side)
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    await mgr.manage_tick(NOW)                            # arm
    assert store.get(rid).status == "open"
    await mgr.manage_tick(NOW)                            # confirmed -> STOP
    assert any(k == "mace_manage_exit" and p.get("reason") == EXIT_STOP for k, p in audits)


@pytest.mark.asyncio
async def test_manager_invalid_spot_never_arms():
    # Interplay with B commit 1: spot 0.0 is not a structural observation at all.
    port = _CountPort(); port.spot = 0.0
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    await mgr.manage_tick(NOW)
    await mgr.manage_tick(NOW)
    assert store.get(rid).status == "open"
    assert not any(k == "mace_structural_stop_pending" for k, _ in audits)
    assert not any(k == "mace_manage_exit" for k, _ in audits)
