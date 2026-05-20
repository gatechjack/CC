"""Tests for the 2026-05-18 kalshi_llm_arbitrage filter additions.

Two new gates:
  1. ticker_prefix_blacklist  — pre-LLM skip for known-loser macro-release
     series (KXUSPPI*, KXUSCPI*, KXAIRFARE*, KXAAAGAS*).
  2. max_divergence_pct       — post-LLM ceiling cap; LLM calibration breaks
     at high-divergence (50%+ bucket 0/12 WR; 30-50% 46% WR -$22 PnL).

Both gates must:
  a) emit an audit event with `strategy` + `division` keys (CLAUDE.md §1).
  b) fire BEFORE the skip (audit-before-decision-branch rule, CLAUDE.md §1).
  c) be hot-reloadable — None/absent config → gate disabled.
"""
from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml


# ─── Minimal stubs ───────────────────────────────────────────────────────────

@dataclass
class _FakeMarket:
    ticker: str
    yes_bid: float = 0.40
    yes_ask: float = 0.45
    no_bid: float = 0.55
    no_ask: float = 0.60
    title: str = "Will X happen?"
    subtitle: str = ""
    event_ticker: str = "KXTEST"
    expected_expiration_time: str = "2026-05-20T12:00:00Z"


@dataclass
class _FakeEvent:
    title: str = "Test event"
    category: str = "Politics"
    event_type: Any = None
    markets: list = field(default_factory=list)

    def __post_init__(self):
        # Import the real EventType so the code's isinstance check passes.
        from trading_corp.data.kalshi_market_map import EventType
        if self.event_type is None:
            self.event_type = EventType.BINARY


@dataclass
class _FakeDiscovery:
    events: list = field(default_factory=list)

    def audit_summary(self):
        return {"n_events": len(self.events)}


class _FakeBroker:
    def __init__(self, discovery: _FakeDiscovery):
        self._discovery = discovery

    async def list_markets(self, **_kwargs):
        return self._discovery


class _FakeLoggerAgent:
    """Capture all log_event calls."""
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def log_event(self, actor: str, kind: str, payload: dict):
        self.events.append((actor, kind, payload))

    def kinds(self) -> list[str]:
        return [e[1] for e in self.events]

    def payloads_for(self, kind: str) -> list[dict]:
        return [e[2] for e in self.events if e[1] == kind]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_agent(tmp_path: Path, extra_yaml: str = "") -> Any:
    """Build a KalshiLLMArbitrageAgent pointed at a temp strategies.yaml."""
    from trading_corp.agents.strategies.kalshi_llm_arbitrage import KalshiLLMArbitrageAgent

    base = textwrap.dedent("""\
        kalshi_llm_arbitrage:
          enabled: true
          auto_execute: false
          division: kalshi_llm_arbitrage
          poll_interval_sec: 60
          k_markets_per_cycle: 20
          market_cooldown_hours: 6
          min_divergence_pct: 10.0
          time_horizon_max_days: 30
          filter:
            min_implied_probability: 0.05
            max_implied_probability: 0.95
          sizing:
            mode: fixed_usdc
            fixed_amount: 1.0
    """)
    full = base.rstrip() + "\n" + textwrap.dedent(extra_yaml)
    strat_yaml = tmp_path / "strategies.yaml"
    strat_yaml.write_text(full, encoding="utf-8")

    risk_yaml = tmp_path / "risk.yaml"
    risk_yaml.write_text("kalshi: {}\n", encoding="utf-8")

    agent = KalshiLLMArbitrageAgent(
        strategies_yaml=strat_yaml,
        risk_yaml=risk_yaml,
        db_url=None,
    )
    # Patch out the LLM call — not needed for filter tests.
    agent._estimate_probability = MagicMock(
        side_effect=AssertionError("LLM should not be called for blacklisted tickers")
    )
    return agent


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── Ticker-prefix blacklist tests ───────────────────────────────────────────

