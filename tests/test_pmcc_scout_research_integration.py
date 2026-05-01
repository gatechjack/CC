"""Phase 1a-2 integration tests — PMCC scout + research firm on-demand.

Pins (design §8.A clauses (a)-(d) + Refinement 2):
  - When universe_source='research_on_demand' AND research_firm_deps wired,
    the scout calls run_engagement(CandidateScope) for new opens.
  - Capacity calc is BUYING-POWER-BASED (no hard count cap on positions) —
    Board direction 2026-05-01, doc divergence from §8.A (a) step 1.
  - Per-candidate gates run via _propose_open_pmcc; acted_on / skipped
    audit rows fire (with the design-doc skip-reason enum strings).
  - On engagement returning None: pmcc_scan_research_unavailable row +
    counter increment; at threshold: pmcc_research_extended_outage row +
    notify_callback invoked (one-shot per outage streak).
  - Counter resets on any successful engagement.
  - When deps NOT wired: one-shot warning + no orders + no division-side
    audit rows. Existing-leg roll/close path unaffected.
  - Existing universe_source values ('positions', 'watchlist') unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research.engagement import ResearchFirmDeps
from trading_corp.agents.research.schemas import (
    Candidate, CandidateRecommendation,
)
from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────


def _future(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _call_chain_entry(strike: float, delta: float, mark: float, dte: int = 14) -> dict:
    return {
        "strike_price": strike,
        "delta": delta,
        "mark_price": mark,
        "bid": round(mark - 0.10, 2),
        "ask": round(mark + 0.10, 2),
        "dte": dte,
        "option_id": f"opt_{strike}_{dte}",
        # Liquidity-gate fields (otherwise _filter_liquid rejects everything).
        # Pad well above defaults: min_open_interest=100, min_avg_options_volume.
        "open_interest": 5000,
        "volume": 1000,
    }


class FakeOptionBroker(Broker):
    """Minimal broker for scout integration tests. Returns whatever option
    chain the test seeds; otherwise returns empty."""
    name = "fake"
    paper = True

    def __init__(
        self,
        *,
        equity: float = 100_000.0,
        buying_power: float | None = None,
        cash: float | None = None,
        option_positions: list[dict] | None = None,
        stock_positions: list[Position] | None = None,
        chains: dict[str, list[dict]] | None = None,
        leap_chains: dict[str, list[dict]] | None = None,
    ) -> None:
        self._equity = equity
        self._buying_power = buying_power if buying_power is not None else equity * 0.5
        self._cash = cash if cash is not None else equity * 0.5
        self._option_positions = option_positions or []
        self._stock_positions = stock_positions or []
        # Per-symbol weekly call chains — used by _find_best_weekly
        self._chains = chains or {}
        # Per-symbol LEAP chains — used by _find_best_leap
        self._leap_chains = leap_chains or {}

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def quote(self, symbol: str) -> float: return 150.0
    async def cancel_order(self, order_id: str) -> bool: return True

    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="fake",
            equity=self._equity,
            buying_power=self._buying_power,
            cash=self._cash,
            positions=self._stock_positions,
        )

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        from datetime import datetime, timezone
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            qty=order.qty, price=order.limit_price or 0.0,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="fake",
        )

    # OptionBroker protocol
    async def get_option_positions_detail(self) -> list[dict]:
        return self._option_positions

    async def get_expiration_dates(self, symbol: str) -> list[str]:
        # LEAP date if a leap chain is seeded, weekly date if a weekly chain is.
        out = []
        if symbol in self._leap_chains:
            out.append(_future(400))   # > 365 DTE → qualifying LEAP
        if symbol in self._chains:
            out.append(_future(14))    # standard weekly window
        return out

    async def get_calls_for_expiry(self, symbol: str, expiry: str) -> list[dict]:
        # Disambiguate: LEAP expiries are far in the future (>365d).
        from datetime import date
        try:
            exp_date = date.fromisoformat(expiry)
            today = date.today()
            days = (exp_date - today).days
        except Exception:
            days = 0
        if days >= 365 and symbol in self._leap_chains:
            return self._leap_chains[symbol]
        return self._chains.get(symbol, [])


class FakeResearchFirmDeps:
    """Stand-in for ResearchFirmDeps. The scout only reads
    `deps.graph.ainvoke()`-equivalent via run_engagement; we intercept
    run_engagement at the module level instead, so this just needs the
    right shape for the constructor."""
    logger_agent: Any = None
    experts: dict = {}
    graph: Any = None


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def strategies_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
robinhood_pmcc:
  enabled: true
  auto_execute: false
  universe_source: research_on_demand
  watchlist: []
  position_exclude: []
  position_min_shares: 1
  scout:
    enabled: true
    universe: []
    capital_per_position_dollars: 25000
    cash_reserve_floor_pct: 0.10
    weights: {}
  strategy:
    underlying_criteria:
      acceptable_categories: ["large_cap"]
      earnings_buffer_days: 7
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def risk_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "risk.yaml"
    p.write_text(
        """
