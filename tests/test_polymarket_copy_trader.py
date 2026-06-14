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
         txh: str | None = None, asset: str = "") -> ActivityRow:
    # Default txhash includes side + ts so BUY and SELL on the same market
    # don't collide in the strategy's dedup set.
    if txh is None:
        txh = f"tx-{condition_id}-{side}-{ts}"
    return ActivityRow(
        proxy_wallet="0xW", timestamp=ts, condition_id=condition_id, type="TRADE",
        size=size, usdc_size=size * price, transaction_hash=txh,
        price=price, asset=asset, side=side, outcome_index=outcome_index,
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
        _act("cid2", 0, price=0.40, size=1250, ts=2000),  # whale $500 bet
        _act("cid1", 0, price=0.40, size=100, ts=1000),
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "buy"
    assert o.symbol == "cid2:Yes"
    # E2·3 (D4): flat ≈$1 sizing (no longer the $500→$2 tier). Default config
    # 120.0 * 0.00833 * 1.0 = 0.9996 USDC; contracts = 0.9996 / 0.40 ≈ 2.499.
    assert o.extra["copy_size_usdc"] == pytest.approx(0.9996)
    assert o.qty == pytest.approx(0.9996 / 0.40)
    assert o.limit_price == pytest.approx(0.40)
    assert o.extra["is_entry"] is True
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
    # exit mirrors the flat-$1 entry size: 0.9996 USDC / 0.40 entry ≈ 2.499 contracts
    assert o.qty == pytest.approx(0.9996 / 0.40)
    assert o.limit_price == pytest.approx(0.65)
    assert o.extra["is_entry"] is False
    # Group A #1: exit carries the flag too; implied_prob_at_entry is the
    # ORIGINAL entry price (0.40), NOT the 0.65 exit price — so the gate's
    # implied-prob bound can't spuriously reject a legitimate close.
    assert o.extra["is_prediction_market"] is True
    assert o.extra["implied_prob_at_entry"] == pytest.approx(0.40)


# ── E2·1: token_id propagation (activity.asset → extra["token_id"]) ──────────


@pytest.mark.asyncio
async def test_entry_extra_carries_token_id(strategy):
    """E2·1: a copy ENTRY puts the whale's activity.asset into extra['token_id']
    so the broker's DIRECT token_id path fires (not the gamma fallback)."""
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": []}))  # cold start
    orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cid2", 0, price=0.40, size=1250, ts=2000, asset="74100200300"),
    ]}))
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].extra["token_id"] == "74100200300"


@pytest.mark.asyncio
async def test_exit_extra_carries_token_id(strategy):
    """E2·1: a copy EXIT carries the close leg's token id (the whale SELL
    activity row's asset — same outcome we hold)."""
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": []}))  # cold start
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cid1", 0, price=0.40, size=1250, ts=2000, asset="888"),  # entry, held
    ]}))
    orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cid1", 0, price=0.65, size=1250, ts=3000, side="SELL", asset="888"),
        _act("cid1", 0, price=0.40, size=1250, ts=2000, asset="888"),
    ]}))
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].extra["token_id"] == "888"


@pytest.mark.asyncio
async def test_absent_asset_token_id_is_none(strategy):
    """E2·1: when activity.asset is absent (empty), extra['token_id'] is None so
    the broker's gamma-lookup fallback stays intact (present→direct, absent→gamma —
    both paths preserved). Does NOT assert a token_id that isn't there."""
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": []}))  # cold start
    orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act("cid2", 0, price=0.40, size=1250, ts=2000),  # asset="" (default)
    ]}))
    assert len(orders) == 1
    assert orders[0].extra.get("token_id") is None


def test_broker_consumes_strategy_token_id_direct_then_fallback():
    """E2·1 linkage: the producer (strategy extra) feeds the consumer
    (PolymarketLiveBroker.resolve_token_id). token_id present → DIRECT (no
    network); absent → NOT direct (gamma territory); absent + fetcher → gamma
    resolves. Confirms the direct path is now the norm AND the fallback is intact."""
    from trading_corp.brokers.polymarket_live import (
        TokenIdResolutionError, resolve_token_id,
    )
    # direct: the strategy's extra["token_id"] is returned verbatim, no fetcher
    assert resolve_token_id({"token_id": "74100200300"}) == "74100200300"
    # absent token_id + no fetcher → not direct; gamma-fallback territory
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id({"condition_id": "0xabc", "outcome_index": 0})
    # absent token_id + gamma fetcher → fallback resolves (path preserved)
    market = {"conditionId": "0xabc", "clobTokenIds": '["111", "222"]',
              "outcomes": '["Yes", "No"]'}
    assert resolve_token_id(
        {"condition_id": "0xabc", "outcome": "Yes", "outcome_index": 0},
        market_fetcher=lambda cid: market,
    ) == "111"


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
async def test_strategy_sizing_is_flat_regardless_of_whale_size(strategy):
    # E2·3 (D4): copy sizing is flat ≈$1 — REPLACED the v1 $1/$2/$5 tier ladder, so
    # the whale's bet size no longer changes our copy size (proves the cutover).
    agent, db_url = strategy
    set_agent_state(
        "polymarket_copy_trader", "selected_whales",
        [{"wallet": "0xW", "user_name": "alice", "category": "Sports"}],
        db_url=db_url,
    )
    apify = _StubDataAPI({"0xW": []})
    await agent.run_scan_cycle(data_api_client=apify)
    apify = _StubDataAPI({"0xW": [
        _act("sm", 0, price=0.50, size=100, ts=1001),    # whale $50 bet
        _act("md", 0, price=0.50, size=1000, ts=1002),   # whale $500 bet
        _act("lg", 0, price=0.50, size=10000, ts=1003),  # whale $5000 bet
    ]})
    orders = await agent.run_scan_cycle(data_api_client=apify)
    sizes = {o.symbol.split(":")[0]: o.extra["copy_size_usdc"] for o in orders}
    qty_by_sym = {o.symbol.split(":")[0]: o.qty for o in orders}
    # all flat ≈$1 (120*0.00833=0.9996) + same contracts (0.9996/0.50), independent
    # of the 100×-varying whale bet sizes.
    for sym in ("sm", "md", "lg"):
        assert sizes[sym] == pytest.approx(0.9996)
        assert qty_by_sym[sym] == pytest.approx(0.9996 / 0.50)


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


