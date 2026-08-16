"""Tests for the K2.4 prediction-markets dashboard data layer.

Network-free. Exercises:
  - venue inference (slug prefix → polymarket/kalshi)
  - _query_pm_round_trips: cross-venue UNION + normalization
  - _query_pm_equity_curve: cross-venue UNION sorted by ts
  - _query_pm_pending_count: would_have_placed sans round-trip resolution
  - _hydrate_pm_overview: attaches counts to prediction-market divisions only
  - build_prediction_market_view: All-mode vs single-division; invalid slug → None
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web import data as wd


# ── helpers ────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "pm_test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url, db_path


def _insert_kalshi_round_trip(db_url, **overrides):
    row = {
        "order_id": "k-1",
        "ticker": "KX-1",
        "event_ticker": "KX",
        "event_title": "Test event",
        "category": "Climate",
        "strategy": "kalshi_llm_arbitrage",
        "division": "kalshi_llm_arbitrage",
        "arb_type": "llm_divergence",
        "arb_set_id": None,
        "outcome_bet": "yes",
        "qty": 10.0,
        "entry_price": 0.30,
        "notional": 3.0,
        # Post-cutoff by default (> DASHBOARD_RT_CUTOFFS['kalshi_llm_arbitrage']
        # 2026-07-07) so default-fixture rows are current-regime and visible; the
        # cutoff itself is still exercised by the cutoff-specific tests below.
        "entry_ts": "2026-08-11T02:00:00+00:00",
        "resolved_ts": "2026-08-11T03:00:00+00:00",
        "market_result": "yes",
        "won": 1,
        "realized_pnl": 7.0,
        "roi_pct": 233.0,
        "implied_at_entry": 0.5,
        "llm_prob": 0.7,
        "divergence_pct": 20.0,
        "edge_cents": None,
        "extra_json": "{}",
    }
    row.update(overrides)
    cols = list(row.keys())
    sql = (
        f"INSERT INTO kalshi_round_trips ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})"
    )
    with _db.connect(db_url) as conn:
        conn.execute(sql, [row[c] for c in cols])


def _insert_poly_round_trip(db_url, **overrides):
    row = {
        "order_id": "p-1",
        "condition_id": "0xabc",
        "slug": "test-poly-market",
        "market_question": "Will it happen?",
        "category": "politics",
        "series": "",
        "outcome_bet": "yes",
        "qty": 5.0,
        "entry_price": 0.40,
        "notional": 2.0,
        # Bumped in lockstep with the kalshi helper (kept newer: 03:30 > kalshi
        # 03:00) so the all-mode resolved_ts-DESC sort assertions still hold.
        "entry_ts": "2026-08-11T01:00:00+00:00",
        "resolved_ts": "2026-08-11T03:30:00+00:00",
        "yes_won": 0,
        "won": 0,
        "realized_pnl": -2.0,
        "roi_pct": -100.0,
        "implied_at_entry": 0.5,
        "llm_prob": 0.8,
        "divergence_pct": 30.0,
        "extra_json": "{}",
    }
    row.update(overrides)
    cols = list(row.keys())
    sql = (
        f"INSERT INTO polymarket_round_trips ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})"
    )
    with _db.connect(db_url) as conn:
        conn.execute(sql, [row[c] for c in cols])


def _insert_audit(db_url, actor, kind, payload, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            (ts, actor, kind, json.dumps(payload)),
        )


def _insert_equity(db_url, table, ts, division, equity, cash_col):
    with _db.connect(db_url) as conn:
        conn.execute(
            f"INSERT INTO {table} (ts, division, equity, {cash_col}, positions_value, n_positions) "
            f"VALUES (?, ?, ?, ?, ?, 0)",
            (ts, division, equity, equity, 0.0),
        )


# ── venue inference ────────────────────────────────────────────────────


def test_pm_venue_kalshi_prefix():
    assert wd._pm_venue("kalshi_arbitrage") == "kalshi"
    assert wd._pm_venue("kalshi_llm_arbitrage") == "kalshi"
    assert wd._pm_venue("kalshi_copy_trading") == "kalshi"


def test_pm_venue_polymarket_default():
    assert wd._pm_venue("polymarket_arbitrage") == "polymarket"
    assert wd._pm_venue("polymarket_copy_trading") == "polymarket"
    assert wd._pm_venue("anything_else") == "polymarket"   # default


# ── round-trip query / normalize ───────────────────────────────────────


def test_query_round_trips_kalshi_only(fresh_db):
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(db_url)

    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.venue == "kalshi"
    assert rt.division == "kalshi_llm_arbitrage"
    assert rt.strategy == "kalshi_llm_arbitrage"
    assert rt.market_title == "Test event"
    assert rt.market_id == "KX-1"
    assert rt.outcome_bet == "yes"
    assert rt.market_result == "yes"
    assert rt.won == 1
    assert rt.realized_pnl == pytest.approx(7.0)
    assert rt.arb_type == "llm_divergence"


def test_query_round_trips_polymarket_only(fresh_db):
    db_url, _ = fresh_db
    _insert_poly_round_trip(db_url)

    rts = wd._query_pm_round_trips(db_url, ["polymarket_arbitrage"], 100)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.venue == "polymarket"
    assert rt.division == "polymarket_arbitrage"
    assert rt.market_title == "Will it happen?"
    assert rt.market_id == "test-poly-market"
    assert rt.outcome_bet == "yes"
    assert rt.won == 0
    assert rt.market_result == "no"     # yes_won=0 → market resolved NO
    assert rt.arb_type is None


def test_query_round_trips_all_mode_unions_and_sorts(fresh_db):
    """All-mode: query crosses both venue tables; rows sort by resolved_ts DESC."""
    db_url, _ = fresh_db
    # Polymarket row is more recent (03:30) than kalshi (03:00)
    _insert_kalshi_round_trip(db_url)
    _insert_poly_round_trip(db_url)

    rts = wd._query_pm_round_trips(
        db_url,
        ["polymarket_arbitrage", "kalshi_arbitrage", "kalshi_llm_arbitrage"],
        100,
    )
    assert len(rts) == 2
    assert rts[0].venue == "polymarket"   # most recent first
    assert rts[1].venue == "kalshi"


def test_query_round_trips_filters_kalshi_by_division(fresh_db):
    """A kalshi row in kalshi_arbitrage is invisible when filter is
    kalshi_llm_arbitrage only."""
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(db_url, order_id="k-A", division="kalshi_arbitrage")
    _insert_kalshi_round_trip(db_url, order_id="k-L", division="kalshi_llm_arbitrage")

    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(rts) == 1
    assert rts[0].order_id == "k-L"


def test_query_round_trips_void_normalizes(fresh_db):
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(
        db_url, market_result="void", won=0, realized_pnl=0.0, roi_pct=0.0,
    )
    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(rts) == 1
    assert rts[0].market_result == "void"
    assert rts[0].won == 0
    assert rts[0].realized_pnl == 0.0


# ── equity-curve query ────────────────────────────────────────────────


def test_query_equity_curve_unions_both_venues(fresh_db):
    db_url, _ = fresh_db
    now = datetime.now(timezone.utc)
    _insert_equity(
        db_url, "polymarket_equity_history",
        (now - timedelta(hours=1)).isoformat(),
        "polymarket_arbitrage", 500.0, "cash_usdc",
    )
    _insert_equity(
        db_url, "kalshi_equity_history",
        (now - timedelta(minutes=30)).isoformat(),
        "kalshi_arbitrage", 499.0, "cash_usd",
    )

    curve = wd._query_pm_equity_curve(
        db_url,
        ["polymarket_arbitrage", "kalshi_arbitrage", "kalshi_llm_arbitrage"],
        7,
    )
    assert len(curve) == 2
    # Sorted ts ASC: polymarket point (1h ago) before kalshi (30min ago)
    assert curve[0].division == "polymarket_arbitrage"
    assert curve[1].division == "kalshi_arbitrage"
    assert curve[0].equity == 500.0
    assert curve[1].equity == 499.0


def test_query_equity_curve_respects_days_cutoff(fresh_db):
    db_url, _ = fresh_db
    now = datetime.now(timezone.utc)
    # Old point — 60 days ago — must be excluded by 30-day window.
    _insert_equity(
        db_url, "kalshi_equity_history",
        (now - timedelta(days=60)).isoformat(),
        "kalshi_arbitrage", 100.0, "cash_usd",
    )
    _insert_equity(
        db_url, "kalshi_equity_history",
        (now - timedelta(days=5)).isoformat(),
        "kalshi_arbitrage", 500.0, "cash_usd",
    )

    curve = wd._query_pm_equity_curve(db_url, ["kalshi_arbitrage"], 30)
    assert len(curve) == 1
    assert curve[0].equity == 500.0


# ── pending count ──────────────────────────────────────────────────────


def test_pending_count_polymarket(fresh_db):
    db_url, _ = fresh_db
    # 2 polymarket would_have_placed rows, none resolved.
    for i in range(2):
        _insert_audit(
            db_url, "polymarket_arbitrage", "would_have_placed",
            {"order_id": f"p-{i}", "condition_id": f"0xab{i}"},
        )
    n = wd._query_pm_pending_count(db_url, ["polymarket_arbitrage"])
    assert n == 2


def test_pending_count_kalshi_filters_by_division(fresh_db):
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "k-1", "division": "kalshi_llm_arbitrage", "ticker": "KX-1"},
    )
    _insert_audit(
        db_url, "kalshi_tail_price_arb", "would_have_placed",
        {"order_id": "k-2", "division": "kalshi_arbitrage", "ticker": "KX-2"},
    )
    # Filter to kalshi_arbitrage only → should see 1 (the tail-price one).
    n = wd._query_pm_pending_count(db_url, ["kalshi_arbitrage"])
    assert n == 1
    # Filter to kalshi_llm_arbitrage → also 1.
    n = wd._query_pm_pending_count(db_url, ["kalshi_llm_arbitrage"])
    assert n == 1


def test_pending_count_excludes_resolved(fresh_db):
    """A would_have_placed with a matching kalshi_round_trips row is not pending."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "k-resolved", "division": "kalshi_llm_arbitrage", "ticker": "KX-R"},
    )
    _insert_kalshi_round_trip(db_url, order_id="k-resolved")

    n = wd._query_pm_pending_count(db_url, ["kalshi_llm_arbitrage"])
    assert n == 0


