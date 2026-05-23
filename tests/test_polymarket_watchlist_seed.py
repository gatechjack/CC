"""Tests for the windowed Polymarket watchlist seed.

Covers:
  - `_fetch_wallet_activity_windowed` termination by each of three paths:
    target_buys_reached / exhausted / max_pages_hit.
  - `_select_resolved_buys_window` picks the most-recent N resolved BUYs,
    skipping non-TRADE, non-BUY, and unresolved rows.
  - `seed_polymarket_watchlist_deep` applies each floor (n, recency, WR,
    PnL) and records the drop_reasons telemetry honestly.
  - `compute_polymarket_stats` is invoked with half_life_days=36500.0
    (effectively no half-life — the window IS the recency mechanism).
  - `provisional` flag fires iff window_size_n < provisional_threshold.
  - True N is reported (never silently 100) when the ceiling hits.

Network-free; uses a fake PolymarketDataAPIClient that supports the async
context-manager protocol plus canned responses for the three fetch methods.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from trading_corp.data.polymarket_data_api_client import (
    ActivityRow,
    LeaderboardEntry,
)
from trading_corp.scripts import seed_polymarket_watchlist_deep as seed_mod
from trading_corp.scripts.seed_polymarket_watchlist_deep import (
    _fetch_wallet_activity_windowed,
    _select_resolved_buys_window,
    seed_polymarket_watchlist_deep,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_activity(
    ts: int, condition_id: str, *, side: str = "BUY",
    outcome_index: int = 0, price: float = 0.5, size: float = 10.0,
    type_: str = "TRADE", wallet: str = "0xabc",
) -> ActivityRow:
    return ActivityRow(
        proxy_wallet=wallet,
        timestamp=ts,
        condition_id=condition_id,
        type=type_,
        size=size,
        usdc_size=size * price,
        transaction_hash="0xhash",
        price=price,
        asset="asset",
        side=side,
        outcome_index=outcome_index,
        title=f"market {condition_id}",
        slug=condition_id,
        event_slug="event",
        outcome="Yes" if outcome_index == 0 else "No",
        name="whale",
    )


def _resolved(winning_outcome_index: int = 0) -> dict[str, Any]:
    return {
        "status": "resolved",
        "winning_outcome_index": winning_outcome_index,
        "yes_won": winning_outcome_index == 0,
        "outcomes": ["Yes", "No"],
        "outcome_prices": [
            1.0 if winning_outcome_index == 0 else 0.0,
            0.0 if winning_outcome_index == 0 else 1.0,
        ],
        "closed": True,
        "title": "title",
    }


def _make_lb(
    wallet: str, *, rank: int = 1, user_name: str = "whale",
    vol: float = 1000.0, pnl: float = 100.0,
) -> LeaderboardEntry:
    return LeaderboardEntry(
        rank=rank,
        proxy_wallet=wallet,
        user_name=user_name,
        x_username="",
        verified_badge=False,
        vol=vol,
        pnl=pnl,
        profile_image="",
    )


class _FakeActivityClient:
    """Fake client supporting only fetch_activity (offset pagination)."""

    def __init__(self, pages: list[list[ActivityRow]]) -> None:
        self._pages = list(pages)
        self.call_count = 0

    async def fetch_activity(
        self, wallet: str, *, limit: int, offset: int,
    ) -> list[ActivityRow]:
        self.call_count += 1
        idx = offset // limit if limit else 0
        if 0 <= idx < len(self._pages):
            return list(self._pages[idx])
        return []


class _FakeFullClient:
    """Fake client supporting the full seed pipeline.

    `leaderboard_by_category` is a dict {category|None: list[LeaderboardEntry]}.
    `activity_by_wallet` is a dict {wallet: list[list[ActivityRow]]} (pages).
    `resolutions` is the pre-built {condition_id: resolution_dict}.
    """

    def __init__(
        self,
        *,
        leaderboard_by_category: dict[str | None, list[LeaderboardEntry]],
        activity_by_wallet: dict[str, list[list[ActivityRow]]],
        resolutions: dict[str, dict[str, Any]],
    ) -> None:
        self._lb = leaderboard_by_category
        self._act = activity_by_wallet
        self._res = resolutions

    async def __aenter__(self) -> "_FakeFullClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def fetch_leaderboard(
        self, *, category: str | None, limit: int, offset: int,
    ):
        rows = list(self._lb.get(category, []))
        return rows[offset:offset + limit]

    async def fetch_activity(
        self, wallet: str, *, limit: int, offset: int,
    ) -> list[ActivityRow]:
        pages = self._act.get(wallet, [])
        idx = offset // limit if limit else 0
        if 0 <= idx < len(pages):
            return list(pages[idx])
        return []

    async def fetch_market_resolutions(
        self, condition_ids: list[str], chunk_size: int = 50,
    ) -> dict[str, dict[str, Any]]:
        return {cid: self._res.get(cid, {"status": "not_found"}) for cid in condition_ids}


# ── _fetch_wallet_activity_windowed termination ───────────────────────────


async def test_termination_target_buys_reached():
    """Dense BUY activity → loop stops when buy_count >= target_buy_rows."""
    page1 = [_make_activity(10_000_000 - i, f"cid_{i}", side="BUY") for i in range(150)]
    page2 = [_make_activity(9_000_000 - i, f"cid2_{i}", side="BUY") for i in range(50)]
    client = _FakeActivityClient([page1, page2])
    rows, pages_fetched, reason = await _fetch_wallet_activity_windowed(
        client, "0xabc", activity_limit=200, max_pages=10, target_buy_rows=150,
    )
    assert reason == "target_buys_reached"
    assert pages_fetched == 1
    assert client.call_count == 1
    assert len(rows) == 150


async def test_termination_exhausted_via_partial_page():
    """Partial page returned → loop exits with reason=exhausted (early exit)."""
    page1 = [_make_activity(10_000_000 - i, f"cid_{i}", side="BUY") for i in range(20)]
    client = _FakeActivityClient([page1])  # subsequent fetches return []
    rows, pages_fetched, reason = await _fetch_wallet_activity_windowed(
        client, "0xabc", activity_limit=500, max_pages=10, target_buy_rows=150,
    )
    assert reason == "exhausted"
    assert pages_fetched == 1
    assert len(rows) == 20


async def test_termination_exhausted_via_empty_page():
    """Full page followed by empty → reason=exhausted."""
    page1 = [_make_activity(10_000_000 - i, f"cid_{i}", side="SELL") for i in range(500)]
    # 0 BUYs so target never reached; next page empty.
    client = _FakeActivityClient([page1])
    rows, pages_fetched, reason = await _fetch_wallet_activity_windowed(
        client, "0xabc", activity_limit=500, max_pages=10, target_buy_rows=150,
    )
    assert reason == "exhausted"
    assert pages_fetched == 2  # 1 full page + 1 empty
    assert len(rows) == 500


async def test_termination_max_pages_hit_sparse_density():
    """Sparse BUYs across 10 full pages → max_pages_hit, true N below target."""
    pages = []
    for p in range(10):
        page = []
        # 6 BUYs + 494 SELLs per page so BUY count = 60 < 150 but len == 500
        for i in range(6):
            page.append(_make_activity(
                10_000_000 - p * 10_000 - i * 100, f"cid_{p}_{i}", side="BUY",
            ))
        for i in range(494):
            page.append(_make_activity(
                10_000_000 - p * 10_000 - 1000 - i, f"sell_{p}_{i}", side="SELL",
            ))
        pages.append(page)
    client = _FakeActivityClient(pages)
    rows, pages_fetched, reason = await _fetch_wallet_activity_windowed(
        client, "0xabc", activity_limit=500, max_pages=10, target_buy_rows=150,
    )
    assert reason == "max_pages_hit"
    assert pages_fetched == 10
    buy_count = sum(1 for a in rows if a.type == "TRADE" and a.side == "BUY")
    assert buy_count == 60  # true N, never silently 100


# ── _select_resolved_buys_window ──────────────────────────────────────────


def test_window_picks_most_recent_resolved_buys_first():
    """Activity is most-recent-first; window takes the first 100 resolved BUYs."""
    # Build 250 BUYs in descending-timestamp order (matches /activity contract).
    activity = []
    for i in range(250):
        ts = 10_000_000 - i  # i=0 newest
        activity.append(_make_activity(ts, f"cid_{i}", side="BUY"))
    resolutions = {f"cid_{i}": _resolved() for i in range(250)}
    window = _select_resolved_buys_window(activity, resolutions, window_size=100)
    assert len(window) == 100
    assert window[0].condition_id == "cid_0"  # most recent
    assert window[-1].condition_id == "cid_99"  # 100th most recent


def test_window_skips_non_buy_non_trade_and_unresolved():
    """Non-BUY, non-TRADE, unresolved, pending → all skipped."""
    activity = [
        _make_activity(1000, "cid_buy_resolved", side="BUY"),
        _make_activity(990, "cid_sell_ignored", side="SELL"),
        _make_activity(980, "cid_buy_unresolved", side="BUY"),
        _make_activity(970, "cid_buy_pending", side="BUY"),
        _make_activity(960, "cid_redeem", side="BUY", type_="REDEEM"),
        _make_activity(950, "cid_buy_resolved_2", side="BUY"),
    ]
    resolutions = {
        "cid_buy_resolved": _resolved(),
        "cid_sell_ignored": _resolved(),  # ignored: SELL side, not BUY
        # cid_buy_unresolved: missing entry
        "cid_buy_pending": {"status": "pending"},
        "cid_redeem": _resolved(),  # ignored: type != TRADE
        "cid_buy_resolved_2": _resolved(),
    }
    window = _select_resolved_buys_window(activity, resolutions, window_size=100)
    assert [a.condition_id for a in window] == [
        "cid_buy_resolved", "cid_buy_resolved_2",
    ]


def test_window_terminates_at_window_size_even_if_more_resolved_remain():
    activity = [_make_activity(1000 - i, f"cid_{i}", side="BUY") for i in range(30)]
    resolutions = {f"cid_{i}": _resolved() for i in range(30)}
    window = _select_resolved_buys_window(activity, resolutions, window_size=5)
    assert len(window) == 5
    assert [a.condition_id for a in window] == [f"cid_{i}" for i in range(5)]


# ── Integration: seed_polymarket_watchlist_deep floor behavior ────────────


def _build_seed_scenario(
    *,
    wallet: str,
    buy_outcomes: list[tuple[int, bool]],  # list of (timestamp, is_win)
    most_recent_any_ts: int | None = None,
) -> tuple[
    dict[str | None, list[LeaderboardEntry]],
    dict[str, list[list[ActivityRow]]],
    dict[str, dict[str, Any]],
]:
    """Build leaderboard+activity+resolutions for a single-wallet scenario.

    `buy_outcomes` is [(timestamp, is_win), ...] — one resolved BUY per tuple.
    `most_recent_any_ts`, if provided, adds a SELL at that timestamp (newer than
    all BUYs) so `last_trade_iso` is governed by the SELL — used to test the
    any-side recency anchoring.
    """
    activity_rows: list[ActivityRow] = []
    # The /activity contract is most-recent first; build the list that way.
    if most_recent_any_ts is not None:
        activity_rows.append(
            _make_activity(most_recent_any_ts, "cid_sell", side="SELL")
        )
    # Sort BUYs newest-first.
    for idx, (ts, _win) in enumerate(sorted(buy_outcomes, key=lambda t: -t[0])):
        activity_rows.append(_make_activity(ts, f"cid_buy_{idx}", side="BUY"))
    resolutions = {
        f"cid_buy_{idx}": _resolved(winning_outcome_index=0 if win else 1)
        for idx, (_, win) in enumerate(sorted(buy_outcomes, key=lambda t: -t[0]))
    }
    lb = {
        "GLOBAL": [_make_lb(wallet, rank=1)],
        None: [_make_lb(wallet, rank=1)],
    }
    return lb, {wallet: [activity_rows]}, resolutions


async def _run_seed(
    *, leaderboard, activity_by_wallet, resolutions,
    fake_now_ts: float, **kwargs,
) -> dict[str, Any]:
    fake = _FakeFullClient(
        leaderboard_by_category=leaderboard,
        activity_by_wallet=activity_by_wallet,
        resolutions=resolutions,
    )
    # `started` inside the function uses datetime.now(UTC) — patch it so
    # recency_days is deterministic regardless of when tests run.
    fake_now_dt = datetime.fromtimestamp(fake_now_ts, tz=timezone.utc)

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return fake_now_dt

        @staticmethod
        def fromtimestamp(ts, tz=None):
            return datetime.fromtimestamp(ts, tz=tz)

    with patch.object(seed_mod, "PolymarketDataAPIClient", return_value=fake), \
         patch.object(seed_mod, "datetime", _FakeDT):
        # categories=() so we only hit the GLOBAL leaderboard bucket.
        return await seed_polymarket_watchlist_deep(
            db_url="sqlite:///:memory:",
            dry_run=True,
            categories=(),
            **kwargs,
        )


async def test_n_floor_drops_whales_below_min_resolved_buys():
    """Whale with 5 resolved BUYs (< floor=10) → dropped, n_floor counter."""
    now_ts = 1_700_000_000  # 2023-11
    # 5 BUYs in the last 30 days; all wins.
    buys = [(now_ts - i * 86400, True) for i in range(5)]
    lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)
    summary = await _run_seed(
        leaderboard=lb, activity_by_wallet=act, resolutions=res,
        fake_now_ts=now_ts,
    )
    assert summary["drop_reasons"]["n_floor"] == 1
    assert summary["quality_gate_pass"] == 0
    assert summary["watch_only_whales"] == []


async def test_recency_floor_drops_dormant_whales():
    """Whale's last activity was 75 days ago (> recency_days=60) → drop."""
    now_ts = 1_700_000_000
    buys = [(now_ts - 80 * 86400 - i, True) for i in range(20)]
    lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)
    summary = await _run_seed(
        leaderboard=lb, activity_by_wallet=act, resolutions=res,
        fake_now_ts=now_ts,
    )
    assert summary["drop_reasons"]["recency_floor"] == 1
    assert summary["quality_gate_pass"] == 0


