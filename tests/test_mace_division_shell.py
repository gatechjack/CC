"""Phase-3 tests for agents/divisions/robinhood_mace.py — the fail-closed
startup assertion (account identity, option-level, exclusivity, foreign-position
guard) + the hot config properties."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from trading_corp.agents.divisions.robinhood_mace import RobinhoodMaceAgent
from trading_corp.mace.broker_port import AccountInfo, OpenOptionPosition, OpenOrder
from trading_corp.mace.domain import CondorSpec, RungState

ACCT = "116637293063"
SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)


def _write(tmp_path, *, exclusivity_conflict=False, strat_enabled=True, auto=False):
    div = tmp_path / "divisions.yaml"
    lines = [
        "divisions:",
        "  - slug: robinhood_mace",
        "    broker: robinhood",
        f'    account_filter: "{ACCT}"',
        "    strategy: robinhood_mace",
        "    standby: true",
        "    enabled: true",
    ]
    if exclusivity_conflict:
        lines += [
            "  - slug: robinhood_joint_iron_condor",
            "    broker: robinhood",
            "    account_filter: joint",
            "    enabled: true",
        ]
    div.write_text("\n".join(lines) + "\n", encoding="utf-8")
    strat = tmp_path / "strategies.yaml"
    strat.write_text(
        f"robinhood_mace:\n  enabled: {str(strat_enabled).lower()}\n"
        f"  auto_execute: {str(auto).lower()}\n", encoding="utf-8")
    return div, strat


def _agent(tmp_path, *, ack=False, manager=None, **kw):
    div, strat = _write(tmp_path, **kw)
    cfg = SimpleNamespace(account_number=ACCT, acknowledge_foreign_positions=ack)
    return RobinhoodMaceAgent(cfg, divisions_yaml=div, strategies_yaml=strat,
                              manager=manager)


class MockPort:
    def __init__(self, info, positions=None, orders=None):
        self._info = info
        self._pos = positions or []
        self._ord = orders or []

    async def account_assertions(self):
        return self._info

    async def open_positions(self):
        return self._pos

    async def open_orders(self):
        return self._ord


def _info(level=3, number=ACCT):
    return AccountInfo(account_number=number, option_level=level,
                       account_type="joint_tenancy_with_ros", margin=True)


# ── config properties ─────────────────────────────────────────────────────

def test_config_properties(tmp_path):
    a = _agent(tmp_path, auto=True)
    assert a.slug == "robinhood_mace"
    assert a.account_filter == ACCT
    assert a.broker_family == "robinhood"
    assert a.standby is True
    assert a.auto_execute is True
    assert a.enabled is True


def test_enabled_requires_both_surfaces(tmp_path):
    a = _agent(tmp_path, strat_enabled=False)
    assert a.enabled is False       # strategies.yaml disabled overrides


# ── startup assertion ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_happy_path_arms_and_enables(tmp_path):
    a = _agent(tmp_path)
    d = await a.assert_startup(MockPort(_info()))
    assert d.armed and d.entries_enabled and d.ok and d.reasons == []


@pytest.mark.asyncio
async def test_option_level_below_3_disables_entries_soft(tmp_path):
    a = _agent(tmp_path)
    d = await a.assert_startup(MockPort(_info(level=2)))
    assert d.armed and not d.entries_enabled and d.exits_enabled
    assert any("option_level" in r for r in d.reasons)


@pytest.mark.asyncio
async def test_account_mismatch_refuses_to_arm(tmp_path):
    a = _agent(tmp_path)
    d = await a.assert_startup(MockPort(_info(number="999999999")))
    assert not d.armed and not d.entries_enabled
    assert any("account mismatch" in r for r in d.reasons)


@pytest.mark.asyncio
async def test_exclusivity_conflict_refuses_to_arm(tmp_path):
    a = _agent(tmp_path, exclusivity_conflict=True)
    d = await a.assert_startup(MockPort(_info()))
    assert not d.armed
    assert any("not exclusive" in r for r in d.reasons)


@pytest.mark.asyncio
async def test_foreign_position_disables_entries(tmp_path):
    a = _agent(tmp_path, manager=SimpleNamespace(
        store=SimpleNamespace(load_by_status=lambda *s: [])))
    pos = OpenOptionPosition(symbol="AAPL", option_id="X", quantity=-1.0,
                             raw={"chain_symbol": "AAPL", "option_type": "call",
                                  "strike_price": 200.0, "expiration_date": "2026-09-18"})
    d = await a.assert_startup(MockPort(_info(), positions=[pos]))
    assert d.armed and not d.entries_enabled
    assert any("foreign positions" in r for r in d.reasons)


@pytest.mark.asyncio
async def test_foreign_position_acknowledged_enables(tmp_path):
    a = _agent(tmp_path, ack=True, manager=SimpleNamespace(
        store=SimpleNamespace(load_by_status=lambda *s: [])))
    pos = OpenOptionPosition(symbol="AAPL", option_id="X", quantity=-1.0,
                             raw={"chain_symbol": "AAPL", "option_type": "call",
                                  "strike_price": 200.0, "expiration_date": "2026-09-18"})
    d = await a.assert_startup(MockPort(_info(), positions=[pos]))
    assert d.armed and d.entries_enabled


@pytest.mark.asyncio
async def test_own_condor_leg_not_flagged_foreign(tmp_path):
    rung = RungState(rung_id="mace-SPY-x", symbol="SPY", status="open",
                     expiry=date(2026, 9, 18), spec=SPEC, width_dollars=3.0, contracts=1)
    a = _agent(tmp_path, manager=SimpleNamespace(
        store=SimpleNamespace(load_by_status=lambda *s: [rung])))
    own = OpenOptionPosition(symbol="SPY", option_id="P585", quantity=-1.0,
                             raw={"chain_symbol": "SPY", "option_type": "put",
                                  "strike_price": 585.0, "expiration_date": "2026-09-18"})
    d = await a.assert_startup(MockPort(_info(), positions=[own]))
    assert d.armed and d.entries_enabled     # attributed to MACE -> not foreign


@pytest.mark.asyncio
async def test_foreign_open_order_disables_entries(tmp_path):
    a = _agent(tmp_path, manager=SimpleNamespace(
        store=SimpleNamespace(load_by_status=lambda *s: [])))
    order = OpenOrder(order_id="O9", state="queued", ref_id="pmcc-roll-1")
    d = await a.assert_startup(MockPort(_info(), orders=[order]))
    assert d.armed and not d.entries_enabled
    assert any("foreign" in r for r in d.reasons)


# ── manager / active accessors (Phase-4 loop gate) ────────────────────────

def test_manager_and_active_accessors(tmp_path):
    a = _agent(tmp_path)                      # fixture is standby:true
    assert a.manager is None and a.active is False
    a.attach_manager(object())
    assert a.manager is not None and a.active is False   # standby still gates it off


def test_active_true_only_when_not_standby(tmp_path):
    div, strat = _write(tmp_path)
    div.write_text(div.read_text(encoding="utf-8").replace("standby: true", "standby: false"),
                   encoding="utf-8")
    cfg = SimpleNamespace(account_number=ACCT, acknowledge_foreign_positions=False)
    a = RobinhoodMaceAgent(cfg, divisions_yaml=div, strategies_yaml=strat, manager=object())
    assert a.active is True and a.manager is not None