# ── summary ────────────────────────────────────────────────────────────


def test_summary_zero_state_clean():
    s = wd._pm_summary([], [], 0)
    assert s.n_resolved == 0
    assert s.n_wins == 0
    assert s.win_rate_pct is None
    assert s.total_realized_pnl == 0.0
    assert s.current_equity is None
    assert s.todays_pnl is None


def test_summary_win_rate_excludes_voids():
    """Voids don't count in win-rate denominator."""
    now = datetime.now(timezone.utc)
    rts = [
        wd.PMRoundTrip(
            order_id=f"r-{i}", venue="kalshi", division="kalshi_arbitrage",
            strategy="x", market_title=f"m-{i}", market_id=f"t-{i}",
            category=None, outcome_bet="yes", qty=1.0, entry_price=0.5,
            notional=0.5, entry_ts=now.isoformat(),
            resolved_ts=now.isoformat(),
            market_result=res, won=won, realized_pnl=pnl, roi_pct=0.0,
            implied_at_entry=None, llm_prob=None, divergence_pct=None,
            arb_type=None,
            rationale=None, llm_reasoning=None, key_unknowns=[],
            llm_confidence=None, subtitle=None,
        )
        for i, (won, res, pnl) in enumerate([
            (1, "yes", 0.5),
            (1, "yes", 0.5),
            (0, "no", -0.5),
            (0, "void", 0.0),
        ])
    ]
    s = wd._pm_summary(rts, [], 0)
    assert s.n_resolved == 4
    assert s.n_wins == 2
    assert s.n_losses == 1
    assert s.n_voids == 1
    # 2 wins out of 3 decisive (voids excluded) → 66.7%
    assert s.win_rate_pct == pytest.approx(200.0 / 3.0, abs=0.1)


# ── full builder ───────────────────────────────────────────────────────


def _make_deps(db_url):
    return SimpleNamespace(
        db_url=db_url,
        data_exec=None,
        trend_agent=None,
        mode="test",
    )


def test_build_view_invalid_slug_returns_none(fresh_db):
    db_url, _ = fresh_db
    view = asyncio.run(wd.build_prediction_market_view(_make_deps(db_url), "not-a-division"))
    assert view is None


def test_build_view_single_division(fresh_db):
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(db_url, division="kalshi_llm_arbitrage")
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "pending-1", "division": "kalshi_llm_arbitrage",
         "ticker": "KX-P", "event_title": "Pending event", "outcome": "yes",
         "qty": 5.0, "limit_price": 0.30, "divergence_pct": 25.0,
         "expires_at": "2026-06-01T00:00:00Z"},
    )

    view = asyncio.run(wd.build_prediction_market_view(
        _make_deps(db_url), "kalshi_llm_arbitrage",
    ))
    assert view is not None
    assert view.selected == "kalshi_llm_arbitrage"
    assert view.selected_label   # display name
    assert view.summary.n_resolved == 1
    assert view.summary.n_pending == 1
    assert view.summary.n_wins == 1
    assert view.summary.win_rate_pct == 100.0
    assert len(view.round_trips) == 1
    assert len(view.open_trades) == 1
    assert view.open_trades[0].outcome_bet == "yes"
    # Dropdown options always show the full list, not just the selected one.
    slugs = [o.slug for o in view.available_divisions]
    assert "kalshi_llm_arbitrage" in slugs