async def test_recency_floor_keys_off_any_side_activity_not_just_buys():
    """Last BUY 90 days ago, but a SELL 10 days ago → NOT dropped."""
    now_ts = 1_700_000_000
    buys = [(now_ts - 90 * 86400 - i, True) for i in range(20)]  # 20/20 wins
    lb, act, res = _build_seed_scenario(
        wallet="0xa", buy_outcomes=buys,
        most_recent_any_ts=now_ts - 10 * 86400,  # SELL 10 days ago
    )
    summary = await _run_seed(
        leaderboard=lb, activity_by_wallet=act, resolutions=res,
        fake_now_ts=now_ts,
    )
    assert summary["drop_reasons"]["recency_floor"] == 0
    assert summary["quality_gate_pass"] == 1
    assert len(summary["watch_only_whales"]) == 1
    # last_trade_iso should reflect the SELL timestamp, not the BUY one.
    last_iso = summary["watch_only_whales"][0]["last_trade_iso"]
    sell_iso = datetime.fromtimestamp(now_ts - 10 * 86400, tz=timezone.utc).isoformat()
    assert last_iso == sell_iso


async def test_wr_floor_drops_below_62_percent():
    """20 BUYs, 12 wins → WR = 0.60, below 0.62 floor → drop."""
    now_ts = 1_700_000_000
    buys = [
        (now_ts - i * 86400, i < 12)  # i=0..11 wins, i=12..19 losses
        for i in range(20)
    ]
    lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)
    summary = await _run_seed(
        leaderboard=lb, activity_by_wallet=act, resolutions=res,
        fake_now_ts=now_ts,
    )
    assert summary["drop_reasons"]["wr_floor"] == 1
    assert summary["quality_gate_pass"] == 0