global:
  per_trade_risk_pct: 0.015
  per_strategy_daily_loss_pct: 0.03
  per_account_max_drawdown_pct: 0.15
  correlation_cap: 0.7
  target_annualized_vol: 0.25
trend_alignment:
  counter_trend_size_multiplier: 0.5
pmcc:
  contracts_per_25k_equity: 1
  short_call_roll_dte: 21
  short_call_roll_profit_pct: 0.50
  long_call_min_dte: 365
  long_call_min_delta: 0.80
  short_call_target_delta: 0.30
  research_outage_alert_threshold: 3
overrides: {}
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    init_db(url)
    return url


@pytest.fixture
def logger_agent(db_url: str) -> LoggerAgent:
    return LoggerAgent(db_url)


@pytest.fixture
def agent_wired(
    strategies_yaml: Path, risk_yaml: Path, logger_agent: LoggerAgent,
) -> tuple[PMCCAgent, FakeResearchFirmDeps, list[str]]:
    """PMCCAgent with research firm wired + a notify_callback that
    captures messages into a list (so tests can assert what fired)."""
    notifications: list[str] = []

    async def _capture(msg: str) -> None:
        notifications.append(msg)

    agent = PMCCAgent(
        strategies_yaml=strategies_yaml,
        risk_yaml=risk_yaml,
        db_url=None,
    )
    fake_deps = FakeResearchFirmDeps()
    agent.attach_research_firm(
        fake_deps,
        logger_agent=logger_agent,
        notify_callback=_capture,
    )
    return agent, fake_deps, notifications


def _patch_run_engagement(monkeypatch, return_value):
    """Patch the run_engagement symbol that PMCCAgent imports inside
    `_run_research_on_demand_new_opens`. Accepts a single return value
    or a callable yielding successive return values for multi-call tests."""
    import trading_corp.agents.research.engagement as engagement_mod

    if callable(return_value) and not hasattr(return_value, "_call_count"):
        # Use the callable as-is per call
        async def _stub(spec, *, deps):
            return return_value(spec)
        monkeypatch.setattr(engagement_mod, "run_engagement", _stub)
    else:
        async def _stub(spec, *, deps):
            return return_value
        monkeypatch.setattr(engagement_mod, "run_engagement", _stub)


# ──────────────────────────────────────────────────────────────────────────
# Tests — happy path
# ──────────────────────────────────────────────────────────────────────────


async def test_scout_calls_research_firm_when_research_on_demand(
    agent_wired, monkeypatch,
):
    """When universe_source='research_on_demand' AND deps wired, the
    scout calls run_engagement and consumes the returned candidates."""
    agent, _deps, _notifs = agent_wired
    captured_specs: list[Any] = []

    async def _capture_engagement(spec, *, deps):
        captured_specs.append(spec)
        return CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division="robinhood_pmcc",
            asset_class="equity",
            candidates=[
                Candidate(
                    symbol="AAPL", thesis="t", conviction="medium",
                    fit_rationale="fr", fit_score=0.6,
                ),
            ],
        )

    import trading_corp.agents.research.engagement as engagement_mod
    monkeypatch.setattr(engagement_mod, "run_engagement", _capture_engagement)

    broker = FakeOptionBroker(
        equity=100_000.0,
        leap_chains={"AAPL": [_call_chain_entry(120, 0.85, 30.0, dte=400)]},
        chains={"AAPL": [_call_chain_entry(160, 0.30, 5.00, dte=14)]},
    )
    orders = await agent.scan(broker)

    assert len(captured_specs) == 1
    spec = captured_specs[0]
    assert spec.requesting_division == "robinhood_pmcc"
    assert spec.product_type == "candidate_recommendation"
    assert spec.asset_class == "equity"
    assert spec.scope.starter_universe_key == "large_mid_cap"
    # AAPL produced both LEAP + weekly orders
    assert len(orders) == 2


