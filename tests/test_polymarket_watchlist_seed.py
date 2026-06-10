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
    _aggregate_window_to_decisions,
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


def _make_redeem(
    ts: int, condition_id: str, *, size: float, wallet: str = "0xabc",
) -> ActivityRow:
    """A market-level REDEEM row (Polymarket sentinel outcome_index=999)."""
    return _make_activity(
        ts, condition_id, side="REDEEM", outcome_index=999,
        price=1.0, size=size, type_="REDEEM", wallet=wallet,
    )


def _with_redeems(
    activity: list[ActivityRow], resolutions: dict[str, dict[str, Any]],
) -> list[ActivityRow]:
    """Append a REDEEM row (size = held qty) for each WINNING decision in
    `activity`, modelling 'held to resolution and redeemed'.

    Under that model REDEEM-grounded realized PnL (option (c) Phase 2) equals
    the naive held-to-resolution number: `redeem - cost = size*(1-price)` for a
    win; losing decisions get no redeem, so `realized = -cost`. That is exactly
    the held-to-resolution basis the pre-option-(c) fixtures assert against, so
    wrapping a fixture lets it exercise the Phase 2 realized compute without
    restating any expected value.
    """
    held_by_cid: dict[str, float] = {}
    ts_by_cid: dict[str, int] = {}
    for a in activity:
        if a.type != "TRADE" or a.side != "BUY" or not a.condition_id:
            continue
        try:
            oi = int(a.outcome_index)
        except (TypeError, ValueError):
            continue
        res = resolutions.get(a.condition_id)
        if not res or (res.get("status") or "").lower() != "resolved":
            continue
        if oi != res.get("winning_outcome_index"):
            continue  # losing side never redeems
        held_by_cid[a.condition_id] = held_by_cid.get(a.condition_id, 0.0) + a.size
        ts_by_cid[a.condition_id] = max(ts_by_cid.get(a.condition_id, 0), a.timestamp)
    redeems = [
        _make_redeem(ts_by_cid[cid], cid, size=size)
        for cid, size in held_by_cid.items()
    ]
    # Prepend: a non-BUY row's position is irrelevant to window selection
    # (which keys off BUY order), and redeem ts == decision ts keeps recency
    # (last_trade) governed by the real trades.
    return redeems + list(activity)


def _patch_now(fake_now_ts: float):
    """A datetime stand-in pinned to `fake_now_ts` for the seed's now()/
    fromtimestamp() calls (deterministic recency)."""
    fake_now_dt = datetime.fromtimestamp(fake_now_ts, tz=timezone.utc)

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return fake_now_dt

        @staticmethod
        def fromtimestamp(ts, tz=None):
            return datetime.fromtimestamp(ts, tz=tz)

    return _FakeDT


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


# ── Decision-unit windowing (Option A, shipped 2026-05-26) ───────────────


def test_window_collapses_cluster_of_same_cid_same_outcome_to_one_slot():
    """29 BUYs on the same (cid, outcome_index) collapse to ONE window slot.

    Canonical Runaround case: a whale fills 29 BUYs on "Spread: Knicks
    (-11.5)" at price ~0.55 over 70 seconds. Under the pre-2026-05-26
    fill-counting bug this saturated 29 of 100 window slots; under
    Option A (dedupe by (cid, outcome_index)) it should collapse to 1.
    Most-recent fill survives (preserves the activity-feed ordering
    contract for window_days_span).
    """
    activity = [
        _make_activity(1000 - i, "cid_knicks_spread", side="BUY",
                       outcome_index=0, price=0.55)
        for i in range(29)
    ]
    resolutions = {"cid_knicks_spread": _resolved(winning_outcome_index=0)}
    window = _select_resolved_buys_window(activity, resolutions, window_size=100)
    assert len(window) == 1
    # Most-recent fill (timestamp 1000) is the one that survives.
    assert window[0].timestamp == 1000


def test_window_keeps_distinct_cids_distinct():
    """Different markets stay as separate slots — only same (cid, oi) collapses."""
    activity = [
        _make_activity(1000, "cid_a", side="BUY", outcome_index=0),
        _make_activity(999, "cid_b", side="BUY", outcome_index=0),
        _make_activity(998, "cid_c", side="BUY", outcome_index=0),
    ]
    resolutions = {
        "cid_a": _resolved(), "cid_b": _resolved(), "cid_c": _resolved(),
    }
    window = _select_resolved_buys_window(activity, resolutions, window_size=100)
    assert len(window) == 3
    assert [a.condition_id for a in window] == ["cid_a", "cid_b", "cid_c"]