class TestTickerPrefixBlacklist:
    def _make_discovery(self, tickers: list[str]) -> _FakeDiscovery:
        markets = [_FakeMarket(ticker=t) for t in tickers]
        event = _FakeEvent(markets=markets)
        return _FakeDiscovery(events=[event])

    def test_blacklisted_tickers_are_skipped(self, tmp_path):
        """KXUSPPI* tickers must not reach the LLM."""
        agent = _make_agent(tmp_path, """\
          ticker_prefix_blacklist:
            - KXUSPPI
            - KXUSCPI
        """)
        # Pre-populate discovery cache so broker.list_markets isn't called.
        agent._discovery_cache = self._make_discovery(["KXUSPPI-2026MAY", "KXUSCPI-2026JUN"])
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        # LLM mock should NOT be called; if it is the test fails via AssertionError.
        orders = _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        assert orders == [], "Blacklisted tickers must not produce orders"

    def test_blacklisted_ticker_emits_audit_event(self, tmp_path):
        """kalshi_llm_ticker_blacklisted must fire with strategy+division."""
        agent = _make_agent(tmp_path, """\
          ticker_prefix_blacklist:
            - KXUSPPI
        """)
        agent._discovery_cache = self._make_discovery(["KXUSPPI-2026MAY"])
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        assert "kalshi_llm_ticker_blacklisted" in logger.kinds(), (
            "Expected kalshi_llm_ticker_blacklisted audit event"
        )
        for p in logger.payloads_for("kalshi_llm_ticker_blacklisted"):
            assert p.get("strategy") == "kalshi_llm_arbitrage", "Missing strategy tag"
            assert p.get("division") == "kalshi_llm_arbitrage", "Missing division tag"
            assert p.get("ticker", "").startswith("KXUSPPI"), "Ticker not in payload"

    def test_blacklist_case_insensitive(self, tmp_path):
        """Prefix matching must be case-insensitive."""
        agent = _make_agent(tmp_path, """\
          ticker_prefix_blacklist:
            - kxusppi
        """)
        agent._discovery_cache = self._make_discovery(["KXUSPPI-2026MAY"])
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        assert "kalshi_llm_ticker_blacklisted" in logger.kinds()

    def test_non_blacklisted_ticker_passes_through(self, tmp_path):
        """A non-blacklisted ticker must NOT be emitted as blacklisted."""
        agent = _make_agent(tmp_path, """\
          ticker_prefix_blacklist:
            - KXUSPPI
        """)
        # KXNEXTPOPE is not blacklisted — it should pass the filter
        # (LLM will be called but we need to allow it; swap mock to return None
        # so no order is emitted, but no AssertionError either).
        from unittest.mock import AsyncMock
        agent._estimate_probability = AsyncMock(return_value=None)

        agent._discovery_cache = self._make_discovery(["KXNEXTPOPE-2026"])
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        assert "kalshi_llm_ticker_blacklisted" not in logger.kinds()

    def test_empty_blacklist_passes_all(self, tmp_path):
        """No ticker_prefix_blacklist key → gate disabled, nothing skipped."""
        agent = _make_agent(tmp_path)  # no extra yaml
        from unittest.mock import AsyncMock
        agent._estimate_probability = AsyncMock(return_value=None)

        agent._discovery_cache = self._make_discovery(["KXUSPPI-2026MAY"])
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        assert "kalshi_llm_ticker_blacklisted" not in logger.kinds()

    def test_all_four_prefixes(self, tmp_path):
        """All four known-loser prefixes are suppressed together."""
        agent = _make_agent(tmp_path, """\
          ticker_prefix_blacklist:
            - KXUSPPI
            - KXUSCPI
            - KXAIRFARE
            - KXAAAGAS
        """)
        tickers = [
            "KXUSPPI-2026MAY",
            "KXUSCPI-2026JUN",
            "KXAIRFARE-2026Q2",
            "KXAAAGAS-MAY26",
        ]
        agent._discovery_cache = self._make_discovery(tickers)
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        orders = _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        assert orders == []
        bl_events = logger.payloads_for("kalshi_llm_ticker_blacklisted")
        assert len(bl_events) == 4, f"Expected 4 blacklist events, got {len(bl_events)}"


# ─── Max divergence cap tests ─────────────────────────────────────────────────

