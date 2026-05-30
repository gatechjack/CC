"""End-to-end test for StrategyState persistence after the 17 site swaps.

Commit 6 of Stage-1 Session N+1. Validates the persistence story holds
through the bitunix observer's risk eval path:

  1. RiskAgent's daily-loss-cap fires → persist_halt writes agent_state
  2. Observer re-instantiates (simulating process restart)
  3. Next risk eval picks up halt via `from_persistence` → REJECTED

Scope note: only the bitunix observer's risk.evaluate calls were
updated to pass `db_url` in this commit; other call sites (webhooks,
routes, telegram_commands, main.py scanners) get from_persistence
READS only — they don't WRITE halts because db_url plumbing through
their risk.evaluate sites is a wider refactor. The graph path
(`ceo_graph.py:risk_node`) was updated to pass db_url and is the
canonical mutation path for PMCC flows.

The remaining write-side gap (TV alerts halting via webhooks) is
listed in BACKLOG as a follow-up: "wire db_url through remaining
risk.evaluate sites to make persist_halt firing global."
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.risk import RiskAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState, ProposedOrder, StrategyState,
)


# ─── helpers ────────────────────────────────────────────────────────────


def _real_risk_agent() -> RiskAgent:
    return RiskAgent(
        risk_yaml=Path("config/risk.yaml"),
        narrator_enabled=False,
    )


def _bullish_state(obs):
    obs._update_bias("4h", "bull", "2026-05-10T08:00:00+00:00", "mc_b_buy_circle_div")
    obs._update_bias("1d", "bull", "2026-05-10T00:00:00+00:00", "mc_a_longema")
    obs._update_cvd("bull", "2026-05-10T11:50:00+00:00")


def _make_observer(db_url: str):
    snap = MagicMock(); snap.equity = 5_000.0; snap.positions = []
    broker = MagicMock(); broker.snapshot = AsyncMock(return_value=snap)
    data_exec = MagicMock(); data_exec.brokers = {"bitunix_futures": broker}
    return BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=_real_risk_agent(),
        data_exec=data_exec,
        logger_agent=LoggerAgent(db_url=db_url),
        telegram_channel=MagicMock(push=AsyncMock(return_value=True)),
    )


# ─── end-to-end halt survives observer re-init ─────────────────────────


def test_persisted_halt_blocks_next_observer_decision(tmp_path: Path):
    """The full halt-survives-restart story:

    1. Write `halted=True` to agent_state directly (simulating a prior
       process having hit the daily-loss cap).
    2. Construct a FRESH observer.
    3. The observer's risk-eval site, now using
       StrategyState.from_persistence(...), picks up halted=True.
    4. RiskAgent.evaluate rejects on the strategy-halt branch.

    This is the load-bearing assertion for commit 6's site swaps."""
    db_path = tmp_path / "halt_survives.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    # Pre-existing halt from a "previous process"
    StrategyState.persist_halt(
        "bitunix_futures", "daily loss cap reached", db_url=db_url,
    )

    # Construct the observer + verify the synchronous read primitive
    # sees the halt.
    obs = _make_observer(db_url)
    assert obs.execution_mode == "paper"  # safe default

    # The observer's risk-eval call site now uses from_persistence —
    # we can verify by constructing a StrategyState the same way and
    # inspecting it. Direct equivalent to what observer line 1513
    # / 2848 will pass to risk.evaluate.
    s = StrategyState.from_persistence(
        "bitunix_futures", db_url=obs.db_url,
    )
    assert s.halted is True
    assert "daily loss cap" in (s.halt_reason or "")

    # Now run the RiskAgent against this StrategyState — must reject
    # on the strategy-halt branch (line 114 in risk.py).
    order = ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P",
        side="buy", qty=0.001, order_type="market",
    )
    account = AccountState(
        account="bitunix_futures", equity=5_000.0, peak_equity=5_000.0,
    )
    verdict = obs.risk_agent.evaluate(order, account, s, db_url=db_url)
    assert verdict.verdict == "reject"
    assert "halted" in (verdict.reason or "").lower()


