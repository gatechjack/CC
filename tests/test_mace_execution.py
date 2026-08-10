"""Phase-3 adversarial tests for mace/execution.py.

Drives MaceExecutor against a scriptable FakePort + an in-memory mace_rung DB.
Coverage: entry credit ladder (walk-down, distinct ref_ids, floor drift, cutoff,
exhaustion), cancel races at every stage, the absolute fake-fill guard (exception
/ partial / unconfirmed NEVER books and NEVER double-places), the resting-GTC PT
lifecycle, the emulated-market exit debit ladder (walk-up, ceiling, exhaustion ->
stays CLOSING + URGENT), and the reconcile state machine (PT poll, crash-recovery
drain by combo_id, abandon past horizon).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.broker_port import OptionsBrokerPort, OpenOrder, OrderResult
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import (
    CondorSpec, OptionQuote, EXIT_PT, EXIT_STOP,
    RUNG_ABANDONED, RUNG_CLOSED, RUNG_CLOSING, RUNG_OPEN, RUNG_SUBMITTING,
)
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod
from trading_corp.utils.time import ET, UTC

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

SESSION = date(2026, 8, 10)          # Monday
EXPIRY = date(2026, 9, 18)
SPEC = CondorSpec("SPY", EXPIRY, 585.0, 582.0, 615.0, 618.0, 3.0)  # width 3
RUNG_ID = SPEC.rung_id(SESSION)
ISO_WK = "2026-W33"


# ── scriptable fake port ───────────────────────────────────────────────────

class FakePort(OptionsBrokerPort):
    def __init__(self) -> None:
        self.quotes: dict[tuple[str, float], OptionQuote] = {}
        self.place_script: list = []       # OrderResult | Exception, popped per place
        self.place_calls: list = []
        self.status_script: dict = {}       # order_id -> OrderResult | list | Exception
        self.status_calls: list = []
        self.cancel_calls: list = []
        self.resting_script: list = []      # id str | Exception, popped
        self.resting_calls: list = []
        self.open_orders_ret = []
        self.positions_ret = []
        self._pt = 0

    async def chain(self, symbol):  # unused here
        raise NotImplementedError

    async def leg_quote(self, symbol, expiry, opt_type, strike):
        return self.quotes.get((opt_type, round(float(strike), 4)))

    async def place_condor(self, spec, contracts, net_limit, combo_id, *,
                           direction, time_in_force, fill_timeout_s):
        self.place_calls.append(SimpleNamespace(
            spec=spec, contracts=contracts, net_limit=net_limit, combo_id=combo_id,
            direction=direction, tif=time_in_force, timeout=fill_timeout_s))
        item = self.place_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def place_resting_close(self, spec, contracts, net_debit_limit, ref_id):
        self.resting_calls.append(SimpleNamespace(
            ref_id=ref_id, debit=net_debit_limit, contracts=contracts))
        if self.resting_script:
            item = self.resting_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        self._pt += 1
        return f"PT-{self._pt}"

    async def cancel(self, order_id):
        self.cancel_calls.append(order_id)

    async def order_status(self, order_id):
        self.status_calls.append(order_id)
        v = self.status_script.get(order_id)
        if v is None:
            raise KeyError(f"no status script for {order_id}")
        if isinstance(v, Exception):
            raise v
        if isinstance(v, list):
            return v.pop(0) if len(v) > 1 else v[0]
        return v

    async def open_orders(self):
        if isinstance(self.open_orders_ret, Exception):
            raise self.open_orders_ret
        return list(self.open_orders_ret)

    async def open_positions(self):
        return list(self.positions_ret)

    async def snapshot(self):
        return bp.PortSnapshot(equity=10000.0)

    async def account_assertions(self):
        return bp.AccountInfo(account_number="116637293063", option_level=3,
                              account_type="joint", margin=True)


# ── helpers ────────────────────────────────────────────────────────────────

class RecChannel:
    def __init__(self):
        self.msgs: list[str] = []

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


def _res(state, order_id="O", pq=0.0):
    return OrderResult(order_id=order_id, state=state, processed_quantity=pq)


def _entry_quotes(port: FakePort):
    """credit_mid = (1.00-0.40)+(1.10-0.50) = 1.20; floor = 0.30*3 = 0.90."""
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 0.98, 1.02, -0.20),
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.38, 0.42, -0.12),
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 1.08, 1.12, 0.20),
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.48, 0.52, 0.12),
    }


def _low_credit_quotes(port: FakePort):
    """credit_mid = 0.91 so attempt-1 limit 0.89 < floor 0.90 -> credit_floor_drift."""
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 0.83, 0.87, -0.20),   # mid .85
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.38, 0.42, -0.12),   # mid .40
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 0.94, 0.98, 0.20),  # mid .96
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.48, 0.52, 0.12),  # mid .50
    }  # (.85-.40)+(.96-.50)=.45+.46=.91


def _exit_quotes(port: FakePort):
    """natural = (sp.ask+sc.ask) - (lp.bid+lc.bid) = (2.0+0.10)-(0.05+0.01)=2.04."""
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 1.90, 2.00, -0.55),
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.05, 0.07, -0.30),
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 0.06, 0.10, 0.10),
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.01, 0.03, 0.05),
    }


def _executor(port, store, chan, *, et_h=15, et_mi=45):
    notifier = MaceNotifier(channel=chan, enabled=True)
    return ex.MaceExecutor(
        CFG, port, ex.RungStore(store) if isinstance(store, sqlite3.Connection) else store,
        notifier,
        now_utc_fn=lambda: datetime(2026, 8, 10, 19, 45, tzinfo=UTC),
        now_et_fn=lambda: datetime(2026, 8, 10, et_h, et_mi, tzinfo=ET),
        poll_interval_s=0.001, poll_timeout_s=0.01)


def _ev(contracts=1):
    return SimpleNamespace(spec=SPEC, contracts=contracts, max_risk_usd=182.0)


def _open_rung(store: ex.RungStore, *, credit=1.18, pt="PT1", pt_debit=0.59):
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-10T19:40:00+00:00",
                            entry_iso_week=ISO_WK, max_risk_usd=182.0)
    store.promote_open(RUNG_ID, credit_actual=credit, entry_order_id="O1",
                       entry_ts="2026-08-10T19:40:00+00:00")
    if pt:
        store.set_pt(RUNG_ID, pt, pt_debit)
    return store.get(RUNG_ID)


# ── ENTRY LADDER ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_fills_first_attempt_books_and_places_pt():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_FILLED, "O1")]
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert out.filled and out.credit == pytest.approx(1.18)
    r = store.get(RUNG_ID)
    assert r.status == RUNG_OPEN and r.credit_actual == pytest.approx(1.18)
    assert r.pt_order_id == "PT-1" and r.pt_debit == pytest.approx(0.59)
    assert port.resting_calls[0].ref_id.endswith("-pt")
    assert chan.any("MACE ENTRY")
    # marketability + credit basis: submitted a CREDIT combo at the mid-0.02 limit
    assert port.place_calls[0].direction == bp.DIR_CREDIT
    assert port.place_calls[0].net_limit == pytest.approx(1.18)


@pytest.mark.asyncio
async def test_entry_walks_credit_down_distinct_ref_ids_fills_third():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_QUEUED, "O1"), _res(bp.STATE_QUEUED, "O2"),
                         _res(bp.STATE_FILLED, "O3")]
    port.status_script = {"O1": _res(bp.STATE_CANCELLED, "O1"),
                          "O2": _res(bp.STATE_CANCELLED, "O2")}
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert out.filled and out.attempts == 3
    assert out.credit == pytest.approx(1.16)          # 1.20 - 0.02 - 2*0.01
    combos = [c.combo_id for c in port.place_calls]
    assert combos == [f"{RUNG_ID}-a1", f"{RUNG_ID}-a2", f"{RUNG_ID}-a3"]
    assert len(set(combos)) == 3                      # distinct ref_id per attempt
    assert [c.net_limit for c in port.place_calls] == pytest.approx([1.18, 1.17, 1.16])
    assert "O1" in port.cancel_calls and "O2" in port.cancel_calls


@pytest.mark.asyncio
async def test_entry_cancel_race_fill_books_off_confirmed_state():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_QUEUED, "O1")]     # pending -> cancel -> race
    port.status_script = {"O1": _res(bp.STATE_FILLED, "O1")}  # filled in the race
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert out.filled and out.credit == pytest.approx(1.18)
    assert "O1" in port.cancel_calls
    assert store.get(RUNG_ID).status == RUNG_OPEN


@pytest.mark.asyncio
async def test_entry_exception_never_books_and_keeps_anchor_for_reconcile():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [RuntimeError("network lost after submit")]
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "error"
    r = store.get(RUNG_ID)
    assert r is not None and r.status == RUNG_SUBMITTING     # anchor kept, NOT booked
    assert r.credit_actual == pytest.approx(1.18)            # basis persisted pre-place
    assert chan.any("rejected")


@pytest.mark.asyncio
async def test_entry_partial_fill_is_urgent_never_booked():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_PARTIAL, "O1", pq=1.0)]
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "partial"
    assert store.get(RUNG_ID).status == RUNG_SUBMITTING
    assert chan.any("URGENT") and chan.any("PARTIAL")


@pytest.mark.asyncio
async def test_entry_cancel_race_unconfirmed_stands_down_and_keeps_anchor():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_QUEUED, "O1")]
    port.status_script = {"O1": _res(bp.STATE_QUEUED, "O1")}   # never confirms terminal
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "unconfirmed"
    assert store.get(RUNG_ID).status == RUNG_SUBMITTING       # not double-placed
    assert len(port.place_calls) == 1                          # refused a 2nd attempt


@pytest.mark.asyncio
async def test_entry_credit_floor_drift_stands_down_clean():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _low_credit_quotes(port)
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "credit_floor_drift"
    assert store.get(RUNG_ID) is None                          # clean stand-down deletes anchor
    assert port.place_calls == []                              # never placed below floor
    assert chan.any("stand-down")


@pytest.mark.asyncio
async def test_entry_cutoff_stands_down_before_placing():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    out = await _executor(port, store, chan, et_h=15, et_mi=59).run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "cutoff"
    assert store.get(RUNG_ID) is None
    assert port.place_calls == []


@pytest.mark.asyncio
async def test_entry_exhausts_five_attempts_no_fill_is_no_trade():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_QUEUED, f"O{k}") for k in range(1, 6)]
    port.status_script = {f"O{k}": _res(bp.STATE_CANCELLED, f"O{k}") for k in range(1, 6)}
    out = await _executor(port, store, chan).run_entry(_ev(), SESSION)
    assert not out.filled and out.standdown_reason == "exhausted" and out.attempts == 5
    assert len(port.place_calls) == 5
    assert store.get(RUNG_ID) is None                          # no fill = no trade, anchor cleaned


# ── EXIT LADDER + PT-FIRST ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exit_cancels_pt_first_then_debit_ladder_fills():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_CANCELLED, "PT1")}
    port.place_script = [_res(bp.STATE_FILLED, "X1")]
    out = await _executor(port, store, chan).close_rung(rung, EXIT_STOP)
    assert out.closed and out.reason == EXIT_STOP
    assert out.exit_debit == pytest.approx(2.04)               # natural rounded up
    assert out.realized_pnl == pytest.approx((1.18 - 2.04) * 100.0)
    assert port.cancel_calls[0] == "PT1"                       # PT cancelled FIRST
    assert port.place_calls[0].direction == bp.DIR_DEBIT
    assert port.place_calls[0].combo_id == f"{RUNG_ID}-x1"
    assert store.get(RUNG_ID).status == RUNG_CLOSED


@pytest.mark.asyncio
async def test_exit_pt_fills_in_race_books_pt_and_stops():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_FILLED, "PT1")}   # PT fills during cancel-confirm
    out = await _executor(port, store, chan).close_rung(rung, EXIT_STOP)
    assert out.closed and out.pt_race and out.reason == EXIT_PT
    assert out.realized_pnl == pytest.approx((1.18 - 0.59) * 100.0)
    assert port.place_calls == []                               # NO second closing order
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSED and r.exit_reason == EXIT_PT


@pytest.mark.asyncio
async def test_exit_aborts_when_pt_cancel_not_confirmed():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_QUEUED, "PT1")}   # never confirms cancelled
    out = await _executor(port, store, chan).close_rung(rung, EXIT_STOP)
    assert out.aborted and not out.closed
    assert port.place_calls == []                               # refused to double-close
    assert store.get(RUNG_ID).status == RUNG_OPEN               # unchanged; retry next tick
    assert chan.any("URGENT") and chan.any("EXIT ABORTED")


@pytest.mark.asyncio
async def test_exit_ladder_exhausted_stays_closing_urgent():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_CANCELLED, "PT1")}
    port.place_script = [_res(bp.STATE_QUEUED, f"X{k}") for k in range(1, 6)]
    for k in range(1, 6):
        port.status_script[f"X{k}"] = _res(bp.STATE_CANCELLED, f"X{k}")
    out = await _executor(port, store, chan).close_rung(rung, EXIT_STOP)
    assert out.exhausted and not out.closed and out.attempts == 5
    assert store.get(RUNG_ID).status == RUNG_CLOSING           # stays CLOSING (manual backstop)
    assert chan.any("URGENT") and chan.any("EXIT UNFILLED")


@pytest.mark.asyncio
async def test_exit_exception_never_books_stays_closing():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _exit_quotes(port)
    rung = _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_CANCELLED, "PT1")}
    port.place_script = [RuntimeError("submit failed")]
    out = await _executor(port, store, chan).close_rung(rung, EXIT_STOP)
    assert out.exhausted and not out.closed
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSING and r.realized_pnl is None


@pytest.mark.asyncio
async def test_exit_debit_never_exceeds_width_ceiling():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    # natural blown out beyond the width -> limit clamps to width*1.00 = 3.00
    port.quotes = {
        ("put", 585.0): OptionQuote("SPY", EXPIRY, 585.0, "put", 3.40, 3.50, -0.9),
        ("put", 582.0): OptionQuote("SPY", EXPIRY, 582.0, "put", 0.02, 0.04, -0.6),
        ("call", 615.0): OptionQuote("SPY", EXPIRY, 615.0, "call", 0.02, 0.04, 0.1),
        ("call", 618.0): OptionQuote("SPY", EXPIRY, 618.0, "call", 0.01, 0.03, 0.05),
    }  # natural = (3.50+0.04)-(0.02+0.01)=3.51 > ceiling 3.00
    rung = _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_CANCELLED, "PT1")}
    port.place_script = [_res(bp.STATE_FILLED, "X1")]
    await _executor(port, store, chan).close_rung(rung, EXIT_STOP)
    assert port.place_calls[0].net_limit == pytest.approx(3.00)   # clamped to width ceiling


# ── RECONCILE STATE MACHINE ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_pt_fill_books_exit():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_FILLED, "PT1")}
    await _executor(port, store, chan).reconcile(SESSION)
    r = store.get(RUNG_ID)
    assert r.status == RUNG_CLOSED and r.exit_reason == EXIT_PT
    assert r.realized_pnl == pytest.approx((1.18 - 0.59) * 100.0)
    assert chan.any("MACE EXIT")


@pytest.mark.asyncio
async def test_reconcile_pt_unexpectedly_dead_replaces():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _open_rung(store)
    port.status_script = {"PT1": _res(bp.STATE_CANCELLED, "PT1")}
    await _executor(port, store, chan).reconcile(SESSION)
    r = store.get(RUNG_ID)
    assert r.status == RUNG_OPEN and r.pt_order_id == "PT-1"    # re-placed
    assert chan.any("re-placing")


@pytest.mark.asyncio
async def test_reconcile_open_rung_missing_pt_gets_one():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _open_rung(store, pt=None)                                   # open, no resting PT
    await _executor(port, store, chan).reconcile(SESSION)
    assert store.get(RUNG_ID).pt_order_id == "PT-1"


@pytest.mark.asyncio
async def test_reconcile_drains_submitting_fill_to_open():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    # crash left a submitting anchor with a persisted broker id + credit basis
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-10T19:40:00+00:00",
                            entry_iso_week=ISO_WK, max_risk_usd=182.0)
    store.set_pending_credit(RUNG_ID, 1.16)
    store.set_entry_order(RUNG_ID, "O3")
    port.status_script = {"O3": _res(bp.STATE_FILLED, "O3")}
    port.open_orders_ret = []
    await _executor(port, store, chan).reconcile(SESSION)
    r = store.get(RUNG_ID)
    assert r.status == RUNG_OPEN and r.credit_actual == pytest.approx(1.16)
    assert r.pt_order_id == "PT-1"                              # PT placed on promote
    assert chan.any("MACE ENTRY")


@pytest.mark.asyncio
async def test_reconcile_matches_submitting_by_combo_id_prefix():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-10T19:40:00+00:00",
                            entry_iso_week=ISO_WK, max_risk_usd=182.0)
    store.set_pending_credit(RUNG_ID, 1.16)
    # no persisted broker id (place-raised window); reconcile matches by ref prefix
    port.open_orders_ret = [OpenOrder(order_id="OB", state="queued",
                                      ref_id=f"{RUNG_ID}-a1")]
    port.status_script = {"OB": _res(bp.STATE_FILLED, "OB")}
    await _executor(port, store, chan).reconcile(SESSION)
    assert store.get(RUNG_ID).status == RUNG_OPEN


@pytest.mark.asyncio
async def test_reconcile_abandons_submitting_past_horizon():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-03T14:00:00+00:00",  # 5 sessions ago
                            entry_iso_week="2026-W32", max_risk_usd=182.0)
    store.set_entry_order(RUNG_ID, "Odead")
    port.status_script = {"Odead": _res(bp.STATE_CANCELLED, "Odead")}
    port.open_orders_ret = []
    await _executor(port, store, chan).reconcile(SESSION)
    assert store.get(RUNG_ID).status == RUNG_ABANDONED
    assert chan.any("abandoned")


@pytest.mark.asyncio
async def test_reconcile_leaves_submitting_within_horizon():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-10T14:00:00+00:00",  # today
                            entry_iso_week=ISO_WK, max_risk_usd=182.0)
    store.set_entry_order(RUNG_ID, "Odead")
    port.status_script = {"Odead": _res(bp.STATE_CANCELLED, "Odead")}
    port.open_orders_ret = []
    await _executor(port, store, chan).reconcile(SESSION)
    assert store.get(RUNG_ID).status == RUNG_SUBMITTING        # within horizon -> leave


@pytest.mark.asyncio
async def test_reconcile_status_error_never_books_and_leaves_anchor():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-03T14:00:00+00:00",
                            entry_iso_week="2026-W32", max_risk_usd=182.0)
    store.set_entry_order(RUNG_ID, "Oerr")
    port.status_script = {"Oerr": RuntimeError("status api down")}  # unknown -> in-flight (safe)
    port.open_orders_ret = []
    await _executor(port, store, chan).reconcile(SESSION)
    # even past horizon, an unconfirmable order is treated as in-flight -> NOT abandoned
    assert store.get(RUNG_ID).status == RUNG_SUBMITTING


# ── tick rounding (marketability direction) ────────────────────────────────

def test_round_to_tick_directions():
    assert ex.round_to_tick(1.184, 0.01, "down") == pytest.approx(1.18)  # credit floors down
    assert ex.round_to_tick(2.031, 0.01, "up") == pytest.approx(2.04)    # debit ceilings up
    assert ex.round_to_tick(0.595, 0.01, "nearest") == pytest.approx(0.60)
