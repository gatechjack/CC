"""MACE single-risk-chokepoint enforcement (Board-pinned 2026-08-10).

MACE's execution drives the broker port directly and bypasses data_exec /
ceo_graph, so the per-leg RiskAgent gate threaded through execution's place-funnel
is MACE's ONLY instance of the platform's single-risk-chokepoint invariant. This
is enforced by TEST, not convention:

  (1) STRUCTURAL — an AST walk asserts `port.place_condor` and
      `port.place_resting_close` are called ONLY inside the funnels `_place` /
      `_place_resting`. No other call site can reach the broker's place.
  (2) BEHAVIORAL — a place attempt with NO gate (fail-closed) or a REJECTING gate
      RAISES `MaceRiskRejected` and never touches the broker; an APPROVING gate
      places; the gate is consulted with (spec, contracts, direction) so a per-leg
      RiskAgent adapter can evaluate every leg.
"""
from __future__ import annotations

import ast
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.broker_port import AccountInfo, OptionsBrokerPort, OrderResult, PortSnapshot
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod

_ROOT = Path(__file__).resolve().parents[1]
_EXEC_PY = _ROOT / "trading_corp" / "mace" / "execution.py"
CFG = load_mace_config(_ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=_ROOT / "config" / "ex_dividend_calendar.yaml")
SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)
_PLACE_ATTRS = {"place_condor", "place_resting_close"}


# ── (1) structural: place_* only inside the funnels ──────────────────────

class _PlaceVisitor(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.hits = []                       # (enclosing_func, attr)

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in _PLACE_ATTRS:
            self.hits.append((self.stack[-1] if self.stack else "<module>", f.attr))
        self.generic_visit(node)


def test_place_calls_are_pinned_to_the_funnels():
    v = _PlaceVisitor()
    v.visit(ast.parse(_EXEC_PY.read_text(encoding="utf-8")))
    condor = {fn for fn, a in v.hits if a == "place_condor"}
    resting = {fn for fn, a in v.hits if a == "place_resting_close"}
    assert condor == {"_place"}, f"place_condor reached outside the funnel: {condor}"
    assert resting == {"_place_resting"}, f"place_resting_close reached outside the funnel: {resting}"
    assert v.hits, "no place_* calls found — AST parse regression"


# ── (2) behavioral: no gate / reject => raise, never place ───────────────

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
        return OrderResult(order_id="O", state=bp.STATE_FILLED, processed_quantity=float(contracts))

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


def _exec(port, risk_gate):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    return ex.MaceExecutor(CFG, port, ex.RungStore(conn), MaceNotifier(enabled=False),
                           risk_gate=risk_gate, poll_interval_s=0.001, poll_timeout_s=0.01)


@pytest.mark.asyncio
async def test_place_funnel_fail_closed_without_gate():
    port = _RecPort(); ex_ = _exec(port, None)
    with pytest.raises(ex.MaceRiskRejected):
        await ex_._place(SPEC, 1, 1.18, "c", direction=bp.DIR_CREDIT,
                         time_in_force="gfd", fill_timeout_s=1)
    assert port.place_calls == 0        # never reached the broker


@pytest.mark.asyncio
async def test_place_funnel_raises_on_reject():
    port = _RecPort(); ex_ = _exec(port, lambda s, c, d: False)
    with pytest.raises(ex.MaceRiskRejected):
        await ex_._place(SPEC, 1, 1.18, "c", direction=bp.DIR_CREDIT,
                         time_in_force="gfd", fill_timeout_s=1)
    assert port.place_calls == 0


@pytest.mark.asyncio
async def test_place_funnel_places_only_on_approve():
    port = _RecPort(); ex_ = _exec(port, lambda s, c, d: True)
    res = await ex_._place(SPEC, 1, 1.18, "c", direction=bp.DIR_CREDIT,
                           time_in_force="gfd", fill_timeout_s=1)
    assert res.is_filled and port.place_calls == 1


@pytest.mark.asyncio
async def test_resting_funnel_fail_closed_without_gate():
    port = _RecPort(); ex_ = _exec(port, None)
    with pytest.raises(ex.MaceRiskRejected):
        await ex_._place_resting(SPEC, 1, 0.59, "pt")
    assert port.resting_calls == 0


@pytest.mark.asyncio
async def test_gate_consulted_with_spec_contracts_direction():
    seen = []
    port = _RecPort()
    ex_ = _exec(port, lambda spec, contracts, direction: (
        seen.append((spec.symbol, contracts, direction)) or True))
    await ex_._place(SPEC, 1, 1.18, "c", direction=bp.DIR_CREDIT,
                     time_in_force="gfd", fill_timeout_s=1)
    await ex_._place_resting(SPEC, 1, 0.59, "pt")
    assert seen == [("SPY", 1, bp.DIR_CREDIT), ("SPY", 1, bp.DIR_DEBIT)]