def test_window_keeps_opposite_outcome_index_on_same_cid_as_distinct_decisions():
    """A whale that bought BOTH sides of one market = TWO decisions, not one.

    Hedges are rare but real (buyer flipped or bought both sides of a
    binary). The decision unit is (condition_id, outcome_index), so each
    side counts independently. Each pair contributes one slot.
    """
    activity = [
        # 5 BUYs on cid_a outcome 0 — collapse to 1
        _make_activity(1000 - i, "cid_a", side="BUY", outcome_index=0)
        for i in range(5)
    ] + [
        # 3 BUYs on cid_a outcome 1 — collapse to 1 (distinct decision)
        _make_activity(900 - i, "cid_a", side="BUY", outcome_index=1)
        for i in range(3)
    ]
    resolutions = {"cid_a": _resolved(winning_outcome_index=0)}
    window = _select_resolved_buys_window(activity, resolutions, window_size=100)
    assert len(window) == 2
    # Both slots are on cid_a, but with different outcome_index.
    assert {(a.condition_id, a.outcome_index) for a in window} == {
        ("cid_a", 0), ("cid_a", 1),
    }


def test_window_picks_most_recent_buy_per_decision_not_first():
    """When a (cid, oi) pair has multiple fills, the most-recent ts survives.

    Activity is most-recent-first, so the first-encountered row IS the
    most-recent fill. Verifies the slot carries the most-recent price /
    size — relevant for downstream avg_entry_price and PnL math.
    """
    activity = [
        _make_activity(2000, "cid_x", side="BUY", outcome_index=0, price=0.55),  # newest
        _make_activity(1500, "cid_x", side="BUY", outcome_index=0, price=0.50),
        _make_activity(1000, "cid_x", side="BUY", outcome_index=0, price=0.45),  # oldest
    ]
    resolutions = {"cid_x": _resolved()}
    window = _select_resolved_buys_window(activity, resolutions, window_size=100)
    assert len(window) == 1
    assert window[0].timestamp == 2000
    assert window[0].price == 0.55


# ── Decision aggregation (PnL fix, shipped 2026-05-26 post-clustering) ───


def test_aggregate_29_winning_fills_sums_all_pnl_not_just_survivor():
    """29 BUYs on (cid_a, oi=0) at price 0.5, size 10 each, all win.

    Per-fill PnL sum = 29 * (1-0.5) * 10 = $145.
    Survivor-only PnL (the pre-aggregation bug) = (1-0.5) * 10 = $5.
    Aggregated row must produce $145 when fed to per-row PnL math.
    """
    activity = [
        _make_activity(1000 - i, "cid_a", side="BUY",
                       outcome_index=0, price=0.5, size=10.0)
        for i in range(29)
    ]
    # Window has 1 survivor (the most-recent fill, ts=1000) — that's what
    # _select_resolved_buys_window would have produced.
    survivor = activity[0]
    window = [survivor]
    agg = _aggregate_window_to_decisions(activity, window)
    assert len(agg) == 1
    assert agg[0].condition_id == "cid_a"
    assert agg[0].outcome_index == 0
    assert agg[0].size == 290.0  # 29 * 10
    assert abs(agg[0].price - 0.5) < 1e-9  # weighted avg of identical prices
    # Per-row PnL math (the formula compute_polymarket_stats uses): for a win,
    # per_contract_pnl = (1 - price) and trade_pnl = per_contract_pnl * size.
    # 290 * 0.5 = $145, matches sum-across-fills.
    assert agg[0].size * (1.0 - agg[0].price) == 145.0


def test_aggregate_29_losing_fills_sums_negative_pnl_not_just_survivor():
    """29 BUYs on (cid_a, oi=0), all losses (winner_idx=1 elsewhere).

    Per-fill loss PnL sum = 29 * (-0.5) * 10 = -$145.
    Survivor-only PnL = -$5. Aggregated PnL must equal -$145.
    """
    activity = [
        _make_activity(1000 - i, "cid_a", side="BUY",
                       outcome_index=0, price=0.5, size=10.0)
        for i in range(29)
    ]
    survivor = activity[0]
    agg = _aggregate_window_to_decisions(activity, [survivor])
    assert len(agg) == 1
    # For a loss, per_contract_pnl = -price; trade_pnl = -price * size.
    # -290 * 0.5 = -$145.
    assert -agg[0].price * agg[0].size == -145.0


