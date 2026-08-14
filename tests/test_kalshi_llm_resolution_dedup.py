"""Tests for kalshi_llm_arbitrage distinct-market (Option B) dedup logic.

Network-free, no live DB.  Exercises:
  (a) _query_kalshi_distinct_market_stats returns n_resolved == COUNT(DISTINCT
      ticker), not emission count.
  (b) Canonical row per ticker = earliest entry_ts emission (tie-break by id).
  (c) event_ticker over-collapses vs ticker — function MUST use ticker.
  (d) Option-A (_query_pm_resolved_stats) is unchanged — still per-emission.
  (e) _fetch_unresolved_orders epoch-scopes kalshi_llm_arbitrage to
      post-2026-07-07; non-llm actors are NOT epoch-filtered.
  (f) Outcome ground-truth: CPI/SARB/BoK seed mirrors real 10-emission set;
      B reports n_resolved=3 markets, n_wins=2 (CPI+SARB), 1 loss (BoK).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import timezone

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web.data import (
    _query_kalshi_distinct_market_stats,
    _query_pm_resolved_stats,
)
from trading_corp.agents import kalshi_resolver as kr


# ── shared helpers ─────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "dedup_test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url, db_path


def _insert_krt(db_url: str, **overrides) -> None:
    """Insert one kalshi_round_trips row.  Minimal defaults; pass overrides."""
    row = {
        "order_id": "k-default",
        "ticker": "KXDEFAULT-1",
        "event_ticker": "KXDEFAULT",
        "event_title": "Default event",
        "category": "Economics",
        "strategy": "kalshi_llm_arbitrage",
        "division": "kalshi_llm_arbitrage",
        "arb_type": "llm_divergence",
        "arb_set_id": None,
        "outcome_bet": "no",
        "qty": 1.0,
        "entry_price": 0.50,
        "notional": 0.50,
        "entry_ts": "2026-07-10T12:00:00+00:00",
        "resolved_ts": "2026-07-15T12:00:00+00:00",
        "market_result": "no",
        "won": 1,
        "realized_pnl": 0.50,
        "roi_pct": 100.0,
        "implied_at_entry": None,
        "llm_prob": None,
        "divergence_pct": None,
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


def _insert_audit(db_url: str, actor: str, kind: str, payload: dict,
                  ts: str | None = None) -> None:
    from datetime import datetime
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            (ts, actor, kind, json.dumps(payload)),
        )


# ── (a) Option-B collapses per-emission to per-ticker count ────────────


def test_b_distinct_market_count(fresh_db):
    """n_resolved == COUNT(DISTINCT ticker), not len(emissions).

    Representative post-epoch seed:
      2 Treasury tickers (KX2YFOMC-26JUL29-T10 x3, -T8 x2)  → 2 markets
      7 Michigan tickers (KXPRIMARYTURNOUT-SENATEMID26-A through -G, x2 each) → 7 markets
      2 additional tickers x1 each                             → 2 markets
    Total emissions = 3+2+14+2 = 21, distinct tickers = 2+7+2 = 11.
    """
    db_url, _ = fresh_db
    division = "kalshi_llm_arbitrage"
    ts_base = "2026-07-10T12:00:00+00:00"

    treasury_tickers = [
        "KX2YFOMC-26JUL29-T10",
        "KX2YFOMC-26JUL29-T8",
    ]
    michigan_tickers = [
        f"KXPRIMARYTURNOUT-SENATEMID26-{c}"
        for c in "ABCDEFG"
    ]
    extra_tickers = ["KXCPICOMBO-26JUN-0202", "KXCBDSA-26JUL23-H25"]

    # Treasury T10: 3 emissions, shared event_ticker
    for i in range(3):
        _insert_krt(db_url,
            order_id=f"t10-{i}", ticker=treasury_tickers[0],
            event_ticker="KX2YFOMC-26JUL29",
            entry_ts=f"2026-07-10T{12+i:02d}:00:00+00:00",
            division=division, won=1, market_result="no", realized_pnl=0.5)
    # Treasury T8: 2 emissions, same event_ticker — but a DIFFERENT ticker
    for i in range(2):
        _insert_krt(db_url,
            order_id=f"t8-{i}", ticker=treasury_tickers[1],
            event_ticker="KX2YFOMC-26JUL29",
            entry_ts=f"2026-07-11T{12+i:02d}:00:00+00:00",
            division=division, won=1, market_result="no", realized_pnl=0.5)
    # Michigan: 7 tickers x 2 emissions each
    for j, mkt in enumerate(michigan_tickers):
        for i in range(2):
            _insert_krt(db_url,
                order_id=f"mich-{j}-{i}", ticker=mkt,
                event_ticker="KXPRIMARYTURNOUT-SENATEMID26",
                entry_ts=f"2026-07-12T{j:02d}:{i*10:02d}:00+00:00",
                division=division, won=0, market_result="yes", realized_pnl=-0.5)
    # Extra: 2 unique tickers, 1 emission each
    for i, tk in enumerate(extra_tickers):
        _insert_krt(db_url,
            order_id=f"ex-{i}", ticker=tk,
            event_ticker=f"KXEXTRA-{i}",
            entry_ts=f"2026-07-13T{i*2:02d}:00:00+00:00",
            division=division, won=1, market_result="no", realized_pnl=0.5)

    total_emissions = 3 + 2 + 7 * 2 + 2   # = 21
    distinct_tickers = 2 + 7 + 2            # = 11

    # Verify seed shape.
    with _db.connect(db_url) as conn:
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM kalshi_round_trips WHERE division=?", (division,)
        ).fetchone()[0]
        n_dist = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM kalshi_round_trips WHERE division=?", (division,)
        ).fetchone()[0]
    assert n_rows == total_emissions
    assert n_dist == distinct_tickers

    dm = _query_kalshi_distinct_market_stats(db_url, [division])
    assert dm["n_resolved"] == distinct_tickers, (
        f"Expected {distinct_tickers} distinct markets, got {dm['n_resolved']}"
    )
    # Treasury contributes 2, Michigan 7.
    assert dm["n_resolved"] >= 9   # 2 Treasury + 7 Michigan at minimum


# ── (b) Canonical = earliest entry_ts ──────────────────────────────────


def test_b_canonical_is_earliest_entry_ts(fresh_db):
    """Three emissions for one ticker at (t0=$0.58 win), (t1=$0.30 loss),
    (t2=$0.063 loss).  Option-B must use the t0 emission's won/realized_pnl,
    NOT the latest or any other.  (Florida-52 analog: first_px 0.58.)
    """
    db_url, _ = fresh_db
    ticker = "KXFL-52-YES"
    division = "kalshi_llm_arbitrage"

    # t0: first emission — the canonical one; price=0.58, won, pnl = qty*(1-0.58)
    qty0 = 10.0
    price0 = 0.58
    pnl0 = qty0 * (1.0 - price0)   # 4.20
    _insert_krt(db_url,
        order_id="fl52-t0", ticker=ticker, event_ticker="KXFL-52",
        entry_price=price0, qty=qty0, notional=qty0*price0,
        entry_ts="2026-07-08T10:00:00+00:00",
        market_result="yes", won=1, realized_pnl=pnl0,
        division=division)

    # t1: second emission — loss, price=0.30
    qty1 = 5.0
    price1 = 0.30
    pnl1 = -qty1 * price1   # -1.50
    _insert_krt(db_url,
        order_id="fl52-t1", ticker=ticker, event_ticker="KXFL-52",
        entry_price=price1, qty=qty1, notional=qty1*price1,
        entry_ts="2026-07-09T10:00:00+00:00",
        market_result="no", won=0, realized_pnl=pnl1,
        division=division)

    # t2: third emission — loss, price=0.063
    qty2 = 20.0
    price2 = 0.063
    pnl2 = -qty2 * price2   # -1.26
    _insert_krt(db_url,
        order_id="fl52-t2", ticker=ticker, event_ticker="KXFL-52",
        entry_price=price2, qty=qty2, notional=qty2*price2,
        entry_ts="2026-07-10T10:00:00+00:00",
        market_result="no", won=0, realized_pnl=pnl2,
        division=division)

    dm = _query_kalshi_distinct_market_stats(db_url, [division])
    # One distinct ticker → n_resolved=1.
    assert dm["n_resolved"] == 1
    # The canonical emission is t0 (earliest) → won=1.
    assert dm["n_wins"] == 1
    # pnl == t0's realized_pnl, not t1 or t2.
    assert abs(dm["total_realized_pnl"] - pnl0) < 1e-6, (
        f"Expected canonical pnl≈{pnl0:.4f} (t0 earliest), "
        f"got {dm['total_realized_pnl']:.4f}"
    )


# ── (c) event_ticker over-collapses vs ticker ───────────────────────────


def test_c_event_ticker_over_collapses(fresh_db):
    """COUNT(DISTINCT event_ticker) < COUNT(DISTINCT ticker) on the Treasury seed.

    T10 and T8 share event_ticker 'KX2YFOMC-26JUL29' but have distinct tickers.
    The function uses ticker (not event_ticker) so n_resolved == ticker count.
    """
    db_url, _ = fresh_db
    division = "kalshi_llm_arbitrage"

    _insert_krt(db_url,
        order_id="t10-x", ticker="KX2YFOMC-26JUL29-T10",
        event_ticker="KX2YFOMC-26JUL29",
        entry_ts="2026-07-10T10:00:00+00:00",
        division=division, won=1, market_result="no", realized_pnl=0.5)
    _insert_krt(db_url,
        order_id="t10-y", ticker="KX2YFOMC-26JUL29-T10",
        event_ticker="KX2YFOMC-26JUL29",
        entry_ts="2026-07-11T10:00:00+00:00",
        division=division, won=1, market_result="no", realized_pnl=0.5)
    _insert_krt(db_url,
        order_id="t8-x", ticker="KX2YFOMC-26JUL29-T8",
        event_ticker="KX2YFOMC-26JUL29",
        entry_ts="2026-07-12T10:00:00+00:00",
        division=division, won=0, market_result="yes", realized_pnl=-0.5)

    with _db.connect(db_url) as conn:
        n_event = conn.execute(
            "SELECT COUNT(DISTINCT event_ticker) FROM kalshi_round_trips WHERE division=?",
            (division,),
        ).fetchone()[0]
        n_ticker = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM kalshi_round_trips WHERE division=?",
            (division,),
        ).fetchone()[0]

    # event_ticker collapses T10+T8 into one event → n_event=1, n_ticker=2.
    assert n_event < n_ticker, (
        f"Expected event_ticker ({n_event}) < ticker ({n_ticker}); "
        "event_ticker over-collapses distinct strikes"
    )

    dm = _query_kalshi_distinct_market_stats(db_url, [division])
    # Function must use ticker → 2 markets.
    assert dm["n_resolved"] == n_ticker, (
        f"Function returned {dm['n_resolved']} but should equal ticker count {n_ticker}"
    )


# ── (d) Option-A (_query_pm_resolved_stats) unchanged ──────────────────


def test_d_option_a_counts_all_emissions(fresh_db):
    """_query_pm_resolved_stats still returns per-emission count (Option A
    intact, not collapsed).
    """
    db_url, _ = fresh_db
    division = "kalshi_llm_arbitrage"
    ticker = "KXSARB-26JUL23-H25"

    for i in range(7):
        _insert_krt(db_url,
            order_id=f"sarb-{i}", ticker=ticker,
            event_ticker="KXSARB-26JUL23",
            entry_ts=f"2026-07-10T{i:02d}:00:00+00:00",
            division=division, won=1, market_result="no", realized_pnl=0.5)

    # Option A: all 7 emissions.
    a = _query_pm_resolved_stats(db_url, [division])
    assert a["n_resolved"] == 7, (
        f"Option A should count all 7 emissions, got {a['n_resolved']}"
    )
    assert a["n_wins"] == 7

    # Option B: 1 distinct market.
    dm = _query_kalshi_distinct_market_stats(db_url, [division])
    assert dm["n_resolved"] == 1, (
        f"Option B should collapse to 1 distinct market, got {dm['n_resolved']}"
    )
    assert dm["n_wins"] == 1


# ── (e) Epoch-scope of _fetch_unresolved_orders ─────────────────────────


def test_e_epoch_scope_kalshi_llm(fresh_db):
    """_fetch_unresolved_orders epoch-scopes kalshi_llm_arbitrage to
    a.ts >= '2026-07-07T16:40:00+00:00'.  Pre-epoch LLM rows must NOT be
    returned; post-epoch LLM rows must be returned.

    Non-LLM actor (kalshi_tail_price_arb) is NOT epoch-filtered — even
    pre-epoch rows must be returned.
    """
    db_url, _ = fresh_db

    # Pre-epoch kalshi_llm row — must be excluded.
    _insert_audit(db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "llm-pre", "ticker": "KXPRE-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.40},
        ts="2026-07-07T10:00:00+00:00")   # before 16:40

    # Post-epoch kalshi_llm row — must be returned.
    _insert_audit(db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "llm-post", "ticker": "KXPOST-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.40},
        ts="2026-07-08T10:00:00+00:00")

    # Pre-epoch kalshi_tail_price_arb row — NOT epoch-filtered for this actor.
    _insert_audit(db_url, "kalshi_tail_price_arb", "would_have_placed",
        {"order_id": "tail-pre", "ticker": "KXTAIL-1", "leg": "yes",
         "qty": 1.0, "limit_price": 0.40},
        ts="2026-07-07T00:00:00+00:00")   # well before cutoff

    rows = kr._fetch_unresolved_orders(db_url, max_per_actor=50)
    order_ids = {r.get("order_id") for r in rows}

    # Post-epoch LLM present.
    assert "llm-post" in order_ids, "post-epoch kalshi_llm row must be returned"
    # Pre-epoch LLM excluded.
    assert "llm-pre" not in order_ids, "pre-epoch kalshi_llm row must NOT be returned"
    # Non-LLM actor pre-epoch present (no epoch filter for this actor).
    assert "tail-pre" in order_ids, "kalshi_tail_price_arb pre-epoch row must NOT be epoch-filtered"


# ── (f) Outcome ground-truth: CPI+SARB+BoK ─────────────────────────────


def test_f_outcome_ground_truth_cpi_sarb_bok(fresh_db):
    """Seed the stuck-3 tickers as resolved round-trips and verify Option-B
    reports 3 markets, 2 wins (CPI + SARB), 1 loss (BoK).

    Per-emission counts: CPI x1, SARB x7, BoK x2 = 10 Option-A rows.
    Per-market (Option-B): 3 tickers.  Win-rate 2/3 ≈ 66.7% — NOT diluted
    by the 7 SARB emissions.
    """
    db_url, _ = fresh_db
    division = "kalshi_llm_arbitrage"

    CPI_TICKER  = "KXCPICOMBO-26JUN-0202"
    SARB_TICKER = "KXCBDSA-26JUL23-H25"
    BOK_TICKER  = "KXCBDECISIONKOREA-26JUL15-H25"

    # CPI: 1 emission, result=no, won=1.
    _insert_krt(db_url,
        order_id="cpi-0", ticker=CPI_TICKER,
        event_ticker="KXCPICOMBO-26JUN",
        entry_ts="2026-07-10T08:00:00+00:00",
        division=division, won=1, market_result="no",
        qty=10.0, entry_price=0.30, notional=3.0,
        realized_pnl=10.0 * (1.0 - 0.30))

    # SARB: 7 emissions, result=no, won=1 each.
    for i in range(7):
        _insert_krt(db_url,
            order_id=f"sarb-{i}", ticker=SARB_TICKER,
            event_ticker="KXCBDSA-26JUL23",
            entry_ts=f"2026-07-1{i+1}T08:00:00+00:00",
            division=division, won=1, market_result="no",
            qty=5.0, entry_price=0.20, notional=1.0,
            realized_pnl=5.0 * (1.0 - 0.20))

    # BoK: 2 emissions, result=yes, won=0 (we bet NO, lost).
    for i in range(2):
        _insert_krt(db_url,
            order_id=f"bok-{i}", ticker=BOK_TICKER,
            event_ticker="KXCBDECISIONKOREA-26JUL15",
            entry_ts=f"2026-07-15T0{i}:00:00+00:00",
            division=division, won=0, market_result="yes",
            qty=8.0, entry_price=0.40, notional=3.2,
            realized_pnl=-8.0 * 0.40)

    # Option A: 10 emission rows.
    a = _query_pm_resolved_stats(db_url, [division])
    assert a["n_resolved"] == 10, f"Option A: expected 10 emissions, got {a['n_resolved']}"
    # 7 SARB wins + 1 CPI win + 0 BoK wins = 8 total wins.
    assert a["n_wins"] == 8,  f"Option A: expected 8 wins (7 SARB + 1 CPI + 0 BoK), got {a['n_wins']}"

    # Option B: 3 distinct markets.
    dm = _query_kalshi_distinct_market_stats(db_url, [division])
    assert dm["n_resolved"] == 3, (
        f"Option B: expected 3 distinct markets (CPI/SARB/BoK), got {dm['n_resolved']}"
    )
    # CPI canonical=only emission → won=1.
    # SARB canonical=first emission (sarb-0) → won=1.
    # BoK canonical=first emission (bok-0) → won=0.
    assert dm["n_wins"] == 2, (
        f"Option B: expected 2 market-wins (CPI+SARB), got {dm['n_wins']}"
    )
    # 1 loss (BoK), 0 voids.
    n_losses_m = dm["n_resolved"] - dm["n_wins"] - dm["n_voids"]
    assert n_losses_m == 1, (
        f"Option B: expected 1 market-loss (BoK), got {n_losses_m}"
    )
    assert dm["n_voids"] == 0

    # Win-rate at the market level: 2/3, not 9/10.
    decisive = dm["n_wins"] + n_losses_m
    win_rate = 100.0 * dm["n_wins"] / decisive if decisive > 0 else None
    assert win_rate is not None
    assert abs(win_rate - (200.0 / 3.0)) < 0.01, (
        f"Option B win-rate should be 2/3 ≈ 66.67%, got {win_rate:.2f}%"
    )
