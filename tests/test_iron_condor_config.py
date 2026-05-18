"""Production-config smoke test for the iron-condor strategy (step 10).

Loads the real `config/strategies.yaml`, `config/divisions.yaml`, and
`config/risk.yaml`, instantiates the strategy module against them, and
asserts every field parses to the expected type and value. This is the
load-bearing safety net: a typo or missed key in production yaml would
break the strategy's behaviour silently (defaults backstop everything),
so we pin the expected state here.

Also verifies:
  - The step-8 divisions.yaml wiring (strategy: robinhood_joint_iron_condor)
    is still present.
  - The step-9 loud-defaults warning does NOT fire on production config
    (i.e., production config is complete relative to _DEFAULTS).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from trading_corp.agents.strategies.robinhood_joint_iron_condor import (
    RobinhoodJointIronCondorAgent,
    STRATEGY_SLUG,
    _DEFAULTS,
)
from trading_corp.utils.divisions import load_divisions


REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_STRATEGIES = REPO_ROOT / "config" / "strategies.yaml"
PROD_DIVISIONS = REPO_ROOT / "config" / "divisions.yaml"
PROD_RISK = REPO_ROOT / "config" / "risk.yaml"


# ---------------------------------------------------------------------------
# divisions.yaml wiring (step-8 carry-through)
# ---------------------------------------------------------------------------


def test_divisions_yaml_wires_strategy_to_robinhood_joint():
    divisions = load_divisions(PROD_DIVISIONS)
    rj = next((d for d in divisions if d.slug == "robinhood_joint"), None)
    assert rj is not None, "robinhood_joint division missing from divisions.yaml"
    assert rj.broker == "robinhood"
    assert rj.account_filter == "joint"
    assert rj.strategy == STRATEGY_SLUG
    assert rj.enabled is True


# ---------------------------------------------------------------------------
# strategies.yaml — full block parses to expected types + values
# ---------------------------------------------------------------------------


def test_strategy_instantiates_cleanly_from_production_yaml():
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    assert a.enabled is True
    assert a.auto_execute is False
    assert a.division == "robinhood_joint"
    assert isinstance(a.universe, list)
    assert a.universe == ["SPY", "QQQ", "IWM", "GLD", "TLT"]
    # wing_widths typed as float per symbol.
    ww = a.wing_widths
    assert set(ww.keys()) == {"SPY", "QQQ", "IWM", "GLD", "TLT"}
    assert all(isinstance(v, float) for v in ww.values())
    assert ww["SPY"] == 3.0
    assert ww["QQQ"] == 4.0


def test_entry_block_parses_correctly():
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    assert a.cfg("entry.target_dte") == 45
    assert a.cfg("entry.short_delta") == 0.16
    assert a.cfg("entry.min_credit_pct_of_width") == 0.33
    assert a.cfg("entry.min_ivr") == 30
    assert a.cfg("entry.min_ivp") == 50
    assert a.cfg("entry.term_structure_max_diff") == 0.05


def test_portfolio_caps_block_parses_correctly():
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    assert a.cfg("portfolio_caps.max_per_trade_pct") == 0.05
    # max_bp_pct present but documented as v1.5 plumbing (not enforced).
    assert a.cfg("portfolio_caps.max_bp_pct") == 0.40
    assert a.cfg("portfolio_caps.max_concurrent") == 3
    assert a.cfg("portfolio_caps.max_correlated") == 2


def test_management_block_parses_correctly():
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    assert a.cfg("management.profit_target_pct") == 0.50
    assert a.cfg("management.force_close_dte") == 21
    assert a.cfg("management.short_dte_force_close") == 7
    assert a.cfg("management.hard_stop_credit_mult") == 2.00
    assert a.cfg("management.catastrophic_stop_account_pct") == 0.10
    assert a.cfg("management.tested_delta_warn") == 0.25
    assert a.cfg("management.tested_delta_adjust") == 0.30
    assert a.cfg("management.tested_delta_close_side") == 0.35
    assert a.cfg("management.tested_side_neutral_band") == 0.05
    assert a.cfg("management.max_adjustments") == 1
    assert a.cfg("management.min_dte_for_adjustment") == 14
    assert a.cfg("management.ex_div_force_close_within_trading_days") == 3
    assert a.cfg("management.ex_div_force_close_short_call_delta") == 0.25
    assert a.cfg("management.adjustment_roll_target_short_delta") == 0.30


def test_circuit_breaker_block_parses_correctly():
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    assert a.cfg("circuit_breaker.consecutive_loss_pause") == 3
    assert a.cfg("circuit_breaker.drawdown_pct_pause") == 0.15
    assert a.cfg("circuit_breaker.pause_days") == 5


def test_paper_simulation_block_parses_correctly():
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    assert a.cfg("paper_simulation.per_leg_slippage_dollars") == 0.03


def test_notifications_block_contains_telegram_bypass_tags():
    with PROD_STRATEGIES.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    block = data[STRATEGY_SLUG]
    notif = block["notifications"]
    assert notif["telegram_batch_window_sec"] == 60
    bypass = notif["telegram_bypass_tags"]
    assert set(bypass) == {
        "circuit_breaker_auto_repause",
        "catastrophic_stop",
        "startup_catchup",
        "late_dte_force_close",
    }


def test_auto_execute_caps_require_approval_for_entries():
    with PROD_STRATEGIES.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    block = data[STRATEGY_SLUG]
    caps = block["auto_execute_caps"]
    require = set(caps["require_approval_for"])
    assert require == {
        "any_neutral_strategy_open_or_close",
        "any_action_when_vix_above_30",
    }


# ---------------------------------------------------------------------------
# Loud-defaults: production config should NOT emit a missing-keys warning
# ---------------------------------------------------------------------------


def test_no_missing_config_warning_on_production_yaml(caplog):
    """Production config is complete — instantiating the agent should
    not emit the `IronCondor config:` warning."""
    with caplog.at_level(logging.WARNING):
        RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    missing_warnings = [
        r for r in caplog.records
        if "IronCondor config" in r.message and "missing" in r.message
    ]
    assert missing_warnings == [], (
        f"production strategies.yaml is missing required keys: "
        f"{[r.message for r in missing_warnings]}"
    )


def test_missing_config_warning_fires_when_keys_absent(tmp_path: Path, caplog):
    """Reverse: a minimal stub yaml DOES emit the warning when enabled."""
    p = tmp_path / "minimal.yaml"
    p.write_text(
        "robinhood_joint_iron_condor:\n"
        "  enabled: true\n"
        "  division: robinhood_joint\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        RobinhoodJointIronCondorAgent(strategies_yaml=p)
    msgs = [r.message for r in caplog.records if "IronCondor config" in r.message]
    assert any("missing" in m for m in msgs), (
        f"expected missing-keys warning, got: {msgs}"
    )


def test_missing_config_warning_silent_when_disabled(tmp_path: Path, caplog):
    """Disabled strategy → no warning (nothing reads the config anyway)."""
    p = tmp_path / "disabled.yaml"
    p.write_text(
        "robinhood_joint_iron_condor:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        RobinhoodJointIronCondorAgent(strategies_yaml=p)
    missing = [r for r in caplog.records if "IronCondor config" in r.message]
    assert missing == []


# ---------------------------------------------------------------------------
# risk.yaml — per_trade_risk_pct override
# ---------------------------------------------------------------------------


def test_risk_yaml_has_robinhood_joint_iron_condor_override():
    with PROD_RISK.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    overrides = data.get("overrides", {})
    section = overrides.get(STRATEGY_SLUG)
    assert section is not None, (
        f"risk.yaml is missing overrides.{STRATEGY_SLUG} section"
    )
    assert section.get("per_trade_risk_pct") == 0.05


# ---------------------------------------------------------------------------
# _DEFAULTS keys are all reachable via cfg()
# ---------------------------------------------------------------------------


def test_every_defaults_key_resolves_via_cfg():
    """Every _DEFAULTS entry should be resolvable via cfg(); pinning this
    catches the case where a code path adds a new tunable but forgets to
    backfill the production YAML or update _DEFAULTS."""
    a = RobinhoodJointIronCondorAgent(strategies_yaml=PROD_STRATEGIES)
    for dotted in _DEFAULTS:
        value = a.cfg(dotted)
        assert value is not None, f"cfg({dotted!r}) returned None"