def test_build_view_all_mode_aggregates(fresh_db):
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(db_url, order_id="k-1", division="kalshi_llm_arbitrage", won=1, realized_pnl=2.0)
    _insert_poly_round_trip(db_url, order_id="p-1", won=0, realized_pnl=-1.0)

    view = asyncio.run(wd.build_prediction_market_view(
        _make_deps(db_url), None,
    ))
    assert view is not None
    assert view.selected is None
    assert view.selected_label == "All Prediction Markets"
    assert view.summary.n_resolved == 2
    assert view.summary.n_wins == 1
    assert view.summary.n_losses == 1
    assert view.summary.win_rate_pct == 50.0
    assert view.summary.total_realized_pnl == pytest.approx(1.0)
    assert len(view.round_trips) == 2


# ── tile hydration ─────────────────────────────────────────────────────


def test_hydrate_pm_overview_only_touches_prediction_market_divisions(fresh_db):
    db_url, _ = fresh_db
    from trading_corp.utils.divisions import Division

    # Mix of pm + non-pm divisions, with a kalshi resolved row.
    _insert_kalshi_round_trip(db_url, division="kalshi_arbitrage", won=1, realized_pnl=5.0)
    _insert_audit(
        db_url, "kalshi_tail_price_arb", "would_have_placed",
        {"order_id": "pending-a", "division": "kalshi_arbitrage", "ticker": "X"},
    )

    divs = [
        Division(slug="kalshi_arbitrage", name="Kalshi Arb",
                 broker="kalshi", account_filter="", intent="aggressive",
                 benchmark="SPY"),
        Division(slug="kalshi_llm_arbitrage", name="Kalshi LLM",
                 broker="kalshi", account_filter="", intent="aggressive",
                 benchmark="SPY"),
        Division(slug="coinbase_spot", name="Coinbase BTC HODL",
                 broker="coinbase", account_filter="", intent="aggressive",
                 benchmark="BTC-USD"),
    ]
    wd._hydrate_pm_overview(divs, db_url)

    # PM divisions get overview attached.
    assert divs[0].pm_overview is not None
    assert divs[0].pm_overview["n_resolved"] == 1
    assert divs[0].pm_overview["n_wins"] == 1
    assert divs[0].pm_overview["n_pending"] == 1
    assert divs[0].pm_overview["win_rate_pct"] == 100.0
    assert divs[0].pm_overview["total_realized_pnl"] == pytest.approx(5.0)

    assert divs[1].pm_overview is not None
    assert divs[1].pm_overview["n_resolved"] == 0   # zero-state init
    assert divs[1].pm_overview["win_rate_pct"] is None

    # Non-PM division untouched.
    assert divs[2].pm_overview is None


# ── Open trades query ──────────────────────────────────────────────────


def test_open_trades_kalshi_llm_payload(fresh_db):
    """LLM strategy uses `outcome` field directly; payload carries
    market title + divergence + expires_at."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {
            "order_id": "k-llm-1", "division": "kalshi_llm_arbitrage",
            "ticker": "KX-1", "event_ticker": "KX",
            "event_title": "Some kalshi event",
            "category": "Climate",
            "outcome": "no",
            "qty": 12.5, "limit_price": 0.08,
            "divergence_pct": 21.5,
            "expires_at": "2026-06-02T00:00:00Z",
        },
    )
    ots = wd._query_pm_open_trades(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(ots) == 1
    ot = ots[0]
    assert ot.venue == "kalshi"
    assert ot.strategy == "kalshi_llm_arbitrage"
    assert ot.market_title == "Some kalshi event"
    assert ot.outcome_bet == "no"
    assert ot.qty == 12.5
    assert ot.entry_price == 0.08
    assert ot.notional == pytest.approx(1.0)   # 12.5 * 0.08
    assert ot.divergence_pct == 21.5
    assert ot.edge_cents is None
    assert ot.arb_type == "llm_divergence"
    assert ot.resolves_at == "2026-06-02T00:00:00Z"


def test_open_trades_kalshi_temporal_bucket_leg_prefix(fresh_db):
    """Temporal/bucket strategy encodes side in `leg` field as
    yes_<ticker>/no_<ticker>; resolver must parse prefix."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_temporal_bucket_arb", "would_have_placed",
        {
            "order_id": "k-tb-1", "division": "kalshi_arbitrage",
            "ticker": "KX-Q1", "event_ticker": "KX",
            "leg": "yes_KX-Q1",
            "kalshi_arb_type": "bucket",
            "qty": 25.0, "limit_price": 0.04,
            "edge_cents": 89.4,
        },
    )
    ots = wd._query_pm_open_trades(db_url, ["kalshi_arbitrage"], 100)
    assert len(ots) == 1
    ot = ots[0]
    assert ot.outcome_bet == "yes"      # parsed from leg prefix
    assert ot.arb_type == "bucket"
    assert ot.edge_cents == 89.4
    assert ot.divergence_pct is None


def test_open_trades_polymarket(fresh_db):
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "polymarket_arbitrage", "would_have_placed",
        {
            "order_id": "p-1", "division": "polymarket_arbitrage",
            "condition_id": "0xabc", "market_slug": "test-market",
            "market_question": "Will it happen?",
            "category": "politics",
            "outcome": "yes",
            "qty": 1.64, "limit_price": 0.61,
            "divergence_pct": 31.0,
            "resolves_at": "2026-05-15T00:00:00Z",
        },
    )
    ots = wd._query_pm_open_trades(db_url, ["polymarket_arbitrage"], 100)
    assert len(ots) == 1
    ot = ots[0]
    assert ot.venue == "polymarket"
    assert ot.market_title == "Will it happen?"
    assert ot.market_id == "test-market"
    assert ot.outcome_bet == "yes"
    assert ot.divergence_pct == 31.0
    assert ot.resolves_at == "2026-05-15T00:00:00Z"


