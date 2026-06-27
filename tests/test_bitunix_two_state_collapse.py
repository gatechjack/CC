"""Two-state collapse (2026-06-27) — TRADING | HALTED-INERT.

bitunix_futures is retired to HALTED-INERT: its observer short-circuits its
public entries BEFORE scoring / would_have_placed / paper_trade_record. The
fail-safe default is HALTED at the orchestration layer (main.py reads
strategies.yaml `mode`; only an explicit `mode: trading` un-halts a division).

Hard contract proven here:
  - `BitunixFuturesObserver.halted` defaults False (back-compat for every
    existing test/fixture that constructs the observer without the arg).
  - When halted=True, `observe_alert` and `observe_and_decide` SHORT-CIRCUIT:
    `_observe_alert_inner` is never reached, no ledger append, and
    `db.insert_paper_trade_record` is never called.
  - The main.py mode predicate is fail-safe: missing/non-"trading" => halted
    (futures) and NOT trading (sfp). Only explicit "trading" arms.
  - The shipped strategies.yaml ships futures: halted, sfp: trading — so the
    fail-safe default does NOT silently kill the LIVE BTC (SFP) edge on restart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.persistence import db

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    db_path = tmp_path / "two_state.db"
    db.init_db(f"sqlite:///{db_path}")
    return f"sqlite:///{db_path}"


def _make(db_url: str, **kwargs) -> BitunixFuturesObserver:
    return BitunixFuturesObserver(db_url=db_url, **kwargs)


# ─── ctor flag: default False (back-compat), explicit True ──────────────────


def test_halted_defaults_false(db_url):
    """No `halted` arg → active. Load-bearing: every existing observer test
    constructs without it and must keep prior behavior."""
    assert _make(db_url)._halted is False


def test_halted_explicit_true_sets_flag(db_url):
    assert _make(db_url, halted=True)._halted is True


# ─── short-circuit proofs (the inert contract) ──────────────────────────────


def test_halted_observe_alert_short_circuits(db_url, monkeypatch):
    """halted=True → observe_alert returns None WITHOUT reaching the inner
    classifier (which does the bias/CVD DB writes + audit row)."""
    obs = _make(db_url, halted=True)
    reached = []
    monkeypatch.setattr(obs, "_observe_alert_inner",
                        lambda *a, **k: reached.append(1))
    out = obs.observe_alert({"signal": "otter_buy", "tf": "3m"}, source="lord_otter")
    assert out is None
    assert reached == [], "halted observer must not reach _observe_alert_inner"


def test_active_observe_alert_reaches_inner(db_url, monkeypatch):
    """Control: a non-halted observer DOES reach the inner classifier — proves
    the guard (not some unrelated early-return) is what stops the halted path."""
    obs = _make(db_url)  # halted defaults False
    reached = []
    monkeypatch.setattr(obs, "_observe_alert_inner",
                        lambda *a, **k: reached.append(1) or None)
    obs.observe_alert({"signal": "otter_buy", "tf": "3m"}, source="lord_otter")
    assert reached == [1], "active observer must reach _observe_alert_inner"


@pytest.mark.asyncio
async def test_halted_observe_and_decide_no_write(db_url, monkeypatch):
    """halted=True → observe_and_decide returns None and never appends to the
    ledger, scores, or writes a paper_trade_record."""
    obs = _make(db_url, halted=True)
    alert_calls, ledger_calls, paper_calls = [], [], []
    monkeypatch.setattr(obs, "observe_alert",
                        lambda *a, **k: alert_calls.append(1))
    monkeypatch.setattr(obs, "_append_to_ledger",
                        lambda *a, **k: ledger_calls.append(1))
    # Patch the module symbol the observer calls (db.insert_paper_trade_record).
    monkeypatch.setattr(db, "insert_paper_trade_record",
                        lambda *a, **k: paper_calls.append(1))
    out = await obs.observe_and_decide(
        {"signal": "otter_buy", "tf": "3m"}, source="lord_otter")
    assert out is None
    assert alert_calls == [], "halted: observe_alert must not run"
    assert ledger_calls == [], "halted: ledger append must not run"
    assert paper_calls == [], "halted: paper_trade_record write must not run"


@pytest.mark.asyncio
async def test_halted_pa_redeem_tick_no_fire(db_url, monkeypatch):
    """halted=True → the deferred-PA redeem tick never re-fires the score path,
    even if a payload were cached."""
    obs = _make(db_url, halted=True)
    obs._pending_pa_payload = {"signal": "otter_buy", "tf": "3m"}
    fired = []
    monkeypatch.setattr(obs, "_score_and_maybe_propose",
                        lambda *a, **k: fired.append(1))
    # Drive exactly one tick with a ~0 interval, then cancel.
    import asyncio
    task = asyncio.create_task(obs.run_pa_redeem_loop(interval_s=0.0))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert fired == [], "halted: pa-redeem must not call _score_and_maybe_propose"


# ─── main.py mode predicate (fail-safe) — documents the exact read site ──────


@pytest.mark.parametrize("block,expected_halted", [
    ({}, True),                       # missing mode → HALTED (fail-safe)
    ({"mode": "trading"}, False),
    ({"mode": "TRADING"}, False),     # case-insensitive
    ({"mode": "halted"}, True),
    ({"mode": "paper"}, True),
    ({"mode": ""}, True),
    ({"mode": "foo"}, True),
    ({"mode": None}, True),
])
def test_futures_mode_predicate_failsafe(block, expected_halted):
    # Mirror main.py: _futures_halted = (str(block.get("mode","halted")).lower() != "trading")
    assert (str(block.get("mode", "halted")).lower() != "trading") is expected_halted


@pytest.mark.parametrize("block,expected_trading", [
    ({}, False),                      # missing mode → NOT trading (fail-safe)
    ({"mode": "trading"}, True),
    ({"mode": "TRADING"}, True),
    ({"mode": "halted"}, False),
    ({"mode": "paper"}, False),
])
def test_sfp_mode_predicate_failsafe(block, expected_trading):
    # Mirror main.py: _sfp_trading = (str(raw.get("mode","halted")).lower() == "trading")
    assert (str(block.get("mode", "halted")).lower() == "trading") is expected_trading


# ─── shipped strategies.yaml contract ───────────────────────────────────────


def test_shipped_yaml_futures_halted_sfp_trading():
    """The fail-safe default must NOT silently kill the live BTC edge: the
    shipped YAML pins futures: halted and sfp: trading explicitly."""
    import yaml
    raw = yaml.safe_load(
        (REPO_ROOT / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    assert raw["bitunix_futures"].get("mode") == "halted", (
        "bitunix_futures must ship mode: halted")
    assert raw["bitunix_sfp"].get("mode") == "trading", (
        "bitunix_sfp MUST ship mode: trading or the live BTC 15m loop won't "
        "start on restart (fail-safe default is halted)")
