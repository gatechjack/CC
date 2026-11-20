"""Fill-basis booking tests (2026-11-19).

Closed/opened rungs book the broker's ACTUAL processed net per contract
(`processed_premium`, else per-leg executions, from OrderResult.raw); the
attempt's limit is the audited fallback only (`mace_fill_booked_at_limit`).
Incident shape: SPY 729/726P 782/785C 2026-11-19 — limit 3.00 booked −$360,
actual fill 1.64 → −$88 against the booked credit.

Style mirrors tests/test_mace_durability.py: object.__new__ + SimpleNamespace,
asyncio.run, no fixtures.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from trading_corp.mace.broker_port import OrderResult, STATE_FILLED
from trading_corp.mace.domain import CondorSpec, RungState
from trading_corp.mace.execution import MaceExecutor

SPEC = CondorSpec(symbol="SPY", expiry=date(2026, 12, 24), short_put=729.0,
                  long_put=726.0, short_call=782.0, long_call=785.0,
                  width_dollars=3.0)

# The 2026-11-19 SPY close as Robinhood returned it: limit 3.00, processed_premium 328.
SPY_CLOSE_RAW = {
    "state": "filled", "direction": "debit",
    "processed_quantity": "2.00000", "price": "3.00000000",
    "processed_premium": "328",
    "legs": [
        {"side": "sell", "option_type": "call", "strike_price": "785.0000",
         "ratio_quantity": 1,
         "executions": [{"price": "0.69", "quantity": "1.0"},
                        {"price": "0.69", "quantity": "1.0"}]},
        {"side": "buy", "option_type": "call", "strike_price": "782.0000",
         "ratio_quantity": 1,
         "executions": [{"price": "0.77", "quantity": "1.0"},
                        {"price": "0.77", "quantity": "1.0"}]},
        {"side": "sell", "option_type": "put", "strike_price": "726.0000",
         "ratio_quantity": 1,
         "executions": [{"price": "23.47", "quantity": "1.0"},
                        {"price": "23.47", "quantity": "1.0"}]},
        {"side": "buy", "option_type": "put", "strike_price": "729.0000",
         "ratio_quantity": 1,
         "executions": [{"price": "25.03", "quantity": "1.0"},
                        {"price": "25.03", "quantity": "1.0"}]},
    ],
}

EXEC_ONLY_RAW = {k: v for k, v in SPY_CLOSE_RAW.items() if k != "processed_premium"}


def _rung(**kw):
    d = dict(rung_id="r-spy", symbol="SPY", status="closing", expiry=SPEC.expiry,
             spec=SPEC, width_dollars=3.0, contracts=2, credit_actual=1.20)
    d.update(kw)
    return RungState(**d)


def _ex(order_status_result=None):
    """Executor shell with the booking collaborators stubbed; order_status returns
    the given OrderResult or raises when None."""
    calls, audits = [], []
    ex = object.__new__(MaceExecutor)
    ex._audit_fn = lambda k, **p: audits.append((k, p))
    ex._now_utc = lambda: datetime(2026, 11, 19, 20, 31, 2, tzinfo=timezone.utc)
    ex.store = SimpleNamespace(
        mark_closed=lambda rid, **k: calls.append(("mark_closed", rid, k)),
        promote_open=lambda rid, **k: calls.append(("promote_open", rid, k)),
        clear_exit_order=lambda rid: calls.append(("clear_exit_order", rid)))
    ex.notifier = SimpleNamespace(exit=lambda **k: None, entry=lambda **k: None)

    async def _status(oid):
        if order_status_result is None:
            raise RuntimeError("order status unavailable")
        return order_status_result

    ex.port = SimpleNamespace(order_status=_status)

    async def _register_pt(*a, **k):
        calls.append(("register_pt", a, k))

    ex._register_pt = _register_pt
    ex._pt_debit_for = lambda credit: round(0.5 * credit, 2)
    ex._floor_for = lambda spec: None
    return ex, calls, audits


# ── extraction ─────────────────────────────────────────────────────────────

def test_actual_net_from_processed_premium():
    assert MaceExecutor._actual_net_from_order(SPY_CLOSE_RAW, 2) == 1.64


def test_actual_net_from_executions_fallback():
    # No processed_premium -> per-leg execution average: 25.03−23.47+0.77−0.69.
    assert MaceExecutor._actual_net_from_order(EXEC_ONLY_RAW, 2) == 1.64


def test_actual_net_unavailable():
    assert MaceExecutor._actual_net_from_order({}, 2) is None
    legs_no_exec = {"legs": [{"side": "buy", "executions": []}]}
    assert MaceExecutor._actual_net_from_order(legs_no_exec, 2) is None
    assert MaceExecutor._actual_net_from_order(SPY_CLOSE_RAW, 0) is None


# ── exit booking ───────────────────────────────────────────────────────────

def test_exit_books_actual_fill_not_limit():
    ex, calls, audits = _ex()
    res = OrderResult(order_id="o1", state=STATE_FILLED, raw=SPY_CLOSE_RAW)
    rung = _rung()
    debit = asyncio.run(ex._booked_close_debit(rung, res, 3.0, "stop"))
    assert debit == 1.64
    out = ex._book_exit_fill(rung, "stop", debit)
    assert out.exit_debit == 1.64
    assert abs(out.realized_pnl - (1.20 - 1.64) * 100.0 * 2) < 1e-9     # −88, not −360
    assert not any(k == "mace_fill_booked_at_limit" for k, _ in audits), audits


def test_exit_readback_when_immediate_fill_has_no_raw():
    # The immediate-fill path carries no payload -> ONE order_status read-back.
    ex, _, audits = _ex(order_status_result=OrderResult(
        order_id="o1", state=STATE_FILLED, raw=SPY_CLOSE_RAW))
    res = OrderResult(order_id="o1", state=STATE_FILLED)
    debit = asyncio.run(ex._booked_close_debit(_rung(), res, 3.0, "stop"))
    assert debit == 1.64
    assert not any(k == "mace_fill_booked_at_limit" for k, _ in audits), audits


def test_exit_falls_back_to_limit_and_audits():
    ex, _, audits = _ex(order_status_result=None)      # read-back raises
    res = OrderResult(order_id="o1", state=STATE_FILLED)
    debit = asyncio.run(ex._booked_close_debit(_rung(), res, 3.0, "stop"))
    assert debit == 3.0
    fb = [p for k, p in audits if k == "mace_fill_booked_at_limit"]
    assert fb and fb[0]["where"] == "exit" and fb[0]["limit"] == 3.0, audits
    assert any(k == "mace_fill_detail_error" for k, _ in audits), audits


# ── entry booking ──────────────────────────────────────────────────────────

def test_entry_books_actual_credit_fill_better_than_limit():
    # Credit entry: limit 1.20, processed_premium 246 for 2 contracts -> 1.23.
    entry_raw = {"state": "filled", "direction": "credit",
                 "processed_quantity": "2.00000", "processed_premium": "246",
                 "legs": []}
    ex, calls, audits = _ex()
    res = OrderResult(order_id="e1", state=STATE_FILLED, raw=entry_raw)
    out = asyncio.run(ex._book_entry_fill("r-spy", SPEC, 2, 1.20, res, 1))
    assert out.filled and out.credit == 1.23
    promoted = [k for c, _, k in calls if c == "promote_open"]
    assert promoted and promoted[0]["credit_actual"] == 1.23, calls
    fill_audits = [p for k, p in audits if k == "mace_entry_fill"]
    assert fill_audits and fill_audits[0]["credit"] == 1.23
    # max_risk follows the BOOKED credit: (3 − 1.23)·100·2
    assert abs(fill_audits[0]["max_risk"] - 354.0) < 1e-9
    assert not any(k == "mace_fill_booked_at_limit" for k, _ in audits), audits


def test_entry_falls_back_to_limit_and_audits():
    ex, calls, audits = _ex(order_status_result=None)
    res = OrderResult(order_id="e1", state=STATE_FILLED)
    out = asyncio.run(ex._book_entry_fill("r-spy", SPEC, 2, 1.20, res, 1))
    assert out.credit == 1.20
    promoted = [k for c, _, k in calls if c == "promote_open"]
    assert promoted and promoted[0]["credit_actual"] == 1.20, calls
    fb = [p for k, p in audits if k == "mace_fill_booked_at_limit"]
    assert fb and fb[0]["where"] == "entry" and fb[0]["limit"] == 1.20, audits


# ── PT booking ─────────────────────────────────────────────────────────────

def test_pt_books_actual_fill():
    ex, calls, audits = _ex()
    out = ex._book_pt_exit(_rung(status="open", pt_debit=0.60), actual=0.52)
    assert out.exit_debit == 0.52
    assert abs(out.realized_pnl - (1.20 - 0.52) * 100.0 * 2) < 1e-9
    assert not any(k == "mace_fill_booked_at_limit" for k, _ in audits), audits


def test_pt_falls_back_to_stored_limit_and_audits():
    ex, calls, audits = _ex()
    out = ex._book_pt_exit(_rung(status="open", pt_debit=0.60), actual=None)
    assert out.exit_debit == 0.60
    fb = [p for k, p in audits if k == "mace_fill_booked_at_limit"]
    assert fb and fb[0]["where"] == "pt" and fb[0]["limit"] == 0.60, audits