async def test_capacity_no_hard_count_cap_buying_power_only(
    agent_wired, monkeypatch,
):
    """Doc divergence pin: capacity is buying-power-based, NOT count-capped.
    With $1M equity and held PMCCs >> any historical count cap, the
    research firm is still called as long as buying power > cash floor."""
    agent, _deps, _notifs = agent_wired
    n_candidates_requested: list[int] = []

    async def _capture(spec, *, deps):
        n_candidates_requested.append(spec.scope.n_candidates)
        return CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division="robinhood_pmcc",
            asset_class="equity", candidates=[],
        )

    import trading_corp.agents.research.engagement as engagement_mod
    monkeypatch.setattr(engagement_mod, "run_engagement", _capture)

    # Big equity; large buying power. No count cap should fire.
    broker = FakeOptionBroker(
        equity=1_000_000.0,
        buying_power=500_000.0,
        cash=500_000.0,
    )
    await agent.scan(broker)

    # n_candidates capped at 5 (Pydantic CandidateScope limit) — but the
    # POINT is: agent didn't refuse to call the engagement based on
    # currently_held_pmcc_count. It scaled up to 5.
    assert n_candidates_requested == [5]


async def test_capacity_zero_skips_engagement_entirely(
    agent_wired, monkeypatch,
):
    """When buying_power < cash_floor, no engagement is run (no LLM cost)."""
    agent, _deps, _notifs = agent_wired
    called = []

    async def _capture(spec, *, deps):
        called.append(spec)
        return None

    import trading_corp.agents.research.engagement as engagement_mod
    monkeypatch.setattr(engagement_mod, "run_engagement", _capture)

    # equity=100k, cash_floor_pct=0.10 → cash_floor=$10k.
    # buying_power=$8k < $10k → available=0.
    broker = FakeOptionBroker(
        equity=100_000.0,
        buying_power=8_000.0,
        cash=8_000.0,
    )
    orders = await agent.scan(broker)

    assert called == [], "engagement should NOT have been called when capacity=0"
    assert orders == []


# ──────────────────────────────────────────────────────────────────────────
# Tests — division-side audit rows
# ──────────────────────────────────────────────────────────────────────────


async def test_acted_on_audit_row_per_consumed_candidate(
    agent_wired, monkeypatch, logger_agent,
):
    """For each candidate where _propose_open_pmcc returns orders,
    `research_candidate_acted_on` fires with the join key."""
    agent, _deps, _notifs = agent_wired

    async def _rec(spec, *, deps):
        return CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division="robinhood_pmcc",
            asset_class="equity",
            candidates=[
                Candidate(symbol="AAPL", thesis="t", conviction="high",
                          fit_rationale="fr", fit_score=0.8),
                Candidate(symbol="MSFT", thesis="t", conviction="medium",
                          fit_rationale="fr", fit_score=0.6),
            ],
        )

    import trading_corp.agents.research.engagement as engagement_mod
    monkeypatch.setattr(engagement_mod, "run_engagement", _rec)

    broker = FakeOptionBroker(
        equity=100_000.0,
        leap_chains={
            "AAPL": [_call_chain_entry(120, 0.85, 30.0, dte=400)],
            "MSFT": [_call_chain_entry(120, 0.85, 30.0, dte=400)],
        },
        chains={
            "AAPL": [_call_chain_entry(160, 0.30, 5.00, dte=14)],
            "MSFT": [_call_chain_entry(160, 0.30, 5.00, dte=14)],
        },
    )
    await agent.scan(broker)

    events = logger_agent.recent_events(limit=200)
    acted = [e for e in events if e["kind"] == "research_candidate_acted_on"]
    assert len(acted) == 2
    syms = {e["payload"]["symbol"] for e in acted}
    assert syms == {"AAPL", "MSFT"}
    for row in acted:
        assert row["actor"] == "robinhood_pmcc"
        assert row["payload"]["engagement_id"]
        assert row["payload"]["proposed_order_id"]
        assert "fit_score" in row["payload"]
        assert "conviction" in row["payload"]