def test_open_trades_excludes_resolved(fresh_db):
    db_url, _ = fresh_db
    # would_have_placed → resolved → NOT in open trades.
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "k-resolved", "division": "kalshi_llm_arbitrage",
         "ticker": "KX-R", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.5},
    )
    _insert_kalshi_round_trip(db_url, order_id="k-resolved")
    # Plus an unresolved one.
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "k-open", "division": "kalshi_llm_arbitrage",
         "ticker": "KX-O", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.5},
    )
    ots = wd._query_pm_open_trades(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(ots) == 1
    assert ots[0].order_id == "k-open"


def test_open_trades_all_mode_unions_and_sorts(fresh_db):
    """All-mode: kalshi + polymarket open trades returned, sorted by emit ts DESC."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "k-old", "division": "kalshi_llm_arbitrage",
         "ticker": "KX-OLD", "outcome": "yes", "qty": 1.0, "limit_price": 0.5},
        ts="2026-08-11T01:00:00+00:00",   # post-cutoff so the kalshi_llm OPEN cutoff keeps it
    )
    _insert_audit(
        db_url, "polymarket_arbitrage", "would_have_placed",
        {"order_id": "p-new", "condition_id": "0x", "market_slug": "p-new",
         "outcome": "yes", "qty": 1.0, "limit_price": 0.5},
        ts="2026-08-11T03:00:00+00:00",   # newer than k-old (01:00) -> sorts first
    )
    ots = wd._query_pm_open_trades(
        db_url,
        ["polymarket_arbitrage", "kalshi_llm_arbitrage"],
        100,
    )
    assert len(ots) == 2
    assert ots[0].order_id == "p-new"   # newer first
    assert ots[1].order_id == "k-old"


# ── Analysis field parsing (LLM reasoning, key unknowns, etc.) ─────────


def test_open_trade_parses_llm_reasoning_from_payload(fresh_db):
    """LLM strategy payload carries llm_reasoning + key_unknowns; the
    open-trades query must surface them on PMOpenTrade."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {
            "order_id": "k-1", "division": "kalshi_llm_arbitrage",
            "ticker": "KX-1", "event_title": "Will X happen?",
            "outcome": "yes", "qty": 1.0, "limit_price": 0.5,
            "rationale": "LLM YES=0.80 vs implied 0.50 (div 30%); buy YES",
            "llm_reasoning": "Base rate analysis suggests this is more likely than the market thinks because of historical precedent.",
            "key_unknowns": ["Recent policy shifts", "Counterparty depth"],
            "llm_confidence": "high",
            "subtitle": "above $50k",
        },
    )
    ots = wd._query_pm_open_trades(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(ots) == 1
    ot = ots[0]
    assert ot.rationale.startswith("LLM YES=0.80")
    assert "historical precedent" in ot.llm_reasoning
    assert ot.key_unknowns == ["Recent policy shifts", "Counterparty depth"]
    assert ot.llm_confidence == "high"
    assert ot.subtitle == "above $50k"


def test_open_trade_structural_arb_has_rationale_no_llm(fresh_db):
    """Structural strategies don't have LLM fields but always have a
    rationale + may carry leg_date for temporal arb."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, "kalshi_temporal_bucket_arb", "would_have_placed",
        {
            "order_id": "k-t-1", "division": "kalshi_arbitrage",
            "ticker": "KX-Q1", "leg": "yes_KX-Q1",
            "kalshi_arb_type": "temporal",
            "qty": 5.0, "limit_price": 0.20,
            "rationale": "Temporal arb on KX: P(early)>P(late) by 5c",
            "edge_cents": 5.0,
            "leg_date": "2026-06-01",
        },
    )
    ots = wd._query_pm_open_trades(db_url, ["kalshi_arbitrage"], 100)
    assert len(ots) == 1
    ot = ots[0]
    assert ot.rationale.startswith("Temporal arb")
    assert ot.llm_reasoning is None
    assert ot.key_unknowns == []
    assert ot.llm_confidence is None
    assert ot.leg_date == "2026-06-01"


def test_round_trip_parses_extra_json_analysis_fields(fresh_db):
    """kalshi_round_trips.extra_json now stores llm_reasoning + key_unknowns
    (post-2026-05-11 enrichment). Query must surface them on PMRoundTrip."""
    db_url, _ = fresh_db
    extra = json.dumps({
        "rationale": "LLM YES=0.80 vs implied 0.50",
        "llm_reasoning": "Long-form reasoning text here.",
        "key_unknowns": ["Unknown A", "Unknown B"],
        "llm_confidence": "medium",
        "subtitle": "≥3 inches",
    })
    _insert_kalshi_round_trip(db_url, extra_json=extra)
    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.rationale == "LLM YES=0.80 vs implied 0.50"
    assert rt.llm_reasoning == "Long-form reasoning text here."
    assert rt.key_unknowns == ["Unknown A", "Unknown B"]
    assert rt.llm_confidence == "medium"
    assert rt.subtitle == "≥3 inches"


def test_round_trip_handles_legacy_empty_extra_json(fresh_db):
    """Rows resolved before the resolver enrichment still parse cleanly
    with all analysis fields defaulting to None / empty."""
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(db_url, extra_json="{}")
    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.rationale is None
    assert rt.llm_reasoning is None
    assert rt.key_unknowns == []
    assert rt.llm_confidence is None


def test_round_trip_handles_malformed_extra_json(fresh_db):
    """A garbled extra_json shouldn't crash the query."""
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(db_url, extra_json="this is not json")
    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert len(rts) == 1
    assert rts[0].rationale is None


# ── dashboard cutoff filter ───────────────────────────────────────────


def test_kalshi_cutoff_clause_empty_dict_returns_empty(monkeypatch):
    """Rollback path: an empty DASHBOARD_RT_CUTOFFS produces no SQL."""
    monkeypatch.setattr(wd, "DASHBOARD_RT_CUTOFFS", {})
    assert wd._kalshi_cutoff_clause("entry_ts") == ""


def test_kalshi_cutoff_clause_emits_per_division_predicate(monkeypatch):
    """Each entry in DASHBOARD_RT_CUTOFFS adds an AND NOT (...) clause."""
    monkeypatch.setattr(
        wd,
        "DASHBOARD_RT_CUTOFFS",
        {"kalshi_weather": "2026-05-16T19:18:00+00:00"},
    )
    clause = wd._kalshi_cutoff_clause("entry_ts")
    assert "kalshi_weather" in clause
    assert "2026-05-16T19:18:00+00:00" in clause
    assert "entry_ts <" in clause
    assert clause.lstrip().startswith("AND NOT")


def _seed_cutoff_fixture(db_url):
    """Two pre-cutoff + two post-cutoff rows for kalshi_weather, plus one
    pre-cutoff row for kalshi_llm_arbitrage (no cutoff → must NOT be filtered)."""
    # kalshi_weather: pre-cutoff (1 win, 1 loss)
    _insert_kalshi_round_trip(
        db_url, order_id="w-pre-W", division="kalshi_weather",
        strategy="kalshi_weather_arb",
        entry_ts="2026-05-15T12:00:00+00:00",
        resolved_ts="2026-05-15T14:00:00+00:00",
        won=1, realized_pnl=5.0, market_result="yes",
    )
    _insert_kalshi_round_trip(
        db_url, order_id="w-pre-L", division="kalshi_weather",
        strategy="kalshi_weather_arb",
        entry_ts="2026-05-15T13:00:00+00:00",
        resolved_ts="2026-05-15T15:00:00+00:00",
        won=0, realized_pnl=-3.0, market_result="no",
    )
    # kalshi_weather: post-cutoff (1 win, 1 void)
    _insert_kalshi_round_trip(
        db_url, order_id="w-post-W", division="kalshi_weather",
        strategy="kalshi_weather_arb",
        entry_ts="2026-05-16T20:00:00+00:00",
        resolved_ts="2026-05-16T22:00:00+00:00",
        won=1, realized_pnl=4.0, market_result="yes",
    )
    _insert_kalshi_round_trip(
        db_url, order_id="w-post-V", division="kalshi_weather",
        strategy="kalshi_weather_arb",
        entry_ts="2026-05-16T20:30:00+00:00",
        resolved_ts="2026-05-16T22:30:00+00:00",
        won=0, realized_pnl=0.0, market_result="void",
    )
    # kalshi_llm_arbitrage: pre-cutoff date but no cutoff for this division
    _insert_kalshi_round_trip(
        db_url, order_id="llm-pre", division="kalshi_llm_arbitrage",
        strategy="kalshi_llm_arbitrage",
        entry_ts="2026-05-15T10:00:00+00:00",
        resolved_ts="2026-05-15T11:00:00+00:00",
        won=1, realized_pnl=2.0, market_result="yes",
    )


def test_resolved_stats_filters_pre_cutoff_kalshi_rows(fresh_db, monkeypatch):
    db_url, _ = fresh_db
    monkeypatch.setattr(
        wd,
        "DASHBOARD_RT_CUTOFFS",
        {"kalshi_weather": "2026-05-16T19:18:00+00:00"},
    )
    _seed_cutoff_fixture(db_url)

    stats = wd._query_pm_resolved_stats(db_url, ["kalshi_weather"])
    assert stats["n_resolved"] == 2     # 2 post-cutoff only
    assert stats["n_wins"] == 1
    assert stats["n_voids"] == 1
    assert stats["total_realized_pnl"] == pytest.approx(4.0)


def test_resolved_stats_does_not_filter_division_without_cutoff(
    fresh_db, monkeypatch,
):
    """kalshi_llm_arbitrage has no entry in DASHBOARD_RT_CUTOFFS — its
    pre-2026-05-16 row must remain in the aggregate."""
    db_url, _ = fresh_db
    monkeypatch.setattr(
        wd,
        "DASHBOARD_RT_CUTOFFS",
        {"kalshi_weather": "2026-05-16T19:18:00+00:00"},
    )
    _seed_cutoff_fixture(db_url)

    stats = wd._query_pm_resolved_stats(db_url, ["kalshi_llm_arbitrage"])
    assert stats["n_resolved"] == 1
    assert stats["n_wins"] == 1


def test_round_trips_history_list_filters_pre_cutoff(fresh_db, monkeypatch):
    db_url, _ = fresh_db
    monkeypatch.setattr(
        wd,
        "DASHBOARD_RT_CUTOFFS",
        {"kalshi_weather": "2026-05-16T19:18:00+00:00"},
    )
    _seed_cutoff_fixture(db_url)

    rts = wd._query_pm_round_trips(db_url, ["kalshi_weather"], 100)
    assert {rt.order_id for rt in rts} == {"w-post-W", "w-post-V"}


def test_summary_attaches_cutoff_label_for_filtered_division(
    fresh_db, monkeypatch,
):
    """End-to-end via build_prediction_market_view: a filtered division
    surface its cutoff_label on the PMSummary; an unfiltered one doesn't."""
    db_url, _ = fresh_db
    monkeypatch.setattr(
        wd,
        "DASHBOARD_RT_CUTOFFS",
        {"kalshi_weather": "2026-05-16T19:18:00+00:00"},
    )
    _seed_cutoff_fixture(db_url)

    # Stub _pm_divisions_all to limit scope.
    fake_div_weather = SimpleNamespace(
        slug="kalshi_weather", name="Kalshi Weather", pm_overview=None,
    )
    fake_div_llm = SimpleNamespace(
        slug="kalshi_llm_arbitrage", name="Kalshi LLM Arb", pm_overview=None,
    )
    monkeypatch.setattr(
        wd, "_pm_divisions_all",
        lambda: [fake_div_weather, fake_div_llm],
    )

    deps = SimpleNamespace(db_url=db_url)

    view_w = asyncio.run(wd.build_prediction_market_view(deps, "kalshi_weather"))
    assert view_w is not None
    assert view_w.summary.cutoff_label == "2026-05-16"
    assert view_w.summary.cutoff_ts == "2026-05-16T19:18:00+00:00"
    assert view_w.summary.n_resolved == 2   # only post-cutoff counted

    view_llm = asyncio.run(
        wd.build_prediction_market_view(deps, "kalshi_llm_arbitrage")
    )
    assert view_llm is not None
    assert view_llm.summary.cutoff_label is None
    assert view_llm.summary.cutoff_ts is None


def test_summary_no_cutoff_in_combined_view(fresh_db, monkeypatch):
    """Combined ("All Prediction Markets") view must NOT show a cutoff
    label — different divisions have different (or no) cutoffs."""
    db_url, _ = fresh_db
    monkeypatch.setattr(
        wd,
        "DASHBOARD_RT_CUTOFFS",
        {"kalshi_weather": "2026-05-16T19:18:00+00:00"},
    )
    _seed_cutoff_fixture(db_url)

    fake_div_weather = SimpleNamespace(
        slug="kalshi_weather", name="Kalshi Weather", pm_overview=None,
    )
    monkeypatch.setattr(
        wd, "_pm_divisions_all", lambda: [fake_div_weather],
    )
    deps = SimpleNamespace(db_url=db_url)

    view = asyncio.run(wd.build_prediction_market_view(deps, None))
    assert view is not None
    assert view.summary.cutoff_label is None
    assert view.summary.cutoff_ts is None


# ── Kalshi copy-trading Paper/Live/All mode toggle ─────────────────────


def test_kalshi_copy_mode_clause_fragments():
    """The 3 slice fragments + custom ts_col + no-epoch no-op."""
    epoch = "2026-07-01T14:08:58+00:00"
    assert wd._kalshi_copy_mode_clause("live", epoch) == f" AND entry_ts >= '{epoch}'"
    assert wd._kalshi_copy_mode_clause("paper", epoch) == f" AND entry_ts < '{epoch}'"
    assert wd._kalshi_copy_mode_clause("all", epoch) == ""
    # Open-trades path scopes on the audit-event ts column.
    assert wd._kalshi_copy_mode_clause("live", epoch, "a.ts") == f" AND a.ts >= '{epoch}'"
    # No epoch → no-op regardless of mode (reversibility path).
    assert wd._kalshi_copy_mode_clause("live", "") == ""


def test_get_kalshi_copy_live_epoch_defaults_to_constant(fresh_db):
    """No agent_state override → the hardcoded go-live constant."""
    db_url, _ = fresh_db
    assert wd._get_kalshi_copy_live_epoch(db_url) == wd.KALSHI_COPY_LIVE_EPOCH


def test_get_kalshi_copy_live_epoch_honors_override(fresh_db):
    """An ISO override in agent_state(kalshi_copy_trader, metrics_epoch)
    wins over the constant."""
    db_url, _ = fresh_db
    _db.set_agent_state(
        "kalshi_copy_trader", "metrics_epoch",
        "2026-08-01T00:00:00+00:00", db_url=db_url,
    )
    assert wd._get_kalshi_copy_live_epoch(db_url) == "2026-08-01T00:00:00+00:00"


def _seed_copy_straddle(db_url):
    """Two PRE-epoch (paper) + two POST-epoch (live) kalshi_copy_trading
    round-trips straddling KALSHI_COPY_LIVE_EPOCH (2026-07-01T14:08:58Z)."""
    # PRE-epoch (paper): 1 win, 1 loss
    _insert_kalshi_round_trip(
        db_url, order_id="cp-pre-W", division="kalshi_copy_trading",
        strategy="kalshi_copy_trader",
        entry_ts="2026-06-20T12:00:00+00:00",
        resolved_ts="2026-06-20T14:00:00+00:00",
        won=1, realized_pnl=6.0, market_result="yes",
    )
    _insert_kalshi_round_trip(
        db_url, order_id="cp-pre-L", division="kalshi_copy_trading",
        strategy="kalshi_copy_trader",
        entry_ts="2026-06-21T12:00:00+00:00",
        resolved_ts="2026-06-21T14:00:00+00:00",
        won=0, realized_pnl=-4.0, market_result="no",
    )
    # POST-epoch (live): 1 win, 1 loss
    _insert_kalshi_round_trip(
        db_url, order_id="cp-post-W", division="kalshi_copy_trading",
        strategy="kalshi_copy_trader",
        entry_ts="2026-07-02T12:00:00+00:00",
        resolved_ts="2026-07-02T14:00:00+00:00",
        won=1, realized_pnl=8.0, market_result="yes",
    )
    _insert_kalshi_round_trip(
        db_url, order_id="cp-post-L", division="kalshi_copy_trading",
        strategy="kalshi_copy_trader",
        entry_ts="2026-07-03T12:00:00+00:00",
        resolved_ts="2026-07-03T14:00:00+00:00",
        won=0, realized_pnl=-2.0, market_result="no",
    )


def _stub_copy_division(monkeypatch):
    fake = SimpleNamespace(
        slug="kalshi_copy_trading", name="Kalshi Copy Trading", pm_overview=None,
    )
    monkeypatch.setattr(wd, "_pm_divisions_all", lambda: [fake])


def test_wr_mode_live_counts_only_post_epoch(fresh_db, monkeypatch):
    db_url, _ = fresh_db
    _seed_copy_straddle(db_url)
    _stub_copy_division(monkeypatch)
    deps = SimpleNamespace(db_url=db_url)

    view = asyncio.run(wd.build_prediction_market_view(
        deps, "kalshi_copy_trading", wr_mode="live",
    ))
    assert view is not None
    assert view.wr_mode == "live"
    assert view.wr_live_epoch == wd.KALSHI_COPY_LIVE_EPOCH
    assert view.summary.n_resolved == 2
    assert {rt.order_id for rt in view.round_trips} == {"cp-post-W", "cp-post-L"}
    assert view.summary.total_realized_pnl == pytest.approx(6.0)   # 8 - 2


def test_wr_mode_paper_counts_only_pre_epoch(fresh_db, monkeypatch):
    db_url, _ = fresh_db
    _seed_copy_straddle(db_url)
    _stub_copy_division(monkeypatch)
    deps = SimpleNamespace(db_url=db_url)

    view = asyncio.run(wd.build_prediction_market_view(
        deps, "kalshi_copy_trading", wr_mode="paper",
    ))
    assert view is not None
    assert view.wr_mode == "paper"
    assert view.summary.n_resolved == 2
    assert {rt.order_id for rt in view.round_trips} == {"cp-pre-W", "cp-pre-L"}
    assert view.summary.total_realized_pnl == pytest.approx(2.0)   # 6 - 4


def test_wr_mode_all_counts_both(fresh_db, monkeypatch):
    db_url, _ = fresh_db
    _seed_copy_straddle(db_url)
    _stub_copy_division(monkeypatch)
    deps = SimpleNamespace(db_url=db_url)

    view = asyncio.run(wd.build_prediction_market_view(
        deps, "kalshi_copy_trading", wr_mode="all",
    ))
    assert view is not None
    assert view.wr_mode == "all"
    assert view.summary.n_resolved == 4
    assert {rt.order_id for rt in view.round_trips} == {
        "cp-pre-W", "cp-pre-L", "cp-post-W", "cp-post-L",
    }
    assert view.summary.total_realized_pnl == pytest.approx(8.0)


def test_wr_mode_does_not_affect_non_kalshi_division(fresh_db, monkeypatch):
    """A polymarket division is byte-identical regardless of wr_mode — the
    mode clause is kalshi_copy_trading-only."""
    db_url, _ = fresh_db
    _insert_poly_round_trip(
        db_url, order_id="p-pre", division="polymarket_copy_trading",
        won=1, realized_pnl=3.0,
        entry_ts="2026-06-20T00:00:00+00:00",
        resolved_ts="2026-06-20T01:00:00+00:00",
    )
    _insert_poly_round_trip(
        db_url, order_id="p-post", division="polymarket_copy_trading",
        won=0, realized_pnl=-1.0,
        entry_ts="2026-07-02T00:00:00+00:00",
        resolved_ts="2026-07-02T01:00:00+00:00",
    )
    fake = SimpleNamespace(
        slug="polymarket_copy_trading", name="Polymarket Copy", pm_overview=None,
    )
    monkeypatch.setattr(wd, "_pm_divisions_all", lambda: [fake])
    deps = SimpleNamespace(db_url=db_url)

    seen = {}
    for mode in ("live", "paper", "all"):
        view = asyncio.run(wd.build_prediction_market_view(
            deps, "polymarket_copy_trading", wr_mode=mode,
        ))
        assert view is not None
        seen[mode] = (
            view.summary.n_resolved,
            view.summary.n_wins,
            round(view.summary.total_realized_pnl, 6),
        )
    # All three modes identical for the polymarket division.
    assert seen["live"] == seen["paper"] == seen["all"] == (2, 1, 2.0)


# ── Poly->Kalshi copy (live) OPEN trades (CP3) ──────────────────────────────
def _pk_order_payload(**over):
    """A poly_kalshi_order audit payload as poly_kalshi_executor._record writes it
    post-CP3 (division + Flag-1 fill fields). Defaults model live fill #1:
    SDTrading YES MIA 9 contracts, limit 0.56, filled 0.54."""
    p = {
        "status": "placed", "division": "poly_kalshi_mlb",
        "whale": "SDTrading", "whale_wallet": "0x16bb99",
        "action": "entry", "ticker": "KXMLBGAME-26AUG161340MIACIN-MIA",
        "side": "bid", "outcome": "yes", "count": 9, "stake_usd": 5.0,
        "order_type": "ioc", "tif": "immediate_or_cancel", "price": "0.5600",
        "confidence": 1.0, "dry_run": False,
        "order_id": "7000441c-mia", "fill_count": 9, "fill_price": 0.54, "fill_fee": 0.09,
    }
    p.update(over)
    return p


def test_open_trades_poly_kalshi_placed_renders_real_fill(fresh_db):
    """A real poly_kalshi_order 'placed' row (live fill #1) renders on OPEN with
    the REAL fill qty/price, not the requested count / limit price."""
    db_url, _ = fresh_db
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", _pk_order_payload())
    ots = wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100)
    assert len(ots) == 1
    ot = ots[0]
    assert ot.venue == "kalshi"
    assert ot.division == "poly_kalshi_mlb"
    assert ot.strategy == "poly_kalshi_mlb"
    assert ot.order_id == "7000441c-mia"
    assert ot.market_id.endswith("-MIA")
    assert ot.outcome_bet == "yes"
    assert ot.qty == 9.0                          # from fill_count
    assert ot.entry_price == 0.54                 # REAL fill, not the 0.56 limit
    assert ot.notional == pytest.approx(9 * 0.54)
    assert ot.whale_handle == "SDTrading"
    assert ot.arb_type == "poly_kalshi_copy"