async def test_pnl_floor_drops_negative_or_zero_pnl():
    """All BUYs at price=0.5 with 50/50 W/L → net PnL = 0 → drop on PnL floor.

    Each win contributes (1-0.5)*10 = +$5; each loss contributes -0.5*10 = -$5.
    With 10W/10L at WR=50%, net = $0. WR floor of 0.62 would drop this too
    but we deliberately push above 62% by giving 13W/7L (65% WR) — that
    clears the WR gate, but net PnL = (13*5) + (7*-5) = $30, which is > 0.

    To isolate the PnL floor: 13 wins at price=0.9 (each contributes +0.1*10
    = +$1) → +$13. 7 losses at price=0.9 (each contributes -0.9*10 = -$9)
    → -$63. Net = -$50 < $0 → PnL floor drops it. WR = 65% (above floor).
    """
    now_ts = 1_700_000_000
    buys: list[tuple[int, bool]] = []
    for i in range(20):
        ts = now_ts - i * 86400
        is_win = i < 13  # 13 wins, 7 losses
        buys.append((ts, is_win))
    activity_rows: list[ActivityRow] = []
    # most-recent first
    for idx, (ts, win) in enumerate(sorted(buys, key=lambda t: -t[0])):
        # price 0.9 → win pays +$1, loss costs -$9 per 10-contract trade
        activity_rows.append(_make_activity(
            ts, f"cid_buy_{idx}", side="BUY", price=0.9, size=10.0,
            outcome_index=0,
        ))
    resolutions = {
        f"cid_buy_{idx}": _resolved(winning_outcome_index=0 if win else 1)
        for idx, (_, win) in enumerate(sorted(buys, key=lambda t: -t[0]))
    }
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
    )
    assert summary["drop_reasons"]["pnl_floor"] == 1
    assert summary["drop_reasons"]["wr_floor"] == 0
    assert summary["quality_gate_pass"] == 0


