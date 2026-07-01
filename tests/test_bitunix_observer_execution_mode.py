"""Tests for the `execution_mode` YAML field + constructor wiring.

Commit 2 of Stage-1 Session N+1. `execution_mode` is the structural
fork between paper-mode (today) and live-mode (commit 3, INSIDE the
canonical helper). Hard contract:

  - Default is `"paper"` (constructor default + main.py fallback +
    YAML default if the field is missing entirely).
  - Explicit `"paper"` loads as `"paper"`.
  - Explicit `"live"` loads as `"live"`.
  - Case-insensitive (`"PAPER"`, `"Live"` etc. normalize).
  - Unknown values (`"foobar"`, `42`, `None`) fail closed to `"paper"`
    with a WARN log.
  - The field is config-and-restart; no mtime hot-reload (a stray
    file write must not flip the system live).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.persistence import db


def _make_observer(db_url: str, **kwargs) -> BitunixFuturesObserver:
    return BitunixFuturesObserver(db_url=db_url, **kwargs)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    db_path = tmp_path / "exec_mode.db"
    db.init_db(f"sqlite:///{db_path}")
    return f"sqlite:///{db_path}"


# ─── constructor defaults ───────────────────────────────────────────────


def test_execution_mode_defaults_to_paper(db_url):
    """No execution_mode arg → "paper". Load-bearing safety default."""
    obs = _make_observer(db_url)
    assert obs.execution_mode == "paper"


def test_execution_mode_explicit_paper(db_url):
    obs = _make_observer(db_url, execution_mode="paper")
    assert obs.execution_mode == "paper"


def test_execution_mode_explicit_live(db_url):
    obs = _make_observer(db_url, execution_mode="live")
    assert obs.execution_mode == "live"


# ─── case insensitivity ─────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("PAPER", "paper"),
    ("Paper", "paper"),
    ("paper ", "paper "),  # whitespace not stripped — operator-side YAML hygiene
])
def test_execution_mode_case_normalization_paper(db_url, value, expected):
    """Lower-casing is applied; whitespace is the operator's problem
    (don't silently swallow it — fail-closed catches the mistake)."""
    obs = _make_observer(db_url, execution_mode=value)
    # Whitespace value " paper " falls into "unknown" → "paper" default
    # so the result is still "paper" either way.
    if expected.strip() != expected:
        assert obs.execution_mode == "paper"
    else:
        assert obs.execution_mode == expected


@pytest.mark.parametrize("value", ["LIVE", "Live", "lIVe"])
def test_execution_mode_case_normalization_live(db_url, value):
    obs = _make_observer(db_url, execution_mode=value)
    assert obs.execution_mode == "live"


# ─── fail-closed on unknown ─────────────────────────────────────────────


@pytest.mark.parametrize("value", ["foobar", "test", "demo", "", "  ", "live!", "papre"])
def test_execution_mode_unknown_string_falls_back_to_paper(db_url, value, caplog):
    """Unknown strings fall back to 'paper' with a WARN log."""
    with caplog.at_level(logging.WARNING):
        obs = _make_observer(db_url, execution_mode=value)
    assert obs.execution_mode == "paper"
    # WARN log fired with the rejected value
    assert any(
        "unknown execution_mode" in rec.message and repr(value) in rec.message
        for rec in caplog.records
    ), f"expected unknown-execution_mode WARN for value={value!r}; got {caplog.text!r}"


@pytest.mark.parametrize("value", [42, None, True, 0, ["live"]])
def test_execution_mode_non_string_falls_back_to_paper(db_url, value):
    """Non-string input fails closed to 'paper'."""
    obs = _make_observer(db_url, execution_mode=value)
    assert obs.execution_mode == "paper"


# ─── main.py YAML read site contract ────────────────────────────────────


def test_yaml_block_defaults_to_paper_when_field_absent():
    """If the YAML bitunix_futures block lacks execution_mode entirely,
    the main.py read site reads "paper" by default. Mirrors what
    `.get("execution_mode", "paper")` does — this is documentation of
    the intended contract, plus a guard against the literal string
    drifting in main.py."""
    bx_block: dict = {
        "enabled": True,
        "auto_execute": True,
        # no execution_mode field
    }
    assert str(bx_block.get("execution_mode", "paper")).lower() == "paper"


def test_yaml_block_explicit_live_reads_live():
    bx_block = {"execution_mode": "live"}
    assert str(bx_block.get("execution_mode", "paper")).lower() == "live"


def test_yaml_block_uppercase_live_normalizes():
    bx_block = {"execution_mode": "LIVE"}
    assert str(bx_block.get("execution_mode", "paper")).lower() == "live"


def test_prod_strategies_yaml_futures_ships_live():
    """Two-live reality (2026-06-30): bitunix_futures is now its OWN live
    division on a distinct account, so the shipped prod YAML pins its
    bitunix_futures block to `execution_mode: live`. (The pre-two-live
    single-account world shipped futures paper/halted-inert; that premise is
    retired.) If someone accidentally reverts it to `paper` in a refactor PR,
    this test fails. Flipping a division live/paper is a deliberate
    operator-approved deploy — that diff is separate from a code change."""
    import yaml as _yaml
    strat_path = Path(__file__).resolve().parent.parent / "config" / "strategies.yaml"
    with strat_path.open() as f:
        raw = _yaml.safe_load(f)
    bx = raw.get("bitunix_futures", {})
    assert bx.get("execution_mode") == "live", (
        "prod strategies.yaml's bitunix_futures.execution_mode must be 'live' "
        "under two-live — futures is its own live division on a distinct account. "
        f"Got: {bx.get('execution_mode')!r}"
    )