def test_aggregate_mixed_price_fills_uses_size_weighted_avg():
    """Two fills on (cid_a, 0): 100 @ $0.50 and 200 @ $0.60.

    Weighted-avg price = (0.50*100 + 0.60*200) / 300 = 170/300 ≈ 0.567.
    Total size = 300. For a win, pnl = (1 - 0.567) * 300 = $130 ≡
    (1-0.5)*100 + (1-0.6)*200 = $50 + $80 = $130. Math identity confirmed.
    """
    activity = [
        _make_activity(2000, "cid_a", side="BUY", outcome_index=0,
                       price=0.50, size=100.0),
        _make_activity(1000, "cid_a", side="BUY", outcome_index=0,
                       price=0.60, size=200.0),
    ]
    agg = _aggregate_window_to_decisions(activity, [activity[0]])
    assert len(agg) == 1
    assert agg[0].size == 300.0
    assert abs(agg[0].price - (170.0 / 300.0)) < 1e-12
    win_pnl = (1.0 - agg[0].price) * agg[0].size
    assert abs(win_pnl - 130.0) < 1e-9


def test_aggregate_hedge_keeps_each_side_separate():
    """A whale that bought both (cid_a, 0) AND (cid_a, 1) = TWO decisions.

    Each side's fills aggregate INDEPENDENTLY — total_size on one side does
    NOT pull in the other side's contracts (those are a separate bet that
    resolves differently). Each synthetic row carries only its own oi's fills.
    """
    activity = [
        # 5 BUYs on cid_a oi=0 (size 10 each)
        _make_activity(2000 - i, "cid_a", side="BUY",
                       outcome_index=0, price=0.45, size=10.0)
        for i in range(5)
    ] + [
        # 3 BUYs on cid_a oi=1 (size 20 each)
        _make_activity(1500 - i, "cid_a", side="BUY",
                       outcome_index=1, price=0.55, size=20.0)
        for i in range(3)
    ]
    # Window picked both survivors (one per side).
    window = [activity[0], activity[5]]
    agg = _aggregate_window_to_decisions(activity, window)
    assert len(agg) == 2
    by_oi = {a.outcome_index: a for a in agg}
    assert by_oi[0].size == 50.0  # 5 fills * 10
    assert abs(by_oi[0].price - 0.45) < 1e-9
    assert by_oi[1].size == 60.0  # 3 fills * 20
    assert abs(by_oi[1].price - 0.55) < 1e-9


def test_aggregate_single_fill_decision_passes_through_unchanged():
    """A 1-fill decision must aggregate to itself (no math changes).

    Edge case to protect: legacy fills that don't cluster should not be
    silently mutated by the aggregator.
    """
    activity = [
        _make_activity(1000, "cid_solo", side="BUY", outcome_index=0,
                       price=0.42, size=33.0),
    ]
    agg = _aggregate_window_to_decisions(activity, [activity[0]])
    assert len(agg) == 1
    assert agg[0].condition_id == "cid_solo"
    assert agg[0].outcome_index == 0
    assert agg[0].size == 33.0
    assert agg[0].price == 0.42


def test_aggregate_preserves_survivor_ts_for_window_days_span():
    """The aggregated row's `timestamp` is the survivor's (most-recent fill).

    `window_days_span` in the seed is `max(ts) - min(ts)` across the window;
    aggregation must not collapse times to e.g. min-of-cluster or risk
    distorting the span column.
    """
    activity = [
        _make_activity(3000, "cid_a", side="BUY", outcome_index=0,
                       price=0.5, size=10.0),  # most recent
        _make_activity(1000, "cid_a", side="BUY", outcome_index=0,
                       price=0.5, size=10.0),  # older fill
    ]
    survivor = activity[0]
    agg = _aggregate_window_to_decisions(activity, [survivor])
    assert agg[0].timestamp == 3000


def test_aggregate_with_empty_activity_falls_back_to_survivor():
    """Defensive: if activity is empty (degenerate caller), use survivor as-is."""
    survivor = _make_activity(1000, "cid_a", side="BUY",
                              outcome_index=0, price=0.5, size=10.0)
    agg = _aggregate_window_to_decisions([], [survivor])
    assert len(agg) == 1
    assert agg[0] is not survivor  # dataclasses.replace produces a new instance
    assert agg[0].size == 10.0
    assert agg[0].price == 0.5


