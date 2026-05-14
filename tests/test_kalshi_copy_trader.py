"""Tests for K3 Kalshi copy-trading: scoring + strategy delta detection.

Network-free. Exercises:
  - Wilson LCB boundary cases
  - Edge factor clipping
  - Category bonus substring matching
  - compute_stats aggregation
  - score_whale exclusion paths (no_visibility, sample-short)
  - Side detection branches (high/medium/low/no-fetcher)
  - Strategy delta detection (cold-start, entry, exit, carryover)
  - Sizing tier boundaries
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trading_corp.brokers.kalshi import KalshiPublicTrade
from trading_corp.data.kalshi_apify_client import WhalePosition, WhaleProfile
from trading_corp.data.kalshi_whale_stats import (
    _category_bonus, _edge_factor, compute_stats, filter_leaderboard_for_discovery,
    score_whale, select_top_n, wilson_lcb_95,
)
from trading_corp.persistence import db as _db


# ── Wilson LCB math ──────────────────────────────────────────────────────


def test_wilson_lcb_zero_data():
    assert wilson_lcb_95(0, 0) == 0.0


def test_wilson_lcb_zero_wins():
    # Floating-point: comes out as a tiny positive epsilon, not exact 0.
    assert wilson_lcb_95(0, 100) == pytest.approx(0.0, abs=1e-9)


def test_wilson_lcb_canonical_cases():
    assert wilson_lcb_95(8, 10) == pytest.approx(0.49, abs=0.01)
    assert wilson_lcb_95(75, 100) == pytest.approx(0.66, abs=0.01)
    assert wilson_lcb_95(12, 20) == pytest.approx(0.39, abs=0.01)


def test_wilson_lcb_penalizes_small_sample():
    """5/5 (100% raw) should LCB lower than 75/100 (75% raw)."""
    assert wilson_lcb_95(5, 5) < wilson_lcb_95(75, 100)


def test_wilson_lcb_monotonic_in_sample():
    """Same win rate but more samples → higher LCB."""
    assert wilson_lcb_95(80, 100) > wilson_lcb_95(8, 10)
    assert wilson_lcb_95(800, 1000) > wilson_lcb_95(80, 100)


# ── Edge factor ──────────────────────────────────────────────────────────


def test_edge_factor_zero():
    assert _edge_factor(0.0) == 1.0


def test_edge_factor_clip_floor():
    """Below -0.5 clips to -0.5."""
    assert _edge_factor(-5.0) == 0.5


def test_edge_factor_clip_ceiling():
    """Above 2.0 clips to 2.0 → multiplier 3.0."""
    assert _edge_factor(5.0) == 3.0


def test_edge_factor_typical():
    assert _edge_factor(0.10) == pytest.approx(1.10)
    assert _edge_factor(-0.10) == pytest.approx(0.90)


# ── Category bonus ───────────────────────────────────────────────────────


def test_category_bonus_no_target():
    assert _category_bonus(("Politics", "Crypto"), None) == 1.0


def test_category_bonus_no_match():
    assert _category_bonus(("Politics",), "Sports") == 1.0


def test_category_bonus_match():
    assert _category_bonus(("Politics", "Crypto"), "Politics") == 1.5


def test_category_bonus_url_encoded():
    """The leaderboard input uses 'Climate+and+Weather' but profile says
    'Climate'. The bonus function should match substring both ways."""
    assert _category_bonus(("Climate",), "Climate+and+Weather") == 1.5
    assert _category_bonus(("Climate and Weather",), "Climate") == 1.5


# ── compute_stats ───────────────────────────────────────────────────────


def _wp(name: str, ticker: str, pnl: float, contracts: int, is_open: bool = False) -> WhalePosition:
    return WhalePosition(
        market_id=f"m_{ticker}", market_ticker=ticker, name=name,
        is_open=is_open, pnl=pnl, contracts=contracts,
    )


def test_compute_stats_filters_by_name_and_closed():
    positions = [
        _wp("alice", "T1", 10.0, 100),   # win, closed
        _wp("alice", "T2", -5.0, 50),    # loss, closed
        _wp("alice", "T3", 8.0, 80, is_open=True),  # excluded (open)
        _wp("bob", "T4", 99.0, 1000),    # excluded (wrong name)
    ]
    stats = compute_stats("alice", positions, profile=None)
    assert stats.closed_positions_count == 2
    assert stats.wins == 1
    assert stats.losses == 1
    assert stats.total_pnl == 5.0
    assert stats.total_contracts == 150


def test_compute_stats_pulls_profile_fields():
    profile = WhaleProfile(
        nickname="alice", social_id="s1", pnl_units=999_999, num_markets_traded=42,
        follower_count=100, profile_view_count=1000, top_categories=("Sports",),
        joined_at="2024-01-01", posts_count=5, volume=None, open_interest=None,
    )
    stats = compute_stats("alice", [_wp("alice", "T1", 5.0, 10)], profile=profile)
    assert stats.top_categories == ("Sports",)
    assert stats.profile_pnl_units == 999_999
    assert stats.lifetime_num_markets_traded == 42


# ── score_whale exclusion paths ──────────────────────────────────────────


def test_score_whale_excludes_no_visibility():
    """Zero closed_positions → excluded as 'no_visibility'."""
    stats = compute_stats("alice", [], profile=None)
    scored = score_whale(stats, target_category="Sports", min_closed_positions=20)
    assert scored.excluded
    assert scored.exclusion_reason == "no_visibility"
    assert scored.composite_score == 0.0


def test_score_whale_excludes_sample_short():
    """Some closed_positions but below min_sample → excluded."""
    positions = [_wp("alice", f"T{i}", 1.0, 10) for i in range(5)]
    stats = compute_stats("alice", positions)
    scored = score_whale(stats, min_closed_positions=20)
    assert scored.excluded
    assert "sample<20" in scored.exclusion_reason


def test_score_whale_passes_when_enough_sample():
    positions = [_wp("alice", f"T{i}", 1.0 if i < 15 else -1.0, 10) for i in range(20)]
    stats = compute_stats("alice", positions)
    scored = score_whale(stats, min_closed_positions=20)
    assert not scored.excluded
    assert scored.composite_score > 0


def test_score_whale_category_bonus_applies():
    profile = WhaleProfile(
        nickname="alice", social_id="s", pnl_units=0, num_markets_traded=0,
        follower_count=0, profile_view_count=0,
        top_categories=("Sports", "Crypto"),
        joined_at="", posts_count=0, volume=None, open_interest=None,
    )
    positions = [_wp("alice", f"T{i}", 0.1, 10) for i in range(20)]
    stats = compute_stats("alice", positions, profile=profile)
    with_match = score_whale(stats, target_category="Sports", min_closed_positions=20)
    without_match = score_whale(stats, target_category="Politics", min_closed_positions=20)
    assert with_match.composite_score == pytest.approx(without_match.composite_score * 1.5)


# ── select_top_n ─────────────────────────────────────────────────────────


def test_select_top_n_drops_excluded_by_default():
    positions_pass = [_wp("alice", f"T{i}", 1.0, 10) for i in range(20)]
    positions_fail = [_wp("bob", f"T{i}", 1.0, 10) for i in range(5)]
    scored = [
        score_whale(compute_stats("alice", positions_pass), min_closed_positions=20),
        score_whale(compute_stats("bob", positions_fail), min_closed_positions=20),
    ]
    top = select_top_n(scored, n=2)
    assert len(top) == 1  # bob dropped (sample-short)
    assert top[0].stats.nickname == "alice"


def test_select_top_n_orders_by_composite():
    def make_passing(name: str, wins_of_20: int):
        positions = [
            _wp(name, f"T{i}", 1.0 if i < wins_of_20 else -1.0, 10)
            for i in range(20)
        ]
        return score_whale(compute_stats(name, positions), min_closed_positions=20)
    scored = [make_passing("low", 11), make_passing("high", 18), make_passing("mid", 14)]
    top = select_top_n(scored, n=3)
    assert [s.stats.nickname for s in top] == ["high", "mid", "low"]


# ── filter_leaderboard_for_discovery ─────────────────────────────────────


def test_filter_leaderboard_skips_anonymous():
    from trading_corp.data.kalshi_apify_client import LeaderboardEntry
    rows = [
        LeaderboardEntry("alice", 1, 100.0, "", "", is_anonymous=False),
        LeaderboardEntry("", 2, 90.0, "", "", is_anonymous=True),
        LeaderboardEntry("bob", 3, 80.0, "", "", is_anonymous=False),
    ]
    handles = filter_leaderboard_for_discovery(rows)
    assert handles == ["alice", "bob"]


def test_filter_leaderboard_respects_max_rank():
    from trading_corp.data.kalshi_apify_client import LeaderboardEntry
    rows = [
        LeaderboardEntry("alice", 5, 100.0, "", "", False),
        LeaderboardEntry("bob", 25, 80.0, "", "", False),
    ]
    assert filter_leaderboard_for_discovery(rows, max_rank=10) == ["alice"]


# ── Strategy: side detection ────────────────────────────────────────────


@pytest.fixture
def strategy(tmp_path):
    """A strategy bound to a fresh sqlite DB, enabled for testing."""
    from trading_corp.agents.strategies.kalshi_copy_trader import KalshiCopyTraderAgent
    db_path = tmp_path / "k3test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)

    # Point the strategy at an empty strategies.yaml so config is defaults +
    # we can force-enable by stubbing _strat_cfg directly after construction.
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text("kalshi_copy_trader:\n  enabled: true\n  poll_interval_sec: 300\n")
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("kalshi: {}\n")
    agent = KalshiCopyTraderAgent(
        strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url,
    )
    return agent, db_url


class _StubFetcher:
    def __init__(self, trades: list[KalshiPublicTrade]):
        self._trades = trades

    async def get_market_trades(
        self, ticker: str, *, since: datetime, until: datetime, limit: int = 100,
    ) -> list[KalshiPublicTrade]:
        return [t for t in self._trades if t.ticker == ticker]


@pytest.mark.asyncio
async def test_side_detection_no_fetcher(strategy):
    agent, _ = strategy
    side, conf, price = await agent._detect_side(
        ticker="T1", target_contracts=10,
        since=datetime.now(timezone.utc), until=datetime.now(timezone.utc),
        fetcher=None,
    )
    assert side == ""
    assert conf == "low"
    assert price is None


@pytest.mark.asyncio
async def test_side_detection_unique_match_high_confidence(strategy):
    agent, _ = strategy
    t0 = datetime(2026, 5, 11, 10, tzinfo=timezone.utc)
    trades = [
        KalshiPublicTrade("T1", 5, 0.42, 0.58, "yes", t0),    # wrong size
        KalshiPublicTrade("T1", 100, 0.50, 0.50, "no", t0),   # size match — NO taker
        KalshiPublicTrade("T1", 3, 0.10, 0.90, "yes", t0),    # wrong size
    ]
    side, conf, price = await agent._detect_side(
        ticker="T1", target_contracts=100,
        since=t0 - timedelta(minutes=10), until=t0 + timedelta(minutes=10),
        fetcher=_StubFetcher(trades),
    )
    assert side == "no"
    assert conf == "high"
    # Matched NO taker on a $0.50/$0.50 trade → captured price = no leg.
    assert price == 0.50


@pytest.mark.asyncio
async def test_side_detection_multiple_matches_medium_confidence(strategy):
    agent, _ = strategy
    t0 = datetime(2026, 5, 11, 10, tzinfo=timezone.utc)
    until = datetime(2026, 5, 11, 12, tzinfo=timezone.utc)
    trades = [
        KalshiPublicTrade("T1", 100, 0.40, 0.60, "yes", t0),
        KalshiPublicTrade("T1", 100, 0.45, 0.55, "no", until - timedelta(minutes=1)),
    ]
    side, conf, price = await agent._detect_side(
        ticker="T1", target_contracts=100,
        since=t0 - timedelta(minutes=1), until=until,
        fetcher=_StubFetcher(trades),
    )
    assert conf == "medium"
    assert side == "no"  # the one closest to `until` wins
    # Nearest-to-until trade was the NO taker @ no_price=0.55.
    assert price == 0.55


@pytest.mark.asyncio
async def test_side_detection_no_match_low_confidence(strategy):
    agent, _ = strategy
    t0 = datetime(2026, 5, 11, 10, tzinfo=timezone.utc)
    trades = [KalshiPublicTrade("T1", 5, 0.50, 0.50, "yes", t0)]
    side, conf, price = await agent._detect_side(
        ticker="T1", target_contracts=500,  # no size-match
        since=t0 - timedelta(minutes=1), until=t0 + timedelta(minutes=1),
        fetcher=_StubFetcher(trades),
    )
    assert side == ""
    assert conf == "low"
    assert price is None


# ── Strategy: sizing tiers ──────────────────────────────────────────────


def test_sizing_tier_boundaries(strategy):
    agent, _ = strategy
    assert agent._size_tier_usd(50) == 1.0
    assert agent._size_tier_usd(99) == 1.0
    assert agent._size_tier_usd(100) == 2.0  # inclusive lower bound on tier 2
    assert agent._size_tier_usd(500) == 2.0
    assert agent._size_tier_usd(999) == 2.0
    assert agent._size_tier_usd(1000) == 3.0
    assert agent._size_tier_usd(50000) == 3.0


# ── Strategy: cold-start ────────────────────────────────────────────────


class _StubApifyClient:
    """Minimal mock of KalshiApifyClient for delta tests."""
    def __init__(self, positions_by_call: list[list[WhalePosition]]):
        self._positions = positions_by_call
        self._call = 0

    async def fetch_open_positions(self, names: list[str]) -> list[WhalePosition]:
        idx = min(self._call, len(self._positions) - 1)
        self._call += 1
        return list(self._positions[idx])


class _StubLogger:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []
        self.proposed: list[Any] = []

    def log_event(self, actor: str, kind: str, payload: dict) -> None:
        self.events.append((actor, kind, payload))

    def log_proposed_order(self, order: Any) -> None:
        self.proposed.append(order)


@pytest.mark.asyncio
async def test_cold_start_emits_nothing(strategy):
    agent, db_url = strategy
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales", ["alice"], db_url=db_url,
    )
    apify = _StubApifyClient([
        [_wp("alice", "T1", 5.0, 100, is_open=True)],
    ])
    logger = _StubLogger()
    orders = await agent.run_scan_cycle(
        apify_client=apify, trade_tape_fetcher=None, logger_agent=logger,
    )
    assert orders == []
    # baseline should be persisted; subsequent polls would detect deltas
    rec = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)
    assert rec is not None
    snapshot, _ = rec
    assert "T1" in snapshot
    assert snapshot["T1"]["our_side"] == ""  # baseline marker, no copy opened


@pytest.mark.asyncio
async def test_entry_detected_on_second_poll_with_side(strategy):
    agent, db_url = strategy
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales", ["alice"], db_url=db_url,
    )
    # Poll 1: alice has T1. Poll 2: alice has T1 + T2 (new entry).
    apify = _StubApifyClient([
        [_wp("alice", "T1", 0.0, 100, is_open=True)],
        [
            _wp("alice", "T1", 0.0, 100, is_open=True),
            _wp("alice", "T2", 0.0, 50, is_open=True),
        ],
    ])
    # Stub trade-tape: one YES trade matching 50 contracts → high confidence
    fetcher = _StubFetcher([
        KalshiPublicTrade("T2", 50, 0.40, 0.60, "yes", datetime.now(timezone.utc)),
    ])
    logger = _StubLogger()

    # Cold-start poll (no orders).
    orders1 = await agent.run_scan_cycle(
        apify_client=apify, trade_tape_fetcher=fetcher, logger_agent=logger,
    )
    assert orders1 == []

    # Second poll: should detect T2 as new entry, emit one ProposedOrder.
    orders2 = await agent.run_scan_cycle(
        apify_client=apify, trade_tape_fetcher=fetcher, logger_agent=logger,
    )
    assert len(orders2) == 1
    order = orders2[0]
    assert order.symbol == "T2:yes"
    assert order.side == "buy"
    assert order.qty == 1.0  # tier 1 ($1) since contracts < 100
    assert order.extra["whale_handle"] == "alice"
    assert order.extra["is_entry"] is True
    assert order.extra["side_detection_confidence"] == "high"


@pytest.mark.asyncio
async def test_entry_skipped_when_side_detection_fails(strategy):
    agent, db_url = strategy
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales", ["alice"], db_url=db_url,
    )
    apify = _StubApifyClient([
        [],  # cold start: no positions
        [_wp("alice", "T1", 0.0, 50, is_open=True)],  # new position
    ])
    # No trade-tape data → low confidence → skip entry
    fetcher = _StubFetcher([])
    logger = _StubLogger()

    await agent.run_scan_cycle(
        apify_client=apify, trade_tape_fetcher=fetcher, logger_agent=logger,
    )
    orders = await agent.run_scan_cycle(
        apify_client=apify, trade_tape_fetcher=fetcher, logger_agent=logger,
    )
    assert orders == []
    # We should still record the position in snapshot (no our_side, so no
    # phantom exit when the whale closes it).
    rec = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)
    snapshot, _ = rec
    assert snapshot["T1"]["our_side"] == ""
    # And we should have logged the skip event.
    kinds = [k for _, k, _ in logger.events]
    assert "kalshi_copy_entry_skipped_no_side" in kinds


@pytest.mark.asyncio
async def test_exit_emitted_when_whale_closes_held_position(strategy):
    agent, db_url = strategy
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales", ["alice"], db_url=db_url,
    )
    apify = _StubApifyClient([
        [],  # poll 1: cold start, no positions
        [_wp("alice", "T1", 0.0, 50, is_open=True)],  # poll 2: enter T1
        [],  # poll 3: alice closes T1
    ])
    fetcher = _StubFetcher([
        KalshiPublicTrade("T1", 50, 0.30, 0.70, "yes", datetime.now(timezone.utc)),
    ])
    logger = _StubLogger()

    await agent.run_scan_cycle(apify_client=apify, trade_tape_fetcher=fetcher,
                               logger_agent=logger)  # cold start
    orders2 = await agent.run_scan_cycle(apify_client=apify, trade_tape_fetcher=fetcher,
                                         logger_agent=logger)  # entry
    assert len(orders2) == 1 and orders2[0].extra["is_entry"] is True
    orders3 = await agent.run_scan_cycle(apify_client=apify, trade_tape_fetcher=fetcher,
                                         logger_agent=logger)  # exit
    assert len(orders3) == 1
    exit_order = orders3[0]
    assert exit_order.symbol == "T1:yes"
    assert exit_order.side == "sell"
    assert exit_order.extra["is_entry"] is False
    assert exit_order.qty == 1.0  # mirrors what we opened
