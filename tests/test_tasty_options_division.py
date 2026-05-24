"""Tests for the TastyOptionsAgent division shell.

Sibling-clone of test_robinhood_joint_division.py — same routing /
kill-switch / hot-reload contract; the only differences are slug,
broker family ("tastytrade"), and strategy name. The production
divisions.yaml smoke-check is intentionally NOT cloned in this commit
because the config entry lands in a later commit; the corresponding
smoke test is added there.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.tasty_options import TastyOptionsAgent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, *, enabled: bool = True,
                account_filter: str = "joint",
                strategy: str | None = "tasty_options_iron_condor",
                include_tasty_options: bool = True) -> Path:
    """Build a divisions.yaml with the tasty_options entry configured."""
    lines = ["divisions:"]
    if include_tasty_options:
        lines += [
            "  - slug: tasty_options",
            "    name: Tasty Options",
            "    broker: tastytrade",
            f"    account_filter: {account_filter}",
            "    intent: aggressive",
            "    benchmark: SPY",
            "    target_annual_return: 0.20",
            f"    enabled: {str(enabled).lower()}",
        ]
        if strategy is not None:
            lines.append(f"    strategy: {strategy}")
    # Decoy entry to verify slug lookup works when others exist.
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
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml)
    assert a.slug == "tasty_options"
    assert a.enabled is True
    assert a.account_filter == "joint"
    assert a.broker_family == "tastytrade"
    assert a.strategy_name == "tasty_options_iron_condor"
    assert a.standby is False
    assert a.has_strategy is False


def test_production_config_has_tasty_options_entry():
    """Smoke-check against the real config/divisions.yaml so a future
    rename of the slug / broker / strategy trips this test loudly."""
    a = TastyOptionsAgent()        # default path = config/divisions.yaml
    assert a.broker_family == "tastytrade"
    assert a.strategy_name == "tasty_options_iron_condor"
    assert a.enabled is True


def test_missing_config_file_returns_inactive_agent(tmp_path: Path):
    a = TastyOptionsAgent(divisions_yaml=tmp_path / "does_not_exist.yaml")
    assert a.enabled is False
    assert a.account_filter == ""
    assert a.broker_family == ""
    assert a.strategy_name is None


def test_missing_tasty_options_entry_returns_inactive(tmp_path: Path):
    p = _write_yaml(tmp_path / "div.yaml", include_tasty_options=False)
    a = TastyOptionsAgent(divisions_yaml=p)
    assert a.enabled is False
    assert a.account_filter == ""


def test_config_hot_reload_picks_up_edits(tmp_path: Path):
    p = _write_yaml(tmp_path / "div.yaml", account_filter="joint")
    a = TastyOptionsAgent(divisions_yaml=p)
    assert a.account_filter == "joint"
    _write_yaml(tmp_path / "div.yaml", account_filter="margin")
    a._mtime = 0.0
    assert a.account_filter == "margin"


# ---------------------------------------------------------------------------
# scan() / manage() routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_routes_to_attached_strategy(divisions_yaml):
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml)
    fake_broker = MagicMock()
    expected = [["leg1", "leg2", "leg3", "leg4"]]
    strategy = MagicMock()
    strategy.scan = AsyncMock(return_value=expected)
    a.attach_strategy(strategy)

    out = await a.scan(fake_broker, regime="uptrend")

    assert out == expected
    strategy.scan.assert_awaited_once_with(fake_broker, regime="uptrend")


@pytest.mark.asyncio
async def test_manage_routes_to_attached_strategy(divisions_yaml):
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml)
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
    a = TastyOptionsAgent(divisions_yaml=p)
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
    a = TastyOptionsAgent(divisions_yaml=p)
    strategy = MagicMock()
    strategy.manage = AsyncMock(return_value=([["leg"]], 60))
    a.attach_strategy(strategy)

    with caplog.at_level("INFO"):
        actions, cadence = await a.manage(broker=MagicMock())

    assert actions == []
    assert cadence == 1800
    strategy.manage.assert_not_called()


@pytest.mark.asyncio
async def test_scan_no_op_when_no_strategy_attached(divisions_yaml, caplog):
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml)
    with caplog.at_level("WARNING"):
        out = await a.scan(broker=MagicMock())
    assert out == []
    assert any("no strategy attached" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_manage_no_op_when_no_strategy_attached(divisions_yaml, caplog):
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml)
    with caplog.at_level("WARNING"):
        actions, cadence = await a.manage(broker=MagicMock())
    assert actions == []
    assert cadence == 1800
    assert any("no strategy attached" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_strategy_passed_via_constructor_is_used(divisions_yaml):
    strategy = MagicMock()
    strategy.scan = AsyncMock(return_value=[["from-ctor"]])
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml, strategy=strategy)
    assert a.has_strategy is True
    out = await a.scan(broker=MagicMock())
    assert out == [["from-ctor"]]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_attach_strategy_is_idempotent(divisions_yaml):
    a = TastyOptionsAgent(divisions_yaml=divisions_yaml)
    s1 = MagicMock(name="strategy_v1")
    s2 = MagicMock(name="strategy_v2")
    a.attach_strategy(s1)
    assert a._strategy is s1
    a.attach_strategy(s2)
    assert a._strategy is s2