# ── E2·3: clamp sizing formula + schema (D4) ─────────────────────────────────
# size = clamp(bankroll_usdc * per_trade_fraction * conviction_mult, min_size, max_size)
# Default → flat ≈$1, conviction OFF. These exercise the sizing fns directly (pure;
# no scan cycle) across the default / clamp / scaling paths.


def _sizing_cfg(**over):
    """A full sizing config; `over` replaces individual keys for clamp/scale cases."""
    base = {
        "bankroll_usdc": 120.0, "per_trade_fraction": 0.00833,
        "min_size": 0.50, "max_size": 2.00,
        "conviction": {"enabled": False, "signal": "composite_score",
                       "floor": 0.5, "cap": 2.0},
    }
    base.update(over)
    return {"sizing": base}


def test_sizing_default_is_flat_about_one_dollar(strategy):
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg()
    size = agent._compute_copy_size_usdc()
    assert size == pytest.approx(0.9996)        # 120.0 * 0.00833 * 1.0
    assert 0.95 <= size <= 1.05                 # lands in the intended ~$1 band


def test_sizing_default_via_code_constants(strategy):
    # No sizing block at all → module-default constants still yield ~$1.
    agent, _ = strategy
    agent._strat_cfg = {"enabled": True}        # no "sizing" key
    assert agent._compute_copy_size_usdc() == pytest.approx(0.9996)


def test_sizing_conviction_inert_under_default(strategy):
    # conviction OFF → a whale_meta signal has NO effect on size (multiplier 1.0).
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg()
    with_signal = agent._compute_copy_size_usdc(whale_meta={"composite_score": 1.8})
    without = agent._compute_copy_size_usdc(whale_meta=None)
    assert with_signal == pytest.approx(without) == pytest.approx(0.9996)
    assert agent._conviction_mult({"composite_score": 1.8}) == 1.0


def test_sizing_clamp_floors_at_min(strategy):
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg(per_trade_fraction=0.0001)    # raw 120*0.0001=0.012
    assert agent._compute_copy_size_usdc() == pytest.approx(0.50)  # floored to min_size


def test_sizing_clamp_ceils_at_max(strategy):
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg(per_trade_fraction=0.5)       # raw 120*0.5=60
    assert agent._compute_copy_size_usdc() == pytest.approx(2.00)  # ceiled to max_size


def test_sizing_scales_with_fraction_and_conviction(strategy):
    # Non-default fraction + conviction ON → the full formula scales end-to-end
    # (proves the schema works, not just the $1 path). 100.0 * 0.05 * 1.5 = 7.5.
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg(
        bankroll_usdc=100.0, per_trade_fraction=0.05,
        min_size=0.10, max_size=100.0,
        conviction={"enabled": True, "signal": "composite_score",
                    "floor": 0.5, "cap": 3.0},
    )
    size = agent._compute_copy_size_usdc(whale_meta={"composite_score": 1.5})
    assert size == pytest.approx(7.5)


def test_conviction_mult_clamps_floor_and_cap(strategy):
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg(
        conviction={"enabled": True, "signal": "composite_score",
                    "floor": 0.5, "cap": 2.0},
    )
    assert agent._conviction_mult({"composite_score": 5.0}) == pytest.approx(2.0)  # capped
    assert agent._conviction_mult({"composite_score": 0.1}) == pytest.approx(0.5)  # floored
    assert agent._conviction_mult({"composite_score": 1.3}) == pytest.approx(1.3)  # passthrough


def test_conviction_mult_missing_or_bad_signal_is_neutral(strategy):
    # conviction ON but the signal is absent/unparseable → neutral 1.0 (never amplify
    # sizing on bad data).
    agent, _ = strategy
    agent._strat_cfg = _sizing_cfg(
        conviction={"enabled": True, "signal": "composite_score",
                    "floor": 0.5, "cap": 2.0},
    )
    assert agent._conviction_mult(None) == 1.0
    assert agent._conviction_mult({}) == 1.0
    assert agent._conviction_mult({"composite_score": "n/a"}) == 1.0