def test_open_trades_poly_kalshi_three_live_fills(fresh_db):
    """All three real 2026-08-16 fills surface as distinct rows; #1 MIA and #2
    CIN are opposite sides of the SAME game (two whales disagreeing) and both
    render."""
    db_url, _ = fresh_db
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", _pk_order_payload(
        order_id="7000441c-mia", whale="SDTrading",
        ticker="KXMLBGAME-26AUG161340MIACIN-MIA", count=9, fill_count=9, fill_price=0.54))
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", _pk_order_payload(
        order_id="d4645fb2-cin", whale="0x0x23kj",
        ticker="KXMLBGAME-26AUG161340MIACIN-CIN", count=10, fill_count=10, fill_price=0.48))
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", _pk_order_payload(
        order_id="5eb8437f-az", whale="xifutloong3",
        ticker="KXMLBGAME-26AUG161335AZATL-AZ", count=10, fill_count=10, fill_price=0.47))
    ots = wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100)
    assert len(ots) == 3
    by_oid = {o.order_id: o for o in ots}
    assert by_oid["7000441c-mia"].market_id.endswith("-MIA")
    assert by_oid["d4645fb2-cin"].market_id.endswith("-CIN")   # opposite side, same game
    assert by_oid["5eb8437f-az"].qty == 10.0
    assert by_oid["5eb8437f-az"].entry_price == 0.47


