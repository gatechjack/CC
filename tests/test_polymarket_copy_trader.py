"""Tests for Polymarket copy-trading: data API client, scoring, strategy.

Network-free. Exercises:
  - dataclass `from_api` constructors (LeaderboardEntry, ActivityRow, PositionRow)
  - `_decode_resolution` (resolved / void / pending paths)
  - time-weighted Wilson LCB sanity (delegates to kalshi_whale_stats)
  - `compute_polymarket_stats` outcome inference
  - strategy delta detection: cold-start, entry, exit, sizing, qty=contracts
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.agents.strategies.polymarket_copy_trader import (
    PolymarketCopyTraderAgent,
)
from trading_corp.data.kalshi_whale_stats import (
    time_weighted_outcomes, wilson_lcb_95_weighted,
)
from trading_corp.data.polymarket_data_api_client import (
    ActivityRow, LeaderboardEntry, PositionRow, _decode_resolution,
)
from trading_corp.data.polymarket_whale_stats import (
    _is_win_for_buy, compute_polymarket_stats, score_polymarket_whale,
)
from trading_corp.persistence import db as _db
from trading_corp.persistence.db import set_agent_state


# ── dataclass parsers ────────────────────────────────────────────────────


def test_leaderboard_entry_from_api():
    row = {
        "rank": "3", "proxyWallet": "0xABC", "userName": "alice",
        "xUsername": "alice_x", "verifiedBadge": True,
        "vol": 12345.67, "pnl": 999.99, "profileImage": "img.png",
    }
    e = LeaderboardEntry.from_api(row)
    assert e.rank == 3
    assert e.proxy_wallet == "0xabc"  # lowercased
    assert e.user_name == "alice"
    assert e.verified_badge is True
    assert e.vol == 12345.67


def test_activity_row_from_api_full_shape():
    row = {
        "proxyWallet": "0xABC", "timestamp": 1778525393,
        "conditionId": "0xcid1", "type": "TRADE",
        "size": 31.57, "usdcSize": 14.92, "transactionHash": "0xtx1",
        "price": 0.48, "asset": "12345", "side": "BUY",
        "outcomeIndex": 0, "title": "Spread", "slug": "spread",
        "eventSlug": "nba-x", "outcome": "Spurs", "name": "yaya",
        "pseudonym": "pseu", "bio": "",
    }
    a = ActivityRow.from_api(row)
    assert a.proxy_wallet == "0xabc"
    assert a.timestamp == 1778525393
    assert a.type == "TRADE"
    assert a.side == "BUY"
    assert a.outcome_index == 0
    assert a.outcome == "Spurs"
    assert a.price == 0.48
    assert "pseudonym" in a.extra  # unconsumed fields preserved


def test_position_row_from_api():
    row = {
        "proxyWallet": "0xWHALE", "conditionId": "0xc",
        "asset": "ass1", "size": 1000.0, "avgPrice": 0.42,
        "initialValue": 420.0, "currentValue": 510.0, "cashPnl": 90.0,
        "title": "T", "outcome": "Yes", "slug": "s", "eventSlug": "e",
    }
    p = PositionRow.from_api(row)
    assert p.size == 1000.0
    assert p.avg_price == 0.42
    assert p.pnl == 90.0


# ── _decode_resolution ──────────────────────────────────────────────────


def test_decode_resolution_resolved_yes():
    m = {"closed": True,
         "outcomePrices": '["0.000001", "0.999999"]',
         "outcomes": '["Yes", "No"]',
         "question": "Q?"}
    r = _decode_resolution(m)
    assert r["status"] == "resolved"
    assert r["winning_outcome_index"] == 1
    assert r["yes_won"] is False


def test_decode_resolution_resolved_first():
    m = {"closed": True,
         "outcomePrices": '["0.95", "0.05"]',
         "outcomes": '["Yes", "No"]'}
    r = _decode_resolution(m)
    assert r["status"] == "resolved"
    assert r["winning_outcome_index"] == 0
    assert r["yes_won"] is True


def test_decode_resolution_void():
    """Closed market with all near-zero prices → void."""
    m = {"closed": True,
         "outcomePrices": '["0", "0"]',
         "outcomes": '["Yes", "No"]'}
    r = _decode_resolution(m)
    assert r["status"] == "void"
    assert r["winning_outcome_index"] is None


def test_decode_resolution_pending():
    m = {"closed": False,
         "outcomePrices": '["0.45", "0.55"]',
         "outcomes": '["Yes", "No"]'}
    r = _decode_resolution(m)
    assert r["status"] == "pending"
    assert r["winning_outcome_index"] is None


# ── _is_win_for_buy ──────────────────────────────────────────────────────


def _act(condition_id: str, outcome_index: int, side: str = "BUY",
         price: float = 0.5, size: float = 100.0, ts: int = 1000,
         txh: str | None = None) -> ActivityRow:
    # Default txhash includes side + ts so BUY and SELL on the same market
    # don't collide in the strategy's dedup set.
    if txh is None:
        txh = f"tx-{condition_id}-{side}-{ts}"
    return ActivityRow(
        proxy_wallet="0xW", timestamp=ts, condition_id=condition_id, type="TRADE",
        size=size, usdc_size=size * price, transaction_hash=txh,
        price=price, asset="", side=side, outcome_index=outcome_index,
        title="t", slug="", event_slug="", outcome="Yes" if outcome_index == 0 else "No",
        name="alice",
    )


def test_is_win_for_buy_correct_side():
    a = _act("cid1", outcome_index=0)
    res = {"status": "resolved", "winning_outcome_index": 0}
    assert _is_win_for_buy(a, res) is True


def test_is_win_for_buy_wrong_side():
    a = _act("cid1", outcome_index=0)
    res = {"status": "resolved", "winning_outcome_index": 1}
    assert _is_win_for_buy(a, res) is False


def test_is_win_for_buy_pending_returns_none():
    a = _act("cid1", outcome_index=0)
    assert _is_win_for_buy(a, {"status": "pending"}) is None


def test_is_win_for_buy_void_returns_none():
    a = _act("cid1", outcome_index=0)
    assert _is_win_for_buy(a, {"status": "void"}) is None


def test_is_win_for_buy_yes_won_fallback():
    """When winning_outcome_index is missing, fall back to yes_won."""
    a = _act("cid1", outcome_index=0)
    assert _is_win_for_buy(a, {"status": "resolved", "yes_won": True}) is True
    assert _is_win_for_buy(a, {"status": "resolved", "yes_won": False}) is False


# ── compute_polymarket_stats end-to-end ─────────────────────────────────


def test_compute_polymarket_stats_filters_resolved_only():
    now = time.time()
    lb = LeaderboardEntry(rank=1, proxy_wallet="0xW", user_name="alice",
                          x_username="", verified_badge=False, vol=1000.0,
                          pnl=200.0, profile_image="")
    activities = [
        _act("cid_win", outcome_index=0, price=0.40, size=100, ts=int(now - 2*86400)),
        _act("cid_loss", outcome_index=0, price=0.55, size=50, ts=int(now - 5*86400)),
        _act("cid_unresolved", outcome_index=0, price=0.30, size=80, ts=int(now - 1*86400)),
        _act("cid_void", outcome_index=0, price=0.20, size=10, ts=int(now - 10*86400)),
        _act("cid_skip_sell", outcome_index=0, price=0.50, size=100, side="SELL",
             ts=int(now - 3*86400)),
    ]
    resolutions = {
        "cid_win": {"status": "resolved", "winning_outcome_index": 0},
        "cid_loss": {"status": "resolved", "winning_outcome_index": 1},
        "cid_unresolved": {"status": "pending"},
        "cid_void": {"status": "void"},
    }
    stats, outcomes = compute_polymarket_stats(
        leaderboard_entry=lb, activity_rows=activities,
        market_resolutions=resolutions, half_life_days=30, now_ts=now,
    )
    assert stats.wins == 1
    assert stats.losses == 1
    assert stats.closed_positions_count == 2
    assert len(outcomes) == 2
    # P&L math: win at $0.40 size 100 → +$60 ($0.60/contract × 100)
    # loss at $0.55 size 50 → -$27.50
    assert stats.total_pnl == pytest.approx(60.0 - 27.5, abs=1e-6)


# ── score_polymarket_whale ──────────────────────────────────────────────


def test_score_polymarket_whale_excludes_no_resolved():
    lb = LeaderboardEntry(0, "0xW", "alice", "", False, 0.0, 0.0, "")
    stats, _ = compute_polymarket_stats(
        leaderboard_entry=lb, activity_rows=[], market_resolutions={},
    )
    sw = score_polymarket_whale(stats, min_resolved=5)
    assert sw.excluded
    assert sw.exclusion_reason == "no_resolved_trades"


def test_score_polymarket_whale_under_sample_excluded():
    now = time.time()
    lb = LeaderboardEntry(0, "0xW", "alice", "", False, 0.0, 0.0, "")
    acts = [_act(f"cid{i}", 0, ts=int(now - i*86400)) for i in range(3)]
    res = {f"cid{i}": {"status": "resolved", "winning_outcome_index": 0} for i in range(3)}
    stats, _ = compute_polymarket_stats(
        leaderboard_entry=lb, activity_rows=acts, market_resolutions=res,
        now_ts=now,
    )
    sw = score_polymarket_whale(stats, min_resolved=10)
    assert sw.excluded
    assert "resolved<10" in sw.exclusion_reason


def test_score_polymarket_whale_passes_with_enough():
    now = time.time()
    lb = LeaderboardEntry(0, "0xW", "alice", "", False, 0.0, 0.0, "")
    acts = [_act(f"cid{i}", 0, ts=int(now - i*86400)) for i in range(15)]
    res = {f"cid{i}": {"status": "resolved", "winning_outcome_index": 0}
           for i in range(15)}
    stats, _ = compute_polymarket_stats(
        leaderboard_entry=lb, activity_rows=acts, market_resolutions=res,
        now_ts=now,
    )
    sw = score_polymarket_whale(stats, min_resolved=10)
    assert not sw.excluded
    assert sw.wilson_lcb > 0.5  # 15/15 wins → high confidence


# ── Strategy: cold-start + delta detection ──────────────────────────────


class _StubDataAPI:
    def __init__(self, by_wallet):
        self._by = by_wallet
    async def fetch_activity(self, wallet, *, limit=20, offset=0):
        return list(self._by.get(wallet, []))


@pytest.fixture
def strategy(tmp_path):
    db_path = tmp_path / "pmtest.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "polymarket_copy_trader:\n  enabled: true\n  poll_interval_sec: 60\n"
    )
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("polymarket: {}\n")
    agent = PolymarketCopyTraderAgent(
        strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url,
    )
    return agent, db_url


@pytest.mark.asyncio
async def test_strategy_cold_start_emits_nothing(strategy):
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    apify = _StubDataAPI({"0xW": [
        _act("cid1", 0, price=0.40, size=100, ts=1000),
        _act("cid2", 1, price=0.30, size=50, ts=2000),
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    assert orders == []


@pytest.mark.asyncio
async def test_strategy_new_buy_emits_entry(strategy):
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    apify = _StubDataAPI({"0xW": [_act("cid1", 0, price=0.40, size=100, ts=1000)]})
    # Cold start
    await agent.run_scan_cycle(data_api_client=apify)
    # Now a NEW buy at later timestamp
    apify = _StubDataAPI({"0xW": [
        _act("cid2", 0, price=0.40, size=1250, ts=2000),  # $500 bet → tier 2
        _act("cid1", 0, price=0.40, size=100, ts=1000),
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "buy"
    assert o.symbol == "cid2:Yes"
    # $2 copy at $0.40 = 5 contracts
    assert o.qty == pytest.approx(5.0)
    assert o.limit_price == pytest.approx(0.40)
    assert o.extra["is_entry"] is True
    assert o.extra["copy_size_usdc"] == 2.0
    # Group A #1: entry now carries the routing flag + implied prob so
    # RiskAgent._evaluate_polymarket actually sees it (was never set before,
    # so the Polymarket caps silently never applied to copy-trader orders).
    assert o.extra["is_prediction_market"] is True
    assert o.extra["implied_prob_at_entry"] == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_strategy_sell_emits_exit_when_we_hold(strategy):
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    # Cold start with empty
    apify = _StubDataAPI({"0xW": []})
    await agent.run_scan_cycle(data_api_client=apify)
    # Round 2: entry @ $0.40
    apify = _StubDataAPI({"0xW": [
        _act("cid1", 0, price=0.40, size=1250, ts=2000),
    ]})
    await agent.run_scan_cycle(data_api_client=apify)
    # Round 3: whale sells @ $0.65 — we should close
    apify = _StubDataAPI({"0xW": [
        _act("cid1", 0, price=0.65, size=1250, ts=3000, side="SELL"),
        _act("cid1", 0, price=0.40, size=1250, ts=2000),
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "sell"
    assert o.qty == pytest.approx(5.0)  # 5 contracts (matches entry size)
    assert o.limit_price == pytest.approx(0.65)
    assert o.extra["is_entry"] is False
    # Group A #1: exit carries the flag too; implied_prob_at_entry is the
    # ORIGINAL entry price (0.40), NOT the 0.65 exit price — so the gate's
    # implied-prob bound can't spuriously reject a legitimate close.
    assert o.extra["is_prediction_market"] is True
    assert o.extra["implied_prob_at_entry"] == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_strategy_sell_without_held_position_no_op(strategy):
    """Whale sells a position we never opened → no order."""
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    apify = _StubDataAPI({"0xW": []})
    await agent.run_scan_cycle(data_api_client=apify)
    # Whale sells a position we never copied
    apify = _StubDataAPI({"0xW": [
        _act("cid_other", 0, price=0.50, size=100, ts=2000, side="SELL"),
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    assert orders == []


@pytest.mark.asyncio
async def test_strategy_sizing_tiers(strategy):
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    apify = _StubDataAPI({"0xW": []})
    await agent.run_scan_cycle(data_api_client=apify)
    apify = _StubDataAPI({"0xW": [
        _act("sm", 0, price=0.50, size=100, ts=1001),    # $50 → tier 1 $1
        _act("md", 0, price=0.50, size=1000, ts=1002),   # $500 → tier 2 $2
        _act("lg", 0, price=0.50, size=10000, ts=1003),  # $5000 → tier 3 $5
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    sizes = {o.symbol.split(":")[0]: o.extra["copy_size_usdc"] for o in orders}
    assert sizes["sm"] == 1.0
    assert sizes["md"] == 2.0
    assert sizes["lg"] == 5.0
    # contracts = copy_usdc / 0.50
    qty_by_sym = {o.symbol.split(":")[0]: o.qty for o in orders}
    assert qty_by_sym["sm"] == pytest.approx(2.0)
    assert qty_by_sym["md"] == pytest.approx(4.0)
    assert qty_by_sym["lg"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_strategy_skips_zero_price_edge_case(strategy):
    """Activity with price=0 (malformed) shouldn't emit a divide-by-zero."""
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    apify = _StubDataAPI({"0xW": []})
    await agent.run_scan_cycle(data_api_client=apify)
    apify = _StubDataAPI({"0xW": [_act("cid1", 0, price=0.0, size=100, ts=2000)]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    assert orders == []  # price=0 is rejected before emitting


@pytest.mark.asyncio
async def test_strategy_dedups_txhash_across_polls(strategy):
    """Same transaction_hash appearing in 2 successive polls should
    emit exactly one order — overlapping windows are normal."""
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": []}))
    new_buy = _act("cid1", 0, price=0.40, size=100, ts=2000)
    orders1 = await agent.run_scan_cycle(
        data_api_client=_StubDataAPI({"0xW": [new_buy]})
    )
    assert len(orders1) == 1
    # Same row again — should not re-emit
    orders2 = await agent.run_scan_cycle(
        data_api_client=_StubDataAPI({"0xW": [new_buy]})
    )
    assert orders2 == []
