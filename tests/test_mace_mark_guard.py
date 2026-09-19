"""Manager-level tests for the PT MARK-TRUST GUARD (2026-09-18).

Drives MaceManager.manage_tick against an expiry-aware fake port. Verifies that a PT-eligible mark
which fails the sane/sibling check is SUPPRESSED (rung HOLDS open, no close placed) and ALERTS
(Telegram `PT held` + `mace_pt_mark_reject` audit); and that a trusted mark still fires the PT close.
The pure guard logic is covered in test_mace_strategy_manage.py; here we prove the wiring."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.broker_port import OptionsBrokerPort, OrderResult
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec, OptionQuote
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod
from trading_corp.utils.time import ET, UTC

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

CAND_EXP = date(2026, 10, 30)   # 42 DTE from the pinned NOW
SIB_EXP = date(2026, 10, 16)    # SAME strikes, shorter-dated sibling
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=ET)
NOW_UTC = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)


class FakePort(OptionsBrokerPort):
    """Expiry-aware fake: leg_quote keys on (expiry, opt_type, strike) so a same-strike
    candidate and sibling get DIFFERENT marks. Records place_condor calls."""

    def __init__(self):
        self.quotes: dict = {}   # (expiry, opt_type, round(strike,4)) -> OptionQuote
        self.place_calls: list = []
        self.place_result = OrderResult(order_id="X1", state=bp.STATE_FILLED, processed_quantity=2.0)

    async def chain(self, symbol):
        raise NotImplementedError

    async def quote(self, symbol):
        return 64.4

    async def leg_quote(self, symbol, expiry, opt_type, strike):
        return self.quotes.get((expiry, opt_type, round(float(strike), 4)))

    async def place_condor(self, spec, contracts, net_limit, combo_id, *,
                           direction, time_in_force, fill_timeout_s):
        self.place_calls.append(SimpleNamespace(direction=direction, combo_id=combo_id,
                                                net_limit=net_limit, expiry=spec.expiry))
        return self.place_result

    async def place_resting_close(self, *a, **k):
        raise NotImplementedError

    async def cancel(self, order_id):
        pass

    async def order_status(self, order_id):
        return OrderResult(order_id=order_id, state=bp.STATE_FILLED, processed_quantity=2.0)

    async def open_orders(self):
        return []

    async def open_positions(self):
        return []

    async def snapshot(self):
        return bp.PortSnapshot(equity=10000.0)

    async def account_assertions(self):
        return bp.AccountInfo(account_number="116637293063", option_level=3,
                              account_type="joint", margin=True)


class RecChannel:
    def __init__(self):
        self.msgs: list = []

    def push(self, t):
        self.msgs.append(t)

    def push_split(self, t):
        self.msgs.append(t)

    def any(self, needle: str) -> bool:
        return any(needle in m for m in self.msgs)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dbmod.SCHEMA)
    return c


def _spec(expiry):
    return CondorSpec("XLE", expiry, 60.0, 59.0, 70.0, 71.0, 1.0)   # sp, lp, sc, lc, width


def _seed_open(store, expiry, credit):
    spec = _spec(expiry)
    rid = spec.rung_id(date(2026, 9, 17))
    store.insert_submitting(rid, spec, 2, entry_ts="2026-09-17T19:46:00+00:00",
                            entry_iso_week="2026-W38", max_risk_usd=140.0)
    store.promote_open(rid, credit_actual=credit, entry_order_id="E-" + expiry.isoformat(),
                       entry_ts="2026-09-17T19:46:00+00:00")
    return rid


def _quotes(port, expiry, sp_mid, lp_mid, sc_mid, lc_mid):
    for opt, strike, mid in (("put", 60.0, sp_mid), ("put", 59.0, lp_mid),
                             ("call", 70.0, sc_mid), ("call", 71.0, lc_mid)):
        port.quotes[(expiry, opt, round(strike, 4))] = OptionQuote(
            "XLE", expiry, strike, opt, mid - 0.01, mid + 0.01)


def _build(conn, port, chan):
    audits: list = []
    store = ex.RungStore(conn)
    notifier = MaceNotifier(channel=chan, enabled=True)
    executor = ex.MaceExecutor(
        CFG, port, store, notifier, risk_gate=lambda s, c, d: True, resting_pt=False,
        now_utc_fn=lambda: NOW_UTC, now_et_fn=lambda: NOW,
        poll_interval_s=0.001, poll_timeout_s=0.01)
    mgr = MaceManager(CFG, port, store, executor, notifier,
                      audit=lambda kind, **p: audits.append((kind, p)),
                      now_utc_fn=lambda: NOW_UTC, now_et_fn=lambda: NOW)
    return store, mgr, audits


@pytest.mark.asyncio
async def test_manager_pt_suppressed_on_arbitrage_mark_holds_and_alerts():
    # The 9/18 shape: candidate net mid 0.13 (PT-eligible at credit 0.30 -> target 0.15) but the
    # identical-strike shorter-dated sibling marks 0.205 -> arbitrage -> HOLD + alert, no close.
    conn = _conn(); port = FakePort(); chan = RecChannel()
    store, mgr, audits = _build(conn, port, chan)
    cand = _seed_open(store, CAND_EXP, credit=0.30)     # pt_target 0.15
    _seed_open(store, SIB_EXP, credit=0.20)             # sibling pt_target 0.10 (0.205 not PT-eligible)
    _quotes(port, CAND_EXP, 0.10, 0.04, 0.11, 0.04)     # candidate net mid 0.13
    _quotes(port, SIB_EXP, 0.15, 0.05, 0.155, 0.05)     # sibling net mid 0.205

    await mgr.manage_tick(NOW)

    assert all(pc.direction != bp.DIR_DEBIT for pc in port.place_calls)   # no close placed
    assert store.get(cand).status == "open"                              # rung HELD open
    assert chan.any("PT held")                                            # Telegram alert
    assert any(k == "mace_pt_mark_reject" for k, _ in audits)             # Activity Pulse audit


@pytest.mark.asyncio
async def test_manager_pt_fires_on_trusted_mark():
    # Trusted: candidate net mid 0.14 (<= target 0.15) and >= the shorter-dated sibling 0.12 ->
    # PT fires -> a DIR_DEBIT close is placed for the candidate expiry; no suppression audit.
    conn = _conn(); port = FakePort(); chan = RecChannel()
    store, mgr, audits = _build(conn, port, chan)
    _seed_open(store, CAND_EXP, credit=0.30)            # pt_target 0.15
    _seed_open(store, SIB_EXP, credit=0.20)             # sibling pt_target 0.10 (0.12 not PT-eligible)
    _quotes(port, CAND_EXP, 0.10, 0.04, 0.12, 0.04)     # candidate net mid 0.14
    _quotes(port, SIB_EXP, 0.10, 0.05, 0.12, 0.05)      # sibling net mid 0.12

    await mgr.manage_tick(NOW)

    assert any(pc.direction == bp.DIR_DEBIT and pc.expiry == CAND_EXP for pc in port.place_calls)
    assert not any(k == "mace_pt_mark_reject" for k, _ in audits)