def test_open_trades_poly_kalshi_resolved_drops_off_open(fresh_db):
    """Once a kalshi_round_trips row exists for the order_id (CP4 resolution),
    the entry leaves OPEN — LEFT JOIN makes r.order_id NOT NULL. This is why
    Flag-1 order_id persistence must land WITH CP3."""
    db_url, _ = fresh_db
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order",
                  _pk_order_payload(order_id="k-resolved"))
    assert len(wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100)) == 1
    _insert_kalshi_round_trip(db_url, order_id="k-resolved", division="poly_kalshi_mlb")
    assert wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100) == []


def test_open_trades_poly_kalshi_exit_and_blocked_excluded(fresh_db):
    """Only placed/would-place ENTRY rows are OPEN positions — exit rows and
    blocked/skip audit rows are not."""
    db_url, _ = fresh_db
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order",
                  _pk_order_payload(order_id="exit-1", action="exit", side="ask"))
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order",
                  _pk_order_payload(order_id="blk-1", status="blocked_slippage"))
    assert wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100) == []


def test_open_trades_poly_kalshi_dry_run_falls_back_to_requested(fresh_db):
    """A shadow/paper DRY_RUN_would_place row (no Flag-1 fill) still renders,
    using the requested count + limit price."""
    db_url, _ = fresh_db
    p = _pk_order_payload(status="DRY_RUN_would_place", dry_run=True)
    for k in ("order_id", "fill_count", "fill_price", "fill_fee"):
        p.pop(k)                                   # a dry-run journal carries no fill
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", p)
    ots = wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100)
    assert len(ots) == 1
    assert ots[0].qty == 9.0                       # requested count
    assert ots[0].entry_price == 0.56              # limit price string
    assert ots[0].order_id == ""


