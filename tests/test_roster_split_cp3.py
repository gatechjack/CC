"""CP3 — roster split made real: config retarget, paper read-time subtract,
boot invariant.

The critical behavior: a whale that is on the LIVE roster
(poly_kalshi_mlb/live_whales) is NOT paper-copied, even if it is still present
in polymarket_copy_trader/selected_whales (the §1.5 refresh-re-add / missed-key
backstop). Plus: the config now points the live loop at live_whales, and the
boot invariant logs-loud-and-continues on an overlap.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from trading_corp.persistence import db
from trading_corp.persistence.db import set_agent_state
from trading_corp.agents.strategies import roster_split as rs
from trading_corp.agents.strategies.polymarket_copy_trader import (
    PolymarketCopyTraderAgent,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── Fixtures / stubs ────────────────────────────────────────────────────


class _RecordingDataAPI:
    """Records which wallets the scan fetches activity for. Returns no rows so
    the cycle is a clean cold-start (we only care about WHO gets fetched)."""

    def __init__(self) -> None:
        self.queried: list[str] = []

    async def fetch_activity(self, wallet, *, limit=20, offset=0):
        self.queried.append(wallet)
        return []


@pytest.fixture()
def paper_agent(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'cp3.db').as_posix()}"
    db.init_db(db_url=db_url)
    strat = tmp_path / "strategies.yaml"
    strat.write_text(
        "polymarket_copy_trader:\n  enabled: true\n  poll_interval_sec: 60\n",
        encoding="utf-8",
    )
    risk = tmp_path / "risk.yaml"
    risk.write_text("polymarket: {}\n", encoding="utf-8")
    agent = PolymarketCopyTraderAgent(
        strategies_yaml=strat, risk_yaml=risk, db_url=db_url,
    )
    return agent, db_url


# ── 1. Config retarget (yaml) ───────────────────────────────────────────


def test_config_retargets_live_loop_to_live_whales():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    pk = cfg["poly_kalshi_mlb"]
    # The live loop reads load_agent_state(roster_actor, roster_key); main.py:1513-1514
    # passes these straight through, so this yaml IS the retarget.
    assert pk["roster_actor"] == "poly_kalshi_mlb"
    assert pk["roster_key"] == "live_whales"
    # It must NOT still point at the shared paper key.
    assert not (pk["roster_actor"] == "polymarket_copy_trader" and pk["roster_key"] == "selected_whales")


# ── 2. Paper-sim read-time subtract (the core CP3 behavior) ─────────────


@pytest.mark.asyncio
async def test_paper_sim_excludes_live_whale(paper_agent):
    """A whale in BOTH selected_whales AND live_whales is NOT papered — the
    read-time subtract wins. Case-insensitive (0xLIVE excluded by 0xlive)."""
    agent, db_url = paper_agent
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xLIVE", "user_name": "liveguy"},
         {"wallet": "0xpaper", "user_name": "paperguy"}],
        db_url=db_url,
    )
    set_agent_state(
        "poly_kalshi_mlb", "live_whales",
        [{"wallet": "0xlive"}],          # lowercase -> must still match 0xLIVE
        db_url=db_url,
    )
    api = _RecordingDataAPI()
    await agent.run_scan_cycle(data_api_client=api)

    assert "0xpaper" in api.queried            # the paper-only whale IS fetched
    assert "0xLIVE" not in api.queried         # the live whale is NOT papered
    assert api.queried == ["0xpaper"]


@pytest.mark.asyncio
async def test_paper_sim_papers_all_when_live_roster_empty(paper_agent):
    """No live roster -> no subtract -> every selected whale is papered."""
    agent, db_url = paper_agent
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xa"}, {"wallet": "0xb"}],
        db_url=db_url,
    )
    # live_whales absent entirely.
    api = _RecordingDataAPI()
    await agent.run_scan_cycle(data_api_client=api)
    assert set(api.queried) == {"0xa", "0xb"}


@pytest.mark.asyncio
async def test_paper_sim_noop_when_all_selected_are_live(paper_agent):
    """If every selected whale is live-copied, the paper cycle is a clean no-op."""
    agent, db_url = paper_agent
    set_agent_state("polymarket_copy_trader", "selected_whales", [{"wallet": "0xa"}], db_url=db_url)
    set_agent_state("poly_kalshi_mlb", "live_whales", [{"wallet": "0xa"}], db_url=db_url)
    api = _RecordingDataAPI()
    orders = await agent.run_scan_cycle(data_api_client=api)
    assert orders == []
    assert api.queried == []                   # nobody fetched


# ── 3. Boot invariant: log-loud-and-continue ────────────────────────────


def test_boot_invariant_disjoint_returns_true(tmp_path, caplog):
    db_url = f"sqlite:///{(tmp_path / 'inv_ok.db').as_posix()}"
    db.init_db(db_url=db_url)
    set_agent_state("poly_kalshi_mlb", "live_whales", [{"wallet": "0xlive"}], db_url=db_url)
    set_agent_state("polymarket_copy_trader", "selected_whales", [{"wallet": "0xpaper"}], db_url=db_url)
    with caplog.at_level(logging.INFO):
        ok = rs.assert_roster_invariant_boot(db_url=db_url)
    assert ok is True
    assert any("roster invariant OK" in r.message for r in caplog.records)


def test_boot_invariant_overlap_logs_loud_and_continues(tmp_path, caplog):
    """An overlap must NOT raise (would brick the multi-division engine); it
    returns False and logs an error for the operator to reconcile."""
    db_url = f"sqlite:///{(tmp_path / 'inv_bad.db').as_posix()}"
    db.init_db(db_url=db_url)
    set_agent_state("poly_kalshi_mlb", "live_whales", [{"wallet": "0xDUP"}], db_url=db_url)
    set_agent_state("polymarket_copy_trader", "selected_whales", [{"wallet": "0xdup"}], db_url=db_url)
    with caplog.at_level(logging.ERROR):
        ok = rs.assert_roster_invariant_boot(db_url=db_url)   # must NOT raise
    assert ok is False
    assert any("ROSTER INVARIANT VIOLATED" in r.message for r in caplog.records)


def test_boot_invariant_read_error_is_non_blocking(caplog):
    """An unexpected read failure is treated as non-blocking (returns True) so a
    DB hiccup never bricks boot."""
    with caplog.at_level(logging.WARNING):
        ok = rs.assert_roster_invariant_boot(db_url="sqlite:///:this-is-not-a-real-path/nope.db")
    assert ok is True