async def test_both_floors_barely_cleared_keeps_whale():
    """WR=62% with PnL=$0.50 → both floors barely cleared, whale kept."""
    now_ts = 1_700_000_000
    # 50 BUYs, 31 wins → WR = 0.62 (exactly at floor)
    # Use price=0.5, size=2 → win=+$1, loss=-$1. 31W - 19L = +$12 > $0.
    buys = [(now_ts - i * 3600, i < 31) for i in range(50)]
    activity_rows: list[ActivityRow] = []
    for idx, (ts, win) in enumerate(sorted(buys, key=lambda t: -t[0])):
        activity_rows.append(_make_activity(
            ts, f"cid_buy_{idx}", side="BUY", price=0.5, size=2.0,
            outcome_index=0,
        ))
    resolutions = {
        f"cid_buy_{idx}": _resolved(winning_outcome_index=0 if win else 1)
        for idx, (_, win) in enumerate(sorted(buys, key=lambda t: -t[0]))
    }
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
    )
    assert summary["quality_gate_pass"] == 1
    whale = summary["watch_only_whales"][0]
    assert whale["wins"] == 31
    assert whale["losses"] == 19
    assert abs(whale["win_rate"] - 0.62) < 1e-6
    assert whale["realized_pnl_usdc"] > 0