def test_open_trades_poly_kalshi_disjoint_from_arb_and_pm_views(fresh_db):
    """poly_kalshi rows must not bleed into a kalshi_ arb or polymarket_ view
    (disjoint slug prefix + different kind/actor)."""
    db_url, _ = fresh_db
    _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", _pk_order_payload())
    assert wd._query_pm_open_trades(db_url, ["kalshi_llm_arbitrage"], 100) == []
    assert wd._query_pm_open_trades(db_url, ["polymarket_arbitrage"], 100) == []


def test_pending_count_equals_open_list_poly_kalshi_three_fills(fresh_db):
    """badge == list: the OPEN badge (_query_pm_pending_count) reads the SAME
    count as the OPEN list length on the 3 real live fills — not 0."""
    db_url, _ = fresh_db
    for oid, whale, tkr, cnt, fp in [
        ("7000441c-mia", "SDTrading",   "KXMLBGAME-26AUG161340MIACIN-MIA", 9, 0.54),
        ("d4645fb2-cin", "0x0x23kj",    "KXMLBGAME-26AUG161340MIACIN-CIN", 10, 0.48),
        ("5eb8437f-az",  "xifutloong3", "KXMLBGAME-26AUG161335AZATL-AZ",   10, 0.47),
    ]:
        _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order", _pk_order_payload(
            order_id=oid, whale=whale, ticker=tkr, count=cnt, fill_count=cnt, fill_price=fp))
    n_list = len(wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100))
    n_badge = wd._query_pm_pending_count(db_url, ["poly_kalshi_mlb"])
    assert n_list == 3
    assert n_badge == n_list          # badge == list, not 0


def test_pending_count_tracks_list_when_one_resolves(fresh_db):
    """When one of the 3 resolves (a kalshi_round_trips row appears), badge AND
    list both drop to 2 in lockstep."""
    db_url, _ = fresh_db
    for oid in ("7000441c-mia", "d4645fb2-cin", "5eb8437f-az"):
        _insert_audit(db_url, "poly_kalshi_mlb", "poly_kalshi_order",
                      _pk_order_payload(order_id=oid))
    _insert_kalshi_round_trip(db_url, order_id="5eb8437f-az", division="poly_kalshi_mlb")
    n_list = len(wd._query_pm_open_trades(db_url, ["poly_kalshi_mlb"], 100))
    n_badge = wd._query_pm_pending_count(db_url, ["poly_kalshi_mlb"])
    assert n_list == 2 and n_badge == 2


# ── Poly->Kalshi copy (live) RESOLVED tiles + History (CP4) ──────────────────
def _pk_round_trip(db_url, **over):
    base = dict(division="poly_kalshi_mlb", strategy="poly_kalshi_mlb",
                arb_type="poly_kalshi_copy", outcome_bet="yes",
                entry_ts="2026-08-16T13:40:00+00:00", resolved_ts="2026-08-16T16:30:00+00:00")
    base.update(over)
    _insert_kalshi_round_trip(db_url, **base)