async def test_skipped_audit_row_carries_skip_reason(
    agent_wired, monkeypatch, logger_agent,
):
    """When _propose_open_pmcc returns ([], reason), the candidate gets
    `research_candidate_skipped` with the reason enum string. Reason
    strings come from the design doc §4.5 enum."""
    agent, _deps, _notifs = agent_wired

    async def _rec(spec, *, deps):
        return CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division="robinhood_pmcc",
            asset_class="equity",
            candidates=[
                Candidate(symbol="ZZZZ", thesis="t", conviction="low",
                          fit_rationale="fr", fit_score=0.2),
            ],
        )

    import trading_corp.agents.research.engagement as engagement_mod
    monkeypatch.setattr(engagement_mod, "run_engagement", _rec)

    # No chain seeded for ZZZZ → _find_best_leap returns None → skip_reason='leap_unavailable'
    broker = FakeOptionBroker(equity=100_000.0)
    orders = await agent.scan(broker)

    assert orders == []
    events = logger_agent.recent_events(limit=200)
    skipped = [e for e in events if e["kind"] == "research_candidate_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["payload"]["symbol"] == "ZZZZ"
    assert skipped[0]["payload"]["reason"] == "leap_unavailable"


# ──────────────────────────────────────────────────────────────────────────
# Tests — engagement returning None / outage tracking
# ──────────────────────────────────────────────────────────────────────────


async def test_engagement_none_writes_unavailable_and_increments_counter(
    agent_wired, monkeypatch, logger_agent,
):
    """When run_engagement returns None (kill switch / cost cap / etc),
    scout writes pmcc_scan_research_unavailable + increments the counter.
    No orders proposed (per §8.A clause c — no fallback)."""
    agent, _deps, _notifs = agent_wired

    import trading_corp.agents.research.engagement as engagement_mod

    async def _none(spec, *, deps):
        return None
    monkeypatch.setattr(engagement_mod, "run_engagement", _none)

    broker = FakeOptionBroker(equity=100_000.0)
    orders = await agent.scan(broker)

    assert orders == []
    events = logger_agent.recent_events(limit=200)
    unavail = [e for e in events if e["kind"] == "pmcc_scan_research_unavailable"]
    assert len(unavail) == 1
    payload = unavail[0]["payload"]
    assert payload["consecutive_failures"] == 1
    assert payload["engagement_id"]
    # Threshold not yet hit
    extended = [e for e in events if e["kind"] == "pmcc_research_extended_outage"]
    assert extended == []


async def test_extended_outage_fires_at_threshold_and_notifies_once(
    agent_wired, monkeypatch, logger_agent,
):
    """3 consecutive failures (default threshold) → pmcc_research_extended_outage
    audit + notify_callback invoked exactly once. Subsequent failures
    don't re-alert (one-shot per outage streak)."""
    agent, _deps, notifications = agent_wired

    import trading_corp.agents.research.engagement as engagement_mod

    async def _none(spec, *, deps):
        return None
    monkeypatch.setattr(engagement_mod, "run_engagement", _none)

    broker = FakeOptionBroker(equity=100_000.0)
    for _ in range(5):
        await agent.scan(broker)

    events = logger_agent.recent_events(limit=400)
    unavail = [e for e in events if e["kind"] == "pmcc_scan_research_unavailable"]
    assert len(unavail) == 5
    extended = [e for e in events if e["kind"] == "pmcc_research_extended_outage"]
    assert len(extended) == 1, f"expected exactly 1 extended-outage row, got {len(extended)}"
    payload = extended[0]["payload"]
    assert payload["consecutive_failures"] == 3
    assert payload["threshold"] == 3
    assert payload["first_failure_ts"]

    # notify_callback fired exactly once
    assert len(notifications) == 1
    assert "extended outage" in notifications[0].lower()


async def test_outage_counter_resets_on_success(
    agent_wired, monkeypatch, logger_agent,
):
    """A successful engagement after failures resets the streak; a fresh
    outage starts the count from 0 again."""
    agent, _deps, notifications = agent_wired

    import trading_corp.agents.research.engagement as engagement_mod

    call_idx = {"i": 0}

    async def _alternating(spec, *, deps):
        call_idx["i"] += 1
        i = call_idx["i"]
        # Calls 1, 2: None (failures)
        # Call 3: success
        # Calls 4, 5, 6: None (would trigger outage if counter hadn't reset)
        if i in (1, 2, 4, 5, 6):
            return None
        return CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division="robinhood_pmcc",
            asset_class="equity", candidates=[],
        )

    monkeypatch.setattr(engagement_mod, "run_engagement", _alternating)

    broker = FakeOptionBroker(equity=100_000.0)
    for _ in range(6):
        await agent.scan(broker)

    # Failures #1, #2: counter 1, 2 — no extended-outage
    # Success #3: counter resets
    # Failures #4, #5, #6: counter 1, 2, 3 — extended-outage fires on #6
    events = logger_agent.recent_events(limit=400)
    extended = [e for e in events if e["kind"] == "pmcc_research_extended_outage"]
    assert len(extended) == 1
    assert extended[0]["payload"]["consecutive_failures"] == 3