def test_aggregate_ignores_non_trade_non_buy_fills_in_activity():
    """SELLs and non-TRADE events on the same (cid, oi) must NOT be summed
    into the decision's PnL — those are exits / redeems, not entry fills."""
    activity = [
        _make_activity(3000, "cid_a", side="BUY", outcome_index=0,
                       price=0.5, size=10.0),
        _make_activity(2500, "cid_a", side="SELL", outcome_index=0,
                       price=0.7, size=5.0),  # SELL — ignore
        _make_activity(2000, "cid_a", side="BUY", outcome_index=0,
                       price=0.5, size=10.0, type_="REDEEM"),  # REDEEM — ignore
        _make_activity(1000, "cid_a", side="BUY", outcome_index=0,
                       price=0.5, size=10.0),
    ]
    agg = _aggregate_window_to_decisions(activity, [activity[0]])
    assert len(agg) == 1
    # Only the 2 BUY+TRADE fills sum: 10 + 10 = 20.
    assert agg[0].size == 20.0


# ── Integration: seed_polymarket_watchlist_deep floor behavior ────────────


def _make_clustered_activity(
    wallet: str, n_decisions: int, fills_per_decision: int = 5,
    *, all_wins: bool = True, ts_anchor: int = 1_700_000_000,
    size: float = 2.0,
) -> tuple[list[ActivityRow], dict[str, dict[str, Any]]]:
    """Build a clustered-fills activity feed for floor / provisional tests.

    `n_decisions` distinct (cid, outcome_index=0) decisions, each with
    `fills_per_decision` repeat-BUYs at the same price within a few
    seconds — the canonical clustering shape from the Runaround case.
    Returns (activity_rows_most_recent_first, resolutions).

    Under the Option A windowing semantics, the effective n = n_decisions,
    NOT n_decisions * fills_per_decision.
    """
    activity: list[ActivityRow] = []
    resolutions: dict[str, dict[str, Any]] = {}
    for d in range(n_decisions):
        cid = f"cid_d{d}"
        win = all_wins
        resolutions[cid] = _resolved(winning_outcome_index=0 if win else 1)
        # decision d sits at ts_anchor - d*3600 (one hour apart); within
        # each decision, fills_per_decision rows within 10 seconds.
        decision_ts = ts_anchor - d * 3600
        for f in range(fills_per_decision):
            activity.append(_make_activity(
                decision_ts - f, cid, side="BUY",
                outcome_index=0, price=0.5, size=size,
                wallet=wallet,
            ))
    return activity, resolutions


async def test_n_floor_drops_clustered_whale_with_lt10_distinct_decisions():
    """8 distinct decisions × 20 fills = 160 fills, but n=8 < 10 floor → drop.

    Pre-2026-05-26 this whale would have passed (160 fills counts as n=100
    after the window cap; n_floor=10 → pass). Under Option A, n=8 distinct
    decisions falls below the floor → dropped, correctly.
    """
    now_ts = 1_700_000_000
    activity_rows, resolutions = _make_clustered_activity(
        wallet="0xa", n_decisions=8, fills_per_decision=20,
        all_wins=True, ts_anchor=now_ts,
    )
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
    )
    assert summary["drop_reasons"]["n_floor"] == 1
    assert summary["quality_gate_pass"] == 0
    assert summary["watch_only_whales"] == []


async def test_clustered_whale_pnl_aggregates_across_all_fills_not_just_survivor():
    """50 distinct decisions, each with 5 winning fills at price 0.5 size 200.

    Under aggregation: each decision = (1-0.5)*1000 = $500 PnL → 50 decisions
    → $25,000 total. Clears the $5k floor.

    Without aggregation (pre-fix): each decision counted as 1 fill's PnL
    = (1-0.5)*200 = $100 → 50 decisions → $5,000 exactly. The same whale
    would borderline-fail the $5k floor on a 1-dollar slip and be dropped
    artifactually. With aggregation, the genuine $25k economic exposure is
    surfaced and the whale rightly survives.
    """
    now_ts = 1_700_000_000
    activity_rows, resolutions = _make_clustered_activity(
        wallet="0xa", n_decisions=50, fills_per_decision=5,
        all_wins=True, ts_anchor=now_ts, size=200.0,
    )
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
        min_windowed_pnl=5000.0,  # production-default floor — must not drop us
    )
    assert summary["drop_reasons"]["pnl_floor"] == 0
    assert summary["quality_gate_pass"] == 1
    whale = summary["watch_only_whales"][0]
    assert whale["window_size_n"] == 50  # distinct decisions, not 250 fills
    # 50 decisions × 5 fills × 200 contracts × (1-0.5) = $25,000.
    assert whale["realized_pnl_usdc"] == 25000.0


