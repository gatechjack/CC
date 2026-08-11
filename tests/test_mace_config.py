"""Phase-1 tests: MaceConfig loader — fail-fast validation + the shipped config.

Loading the real config/mace.yaml here doubles as a guard: a future edit that
breaks the shipped config fails this test. Fail-fast cases mutate a copy of the
real config into a tmp file and assert the specific violation is reported.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from trading_corp.mace.config import MaceConfig, load_mace_config

ROOT = Path(__file__).resolve().parents[1]
MACE_YAML = ROOT / "config" / "mace.yaml"
EXDIV_YAML = ROOT / "config" / "ex_dividend_calendar.yaml"   # real calendar (SPY has dates)


def _base() -> dict:
    return yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "mace.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _load(tmp_path: Path, data: dict, exdiv=EXDIV_YAML):
    """Load a mutated config against the real ex-div calendar (cwd-robust)."""
    return load_mace_config(_write(tmp_path, data), exdiv_calendar_path=exdiv)


def test_shipped_config_loads():
    cfg = load_mace_config(MACE_YAML, exdiv_calendar_path=EXDIV_YAML)
    assert isinstance(cfg, MaceConfig)
    assert cfg.account_number == "116637293063"
    assert cfg.acknowledge_foreign_positions is False
    assert cfg.universe == ("SPY",)
    assert cfg.max_contracts == 1
    assert cfg.entry.ivr_floor == 25
    assert cfg.entry.dte_min == 30 and cfg.entry.dte_max == 45
    assert cfg.entry.overflow_max_per_symbol_session == 1
    assert cfg.entry.risk_band_min_per_width_usd == 50       # width-scaled band
    assert cfg.entry.risk_band_max_usd == 250
    assert cfg.management.pt_pct_of_credit == 0.50
    assert cfg.breakers.breaker_enforcement == "off"
    assert cfg.symbols["IBIT"].overflow_only is True
    assert cfg.symbols["FXI"].fallback_width_dollars == 1
    assert cfg.symbols["SPY"].blackout_event_types == ("FOMC", "CPI")
    assert cfg.config_hash == hashlib.sha256(MACE_YAML.read_bytes()).hexdigest()


def test_account_must_be_digits(tmp_path):
    d = _base(); d["account_number"] = "joint"
    with pytest.raises(ValueError, match="account_number"):
        _load(tmp_path, d)


def test_delta_target_outside_band(tmp_path):
    d = _base(); d["entry"]["short_delta_target"] = 0.30   # band [0.15, 0.25]
    with pytest.raises(ValueError, match="short_delta_target"):
        _load(tmp_path, d)


def test_dte_min_gt_max(tmp_path):
    d = _base(); d["entry"]["dte_min"] = 50                # dte_max 45
    with pytest.raises(ValueError, match="dte_min"):
        _load(tmp_path, d)


def test_hwm_soft_must_exceed_hard(tmp_path):
    d = _base(); d["breakers"]["hwm_soft_pct"] = 0.70      # hard 0.75
    with pytest.raises(ValueError, match="hwm_soft"):
        _load(tmp_path, d)


def test_overflow_only_in_universe_rejected(tmp_path):
    d = _base(); d["universe"] = ["SPY", "IBIT"]           # OQ-3: IBIT never primary
    with pytest.raises(ValueError, match="overflow_only"):
        _load(tmp_path, d)


def test_bool_as_number_rejected(tmp_path):
    d = _base(); d["max_contracts"] = True                 # bool is an int subclass
    with pytest.raises(ValueError, match="max_contracts"):
        _load(tmp_path, d)


def test_enforcement_mode_validated(tmp_path):
    d = _base(); d["breakers"]["breaker_enforcement"] = "halt"   # not a valid mode
    with pytest.raises(ValueError, match="breaker_enforcement"):
        _load(tmp_path, d)


def test_snapshot_must_precede_eval(tmp_path):
    d = _base(); d["sizing"]["equity_snapshot_time_et"] = "15:50"  # after eval 15:45
    with pytest.raises(ValueError, match="equity_snapshot_time_et"):
        _load(tmp_path, d)


def test_universe_symbol_needs_enabled_block(tmp_path):
    d = _base(); d["symbols"]["SPY"]["enabled"] = False
    with pytest.raises(ValueError, match="enabled is false"):
        _load(tmp_path, d)


def test_fallback_width_must_be_less_than_width(tmp_path):
    d = _base(); d["symbols"]["FXI"]["fallback_width_dollars"] = 5   # width 2
    with pytest.raises(ValueError, match="fallback_width_dollars"):
        _load(tmp_path, d)


def test_multiple_violations_all_listed(tmp_path):
    d = _base(); d["account_number"] = "x"; d["max_contracts"] = 0
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, d)
    msg = str(ei.value)
    assert "account_number" in msg and "max_contracts" in msg


# ── ex-div guard boot gate (Board ruling 2026-08-09, Checkpoint 1) ────────

def _exdiv(tmp_path: Path, *symbols: str) -> Path:
    p = tmp_path / "exdiv.yaml"
    body = "ex_dividends:\n" + "".join(
        f'  - symbol: {s}\n    ex_date: "2026-09-18"\n' for s in symbols)
    p.write_text(body, encoding="utf-8")
    return p


def test_shipped_config_passes_exdiv_gate():
    # SPY (the only enabled symbol at launch) has real dates in the shipped calendar
    cfg = load_mace_config(MACE_YAML, exdiv_calendar_path=EXDIV_YAML)
    assert cfg.symbols["SPY"].enabled and cfg.symbols["SPY"].exdiv_guard


def test_enabled_guard_without_dates_fails(tmp_path):
    d = _base(); d["symbols"]["EWZ"]["enabled"] = True   # EWZ exdiv_guard is true
    exdiv = _exdiv(tmp_path, "SPY")                       # calendar has SPY, NOT EWZ
    with pytest.raises(ValueError, match="EWZ"):
        _load(tmp_path, d, exdiv)


def test_enabled_guard_off_without_dates_ok(tmp_path):
    d = _base(); d["symbols"]["USO"]["enabled"] = True    # USO exdiv_guard is false
    cfg = _load(tmp_path, d, _exdiv(tmp_path, "SPY"))
    assert cfg.symbols["USO"].enabled is True


def test_enabled_guard_with_dates_ok(tmp_path):
    d = _base(); d["symbols"]["EWZ"]["enabled"] = True
    cfg = _load(tmp_path, d, _exdiv(tmp_path, "SPY", "EWZ"))
    assert cfg.symbols["EWZ"].enabled is True


def test_unreadable_calendar_fails_for_guarded_symbol(tmp_path):
    d = _base()  # SPY enabled + exdiv_guard, calendar missing
    with pytest.raises(ValueError, match="unreadable"):
        _load(tmp_path, d, tmp_path / "does_not_exist.yaml")