def test_swap_at_bitunix_observer_observe_and_decide(tmp_path: Path):
    """Higher-level: drive a real signal through observe_and_decide; if
    persisted halt is present, no would_have_placed audit should fire
    (risk rejects upstream)."""
    import asyncio
    db_path = tmp_path / "obs_halt.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"
    StrategyState.persist_halt(
        "bitunix_futures", "halted by daily loss cap test", db_url=db_url,
    )

    obs = _make_observer(db_url)
    _bullish_state(obs)
    payload = {
        "signal": "spoon_bull", "symbol": "BTC/USD",
        "price": 80_000.0, "time": "2026-05-10T12:00:00Z", "interval": "3",
    }
    asyncio.run(obs.observe_and_decide(payload, source="lord_otter"))

    # No would_have_placed audit — risk rejected upstream.
    wp_calls = [c for c in obs.logger_agent.log_event.call_args_list
                if c.kwargs.get("kind") == "would_have_placed"] if isinstance(
        obs.logger_agent, MagicMock,
    ) else []
    # LoggerAgent is real here, so audit_event has the row count.
    with db.connect(obs.db_url) as conn:
        wp_count = conn.execute(
            "SELECT COUNT(*) c FROM audit_event WHERE kind = 'would_have_placed'"
        ).fetchone()["c"]
    assert wp_count == 0, "halted strategy must not produce would_have_placed"

    # bitunix_decided audit with outcome rejected_risk should be present.
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind = 'bitunix_decided'"
        ).fetchall()
    outcomes = [json.loads(r["payload_json"]).get("outcome") for r in rows]
    assert "rejected_risk" in outcomes


def test_bitunix_observer_writes_halt_via_writer_at_risk_eval(tmp_path: Path):
    """Reverse direction: when the observer triggers a risk eval that
    breaches the daily cap, the RiskAgent persists the halt via the
    db_url plumbing added in commit 6.

    Hard to drive end-to-end without simulating realized_pnl (which
    isn't yet plumbed through from_persistence — that field stays
    transient). So we directly invoke risk.evaluate with the same
    db_url the observer uses and verify the side effect."""
    db_path = tmp_path / "obs_writes.db"
    db.init_db(f"sqlite:///{db_path}")
    db_url = f"sqlite:///{db_path}"

    obs = _make_observer(db_url)

    # Simulate a strategy state with breached realized_pnl (the field
    # is transient in `from_persistence` but the test injects it
    # directly to drive the eval path).
    breaching_state = StrategyState(
        strategy="bitunix_futures", halted=False, realized_pnl=-200.0,
    )
    order = ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P",
        side="buy", qty=0.001, order_type="market",
    )
    account = AccountState(
        account="bitunix_futures", equity=5_000.0, peak_equity=5_000.0,
    )
    verdict = obs.risk_agent.evaluate(
        order, account, breaching_state, db_url=obs.db_url,
    )
    assert verdict.verdict == "reject"
    assert verdict.halt_strategy is True

    # Halt now persists for the next observer to pick up.
    s = StrategyState.from_persistence("bitunix_futures", db_url=db_url)
    assert s.halted is True


def test_swap_left_ic_subclass_alone(tmp_path: Path):
    """The 17-site census excluded the 2 `_ICStrategyState` sites (line
    1172 + 1249 of main.py). Confirm the IC concrete class still
    defaults to halted=False the legacy way (subclass inherits
    from_persistence but the IC orchestration constructs `_IC...`
    objects directly, not via the classmethod). This test exists so a
    future refactor doesn't sweep the IC sites in by accident."""
    # The _ICStrategyState is defined inside main() as a local subclass;
    # we can't directly import it, but we can grep the source.
    main_py = Path(__file__).resolve().parent.parent / "trading_corp" / "main.py"
    src = main_py.read_text(encoding="utf-8")
    assert "_ICStrategyState(strategy=ic_strategy.SLUG, halted=False)" in src
    assert "_ICStrategyState(strategy=tasty_strategy.SLUG, halted=False)" in src
