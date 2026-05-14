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
        "entry_ts": "2026-05-11T02:00:00+00:00",
        "resolved_ts": "2026-05-11T03:00:00+00:00",
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
        "entry_ts": "2026-05-11T01:00:00+00:00",
        "resolved_ts": "2026-05-11T03:30:00+00:00",
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
        ts="2026-05-11T01:00:00+00:00",
    )
    _insert_audit(
        db_url, "polymarket_arbitrage", "would_have_placed",
        {"order_id": "p-new", "condition_id": "0x", "market_slug": "p-new",
         "outcome": "yes", "qty": 1.0, "limit_price": 0.5},
        ts="2026-05-11T03:00:00+00:00",
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