async def test_clustered_whale_with_42_distinct_decisions_fires_provisional():
    """42 distinct decisions × 5 fills = 210 fills, but window n=42 → provisional.

    Confirms that the provisional flag (n<50 threshold) fires on the
    distinct-decision count, not the fill count.
    """
    now_ts = 1_700_000_000
    activity_rows, resolutions = _make_clustered_activity(
        wallet="0xa", n_decisions=42, fills_per_decision=5,
        all_wins=True, ts_anchor=now_ts,
    )
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
    )
    assert len(summary["watch_only_whales"]) == 1
    whale = summary["watch_only_whales"][0]
    assert whale["window_size_n"] == 42, "n must reflect distinct decisions, not fills"
    assert whale["wins"] == 42
    assert whale["losses"] == 0
    assert whale["provisional"] is True
    assert summary["provisional_count"] == 1





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
    # option (c) Phase 2: the seed now computes REDEEM-grounded realized PnL,
    # so held-to-resolution fixtures need a REDEEM leg per winning decision for
    # realized to equal the naive numbers these assertions were written for.
    # Injected centrally so every integration scenario exercises the realized
    # compute unchanged. (Drop tests stay dropped — their naive PnL was already
    # sub-floor, and -cost on a no-redeem loss is only more negative.)
    activity_by_wallet = {
        w: [_with_redeems(page, resolutions) for page in pages]
        for w, pages in activity_by_wallet.items()
    }
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

    # The production default for min_windowed_pnl is $5,000 (calibrated
    # against the live Polymarket survivor pool). Test synthetic scenarios
    # use small synthetic PnL and would all be dropped on the production
    # floor; relax it to $0.01 here so tests exercise the floor *mechanic*,
    # not the production calibration. Individual tests override to test the
    # PnL floor explicitly.
    kwargs.setdefault("min_windowed_pnl", 0.01)
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


async def test_avg_entry_price_and_share_below_70_computed_correctly():
    """Per-whale avg_entry_price + share_below_70 reflect the windowed slice."""
    now_ts = 1_700_000_000
    # Construct a whale with mixed entry prices: 5 at $0.40, 5 at $0.80,
    # 5 at $0.95, all wins.
    activity: list[ActivityRow] = []
    cid = 0
    for px in [0.40, 0.80, 0.95]:
        for _ in range(5):
            cid += 1
            activity.append(_make_activity(
                now_ts - cid * 3600, f"cid_{cid}", side="BUY", price=px,
                outcome_index=0,
            ))
    # Activity is most-recent-first; ours already iterates newest cid_15
    # last, oldest cid_1 first — reverse so newest is at index 0.
    activity.reverse()
    resolutions = {
        f"cid_{i}": _resolved(winning_outcome_index=0) for i in range(1, 16)
    }
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
    )
    assert len(summary["watch_only_whales"]) == 1
    whale = summary["watch_only_whales"][0]
    expected_avg = (5 * 0.40 + 5 * 0.80 + 5 * 0.95) / 15
    # Payload rounds to 4 decimal places — match tolerance.
    assert abs(whale["avg_entry_price"] - expected_avg) < 1e-3
    # 5 of 15 BUYs below $0.70 → 1/3
    assert abs(whale["share_below_70"] - (5 / 15)) < 1e-3


