"""Spot-validity guard on the dead-wing structural stop (2026-11-17, Part B commit 1).

2026-11-16: a dying session returned spot 0.0 on every symbol; with the condor mark already
unpriceable, `stop_triggered`'s structural fallback read `0.0 <= short_put` as a short ITM and
false-stopped five rungs (IWM-262/250/263, GDX-76/78). The guard: a missing, zero or negative
spot must NEVER satisfy the structural stop -- the rung holds, and the tick already counts as
unpriced (the mark is None on that branch, so `_tick_marked_ok` was never incremented).

Companion manager fix: the CLOSING-rung spurious-stop self-heal re-evaluates the stop on fresh
quotes. With the guard alone, no-information inputs (mark None + spot 0.0 mid-outage) would read
as "stop no longer valid" and REOPEN a genuinely stopped rung. `_drive_closing` now skips the
self-heal when the re-check carries no information; the reopen still happens on real quotes.
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


# ── pure: stop_triggered spot-validity ───────────────────────────────────

def _iwm_rung(credit=0.94):
    # The real 2026-11-16 false-stop shape: IWM 262/259P 297/300C, credit 0.94, mark None.
    spec = CondorSpec("IWM", date(2026, 12, 18), 262.0, 259.0, 297.0, 300.0, 3.0)
    return RungState(rung_id="r-iwm", symbol="IWM", status="open",
                     expiry=date(2026, 12, 18), spec=spec, width_dollars=3.0,
                     contracts=2, credit_actual=credit)


def test_replay_2026_11_16_spot_zero_no_stop():
    # THE 11-16 inputs: mark None (session dying, legs unpriceable) + spot 0.0 -> must HOLD.
    rung = _iwm_rung()
    assert st.stop_triggered(rung, None, 0.0, CFG.management) is False
    d = st.evaluate_management(rung, None, 0.0, NOW, CFG, CFG.symbols["IWM"],
                               exdiv_within=False)
    assert d.exit_reason is None and not d.should_exit


def test_spot_negative_no_stop():
    rung = _iwm_rung()
    assert st.stop_triggered(rung, None, -1.0, CFG.management) is False


def test_spot_none_no_stop_unchanged():
    rung = _iwm_rung()
    assert st.stop_triggered(rung, None, None, CFG.management) is False


def test_real_itm_short_with_valid_spot_still_stops():
    # The 2026-10-30 GDX-81 genuine stop shape: mark None, spot 78.32 <= short_put 81 -> STOP.
    spec = CondorSpec("GDX", date(2026, 11, 20), 81.0, 79.0, 102.0, 104.0, 2.0)
    rung = RungState(rung_id="r-gdx", symbol="GDX", status="open",
                     expiry=date(2026, 11, 20), spec=spec, width_dollars=2.0,
                     contracts=2, credit_actual=0.64)
    assert st.stop_triggered(rung, None, 78.32, CFG.management) is True


def test_mark_based_stop_ignores_spot_validity():
    # The mark path is untouched: a real 2x mark stops even when the spot read is garbage.
    rung = _iwm_rung(credit=0.94)
    assert st.stop_triggered(rung, 1.90, 0.0, CFG.management) is True


# ── manager-level: the 11-16 outage shape holds open; closing self-heal skips on no info ──

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


@pytest.mark.asyncio
async def test_manager_outage_spot_zero_open_rung_holds():
    # 11-16 shape at manager level: no leg quotes (mark None) + spot 0.0 -> the open rung HOLDS
    # across repeated ticks; no exit decision, no close order, no stop audit.
    port = _CountPort(); port.spot = 0.0
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    await mgr.manage_tick(NOW)
    await mgr.manage_tick(NOW)
    assert store.get(rid).status == "open"
    assert not any(k == "mace_manage_exit" for k, _ in audits)
    assert port.place_calls == []


@pytest.mark.asyncio
async def test_manager_closing_rung_no_info_keeps_driving_not_reopened():
    # A CLOSING stop rung re-checked on NO-INFORMATION inputs (mark None + spot 0.0) must NOT
    # self-heal-reopen -- garbage cannot prove the stop invalid. It stays on the redrive/park path.
    port = _CountPort(); port.spot = 0.0                  # outage spot; no quotes -> mark None
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    store.mark_closing(rid, exit_reason=EXIT_STOP, ts="2026-11-16T19:40:00+00:00")
    await mgr.manage_tick(NOW)
    assert store.get(rid).status == "closing"
    assert not any(k == "mace_closing_reopen" for k, _ in audits)


@pytest.mark.asyncio
async def test_manager_closing_rung_real_quotes_still_reopens():
    # The existing self-heal is PRESERVED on real inputs: fresh valid spot between the shorts +
    # mark None (dead wing) -> stop no longer valid -> reopened to open.
    port = _CountPort(); port.spot = 64.77
    store, mgr, audits, chan = _build(port)
    rid = _seed(store, credit=0.30)
    port.quotes[(EXP, "put", 60.0)] = OptionQuote("XLE", EXP, 60.0, "put", 0.07, 0.09)
    port.quotes[(EXP, "put", 59.0)] = OptionQuote("XLE", EXP, 59.0, "put", None, 0.03)
    port.quotes[(EXP, "call", 70.0)] = OptionQuote("XLE", EXP, 70.0, "call", 0.07, 0.09)
    port.quotes[(EXP, "call", 71.0)] = OptionQuote("XLE", EXP, 71.0, "call", None, 0.41)
    store.mark_closing(rid, exit_reason=EXIT_STOP, ts="2026-10-08T13:46:00+00:00")
    await mgr.manage_tick(NOW)
    assert store.get(rid).status == "open"
    assert any(k == "mace_closing_reopen" for k, _ in audits)