def test_resolved_tiles_and_history_populate_poly_kalshi(fresh_db):
    """CP4: composed poly_kalshi_mlb round-trips populate the resolved tiles
    (count / wins / realized) and the History list, sourced by division."""
    db_url, _ = fresh_db
    _pk_round_trip(db_url, order_id="mia", ticker="KXMLBGAME-A-MIA", event_title="MIA vs CIN",
                   qty=9.0, entry_price=0.54, notional=9 * 0.54, won=1, market_result="yes",
                   realized_pnl=9 * (1 - 0.54), roi_pct=85.2)
    _pk_round_trip(db_url, order_id="cin", ticker="KXMLBGAME-A-CIN", event_title="MIA vs CIN",
                   qty=10.0, entry_price=0.48, notional=10 * 0.48, won=0, market_result="no",
                   realized_pnl=-10 * 0.48, roi_pct=-100.0)
    stats = wd._query_pm_resolved_stats(db_url, ["poly_kalshi_mlb"])
    assert stats["n_resolved"] == 2
    assert stats["n_wins"] == 1
    assert stats["total_realized_pnl"] == pytest.approx(9 * (1 - 0.54) - 10 * 0.48)   # -0.66
    hist = wd._query_pm_round_trips(db_url, ["poly_kalshi_mlb"], 100)
    assert {h.order_id for h in hist} == {"mia", "cin"}
    mia = next(h for h in hist if h.order_id == "mia")
    assert mia.venue == "kalshi" and mia.division == "poly_kalshi_mlb"
    assert mia.outcome_bet == "yes" and mia.qty == 9.0 and mia.entry_price == 0.54
    assert mia.won == 1 and mia.realized_pnl == pytest.approx(9 * (1 - 0.54))
    assert mia.arb_type == "poly_kalshi_copy"


def test_resolved_poly_kalshi_not_bled_into_arb_view(fresh_db):
    """Resolved poly_kalshi rows are scoped by division -- they do not appear under a
    kalshi_ arb division view."""
    db_url, _ = fresh_db
    _pk_round_trip(db_url, order_id="mia", ticker="KXMLBGAME-A-MIA", won=1,
                   realized_pnl=4.14)
    assert wd._query_pm_resolved_stats(db_url, ["kalshi_llm_arbitrage"])["n_resolved"] == 0
    assert wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100) == []


# ── Kalshi division agent_state metrics-epoch (CP5) ─────────────────────────
def test_get_kalshi_division_epoch_unset_is_none(fresh_db):
    db_url, _ = fresh_db
    assert wd._get_kalshi_division_epoch(db_url, "poly_kalshi_mlb") is None


def test_get_kalshi_division_epoch_reads_agent_state(fresh_db):
    db_url, _ = fresh_db
    _db.set_agent_state("poly_kalshi_mlb", "metrics_epoch",
                        "2026-08-15T00:00:00+00:00", db_url=db_url)
    assert wd._get_kalshi_division_epoch(db_url, "poly_kalshi_mlb") == "2026-08-15T00:00:00+00:00"


def _pk_rt(db_url, oid, entry_ts):
    _insert_kalshi_round_trip(
        db_url, order_id=oid, ticker=f"KXMLBGAME-{oid}", division="poly_kalshi_mlb",
        strategy="poly_kalshi_mlb", arb_type="poly_kalshi_copy", outcome_bet="yes",
        entry_ts=entry_ts, resolved_ts=entry_ts, won=1, market_result="yes",
        realized_pnl=1.0)


def test_kalshi_division_epoch_filters_and_is_reversible(fresh_db):
    """Set agent_state epoch -> pre-epoch rows drop from History + tiles; delete it
    -> all rows show again (rows never deleted). Runtime, reversible, per-division."""
    db_url, _ = fresh_db
    _pk_rt(db_url, "pre", "2026-08-10T12:00:00+00:00")
    _pk_rt(db_url, "post", "2026-08-16T12:00:00+00:00")
    # unset -> both show (poly_kalshi_mlb has no hardcoded cutoff)
    assert {r.order_id for r in wd._query_pm_round_trips(db_url, ["poly_kalshi_mlb"], 100)} == {"pre", "post"}
    assert wd._query_pm_resolved_stats(db_url, ["poly_kalshi_mlb"])["n_resolved"] == 2
    # set epoch 2026-08-15 -> only 'post' survives (History AND tiles)
    _db.set_agent_state("poly_kalshi_mlb", "metrics_epoch", "2026-08-15T00:00:00+00:00", db_url=db_url)
    assert {r.order_id for r in wd._query_pm_round_trips(db_url, ["poly_kalshi_mlb"], 100)} == {"post"}
    assert wd._query_pm_resolved_stats(db_url, ["poly_kalshi_mlb"])["n_resolved"] == 1
    # reversible: delete the row -> both show again
    with _db.connect(db_url) as c:
        c.execute("DELETE FROM agent_state WHERE agent='poly_kalshi_mlb' AND key='metrics_epoch'")
    assert {r.order_id for r in wd._query_pm_round_trips(db_url, ["poly_kalshi_mlb"], 100)} == {"pre", "post"}


def test_kalshi_division_epoch_overrides_hardcoded_cutoff(fresh_db):
    """agent_state epoch takes precedence over DASHBOARD_RT_CUTOFFS: an EARLIER
    agent_state epoch un-hides a row the hardcoded 2026-07-07 cutoff would hide."""
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(
        db_url, order_id="llm-old", division="kalshi_llm_arbitrage",
        strategy="kalshi_llm_arbitrage", entry_ts="2026-05-11T00:00:00+00:00",
        resolved_ts="2026-05-11T00:00:00+00:00")
    assert wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100) == []   # hardcoded hides it
    _db.set_agent_state("kalshi_llm_arbitrage", "metrics_epoch",
                        "2026-01-01T00:00:00+00:00", db_url=db_url)
    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert [r.order_id for r in rts] == ["llm-old"]   # agent_state override wins


def test_existing_kalshi_division_cutoff_unaffected_without_agent_state(fresh_db):
    """No agent_state epoch -> kalshi_llm_arbitrage keeps its hardcoded 2026-07-07
    cutoff: pre-cutoff hidden, post-cutoff shown. Proves the fix is real (the cutoff
    still filters), not a hidden/disabled cutoff."""
    db_url, _ = fresh_db
    _insert_kalshi_round_trip(
        db_url, order_id="llm-pre", division="kalshi_llm_arbitrage",
        strategy="kalshi_llm_arbitrage", entry_ts="2026-05-11T00:00:00+00:00",
        resolved_ts="2026-05-11T00:00:00+00:00")
    _insert_kalshi_round_trip(
        db_url, order_id="llm-post", division="kalshi_llm_arbitrage",
        strategy="kalshi_llm_arbitrage", entry_ts="2026-08-11T00:00:00+00:00",
        resolved_ts="2026-08-11T00:00:00+00:00")
    rts = wd._query_pm_round_trips(db_url, ["kalshi_llm_arbitrage"], 100)
    assert [r.order_id for r in rts] == ["llm-post"]   # pre-cutoff still hidden