async def test_pnl_floor_at_production_default_drops_low_pnl_whales():
    """At the production default $5,000 floor, a $500-PnL whale must drop."""
    now_ts = 1_700_000_000
    # 100 wins at price=0.5, size=1 → PnL = 100 * (1-0.5) * 1 = $50. Way below
    # the $5,000 floor.
    buys = [(now_ts - i * 3600, True) for i in range(100)]
    activity_rows: list[ActivityRow] = []
    for idx, (ts, win) in enumerate(sorted(buys, key=lambda t: -t[0])):
        activity_rows.append(_make_activity(
            ts, f"cid_{idx}", side="BUY", price=0.5, size=1.0, outcome_index=0,
        ))
    resolutions = {
        f"cid_{idx}": _resolved(winning_outcome_index=0 if win else 1)
        for idx, (_, win) in enumerate(sorted(buys, key=lambda t: -t[0]))
    }
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    # Override _run_seed's relaxed default with the production value.
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
        min_windowed_pnl=5000.0,
    )
    assert summary["drop_reasons"]["pnl_floor"] == 1
    assert summary["quality_gate_pass"] == 0


async def test_pnl_floor_keeps_whale_at_or_above_threshold():
    """A whale with PnL exactly equal to the floor must NOT be dropped."""
    now_ts = 1_700_000_000
    # 50 wins at price=0.5, size=200 → PnL = 50 * 0.5 * 200 = $5,000 exactly.
    buys = [(now_ts - i * 3600, True) for i in range(50)]
    activity_rows: list[ActivityRow] = []
    for idx, (ts, win) in enumerate(sorted(buys, key=lambda t: -t[0])):
        activity_rows.append(_make_activity(
            ts, f"cid_{idx}", side="BUY", price=0.5, size=200.0,
            outcome_index=0,
        ))
    resolutions = {
        f"cid_{idx}": _resolved(winning_outcome_index=0 if win else 1)
        for idx, (_, win) in enumerate(sorted(buys, key=lambda t: -t[0]))
    }
    lb = {"GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)]}
    summary = await _run_seed(
        leaderboard=lb,
        activity_by_wallet={"0xa": [activity_rows]},
        resolutions=resolutions,
        fake_now_ts=now_ts,
        min_windowed_pnl=5000.0,
    )
    assert summary["drop_reasons"]["pnl_floor"] == 0
    assert summary["quality_gate_pass"] == 1
    assert summary["watch_only_whales"][0]["realized_pnl_usdc"] == 5000.0


async def test_seed_routes_compute_through_build_audit_report():
    """option (c) Phase 2: the seed computes via the Phase 1 REDEEM-grounded
    `build_audit_report` (over the windowed RAW fills, REDEEM legs included),
    not the legacy naive `compute_polymarket_stats`."""
    now_ts = 1_700_000_000
    buys = [(now_ts - i * 3600, True) for i in range(15)]
    lb, act, res = _build_seed_scenario(wallet="0xa", buy_outcomes=buys)

    calls: list[dict[str, Any]] = []
    real_fn = seed_mod.build_audit_report

    def _spy(**kwargs):
        calls.append(dict(kwargs))
        return real_fn(**kwargs)

    with patch.object(seed_mod, "build_audit_report", side_effect=_spy):
        await _run_seed(
            leaderboard=lb, activity_by_wallet=act, resolutions=res,
            fake_now_ts=now_ts,
        )

    assert calls, "build_audit_report was never called"
    for c in calls:
        assert "activity_rows" in c and "resolutions" in c
        assert c.get("proxy_wallet") == "0xa"
        # The windowed slice must carry REDEEM legs — that is what makes the
        # compute realized rather than naive held-to-resolution.
        assert any(r.type == "REDEEM" for r in c["activity_rows"]), (
            "windowed slice fed to build_audit_report carried no REDEEM rows"
        )
    # The legacy naive entry point is gone from the seed module.
    assert not hasattr(seed_mod, "compute_polymarket_stats")


async def test_realized_basis_reflects_early_sell_not_held_to_resolution():
    """The swap is REDEEM/SELL-grounded, not naive held-to-resolution: a whale
    that wins on resolution but SOLD early books only its realized sell PnL.
    Also pins the watch_only record contract (21 original fields + the option
    (c) Phase 2 additive fields).
    """
    now_ts = 1_700_000_000
    rows: list[ActivityRow] = []
    resolutions: dict[str, dict[str, Any]] = {}
    # 12 winning decisions (resolved oi=0): BUY 100 @ $0.50 (cost $50), then
    # SELL 100 @ $0.60 (proceeds $60) -> realized +$10/decision = +$120 total.
    # Naive held-to-resolution would book (1-0.5)*100 = +$50/decision = +$600.
    # NO REDEEM rows: the whale exited via SELL, it did not hold to redeem.
    for d in range(12):
        cid = f"cid_{d}"
        resolutions[cid] = _resolved(winning_outcome_index=0)
        rows.append(_make_activity(
            now_ts - d * 7200 - 1, cid, side="SELL", price=0.60, size=100.0,
            outcome_index=0,
        ))
        rows.append(_make_activity(
            now_ts - d * 7200 - 2, cid, side="BUY", price=0.50, size=100.0,
            outcome_index=0,
        ))
    fake = _FakeFullClient(
        leaderboard_by_category={
            "GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)],
        },
        activity_by_wallet={"0xa": [rows]},
        resolutions=resolutions,
    )
    with patch.object(seed_mod, "PolymarketDataAPIClient", return_value=fake), \
         patch.object(seed_mod, "datetime", _patch_now(now_ts)):
        summary = await seed_polymarket_watchlist_deep(
            db_url="sqlite:///:memory:", dry_run=True, categories=(),
            min_resolved_buys=1, min_windowed_pnl=0.01,
        )

    assert len(summary["watch_only_whales"]) == 1
    whale = summary["watch_only_whales"][0]
    # Realized = sell proceeds - cost = +$120, NOT the naive +$600.
    assert whale["realized_pnl_usdc"] == 120.0
    # Wins are still resolution-based (decision won), unaffected by the exit.
    assert whale["wins"] == 12 and whale["losses"] == 0
    assert whale["n_resolved_decisions"] == 12
    assert whale["realized_roi"] == round(120.0 / 600.0, 4)
    assert whale["window_truncated"] is False
    # Output contract: the 21 original fields + the 4 Phase 2 additive fields.
    assert set(whale.keys()) == {
        "rank", "proxy_wallet", "user_name", "x_username", "verified_badge",
        "total_resolved_positions", "wins", "losses", "win_rate",
        "realized_pnl_usdc", "total_usdc_size_resolved",
        "lifetime_pnl_from_leaderboard", "lifetime_vol_from_leaderboard",
        "best_category", "included_iso", "window_size_n", "window_days_span",
        "last_trade_iso", "provisional", "avg_entry_price", "share_below_70",
        "window_truncated", "pnl_inflation_ratio", "realized_roi",
        "n_resolved_decisions",
    }


async def test_window_truncated_whale_stays_in_roster_flagged():
    """Flag-only (operator decision): a window_truncated whale is NOT excluded
    from the observation roster — it stays, flagged window_truncated=True, so
    the operator still sees it for manual review. (Contrast the execution-
    gating refresh, which HARD-excludes truncated whales.)
    """
    now_ts = 1_700_000_000
    rows: list[ActivityRow] = []
    resolutions: dict[str, dict[str, Any]] = {}
    # 3 winning decisions, each held to resolution (BUY 200 @ $0.50 + REDEEM).
    for d in range(3):
        cid = f"cid_{d}"
        resolutions[cid] = _resolved(winning_outcome_index=0)
        rows.append(_make_activity(
            now_ts - d * 3600, cid, side="BUY", price=0.5, size=200.0,
            outcome_index=0,
        ))
    rows = _with_redeems(rows, resolutions)  # 3 BUY + 3 REDEEM = 6 rows
    # A single FULL page (== activity_limit) + max_pages=1 forces the walk to
    # stop at the page ceiling -> term_reason 'max_pages_hit' -> truncated.
    fake = _FakeFullClient(
        leaderboard_by_category={
            "GLOBAL": [_make_lb("0xa", rank=1)], None: [_make_lb("0xa", rank=1)],
        },
        activity_by_wallet={"0xa": [rows]},
        resolutions=resolutions,
    )
    with patch.object(seed_mod, "PolymarketDataAPIClient", return_value=fake), \
         patch.object(seed_mod, "datetime", _patch_now(now_ts)):
        summary = await seed_polymarket_watchlist_deep(
            db_url="sqlite:///:memory:", dry_run=True, categories=(),
            min_resolved_buys=1, min_windowed_pnl=0.01,
            activity_limit=len(rows), max_pages_per_wallet=1,
        )

    assert summary["window_truncated_count"] == 1
    roster = summary["watch_only_whales"]
    assert len(roster) == 1, "flag-only: a truncated whale must NOT be excluded"
    assert roster[0]["window_truncated"] is True
    assert roster[0]["realized_pnl_usdc"] > 0
