"""Tests for the Polymarket Watch List server-side sort plumbing.

`_query_polymarket_watch_only_rows` accepts a `sort_key` (a whitelisted
user-facing key) plus `sort_desc`. The sort is applied AFTER the
selected_whales filter. Unknown keys fall back to the default
(`rank` ascending, which mirrors the seed's pre-sort by realized PnL).

Covers:
  - default sort (None) preserves rank order
  - sort by avg_entry_price asc/desc reorders the list
  - sort by share_below_70 desc reorders the list
  - sort by realized_pnl_usdc desc matches the rank order
  - unknown sort_key falls back to default
  - None values in sort target (e.g. last_trade_iso) sink to the bottom
    regardless of asc/desc direction
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trading_corp.persistence import db
from trading_corp.web import data as wdata


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "watch_sort.db"
    url = f"sqlite:///{p.as_posix()}"
    db.init_db(db_url=url)
    return url


def _seed_three_whales(db_url: str) -> None:
    """Three whales with distinct characteristics so sort orderings are
    unambiguous:
      0xAAAA: rank=1, PnL $50k, AvgPx 0.35, <.70 share 0.95  (sharp)
      0xBBBB: rank=2, PnL $30k, AvgPx 0.92, <.70 share 0.05  (favorite)
      0xCCCC: rank=3, PnL $10k, AvgPx 0.65, <.70 share 0.55  (mid)
    """
    db.set_agent_state(
        "polymarket_copy_trader", "watch_only_whales",
        [
            {
                "rank": 1, "proxy_wallet": "0xAAAA", "user_name": "sharp",
                "realized_pnl_usdc": 50_000.0, "wins": 60, "losses": 40,
                "win_rate": 0.60, "total_resolved_positions": 100,
                "lifetime_pnl_from_leaderboard": 100_000.0,
                "lifetime_vol_from_leaderboard": 500_000.0,
                "best_category": "Politics", "verified_badge": False,
                "x_username": "", "included_iso": "2026-05-23T00:00:00Z",
                "window_size_n": 100, "window_days_span": 10.0,
                "last_trade_iso": "2026-05-23T00:00:00+00:00",
                "provisional": False,
                "avg_entry_price": 0.35, "share_below_70": 0.95,
            },
            {
                "rank": 2, "proxy_wallet": "0xBBBB", "user_name": "favorite",
                "realized_pnl_usdc": 30_000.0, "wins": 95, "losses": 5,
                "win_rate": 0.95, "total_resolved_positions": 100,
                "lifetime_pnl_from_leaderboard": 200_000.0,
                "lifetime_vol_from_leaderboard": 1_000_000.0,
                "best_category": "Sports", "verified_badge": True,
                "x_username": "", "included_iso": "2026-05-23T00:00:00Z",
                "window_size_n": 100, "window_days_span": 5.0,
                "last_trade_iso": "2026-05-22T00:00:00+00:00",
                "provisional": False,
                "avg_entry_price": 0.92, "share_below_70": 0.05,
            },
            {
                "rank": 3, "proxy_wallet": "0xCCCC", "user_name": "mid",
                "realized_pnl_usdc": 10_000.0, "wins": 70, "losses": 30,
                "win_rate": 0.70, "total_resolved_positions": 100,
                "lifetime_pnl_from_leaderboard": 50_000.0,
                "lifetime_vol_from_leaderboard": 250_000.0,
                "best_category": "Crypto", "verified_badge": False,
                "x_username": "", "included_iso": "2026-05-23T00:00:00Z",
                "window_size_n": 100, "window_days_span": 30.0,
                "last_trade_iso": "2026-05-21T00:00:00+00:00",
                "provisional": False,
                "avg_entry_price": 0.65, "share_below_70": 0.55,
            },
        ],
        db_url=db_url,
    )


def test_default_sort_preserves_rank_order(db_url):
    _seed_three_whales(db_url)
    rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
    )
    assert [r.user_name for r in rows] == ["sharp", "favorite", "mid"]


def test_sort_by_avg_entry_price_desc(db_url):
    _seed_three_whales(db_url)
    rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="avg_entry_price", sort_desc=True,
    )
    assert [r.user_name for r in rows] == ["favorite", "mid", "sharp"]


def test_sort_by_avg_entry_price_asc(db_url):
    _seed_three_whales(db_url)
    rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="avg_entry_price", sort_desc=False,
    )
    assert [r.user_name for r in rows] == ["sharp", "mid", "favorite"]


def test_sort_by_share_below_70_desc(db_url):
    _seed_three_whales(db_url)
    rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="share_below_70", sort_desc=True,
    )
    assert [r.user_name for r in rows] == ["sharp", "mid", "favorite"]


def test_sort_by_realized_pnl_desc_matches_default(db_url):
    _seed_three_whales(db_url)
    default_rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
    )
    explicit_rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="realized_pnl_usdc", sort_desc=True,
    )
    assert (
        [r.user_name for r in default_rows]
        == [r.user_name for r in explicit_rows]
    )


def test_sort_key_alias_pnl_resolves_to_realized_pnl_usdc(db_url):
    _seed_three_whales(db_url)
    rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="pnl", sort_desc=True,
    )
    assert [r.user_name for r in rows] == ["sharp", "favorite", "mid"]


def test_unknown_sort_key_falls_back_to_default(db_url):
    _seed_three_whales(db_url)
    rows = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="not_a_real_column; DROP TABLE", sort_desc=True,
    )
    # Falls back to default rank ordering — sharp first.
    assert [r.user_name for r in rows] == ["sharp", "favorite", "mid"]


def test_sort_handles_none_values_gracefully(db_url):
    """Old-schema entries with last_trade_iso=None must not crash the sort."""
    # Seed one with last_trade_iso=None — would come from a pre-windowed
    # entry written by the old seed code.
    db.set_agent_state(
        "polymarket_copy_trader", "watch_only_whales",
        [
            {
                "rank": 1, "proxy_wallet": "0xAAAA", "user_name": "has_last",
                "realized_pnl_usdc": 50_000.0, "wins": 60, "losses": 40,
                "win_rate": 0.60, "total_resolved_positions": 100,
                "lifetime_pnl_from_leaderboard": 100_000.0,
                "lifetime_vol_from_leaderboard": 500_000.0,
                "best_category": "Politics", "verified_badge": False,
                "x_username": "", "included_iso": "2026-05-23T00:00:00Z",
                "last_trade_iso": "2026-05-23T00:00:00+00:00",
            },
            {
                "rank": 2, "proxy_wallet": "0xBBBB", "user_name": "no_last",
                "realized_pnl_usdc": 30_000.0, "wins": 95, "losses": 5,
                "win_rate": 0.95, "total_resolved_positions": 100,
                "lifetime_pnl_from_leaderboard": 200_000.0,
                "lifetime_vol_from_leaderboard": 1_000_000.0,
                "best_category": "Sports", "verified_badge": True,
                "x_username": "", "included_iso": "2026-05-23T00:00:00Z",
                # No last_trade_iso → coerces to None in the mapping
            },
        ],
        db_url=db_url,
    )
    # asc: real timestamp comes first, None whale sinks to bottom
    rows_asc = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="last_trade_iso", sort_desc=False,
    )
    assert [r.user_name for r in rows_asc] == ["has_last", "no_last"]
    # desc: real timestamp still first (None always at bottom)
    rows_desc = wdata._query_polymarket_watch_only_rows(
        db_url, ["polymarket_copy_trading"],
        sort_key="last_trade_iso", sort_desc=True,
    )
    assert [r.user_name for r in rows_desc] == ["has_last", "no_last"]


def test_pmdashboardview_carries_sort_state(db_url):
    """build_prediction_market_view must round-trip the sort state into
    the view so the template can render the active-column arrow."""
    # build_prediction_market_view is async; use asyncio.run.
    import asyncio
    from trading_corp.web import data as wdata2

    _seed_three_whales(db_url)

    class _StubDeps:
        def __init__(self, url):
            self.db_url = url

    deps = _StubDeps(db_url)
    view = asyncio.run(
        wdata2.build_prediction_market_view(
            deps, "polymarket_copy_trading",
            pm_watch_sort="avg_entry_price", pm_watch_desc=False,
        )
    )
    assert view is not None
    assert view.pm_watch_sort == "avg_entry_price"
    assert view.pm_watch_desc is False
    # And the rows are sorted accordingly.
    assert [r.user_name for r in view.polymarket_watch_only] == [
        "sharp", "mid", "favorite",
    ]
