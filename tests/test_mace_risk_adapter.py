"""MACE RiskAgent per-leg adapter — the safety-critical gate that plugs into the
structural single-risk-chokepoint (funnel + AST pin already at `f6c94b3`).

These tests prove the adapter's contract end-to-end:
  * EVERY condor leg is evaluated via RiskAgent with `is_option=True` and the
    `robinhood_mace` strategy tag (so the T5 override applies);
  * ANY single leg's reject aborts the WHOLE condor (returns False);
  * the reject-only semantic (V2): a `resize` verdict is IGNORED;
  * fail-closed: an internal error (RiskAgent raises) -> False, NEVER raises out;
  * the two signatures use the right legs (opening on credit, closing on debit);
  * WIRED INTO THE EXECUTOR: a rejecting adapter makes `_place` raise
    `MaceRiskRejected` and the broker's `place_condor` is NEVER reached.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.broker_port import (
    AccountInfo, OptionsBrokerPort, OrderResult, PortSnapshot,
)
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec
from trading_corp.mace.notify import MaceNotifier
from trading_corp.mace.risk_adapter import MaceRiskAdapter
from trading_corp.persistence import db as dbmod
from trading_corp.agents.risk import RiskVerdict

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(_ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=_ROOT / "config" / "ex_dividend_calendar.yaml")
SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)

# The four opening legs, in order: (side, opt_type, effect).
_OPENING = [("sell", "put", "open"), ("buy", "put", "open"),
            ("sell", "call", "open"), ("buy", "call", "open")]
_CLOSING = [("buy", "put", "close"), ("sell", "put", "close"),
            ("buy", "call", "close"), ("sell", "call", "close")]


class FakeRisk:
    """Records every evaluate() call; returns a configurable verdict per call.

    `verdicts` is a list consumed in order; when exhausted, defaults to approve.
    `raise_on` (0-based index) makes that call raise, exercising fail-closed.
    """

    def __init__(self, verdicts=None, raise_on=None):
        self.calls = []                       # (order, account, strategy_state)
        self._verdicts = list(verdicts or [])
        self._raise_on = raise_on

    def evaluate(self, order, account, strategy_state, regime=None,
                 realized_vol=None, db_url=None, forced_reject_reason=None):
        idx = len(self.calls)
        self.calls.append((order, account, strategy_state))
        if self._raise_on is not None and idx == self._raise_on:
            raise RuntimeError("boom")
        if idx < len(self._verdicts):
            return self._verdicts[idx]
        return RiskVerdict(verdict="approve", reason="ok")


def _approve(n):
    return [RiskVerdict(verdict="approve", reason="ok") for _ in range(n)]


def _adapter(risk, *, equity=10_000.0, audit=None):
    return MaceRiskAdapter(risk, account_number="116637293063",
                           equity_provider=(lambda: equity), db_url=None, audit=audit)


# ── every leg evaluated, correct shape ───────────────────────────────────

def test_strategy_gate_evaluates_all_four_opening_legs():
    risk = FakeRisk()
    ok = _adapter(risk).strategy_gate("SPY", SPEC, 1)
    assert ok is True
    assert len(risk.calls) == 4
    shapes = [(o.side, o.extra["option_type"], o.extra["position_effect"])
              for o, _a, _s in risk.calls]
    assert shapes == _OPENING


def test_executor_gate_credit_uses_opening_legs():
    risk = FakeRisk()
    assert _adapter(risk).executor_gate(SPEC, 1, bp.DIR_CREDIT) is True
    shapes = [(o.side, o.extra["option_type"], o.extra["position_effect"])
              for o, _a, _s in risk.calls]
    assert shapes == _OPENING


def test_executor_gate_debit_uses_closing_legs():
    risk = FakeRisk()
    assert _adapter(risk).executor_gate(SPEC, 1, bp.DIR_DEBIT) is True
    shapes = [(o.side, o.extra["option_type"], o.extra["position_effect"])
              for o, _a, _s in risk.calls]
    assert shapes == _CLOSING


def test_every_leg_carries_is_option_and_mace_strategy_tag():
    risk = FakeRisk()
    _adapter(risk).strategy_gate("SPY", SPEC, 2)
    for order, _a, _s in risk.calls:
        assert order.extra["is_option"] is True
        assert order.strategy == "robinhood_mace"
        assert order.qty == 2.0
        assert order.symbol == "SPY"


# ── any single leg reject aborts the whole condor ────────────────────────

def test_reject_on_first_leg_aborts_immediately():
    risk = FakeRisk(verdicts=[RiskVerdict(verdict="reject", reason="nope")])
    assert _adapter(risk).strategy_gate("SPY", SPEC, 1) is False
    assert len(risk.calls) == 1               # short-circuits — whole condor dead


def test_reject_on_third_leg_aborts_condor():
    risk = FakeRisk(verdicts=_approve(2) + [RiskVerdict(verdict="reject", reason="x")])
    assert _adapter(risk).executor_gate(SPEC, 1, bp.DIR_CREDIT) is False
    assert len(risk.calls) == 3


def test_reject_on_last_leg_aborts_condor():
    risk = FakeRisk(verdicts=_approve(3) + [RiskVerdict(verdict="reject", reason="x")])
    assert _adapter(risk).executor_gate(SPEC, 1, bp.DIR_DEBIT) is False
    assert len(risk.calls) == 4


# ── reject-only semantic: resize is ignored (V2) ─────────────────────────

def test_resize_verdict_is_ignored():
    risk = FakeRisk(verdicts=[RiskVerdict(verdict="resize", reason="smaller", new_qty=1.0)]
                    + _approve(3))
    assert _adapter(risk).strategy_gate("SPY", SPEC, 1) is True
    assert len(risk.calls) == 4               # kept evaluating — resize is not a reject


# ── fail-closed: never raises out of the gate ────────────────────────────

def test_fail_closed_when_risk_agent_raises():
    risk = FakeRisk(raise_on=1)
    # Must NOT propagate — returns False (reject) so the executor raises, not the gate.
    assert _adapter(risk).executor_gate(SPEC, 1, bp.DIR_CREDIT) is False


def test_fail_closed_when_verdict_is_none():
    class NoneRisk:
        def evaluate(self, *a, **k):
            return None
    assert _adapter(NoneRisk()).strategy_gate("SPY", SPEC, 1) is False


def test_equity_provider_raise_does_not_reject():
    # Equity is audit-only for MACE (resize ignored); a snapshot miss must NOT
    # spuriously reject a correctly-sized condor — it still evaluates every leg.
    risk = FakeRisk()

    def boom():
        raise RuntimeError("no snapshot")

    adapter = MaceRiskAdapter(risk, account_number="X", equity_provider=boom)
    assert adapter.strategy_gate("SPY", SPEC, 1) is True
    assert len(risk.calls) == 4
    # equity fell back to 0.0, halted False -> no drawdown/daily-loss reject
    _o, account, _s = risk.calls[0]
    assert account.equity == 0.0 and account.peak_equity == 0.0 and account.halted is False


def test_account_state_uses_equity_provider():
    risk = FakeRisk()
    _adapter(risk, equity=54321.0).strategy_gate("SPY", SPEC, 1)
    _o, account, strat = risk.calls[0]
    assert account.equity == 54321.0 and account.peak_equity == 54321.0
    assert account.account == "116637293063"
    assert strat.strategy == "robinhood_mace" and strat.halted is False
    assert strat.realized_pnl == 0.0          # never trips the daily-loss autohalt


# ── audit trail on reject ────────────────────────────────────────────────

def test_audit_records_leg_reject_with_role_and_reason():
    events = []
    risk = FakeRisk(verdicts=_approve(2)
                    + [RiskVerdict(verdict="reject", reason="short-call too fat")])
    _adapter(risk, audit=lambda k, **p: events.append((k, p))).executor_gate(
        SPEC, 1, bp.DIR_CREDIT)
    kinds = [k for k, _p in events]
    assert "mace_risk_leg_reject" in kinds
    payload = next(p for k, p in events if k == "mace_risk_leg_reject")
    assert payload["reason"] == "short-call too fat"
    assert payload["leg"] == "short_call"     # 3rd opening leg = sell call


# ── END-TO-END: adapter wired into the executor chokepoint ───────────────

class _RecPort(OptionsBrokerPort):
    def __init__(self):
        self.place_calls = 0
        self.resting_calls = 0

    async def chain(self, symbol):
        raise NotImplementedError

    async def leg_quote(self, symbol, expiry, opt_type, strike):
        return None

    async def place_condor(self, spec, contracts, net_limit, combo_id, *,
                           direction, time_in_force, fill_timeout_s):
        self.place_calls += 1
        return OrderResult(order_id="O", state=bp.STATE_FILLED,
                           processed_quantity=float(contracts))

    async def place_resting_close(self, spec, contracts, net_debit_limit, ref_id):
        self.resting_calls += 1
        return "PT"

    async def cancel(self, order_id):
        pass

    async def order_status(self, order_id):
        return OrderResult(order_id=order_id, state=bp.STATE_CANCELLED)

    async def open_orders(self):
        return []

    async def open_positions(self):
        return []

    async def snapshot(self):
        return PortSnapshot(equity=1.0)

    async def account_assertions(self):
        return AccountInfo(account_number="1", option_level=3)


def _exec_with(port, gate):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    return ex.MaceExecutor(CFG, port, ex.RungStore(conn), MaceNotifier(enabled=False),
                           risk_gate=gate, poll_interval_s=0.001, poll_timeout_s=0.01)


@pytest.mark.asyncio
async def test_executor_raises_before_placement_when_adapter_rejects():
    """The safety property, end-to-end: a RiskAgent that rejects one leg makes the
    executor's place-funnel RAISE, and the broker's place_condor is NEVER called."""
    risk = FakeRisk(verdicts=_approve(1) + [RiskVerdict(verdict="reject", reason="halt")])
    port = _RecPort()
    ex_ = _exec_with(port, _adapter(risk).executor_gate)
    with pytest.raises(ex.MaceRiskRejected):
        await ex_._place(SPEC, 1, 1.18, "c", direction=bp.DIR_CREDIT,
                         time_in_force="gfd", fill_timeout_s=1)
    assert port.place_calls == 0              # reject happened BEFORE placement


@pytest.mark.asyncio
async def test_executor_places_when_adapter_approves():
    risk = FakeRisk()                          # all approve
    port = _RecPort()
    ex_ = _exec_with(port, _adapter(risk).executor_gate)
    res = await ex_._place(SPEC, 1, 1.18, "c", direction=bp.DIR_CREDIT,
                           time_in_force="gfd", fill_timeout_s=1)
    assert res.is_filled and port.place_calls == 1
    assert len(risk.calls) == 4               # every opening leg evaluated first


@pytest.mark.asyncio
async def test_resting_pt_gates_on_closing_legs_via_adapter():
    risk = FakeRisk()
    port = _RecPort()
    ex_ = _exec_with(port, _adapter(risk).executor_gate)
    pt = await ex_._place_resting(SPEC, 1, 0.59, "pt")
    assert pt == "PT" and port.resting_calls == 1
    shapes = [(o.side, o.extra["option_type"], o.extra["position_effect"])
              for o, _a, _s in risk.calls]
    assert shapes == _CLOSING                  # a resting PT is a net-debit close