# ──────────────────────────────────────────────────────────────────────────
# Tests — backwards compat / no-deps fallback
# ──────────────────────────────────────────────────────────────────────────


async def test_no_research_deps_wired_no_orders_no_audit(
    strategies_yaml, risk_yaml, logger_agent, monkeypatch,
):
    """When universe_source='research_on_demand' but deps NOT wired,
    scout logs warning + produces no new opens. No audit rows fire.
    Pre-existing universe_source modes ('positions', 'watchlist') stay
    unaffected."""
    agent = PMCCAgent(
        strategies_yaml=strategies_yaml, risk_yaml=risk_yaml,
        db_url=None, logger_agent=logger_agent,
    )
    # Deliberately do NOT call attach_research_firm — research deps stay None.

    # Patch run_engagement to verify it's NEVER called when deps are missing.
    import trading_corp.agents.research.engagement as engagement_mod
    called = []

    async def _trap(spec, *, deps):
        called.append(spec)
        return None
    monkeypatch.setattr(engagement_mod, "run_engagement", _trap)

    broker = FakeOptionBroker(equity=100_000.0)
    orders = await agent.scan(broker)

    assert orders == []
    assert called == [], (
        "run_engagement must NOT be called when research_firm_deps is None — "
        "scout should fall back to no-new-opens with a warning"
    )
    # No division-side audit rows when research isn't wired
    events = logger_agent.recent_events(limit=50)
    research_rows = [
        e for e in events
        if e["kind"] in (
            "research_candidate_acted_on",
            "research_candidate_skipped",
            "pmcc_scan_research_unavailable",
            "pmcc_research_extended_outage",
        )
    ]
    assert research_rows == []


async def test_existing_legs_roll_close_path_unaffected_by_research_on_demand(
    agent_wired, monkeypatch,
):
    """Regression check: the existing-leg roll/close logic in scan() is
    not touched by the new research-on-demand new-opens path. Universe
    is empty (no held positions) but agent still tries to call research
    firm — confirms the existing-leg branch is independent."""
    agent, _deps, _notifs = agent_wired

    captured: list[Any] = []

    async def _capture(spec, *, deps):
        captured.append(spec)
        return CandidateRecommendation(
            engagement_id=spec.engagement_id,
            requesting_division="robinhood_pmcc",
            asset_class="equity", candidates=[],
        )

    import trading_corp.agents.research.engagement as engagement_mod
    monkeypatch.setattr(engagement_mod, "run_engagement", _capture)

    # No existing option positions, no stock positions — but research
    # firm is still asked because new-opens have their own capacity gate.
    broker = FakeOptionBroker(equity=100_000.0)
    orders = await agent.scan(broker)

    assert len(captured) == 1, (
        "even with empty universe, research firm should be called for new opens "
        "(scan() no longer early-returns on empty universe in research_on_demand mode)"
    )
    assert orders == []
