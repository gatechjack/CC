"""Tests for the per-whale audit deterministic compute core.

Covers:
  - `group_fills_by_decision` correctly buckets BUY+SELL by (cid, oi)
    and attributes REDEEM-at-(cid, 999) ONLY to the winning side
  - `compute_clustering` reports the raw-fill vs decision-count ratio
  - `compute_sell_footprint` round-trip and partial-sell flags COMPOSE
    with no gap — round-trip is a strict subset of partial-sell, the
    95%-sold-5%-held case registers under BOTH (this is the
    Magamyman-style failure mode the audit was built to catch)
  - `compute_realized_pnl` uses REDEEM-grounded held quantities, not
    buy-sell inference; the math identity holds against per-fill walks
  - `compute_edge_profile` distribution math
  - `compute_category_concentration` event_slug bucketing
"""
from __future__ import annotations

from typing import Any

import pytest

from trading_corp.data.polymarket_data_api_client import ActivityRow
from trading_corp.data.polymarket_whale_audit import (
    DEFAULT_PARTIAL_SELL_THRESHOLD,
    REDEEM_OUTCOME_INDEX_SENTINEL,
    build_audit_report,
    compute_category_concentration,
    compute_clustering,
    compute_edge_profile,
    compute_realized_pnl,
    compute_sell_footprint,
    group_fills_by_decision,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _act(
    ts: int, cid: str, *, side: str = "BUY", oi: int = 0,
    price: float = 0.5, size: float = 100.0, type_: str = "TRADE",
    event_slug: str = "event-default", title: str = "",
) -> ActivityRow:
    return ActivityRow(
        proxy_wallet="0xwhale",
        timestamp=ts,
        condition_id=cid,
        type=type_,
        size=size,
        usdc_size=size * price,
        transaction_hash=f"0xhash{ts}",
        price=price,
        asset="",
        side=side,
        outcome_index=oi,
        title=title or f"market {cid}",
        slug=cid,
        event_slug=event_slug,
        outcome="Yes" if oi == 0 else "No",
        name="whale",
    )


def _redeem(ts: int, cid: str, size: float) -> ActivityRow:
    """Polymarket emits REDEEM at sentinel oi=999, side="", price=0,
    size=usdcSize. Construct that shape exactly."""
    return ActivityRow(
        proxy_wallet="0xwhale",
        timestamp=ts,
        condition_id=cid,
        type="REDEEM",
        size=size,
        usdc_size=size,
        transaction_hash=f"0xredeem{ts}",
        price=0.0,
        asset="",
        side="",
        outcome_index=REDEEM_OUTCOME_INDEX_SENTINEL,
        title="",
        slug=cid,
        event_slug="event-default",
        outcome="",
        name="whale",
    )


def _resolved(winning_outcome_index: int = 0) -> dict[str, Any]:
    return {
        "status": "resolved",
        "winning_outcome_index": winning_outcome_index,
    }


def _unresolved() -> dict[str, Any]:
    return {"status": "pending"}


# ── group_fills_by_decision ──────────────────────────────────────────────


def test_group_fills_buckets_by_cid_and_oi():
    rows = [
        _act(1000, "cid_a", oi=0, size=10.0),
        _act(999, "cid_a", oi=0, size=20.0),
        _act(998, "cid_a", oi=1, size=15.0),  # hedge — separate decision
        _act(997, "cid_b", oi=0, size=5.0),
    ]
    resolutions = {"cid_a": _resolved(0), "cid_b": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    assert set(decisions.keys()) == {("cid_a", 0), ("cid_a", 1), ("cid_b", 0)}
    # cid_a oi=0 has 2 fills summing to 30
    assert decisions[("cid_a", 0)].sum_buy_size == 30.0
    # cid_a oi=1 has 1 fill of 15
    assert decisions[("cid_a", 1)].sum_buy_size == 15.0


def test_group_fills_redeem_attributed_only_to_winning_side():
    """The REDEEM row at (cid, 999) carries the held-to-resolution
    USDC payout. It must attribute ONLY to the winning side, never to
    losing sides (which settle to $0)."""
    rows = [
        _act(1000, "cid_a", oi=0, size=100.0, price=0.30),  # winning side BUYs
        _act(999, "cid_a", oi=1, size=50.0, price=0.70),    # losing-side BUY (hedge)
        _redeem(1100, "cid_a", size=100.0),  # 100 contracts held to resolution
    ]
    resolutions = {"cid_a": _resolved(winning_outcome_index=0)}
    decisions = group_fills_by_decision(rows, resolutions)
    # Winning side (oi=0): gets the full REDEEM payout
    assert decisions[("cid_a", 0)].is_winning_side is True
    assert decisions[("cid_a", 0)].redeem_payout_usdc == 100.0
    # Losing side (oi=1): gets ZERO redeem payout
    assert decisions[("cid_a", 1)].is_winning_side is False
    assert decisions[("cid_a", 1)].redeem_payout_usdc == 0.0


def test_group_fills_skips_non_trade_non_redeem():
    rows = [
        _act(1000, "cid_a", oi=0, size=10.0),
        _act(999, "cid_a", oi=0, size=5.0, type_="REWARD"),  # ignored
        _act(998, "cid_a", oi=0, size=20.0, side="SELL"),    # SELL counted
    ]
    resolutions = {"cid_a": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    d = decisions[("cid_a", 0)]
    assert d.sum_buy_size == 10.0
    assert d.sum_sell_size == 20.0
    assert d.redeem_payout_usdc == 0.0  # no REDEEM row


def test_group_fills_resolves_unresolved_to_is_resolved_false():
    rows = [_act(1000, "cid_a", oi=0, size=10.0)]
    decisions = group_fills_by_decision(rows, {"cid_a": _unresolved()})
    assert decisions[("cid_a", 0)].is_resolved is False
    assert decisions[("cid_a", 0)].is_winning_side is False


# ── compute_clustering ───────────────────────────────────────────────────


def test_compute_clustering_known_ratio():
    """29 fills on one (cid, oi) + 1 fill each on 9 other decisions = 38
    raw fills / 10 decisions = ratio 3.8x."""
    rows = (
        [_act(1000 - i, "cid_cluster", oi=0, size=10.0) for i in range(29)]
        + [_act(900 - i, f"cid_{i}", oi=0, size=10.0) for i in range(9)]
    )
    resolutions = {"cid_cluster": _resolved(0)}
    for i in range(9):
        resolutions[f"cid_{i}"] = _resolved(0)
    decisions = group_fills_by_decision(rows, resolutions)
    c = compute_clustering(rows, decisions)
    assert c.n_raw_fills == 38
    assert c.n_decisions == 10
    assert c.clustering_ratio == 3.8
    # One cluster has 29 fills (decisions_with_5_or_more_fills == 1)
    assert c.decisions_with_ge_5_fills == 1
    # Top cluster is the 29-fill one
    top_cid, top_oi, top_n = c.top_clusters_by_fill_count[0]
    assert top_n == 29


# ── compose: round-trip ⊂ partial-sell, no gap ───────────────────────────


def test_round_trip_is_strict_subset_of_partial_sell():
    """A position with sell_share = 0.95 (round-trip) MUST be flagged
    under BOTH round-trip AND partial-sell. A position with sell_share
    = 0.50 is partial-sell ONLY. A position with sell_share = 0.10 is
    neither.
    """
    rows = [
        # Decision A: sell_share = 0.95 → BOTH flags (round-trip AND partial-sell)
        _act(1000, "cid_rt", oi=0, size=100.0),
        _act(999, "cid_rt", oi=0, side="SELL", size=95.0),
        # Decision B: sell_share = 0.50 → partial-sell only
        _act(998, "cid_partial", oi=0, size=100.0),
        _act(997, "cid_partial", oi=0, side="SELL", size=50.0),
        # Decision C: sell_share = 0.10 → NEITHER (held cleanly)
        _act(996, "cid_clean", oi=0, size=100.0),
        _act(995, "cid_clean", oi=0, side="SELL", size=10.0),
    ]
    resolutions = {
        "cid_rt": _resolved(0),
        "cid_partial": _resolved(0),
        "cid_clean": _resolved(0),
    }
    decisions = group_fills_by_decision(rows, resolutions)
    s = compute_sell_footprint(
        decisions, partial_sell_threshold=DEFAULT_PARTIAL_SELL_THRESHOLD,
    )
    assert s.n_decisions_total == 3
    assert s.n_round_trips == 1  # only cid_rt
    assert s.n_partial_sells == 2  # cid_rt AND cid_partial (round-trip INCLUDED)
    assert s.n_held_cleanly == 1  # only cid_clean
    # Invariant: n_partial_sells + n_held_cleanly == n_decisions_total
    assert s.n_partial_sells + s.n_held_cleanly == s.n_decisions_total


def test_95pct_sold_5pct_held_caught_by_partial_sell_flag():
    """The exact case the operator flagged as a composition risk: a
    95%-sold-5%-held position that ISN'T quite a round-trip (sell_share
    = 0.945, just below the 0.95 cut) but is 95% inflated. Must still
    register as partial-sell (≥ 0.20)."""
    rows = [
        _act(1000, "cid_x", oi=0, size=100.0),
        _act(999, "cid_x", oi=0, side="SELL", size=94.5),
    ]
    resolutions = {"cid_x": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    d = decisions[("cid_x", 0)]
    assert d.sell_share == pytest.approx(0.945)
    assert d.is_round_trip is False  # 0.945 < 0.95
    assert d.is_partial_sell(0.20) is True  # 0.945 >= 0.20 → caught
    s = compute_sell_footprint(
        decisions, partial_sell_threshold=DEFAULT_PARTIAL_SELL_THRESHOLD,
    )
    assert s.n_round_trips == 0
    assert s.n_partial_sells == 1
    assert s.n_held_cleanly == 0
    # Aggregate inflation_ratio CATCHES this case even though round-trip count = 0
    p = compute_realized_pnl(decisions, partial_sell_threshold=DEFAULT_PARTIAL_SELL_THRESHOLD)
    # Cost basis: 100 * 0.5 = $50
    # SELL proceeds: 94.5 * 0.5 = $47.25
    # held qty: 100 - 94.5 = 5.5 contracts; no REDEEM row in this test
    # → realized = 47.25 + 0 - 50 = -$2.75
    # held-to-resolution: (1-0.5)*100 = $50
    # inflation_usdc = 50 - (-2.75) = $52.75
    assert p.realized_pnl_usdc == pytest.approx(-2.75)
    assert p.held_to_resolution_pnl_usdc == pytest.approx(50.0)
    # inflation_ratio = 52.75 / max(50, 1) = 1.055 — clearly > 0.5 → narrator flags
    assert p.pnl_inflation_ratio > 0.5


# ── compute_realized_pnl — REDEEM-grounded ──────────────────────────────


def test_realized_pnl_redeem_grounded_clean_hold():
    """100% held to resolution, winning: REDEEM = 100 contracts; realized
    = $100 (payout) - $50 (cost) = +$50. Matches held-to-resolution
    formula (1-0.5)*100 = $50 — no inflation when fully held."""
    rows = [
        _act(1000, "cid_a", oi=0, size=100.0, price=0.5),
        _redeem(1100, "cid_a", size=100.0),
    ]
    resolutions = {"cid_a": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    p = compute_realized_pnl(decisions)
    assert p.realized_pnl_usdc == pytest.approx(50.0)
    assert p.held_to_resolution_pnl_usdc == pytest.approx(50.0)
    assert p.pnl_inflation_usdc == pytest.approx(0.0)
    assert p.pnl_inflation_ratio == pytest.approx(0.0)


def test_realized_pnl_magamyman_us_iran_shape():
    """Reproduce the Magamyman US-strikes-Iran shape: 861k BUYs at wavg
    $0.211, 570k SELL pre-resolution at $0.50, 291k REDEEM (winning side).

    Math:
      cost basis = 861154 * 0.211 = $181,704
      SELL proceeds = 570098 * 0.50 = $285,049
      REDEEM payout = $291,056
      realized = 285,049 + 291,056 − 181,704 = +$394,401
      held-to-res = (1-0.211) * 861154 = $679,250
      inflation = 679,250 - 394,401 = $284,849   (~42% of headline)
    """
    rows = [
        _act(1000, "cid_iran", oi=0, size=861154.15, price=0.211),
        _act(999, "cid_iran", oi=0, side="SELL", size=570098.04, price=0.50),
        _redeem(1100, "cid_iran", size=291056.11),
    ]
    resolutions = {"cid_iran": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    p = compute_realized_pnl(decisions)
    # Cost basis (use exact: sum of size * price across BUYs)
    expected_buy_usdc = 861154.15 * 0.211
    expected_sell_usdc = 570098.04 * 0.50
    expected_realized = expected_sell_usdc + 291056.11 - expected_buy_usdc
    assert p.realized_pnl_usdc == pytest.approx(expected_realized, rel=1e-4)
    # Inflation gap meaningful (>$200k difference)
    assert p.pnl_inflation_usdc > 200_000.0
    # Inflation ratio in ballpark of 0.4 (40% of headline is paper)
    assert 0.3 < p.pnl_inflation_ratio < 0.6


def test_realized_pnl_losing_side_no_redeem_credit():
    """A losing-side hold gets NO REDEEM credit even if there's a REDEEM
    row at (cid, 999) for the OTHER side. The REDEEM payout is attributed
    only to the winning side."""
    rows = [
        # Bought the LOSING side (oi=1, but winner is oi=0)
        _act(1000, "cid_a", oi=1, size=100.0, price=0.40),
        # REDEEM row exists for the cid (because someone else's winning
        # side gets paid; here our whale's winning side has 0 BUYs)
        _redeem(1100, "cid_a", size=0.0),  # but their losing-side held qty is reflected as redeem=0
    ]
    resolutions = {"cid_a": _resolved(winning_outcome_index=0)}
    decisions = group_fills_by_decision(rows, resolutions)
    d = decisions[("cid_a", 1)]
    assert d.is_winning_side is False
    assert d.redeem_payout_usdc == 0.0  # losing side gets nothing
    # realized = 0 (SELL) + 0 (REDEEM, losing side) - 40 (BUY cost) = -$40
    p = compute_realized_pnl(decisions)
    assert p.realized_pnl_usdc == pytest.approx(-40.0)
    # held-to-resolution for losing: -wavg*size = -0.4 * 100 = -$40 (same)
    assert p.held_to_resolution_pnl_usdc == pytest.approx(-40.0)


# ── compute_sell_footprint top_flagged_by_inflation ─────────────────────


def test_sell_footprint_top_flagged_orders_by_inflation():
    """Top-flagged list ranks by held_to_res − realized_pnl USDC."""
    rows = [
        # Big inflation: $100 buy, $50 sell, REDEEM 0 (won, 50% sold)
        _act(1000, "cid_big", oi=0, size=200.0, price=0.5),
        _act(999, "cid_big", oi=0, side="SELL", size=100.0, price=0.5),
        _redeem(1100, "cid_big", size=100.0),
        # Small inflation: $10 buy, $0 sell, fully held
        _act(998, "cid_small", oi=0, size=20.0, price=0.5),
        _redeem(1100, "cid_small", size=20.0),
    ]
    resolutions = {"cid_big": _resolved(0), "cid_small": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    s = compute_sell_footprint(decisions)
    # cid_big has inflation (sold half before resolution); cid_small does not
    cids_flagged = [fd.condition_id_short[:18] for fd in s.top_flagged_by_inflation_usdc]
    # cid_big should be ranked first (or only) since it's the inflating one
    assert any("cid_big" in c for c in cids_flagged)


# ── compute_edge_profile ─────────────────────────────────────────────────


def test_edge_profile_distribution():
    """3 decisions: weighted_avg prices 0.30, 0.65, 0.90.
    share_below_70 = 2/3; share_above_85 = 1/3."""
    rows = [
        _act(1000, "cid_a", oi=0, price=0.30, size=100.0),
        _act(999, "cid_b", oi=0, price=0.65, size=100.0),
        _act(998, "cid_c", oi=0, price=0.90, size=100.0),
    ]
    resolutions = {"cid_a": _resolved(0), "cid_b": _resolved(0), "cid_c": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    e = compute_edge_profile(decisions)
    assert e.n_decisions == 3
    assert e.share_below_70 == pytest.approx(2/3, rel=1e-3)
    assert e.share_above_85 == pytest.approx(1/3, rel=1e-3)
    assert e.avg_entry_price_decision_weighted == pytest.approx((0.30 + 0.65 + 0.90) / 3, rel=1e-3)


# ── compute_category_concentration ──────────────────────────────────────


def test_category_concentration_single_event():
    """3 decisions, all on the same event_slug → single-event concentration."""
    rows = [
        _act(1000, "cid_a", oi=0, event_slug="playoffs-game-7"),
        _act(999, "cid_b", oi=0, event_slug="playoffs-game-7"),
        _act(998, "cid_c", oi=0, event_slug="playoffs-game-7"),
    ]
    resolutions = {"cid_a": _resolved(0), "cid_b": _resolved(0), "cid_c": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    cat = compute_category_concentration(rows, decisions)
    assert cat.n_distinct_event_slugs == 1
    assert cat.largest_event_share == pytest.approx(1.0)


def test_category_concentration_three_events():
    rows = [
        _act(1000, "cid_a", oi=0, event_slug="event-1"),
        _act(999, "cid_b", oi=0, event_slug="event-2"),
        _act(998, "cid_c", oi=0, event_slug="event-3"),
    ]
    resolutions = {"cid_a": _resolved(0), "cid_b": _resolved(0), "cid_c": _resolved(0)}
    decisions = group_fills_by_decision(rows, resolutions)
    cat = compute_category_concentration(rows, decisions)
    assert cat.n_distinct_event_slugs == 3
    assert cat.largest_event_share == pytest.approx(1/3, rel=1e-3)


# ── build_audit_report end-to-end ───────────────────────────────────────


def test_build_audit_report_composes_all_sections():
    rows = [
        _act(1000, "cid_a", oi=0, size=100.0, price=0.5, event_slug="ev-1"),
        _act(999, "cid_a", oi=0, side="SELL", size=30.0, price=0.5),
        _redeem(1100, "cid_a", size=70.0),
        _act(998, "cid_b", oi=0, size=50.0, price=0.4, event_slug="ev-2"),
        _redeem(1100, "cid_b", size=50.0),
    ]
    resolutions = {"cid_a": _resolved(0), "cid_b": _resolved(0)}
    report = build_audit_report(
        leaderboard_entry=None,
        activity_rows=rows,
        resolutions=resolutions,
        proxy_wallet="0xWHALE",
    )
    assert report.proxy_wallet == "0xwhale"
    assert report.user_name == "whale"  # from activity row's `name`
    assert report.n_resolved_decisions == 2
    assert report.clustering.n_decisions == 2
    assert report.sell_footprint.n_decisions_total == 2
    assert report.realized_pnl.realized_pnl_usdc != 0.0


def test_build_audit_report_proxy_wallet_lowercased():
    rows = [_act(1000, "cid_a", oi=0, size=10.0)]
    resolutions = {"cid_a": _resolved(0)}
    report = build_audit_report(
        leaderboard_entry=None,
        activity_rows=rows,
        resolutions=resolutions,
        proxy_wallet="0xABCDEF1234567890ABCDEF1234567890ABCDEF12",
    )
    assert report.proxy_wallet == "0xabcdef1234567890abcdef1234567890abcdef12"


def test_build_audit_report_handles_empty_activity():
    report = build_audit_report(
        leaderboard_entry=None,
        activity_rows=[],
        resolutions={},
        proxy_wallet="0xempty",
    )
    assert report.n_raw_rows_examined == 0
    assert report.n_resolved_decisions == 0
    assert report.realized_pnl.realized_pnl_usdc == 0.0