class TestMaxDivergenceCap:
    """Tests for post-LLM divergence ceiling gate."""

    def _make_discovery_one(self, ticker: str = "KXTEST-001") -> _FakeDiscovery:
        # yes_bid=0.40, yes_ask=0.45 → implied mid = 0.425
        market = _FakeMarket(ticker=ticker, yes_bid=0.40, yes_ask=0.45)
        event = _FakeEvent(markets=[market])
        return _FakeDiscovery(events=[event])

    def _prep_with_estimate(self, tmp_path: Path, llm_prob: float, max_div: float | None):
        """Returns (agent, logger, orders) after one scan cycle.

        llm_prob is the mocked LLM output. implied mid ≈ 0.425.
        divergence_pct = abs(llm_prob - 0.425) * 100
        """
        from unittest.mock import AsyncMock
        from trading_corp.agents.strategies.kalshi_llm_arbitrage import _ProbabilityEstimate

        extra = ""
        if max_div is not None:
            extra = f"  max_divergence_pct: {max_div}\n"

        agent = _make_agent(tmp_path, extra)
        agent._estimate_probability = AsyncMock(return_value=_ProbabilityEstimate(
            prob_yes=llm_prob, confidence="high", reasoning="test", key_unknowns=[]
        ))
        agent._discovery_cache = self._make_discovery_one()
        from datetime import datetime, timezone
        agent._discovery_ts = datetime.now(timezone.utc)

        logger = _FakeLoggerAgent()
        orders = _run(agent.run_scan_cycle(
            _FakeBroker(agent._discovery_cache), logger_agent=logger
        ))
        return agent, logger, orders

    def test_within_cap_produces_order(self, tmp_path):
        """divergence=20% with max_divergence_pct=30 → should emit order."""
        # implied ≈ 0.425, llm=0.225 → divergence ≈ 20%
        _, logger, orders = self._prep_with_estimate(tmp_path, llm_prob=0.225, max_div=30.0)
        assert len(orders) == 1, "Expected order for in-range divergence"
        assert "kalshi_llm_divergence_capped" not in logger.kinds()

    def test_above_cap_suppressed(self, tmp_path):
        """divergence=55% with max_divergence_pct=30 → order suppressed."""
        # implied ≈ 0.425, llm=0.975 → divergence ≈ 55%
        _, logger, orders = self._prep_with_estimate(tmp_path, llm_prob=0.975, max_div=30.0)
        assert orders == [], "Order above max_divergence_pct must be suppressed"

    def test_above_cap_emits_audit_event(self, tmp_path):
        """kalshi_llm_divergence_capped must fire with strategy+division keys."""
        # implied ≈ 0.425, llm=0.975 → divergence ≈ 55%
        _, logger, orders = self._prep_with_estimate(tmp_path, llm_prob=0.975, max_div=30.0)
        assert "kalshi_llm_divergence_capped" in logger.kinds()
        for p in logger.payloads_for("kalshi_llm_divergence_capped"):
            assert p.get("strategy") == "kalshi_llm_arbitrage"
            assert p.get("division") == "kalshi_llm_arbitrage"
            assert "divergence_pct" in p
            assert "max_divergence_pct" in p

    def test_no_max_divergence_config_passes_high(self, tmp_path):
        """Absent max_divergence_pct → gate disabled; high divergence still emits."""
        # implied ≈ 0.425, llm=0.975 → divergence ≈ 55% — passes without cap
        _, logger, orders = self._prep_with_estimate(tmp_path, llm_prob=0.975, max_div=None)
        assert len(orders) == 1, "Without max_divergence_pct, high divergence should emit"
        assert "kalshi_llm_divergence_capped" not in logger.kinds()

    def test_exact_boundary_passes(self, tmp_path):
        """divergence == max_divergence_pct exactly → must NOT be capped (strict >)."""
        # implied=0.425, llm=0.125 → divergence=30% exactly
        _, logger, orders = self._prep_with_estimate(tmp_path, llm_prob=0.125, max_div=30.0)
        # 30% == 30% is NOT > 30%, so the cap check (divergence_pct > max_div_pct) is False
        # Order should pass through.
        assert len(orders) == 1, "Boundary value (equal) must not be capped"
