"""Tests for kalshi_structure_arb strategy.

10 test cases per spec:
  1. sum_yes=4.6, K=7 → 3 NO orders (top-3 by implied_yes)
  2. sum_yes=1.2, K=3 → 0 orders (below threshold=1.5)
  3. K=1 (binary) → skipped (below_min_k)
  4. Crypto category → skipped
  5. Weather/Climate category → skipped
  6. Risk gate rejection → no would_have_placed, but evaluated audit IS written
  7. KXAAAGASD-26MAY17-T-50 ticker → skipped (price_bucket, dash-separator)
  8. KXAUNABCONF-26MAY17-B-100 ticker → skipped (price_bucket, dash-separator)
  9. First observation → first_observation=True, prior_implied_yes_per_ticker=None
  10. Second scan → first_observation=False, prior_implied_yes_per_ticker={...}

All broker and risk-agent calls are mocked — no live network requests.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — lightweight stubs for market objects and discovery result
# ---------------------------------------------------------------------------

@dataclass
class _Market:
    """Minimal stub for a Kalshi market object."""
    ticker: str
    event_ticker: str
    yes_ask: float = 0.0
    no_ask: float = 0.0
    yes_bid: float = 0.0
    no_bid: float = 0.0


@dataclass
class _Event:
    """Minimal stub for a Kalshi event object."""
    event_ticker: str
    category: str
    markets: list[_Market] = field(default_factory=list)


@dataclass
class _DiscoveryResult:
    events: list[_Event]


def _make_market(ticker: str, event_ticker: str, implied_yes: float) -> _Market:
    """Create a market with yes_ask=implied_yes, no_ask=1-implied_yes."""
    yes_ask = max(0.01, min(0.99, implied_yes))
    no_ask = max(0.01, min(0.99, 1.0 - implied_yes))
    return _Market(
        ticker=ticker,
        event_ticker=event_ticker,
        yes_ask=yes_ask,
        no_ask=no_ask,
    )


# ---------------------------------------------------------------------------
# Config stub — avoids touching the filesystem
# ---------------------------------------------------------------------------

_DEFAULT_CFG = {
    "enabled": True,
    "auto_execute": False,
    "division": "kalshi_structure_arb",
    "threshold": {
        "sum_yes_implied_min": 1.5,
        "min_k": 3,
        "top_m": 3,
    },
    "sizing": {
        "mode": "fixed_usdc",
        "fixed_amount": 1.0,
    },
    "cadence": {
        "default_seconds": 60,
        "rapid_seconds": 15,
        "rapid_window_hours": 24,
    },
    "discovery": {
        "cache_ttl_sec": 600,
        "max_series_per_category": 30,
        "max_markets_per_series": 50,
        "categories": [],
    },
    "kill_criterion": {
        "review_at_days": 30,
        "min_resolved_bets": 20,
        "min_win_rate": 0.55,
        "min_gross_pnl_usd": 0.0,
    },
}


def _make_agent_with_config(cfg: dict | None = None):
    """Build a KalshiStructureArbAgent with patched config (no filesystem)."""
    from trading_corp.agents.strategies.kalshi_structure_arb import (
        KalshiStructureArbAgent,
    )

    agent = KalshiStructureArbAgent.__new__(KalshiStructureArbAgent)
    agent._db_url = None
    agent._strategies_yaml = MagicMock()
    agent._strat_mtime = 0.0
    agent._strat_cfg = cfg if cfg is not None else dict(_DEFAULT_CFG)
    agent._discovery_cache = None
    agent._discovery_ts = None
    agent._seen_event_tickers = set()
    agent._prev_implied_yes = {}
    agent._first_seen_ts = {}
    return agent


async def _run_scan(agent, events: list[_Event]) -> tuple[list, list[dict]]:
    """Run one scan cycle with the given events; return (orders, audit_events)."""
    discovery = _DiscoveryResult(events=events)

    audit_log: list[dict] = []

    class _MockLogger:
        def log_event(self, actor, kind, payload):
            audit_log.append({"actor": actor, "kind": kind, "payload": dict(payload)})

    mock_broker = MagicMock()
    mock_broker.list_markets = MagicMock(return_value=None)

    # Inject the discovery result directly into the agent's cache so
    # list_markets is not called (no async broker needed).
    from datetime import datetime, timezone
    agent._discovery_cache = discovery
    agent._discovery_ts = datetime.now(timezone.utc)

    logger = _MockLogger()
    orders = await agent.run_scan_cycle(mock_broker, logger_agent=logger)
    return orders, audit_log


# ---------------------------------------------------------------------------
# Helper: find audit events by kind prefix
# ---------------------------------------------------------------------------

def _find(audit_log: list[dict], kind_prefix: str) -> list[dict]:
    return [e for e in audit_log if e["kind"].startswith(kind_prefix)]


# ===========================================================================
# TEST 1 — sum_yes=4.6, K=7 → 3 NO orders (top-3 by implied_yes)
# ===========================================================================

@pytest.mark.asyncio
async def test_qualifying_event_emits_three_orders():
    """Event with K=7 markets and sum_yes=4.6 fires top-3 NO orders."""
    et = "KXCHINAANNOUNCE-26MAY"
    # 7 sub-markets with varying implied_yes; sum = 4.6
    implied_yeses = [0.84, 0.83, 0.68, 0.52, 0.57, 0.29, 0.87]
    assert abs(sum(implied_yeses) - 4.60) < 0.01
    markets = [
        _make_market(f"{et}-{i}", et, iy)
        for i, iy in enumerate(implied_yeses)
    ]
    event = _Event(event_ticker=et, category="Politics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    # 3 orders
    assert len(orders) == 3, f"Expected 3 orders, got {len(orders)}"

    # All are BUY-NO
    for o in orders:
        assert o.side == "buy"
        assert o.symbol.endswith(":no")

    # Top-3 by implied_yes should be index 6 (0.87), 0 (0.84), 1 (0.83)
    top3_iy = sorted(implied_yeses, reverse=True)[:3]
    order_iy = sorted(
        [(o.extra or {}).get("implied_yes_at_entry", 0) for o in orders],
        reverse=True,
    )
    for expected, actual in zip(top3_iy, order_iy):
        assert abs(expected - actual) < 0.001, f"Top-3 mismatch: {top3_iy} vs {order_iy}"

    # evaluated audit written
    evaluated = _find(audit_log, "kalshi_structure_arb_evaluated")
    assert len(evaluated) == 1
    assert evaluated[0]["payload"]["strategy"] == "kalshi_structure_arb"
    assert evaluated[0]["payload"]["division"] == "kalshi_structure_arb"
    assert evaluated[0]["payload"]["K"] == 7

    # scan audit written
    scans = _find(audit_log, "kalshi_structure_arb_scan")
    assert len(scans) == 1
    assert scans[0]["payload"]["n_orders_emitted"] == 3


# ===========================================================================
# TEST 2 — sum_yes=1.2, K=3 → 0 orders (below threshold=1.5)
# ===========================================================================

@pytest.mark.asyncio
async def test_below_threshold_no_orders():
    """sum_yes=1.2 < threshold=1.5 → skipped."""
    et = "KXTEST-BELOW-THRESH"
    markets = [
        _make_market(f"{et}-A", et, 0.45),
        _make_market(f"{et}-B", et, 0.40),
        _make_market(f"{et}-C", et, 0.35),
    ]
    # sum = 1.20
    event = _Event(event_ticker=et, category="Politics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) == 0
    skipped = _find(audit_log, "kalshi_structure_arb_skipped_below_threshold")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["strategy"] == "kalshi_structure_arb"
    assert skipped[0]["payload"]["division"] == "kalshi_structure_arb"


# ===========================================================================
# TEST 3 — K=1 (binary market, implied_yes=0.55) → skipped (below_min_k)
# ===========================================================================

@pytest.mark.asyncio
async def test_binary_market_skipped():
    """K=1 binary YES/NO market → below_min_k skip."""
    et = "KXBINARY-26MAY17"
    markets = [_make_market(f"{et}-YES", et, 0.55)]
    event = _Event(event_ticker=et, category="Politics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) == 0
    skipped = _find(audit_log, "kalshi_structure_arb_skipped_below_min_k")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["strategy"] == "kalshi_structure_arb"


# ===========================================================================
# TEST 4 — Crypto category → skipped
# ===========================================================================

@pytest.mark.asyncio
async def test_crypto_category_skipped():
    """Crypto category event → skip."""
    et = "KXBTCMONTH-26MAY17"
    markets = [
        _make_market(f"{et}-{i}", et, 0.55)
        for i in range(5)
    ]
    event = _Event(event_ticker=et, category="Crypto", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) == 0
    skipped = _find(audit_log, "kalshi_structure_arb_skipped_crypto")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["strategy"] == "kalshi_structure_arb"


# ===========================================================================
# TEST 5 — Weather/Climate category → skipped
# ===========================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["Climate", "Weather", "Climate and Weather"])
async def test_weather_category_skipped(category: str):
    """Climate/Weather categories → skip."""
    et = f"KXTEMPNYC-26MAY17"
    markets = [
        _make_market(f"{et}-{i}", et, 0.55)
        for i in range(4)
    ]
    event = _Event(event_ticker=et, category=category, markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) == 0
    skipped = _find(audit_log, "kalshi_structure_arb_skipped_weather")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["strategy"] == "kalshi_structure_arb"


# ===========================================================================
# TEST 6 — Risk gate rejection
# ===========================================================================

@pytest.mark.asyncio
async def test_risk_gate_rejection():
    """Risk rejection: no would_have_placed; evaluated audit IS written.

    The main.py loop handles risk gating after run_scan_cycle returns.
    Here we simulate what the loop does: call run_scan_cycle (which writes
    the evaluated audit), then simulate a risk rejection and verify no
    would_have_placed is emitted.
    """
    et = "KXCHINAANNOUNCE-26MAY"
    implied_yeses = [0.84, 0.83, 0.68, 0.52, 0.57, 0.29, 0.87]
    markets = [
        _make_market(f"{et}-{i}", et, iy)
        for i, iy in enumerate(implied_yeses)
    ]
    event = _Event(event_ticker=et, category="Politics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    # Evaluated audit written BEFORE risk decision
    evaluated = _find(audit_log, "kalshi_structure_arb_evaluated")
    assert len(evaluated) == 1

    # Simulate main.py loop: risk agent rejects everything
    from trading_corp.agents.risk import RiskVerdict

    mock_risk = MagicMock()
    mock_risk.evaluate.return_value = RiskVerdict(
        verdict="reject", reason="test rejection"
    )

    audit_log_2: list[dict] = []

    class _MockLogger2:
        def log_event(self, actor, kind, payload):
            audit_log_2.append({"actor": actor, "kind": kind, "payload": dict(payload)})

    # Simulate the loop's behavior on rejection
    for order in orders:
        verdict = mock_risk.evaluate(order, None, None)
        if verdict.verdict != "reject":
            _MockLogger2().log_event(
                agent.name, "would_have_placed", {"order_id": order.id}
            )
        else:
            # Log a rejection (as the main loop does)
            _MockLogger2().log_event(
                agent.name, "kalshi_structure_arb_order_rejected_by_risk",
                {"order_id": order.id, "risk_reason": verdict.reason},
            )

    # No would_have_placed
    placed = [e for e in audit_log_2 if e["kind"] == "would_have_placed"]
    assert len(placed) == 0, "No would_have_placed should be written on risk rejection"

    # evaluated audit IS written (from run_scan_cycle above)
    assert len(evaluated) == 1


# ===========================================================================
# TEST 7 — Dash-separator price bucket (KXAAAGASD-26MAY17-T-50) → skipped
# ===========================================================================

@pytest.mark.asyncio
async def test_dash_separator_price_bucket_T_skipped():
    """KXAAAGASD-style ticker with -T-<digit> → price_bucket skip."""
    et = "KXAAAGASD-26MAY17"
    markets = [
        _Market(
            ticker=f"KXAAAGASD-26MAY17-T-50",
            event_ticker=et,
            yes_ask=0.55, no_ask=0.45,
        ),
        _Market(
            ticker=f"KXAAAGASD-26MAY17-T-45",
            event_ticker=et,
            yes_ask=0.35, no_ask=0.65,
        ),
        _Market(
            ticker=f"KXAAAGASD-26MAY17-T-40",
            event_ticker=et,
            yes_ask=0.25, no_ask=0.75,
        ),
    ]
    event = _Event(event_ticker=et, category="Economics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) == 0
    skipped = _find(audit_log, "kalshi_structure_arb_skipped_price_bucket")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["strategy"] == "kalshi_structure_arb"


# ===========================================================================
# TEST 8 — Dash-separator price bucket (KXAUNABCONF-26MAY17-B-100) → skipped
# ===========================================================================

@pytest.mark.asyncio
async def test_dash_separator_price_bucket_B_skipped():
    """KXAUNABCONF-style ticker with -B-<digit> → price_bucket skip."""
    et = "KXAUNABCONF-26MAY17"
    markets = [
        _Market(
            ticker="KXAUNABCONF-26MAY17-B-100",
            event_ticker=et,
            yes_ask=0.60, no_ask=0.40,
        ),
        _Market(
            ticker="KXAUNABCONF-26MAY17-B-80",
            event_ticker=et,
            yes_ask=0.40, no_ask=0.60,
        ),
        _Market(
            ticker="KXAUNABCONF-26MAY17-B-60",
            event_ticker=et,
            yes_ask=0.30, no_ask=0.70,
        ),
    ]
    event = _Event(event_ticker=et, category="Economics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) == 0
    skipped = _find(audit_log, "kalshi_structure_arb_skipped_price_bucket")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["strategy"] == "kalshi_structure_arb"


# ===========================================================================
# TEST 9 — First observation → first_observation=True, prior=None
# ===========================================================================

@pytest.mark.asyncio
async def test_first_observation_audit_fields():
    """First scan of an event_ticker → first_observation=True, prior=None."""
    et = "KXCHINAANNOUNCE-26MAY-FIRST"
    markets = [
        _make_market(f"{et}-A", et, 0.80),
        _make_market(f"{et}-B", et, 0.75),
        _make_market(f"{et}-C", et, 0.70),
        _make_market(f"{et}-D", et, 0.65),
    ]
    # sum = 2.90 > 1.5
    event = _Event(event_ticker=et, category="Politics", markets=markets)

    agent = _make_agent_with_config()
    orders, audit_log = await _run_scan(agent, [event])

    assert len(orders) > 0
    evaluated = _find(audit_log, "kalshi_structure_arb_evaluated")
    assert len(evaluated) == 1
    payload = evaluated[0]["payload"]
    assert payload["first_observation"] is True
    assert payload["prior_implied_yes_per_ticker"] is None


# ===========================================================================
# TEST 10 — Second scan → first_observation=False, prior_implied_yes = {...}
# ===========================================================================

@pytest.mark.asyncio
async def test_second_scan_observation_audit_fields():
    """Second scan of same event_ticker → first_observation=False, prior set."""
    et = "KXCHINAANNOUNCE-26MAY-SECOND"
    markets = [
        _make_market(f"{et}-A", et, 0.80),
        _make_market(f"{et}-B", et, 0.75),
        _make_market(f"{et}-C", et, 0.70),
        _make_market(f"{et}-D", et, 0.65),
    ]
    event = _Event(event_ticker=et, category="Politics", markets=markets)

    agent = _make_agent_with_config()

    # First scan — seeds the state
    _, _ = await _run_scan(agent, [event])

    # Second scan — verify prior_implied_yes_per_ticker is populated
    _, audit_log_2 = await _run_scan(agent, [event])

    evaluated = _find(audit_log_2, "kalshi_structure_arb_evaluated")
    assert len(evaluated) == 1
    payload = evaluated[0]["payload"]
    assert payload["first_observation"] is False
    assert payload["prior_implied_yes_per_ticker"] is not None
    assert len(payload["prior_implied_yes_per_ticker"]) > 0


# ===========================================================================
# TEST 11 — Hard-stop class constants + auto_execute gating
# ===========================================================================

def test_hard_stop_constants_and_auto_execute_gate():
    """Class constants are set to paper-only values and gate auto_execute."""
    from trading_corp.agents.strategies.kalshi_structure_arb import (
        KalshiStructureArbAgent,
    )

    # 1. Class-level constant values
    assert KalshiStructureArbAgent.PAPER_MODE_ONLY is True
    assert KalshiStructureArbAgent.LIVE_MODE_BOARD_APPROVED is False

    # 2. yaml says auto_execute=True but constants gate it to False
    agent = _make_agent_with_config(dict(_DEFAULT_CFG))
    # Make _reload() a no-op: mock stat().st_mtime to match _strat_mtime
    agent._strategies_yaml.stat.return_value.st_mtime = agent._strat_mtime
    agent._strat_cfg = {"auto_execute": True, "enabled": True, "division": "kalshi_structure_arb"}
    assert agent.auto_execute is False, (
        "auto_execute must be False while PAPER_MODE_ONLY=True, "
        "even when yaml says auto_execute=True"
    )

    # 3. Monkeypatch constants to simulate Board approval path
    agent.PAPER_MODE_ONLY = False
    agent.LIVE_MODE_BOARD_APPROVED = True
    # yaml True → property returns True
    agent._strat_cfg = {"auto_execute": True, "enabled": True, "division": "kalshi_structure_arb"}
    assert agent.auto_execute is True, (
        "auto_execute should read yaml when constants are cleared"
    )
    # yaml False → property returns False
    agent._strat_cfg = {"auto_execute": False, "enabled": True, "division": "kalshi_structure_arb"}
    assert agent.auto_execute is False, (
        "auto_execute should still respect yaml=False when constants allow"
    )


# ===========================================================================
# TEST — regex unit tests for PRICE_BUCKET_REGEX
# ===========================================================================

def test_price_bucket_regex_catches_dash_separator():
    """Verify fixed regex catches both -Bdigit/-Tdigit and -B-digit/-T-digit."""
    from trading_corp.agents.strategies.kalshi_structure_arb import PRICE_BUCKET_REGEX

    # Should match (price buckets)
    should_match = [
        "KXAAAGASD-26MAY17-T-50",        # dash-separator T (the bug case)
        "KXAUNABCONF-26MAY17-B-100",     # dash-separator B (the bug case)
        "KXBTC-26MAY17-T100000",         # no-separator T (original pattern)
        "KXETH-26MAY17-B50000",          # no-separator B
        "KXBTC15M-26MAY17-T-5000",       # dash-separator, longer ticker
        "KXH100MON-26MAY31-T-1800",      # Hedgeye index (from backtest bug list)
        "KXTEMPNYCH-26MAY17-T-65",       # temperature/weather bucket
    ]
    for t in should_match:
        assert PRICE_BUCKET_REGEX.search(t), f"Expected match for: {t}"

    # Should NOT match (legitimate structure-arb candidates)
    should_not_match = [
        "KXCHINAANNOUNCE-26MAY-BOEING",  # no B/T suffix
        "KXGOVCAPRIMARY-26",             # no B/T suffix
        "KXTXRUNOFFENDORSE-26MAY26",     # no B/T suffix
        "KXCHINAANNOUNCE-26MAY-BOT",     # "BOT" has T but not -T-digit pattern
        "KXNEXTPOPE-26MAY-BERGOGLIO",    # no B/T suffix
    ]
    for t in should_not_match:
        assert not PRICE_BUCKET_REGEX.search(t), f"Unexpected match for: {t}"