async def test_provisional_flag_fires_below_threshold():
    """n=42 → provisional=true (below default 50); n=80 → provisional=false."""
    now_ts = 1_700_000_000

    async def _scenario(n: int) -> dict:
        # All wins so all floors cleared regardless of n.
        buys = [(now_ts - i * 3600, True) for i in range(n)]
        lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)
        return await _run_seed(
            leaderboard=lb, activity_by_wallet=act, resolutions=res,
            fake_now_ts=now_ts,
        )

    s42 = await _scenario(42)
    assert len(s42["watch_only_whales"]) == 1
    assert s42["watch_only_whales"][0]["provisional"] is True
    assert s42["watch_only_whales"][0]["window_size_n"] == 42
    assert s42["provisional_count"] == 1

    s80 = await _scenario(80)
    assert len(s80["watch_only_whales"]) == 1
    assert s80["watch_only_whales"][0]["provisional"] is False
    assert s80["watch_only_whales"][0]["window_size_n"] == 80
    assert s80["provisional_count"] == 0


async def test_true_n_recorded_when_window_under_100():
    """n=42 must surface as window_size_n=42, NOT silently 100."""
    now_ts = 1_700_000_000
    buys = [(now_ts - i * 3600, True) for i in range(42)]
    lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)
    summary = await _run_seed(
        leaderboard=lb, activity_by_wallet=act, resolutions=res,
        fake_now_ts=now_ts,
    )
    whale = summary["watch_only_whales"][0]
    assert whale["window_size_n"] == 42
    assert whale["wins"] == 42
    assert whale["losses"] == 0


async def test_compute_polymarket_stats_called_with_half_life_36500():
    """Confirm the seed passes half_life_days=36500.0 (effectively infinite)."""
    now_ts = 1_700_000_000
    buys = [(now_ts - i * 3600, True) for i in range(15)]
    lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)

    captured_kwargs: list[dict[str, Any]] = []
    real_fn = seed_mod.compute_polymarket_stats

    def _spy(**kwargs):
        captured_kwargs.append(dict(kwargs))
        return real_fn(**kwargs)

    with patch.object(seed_mod, "compute_polymarket_stats", side_effect=_spy):
        await _run_seed(
            leaderboard=lb, activity_by_wallet=act, resolutions=res,
            fake_now_ts=now_ts,
        )

    # At least one call (for the surviving whale); all calls must pass 36500.0.
    assert captured_kwargs, "compute_polymarket_stats was never called"
    for call in captured_kwargs:
        assert call.get("half_life_days") == 36500.0, (
            f"expected half_life_days=36500.0, got {call.get('half_life_days')}"
        )
