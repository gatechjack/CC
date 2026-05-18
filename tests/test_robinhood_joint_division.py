"""Tests for the RobinhoodJointAgent division shell (step 8).

The shell is deliberately thin — it reads divisions.yaml, exposes
metadata properties, and routes scan()/manage() to an injected strategy
module. Tests use a tmp divisions.yaml fixture so we can flip
enabled/disabled/strategy fields without touching the production
config, and a MagicMock for the strategy to verify routing.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.robinhood_joint import (
    RobinhoodJointAgent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, *, enabled: bool = True,
                account_filter: str = "joint",
                strategy: str | None = "robinhood_joint_iron_condor",
                include_robinhood_joint: bool = True) -> Path:
    """Build a divisions.yaml with the robinhood_joint entry configured."""
    lines = ["divisions:"]
    if include_robinhood_joint:
        lines += [
            "  - slug: robinhood_joint",
            "    name: Robinhood Joint",
            "    broker: robinhood",
            f"    account_filter: {account_filter}",
            "    intent: aggressive",
            "    benchmark: SPY",
            "    target_annual_return: 0.20",
            f"    enabled: {str(enabled).lower()}",
        ]
        if strategy is not None:
            lines.append(f"    strategy: {strategy}")
    # Add a decoy entry to verify slug lookup works when others exist.
    lines += [
        "  - slug: robinhood_pmcc",
        "    name: Robinhood PMCC",
        "    broker: robinhood",
        "    account_filter: individual",
        "    intent: aggressive",
        "    benchmark: SPY",
        "    target_annual_return: 0.30",
        "    strategy: robinhood_pmcc",
        "    enabled: true",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def divisions_yaml(tmp_path: Path) -> Path:
    return _write_yaml(tmp_path / "divisions.yaml")


# ---------------------------------------------------------------------------
# Instantiation + config reads
# ---------------------------------------------------------------------------


def test_instantiates_and_reads_config(divisions_yaml):
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml)
    assert a.slug == "robinhood_joint"
    assert a.enabled is True
    assert a.account_filter == "joint"
    assert a.broker_family == "robinhood"
    assert a.strategy_name == "robinhood_joint_iron_condor"
    assert a.standby is False
    assert a.has_strategy is False


def test_account_filter_is_joint_in_production_config():
    """Smoke-check against the real config/divisions.yaml so a future
    rename of the joint account_filter trips this test loudly."""
    a = RobinhoodJointAgent()        # default path = config/divisions.yaml
    assert a.account_filter == "joint"
    assert a.broker_family == "robinhood"
    assert a.strategy_name == "robinhood_joint_iron_condor"


def test_missing_config_file_returns_inactive_agent(tmp_path: Path):
    a = RobinhoodJointAgent(divisions_yaml=tmp_path / "does_not_exist.yaml")
    assert a.enabled is False
    assert a.account_filter == ""
    assert a.broker_family == ""
    assert a.strategy_name is None


def test_missing_robinhood_joint_entry_returns_inactive(tmp_path: Path):
    p = _write_yaml(tmp_path / "div.yaml", include_robinhood_joint=False)
    a = RobinhoodJointAgent(divisions_yaml=p)
    assert a.enabled is False
    assert a.account_filter == ""


def test_config_hot_reload_picks_up_edits(tmp_path: Path):
    p = _write_yaml(tmp_path / "div.yaml", account_filter="joint")
    a = RobinhoodJointAgent(divisions_yaml=p)
    assert a.account_filter == "joint"

    # Rewrite with a different account_filter; reset mtime cache so the
    # next read picks it up regardless of clock resolution.
    _write_yaml(tmp_path / "div.yaml", account_filter="joint_brokerage")
    a._mtime = 0.0
    assert a.account_filter == "joint_brokerage"


# ---------------------------------------------------------------------------
# scan() / manage() routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_routes_to_attached_strategy(divisions_yaml):
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml)
    fake_broker = MagicMock()
    expected = [["leg1", "leg2", "leg3", "leg4"]]   # sentinel
    strategy = MagicMock()
    strategy.scan = AsyncMock(return_value=expected)
    a.attach_strategy(strategy)

    out = await a.scan(fake_broker, regime="uptrend")

    assert out == expected
    strategy.scan.assert_awaited_once_with(fake_broker, regime="uptrend")


@pytest.mark.asyncio
async def test_manage_routes_to_attached_strategy(divisions_yaml):
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml)
    fake_broker = MagicMock()
    strategy = MagicMock()
    expected_actions = [["close_leg1", "close_leg2", "close_leg3", "close_leg4"]]
    strategy.manage = AsyncMock(return_value=(expected_actions, 300))
    a.attach_strategy(strategy)

    actions, cadence = await a.manage(fake_broker)

    assert actions == expected_actions
    assert cadence == 300
    strategy.manage.assert_awaited_once_with(fake_broker)


# ---------------------------------------------------------------------------
# Kill-switches: disabled config + no strategy attached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_short_circuits_when_disabled(tmp_path: Path, caplog):
    p = _write_yaml(tmp_path / "div.yaml", enabled=False)
    a = RobinhoodJointAgent(divisions_yaml=p)
    strategy = MagicMock()
    strategy.scan = AsyncMock(return_value=[["leg"]])
    a.attach_strategy(strategy)

    with caplog.at_level("INFO"):
        out = await a.scan(broker=MagicMock())

    assert out == []
    strategy.scan.assert_not_called()
    assert any("disabled" in r.message and "scan skipped" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_manage_short_circuits_when_disabled(tmp_path: Path, caplog):
    p = _write_yaml(tmp_path / "div.yaml", enabled=False)
    a = RobinhoodJointAgent(divisions_yaml=p)
    strategy = MagicMock()
    strategy.manage = AsyncMock(return_value=([["leg"]], 60))
    a.attach_strategy(strategy)

    with caplog.at_level("INFO"):
        actions, cadence = await a.manage(broker=MagicMock())

    assert actions == []
    assert cadence == 1800                       # default idle cadence
    strategy.manage.assert_not_called()


@pytest.mark.asyncio
async def test_scan_no_op_when_no_strategy_attached(divisions_yaml, caplog):
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml)
    # Note: no attach_strategy call.
    with caplog.at_level("WARNING"):
        out = await a.scan(broker=MagicMock())
    assert out == []
    assert any("no strategy attached" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_manage_no_op_when_no_strategy_attached(divisions_yaml, caplog):
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml)
    with caplog.at_level("WARNING"):
        actions, cadence = await a.manage(broker=MagicMock())
    assert actions == []
    assert cadence == 1800
    assert any("no strategy attached" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_strategy_passed_via_constructor_is_used(divisions_yaml):
    """Strategy can be wired at construction time OR via attach_strategy."""
    strategy = MagicMock()
    strategy.scan = AsyncMock(return_value=[["from-ctor"]])
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml, strategy=strategy)
    assert a.has_strategy is True
    out = await a.scan(broker=MagicMock())
    assert out == [["from-ctor"]]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_attach_strategy_is_idempotent(divisions_yaml):
    """Re-attaching replaces the strategy — supports test setup that
    swaps in fixtures across scenarios."""
    a = RobinhoodJointAgent(divisions_yaml=divisions_yaml)
    s1 = MagicMock(name="strategy_v1")
    s2 = MagicMock(name="strategy_v2")
    a.attach_strategy(s1)
    assert a._strategy is s1
    a.attach_strategy(s2)
    assert a._strategy is s2
