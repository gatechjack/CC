"""Tests for trading_corp.agents.kalshi_resolver.

Network-free. Exercises:
  - schema migration creates kalshi_round_trips + kalshi_equity_history
  - side detection across outcome/leg fields for all three Kalshi strategies
  - P&L math: win, loss, void, malformed
  - INSERT OR IGNORE on order_id (re-run safety)
  - equity_snapshot write shape
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trading_corp.agents import kalshi_resolver as kr
from trading_corp.persistence import db as _db


# ── helpers ────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "kalshi_resolver_test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url, db_path


def _insert_audit_event(db_url: str, actor: str, kind: str, payload: dict,
                        ts: str | None = None):
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            (ts, actor, kind, json.dumps(payload)),
        )


class _StubKalshiBroker:
    """Returns a canned resolution map keyed on ticker. Snapshot returns
    a configurable AccountSnapshot-like SimpleNamespace."""

    def __init__(self, resolutions: dict[str, dict],
                 snap: SimpleNamespace | None = None,
                 raise_on: set[str] | None = None) -> None:
        self.resolutions = resolutions
        self.snap = snap
        self.raise_on = raise_on or set()
        self.calls: list[str] = []

    async def get_market_resolution(self, ticker: str) -> dict:
        self.calls.append(ticker)
        if ticker in self.raise_on:
            raise RuntimeError("simulated broker error")
        return self.resolutions.get(
            ticker,
            {"status": "not_found", "result": None, "ticker": ticker, "close_time": ""},
        )

    async def snapshot(self):
        if self.snap is None:
            raise RuntimeError("snapshot not configured")
        return self.snap


# ── schema ─────────────────────────────────────────────────────────────


def test_schema_creates_round_trips_and_equity_tables(fresh_db):
    db_url, db_path = fresh_db
    with sqlite3.connect(db_path) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "kalshi_round_trips" in names
    assert "kalshi_equity_history" in names


# ── side detection ─────────────────────────────────────────────────────


def test_detect_side_llm_outcome_yes():
    assert kr._detect_side({"outcome": "yes"}) == "yes"


def test_detect_side_llm_outcome_no():
    assert kr._detect_side({"outcome": "NO"}) == "no"


def test_detect_side_tail_leg_yes():
    assert kr._detect_side({"leg": "yes"}) == "yes"


def test_detect_side_tail_leg_no():
    assert kr._detect_side({"leg": "no"}) == "no"


def test_detect_side_temporal_bucket_leg_prefix():
    assert kr._detect_side({"leg": "yes_KXABC-Q1-2026"}) == "yes"
    assert kr._detect_side({"leg": "no_KXABC-Q1-2026"}) == "no"


def test_detect_side_missing_returns_none():
    assert kr._detect_side({}) is None
    assert kr._detect_side({"outcome": "maybe"}) is None
    assert kr._detect_side({"leg": "weird"}) is None


# ── P&L math ───────────────────────────────────────────────────────────


def _baseline_row(**overrides) -> dict:
    row = {
        "_ts": "2026-05-11T01:00:00+00:00",
        "_actor": "kalshi_llm_arbitrage",
        "order_id": "ord-1",
        "ticker": "KXFOO-26MAY-1",
        "event_ticker": "KXFOO-26MAY",
        "event_title": "Foo above bar in May 2026?",
        "category": "Climate",
        "outcome": "yes",
        "qty": 10.0,
        "limit_price": 0.40,
        "implied_prob_at_entry": 0.50,
        "llm_prob_estimate": 0.65,
        "divergence_pct": 15.0,
    }
    row.update(overrides)
    return row


def test_compute_yes_bet_wins():
    row = _baseline_row()
    res = {"status": "resolved", "result": "yes"}
    rt = kr._compute_round_trip_row(row, res)
    assert rt is not None
    assert rt["outcome_bet"] == "yes"
    assert rt["market_result"] == "yes"
    assert rt["won"] == 1
    # qty=10 @ 0.40 → notional 4.0; payout 10*1=$10; pnl=10-4=6.0
    assert rt["notional"] == pytest.approx(4.0)
    assert rt["realized_pnl"] == pytest.approx(6.0)
    assert rt["roi_pct"] == pytest.approx(150.0)


def test_compute_yes_bet_loses():
    row = _baseline_row()
    res = {"status": "resolved", "result": "no"}
    rt = kr._compute_round_trip_row(row, res)
    assert rt is not None
    assert rt["won"] == 0
    # entire notional lost: pnl = -4.0
    assert rt["realized_pnl"] == pytest.approx(-4.0)
    assert rt["roi_pct"] == pytest.approx(-100.0)


def test_compute_no_bet_wins():
    row = _baseline_row(outcome="no", qty=20.0, limit_price=0.05)
    res = {"status": "resolved", "result": "no"}
    rt = kr._compute_round_trip_row(row, res)
    # notional=1.0; payout=20.0; pnl=19.0; roi=1900%
    assert rt["won"] == 1
    assert rt["notional"] == pytest.approx(1.0)
    assert rt["realized_pnl"] == pytest.approx(19.0)


def test_compute_void_market_zero_pnl():
    row = _baseline_row()
    res = {"status": "void", "result": "void"}
    rt = kr._compute_round_trip_row(row, res)
    assert rt is not None
    assert rt["market_result"] == "void"
    assert rt["won"] == 0
    assert rt["realized_pnl"] == 0.0
    assert rt["roi_pct"] == 0.0


def test_compute_skips_pending():
    row = _baseline_row()
    assert kr._compute_round_trip_row(row, {"status": "pending"}) is None


def test_compute_skips_malformed_price():
    assert kr._compute_round_trip_row(
        _baseline_row(limit_price=0.0), {"status": "resolved", "result": "yes"}
    ) is None
    assert kr._compute_round_trip_row(
        _baseline_row(limit_price=1.0), {"status": "resolved", "result": "yes"}
    ) is None
    assert kr._compute_round_trip_row(
        _baseline_row(qty=0), {"status": "resolved", "result": "yes"}
    ) is None


def test_compute_carries_kalshi_specific_fields():
    row = _baseline_row(
        _actor="kalshi_temporal_bucket_arb",
        kalshi_arb_type="bucket",
        kalshi_arb_set_id="set-abc",
        edge_cents=89.4,
        outcome=None,
        leg="yes_KXFOO-Q1",
    )
    rt = kr._compute_round_trip_row(row, {"status": "resolved", "result": "yes"})
    assert rt["strategy"] == "kalshi_temporal_bucket_arb"
    assert rt["division"] == "kalshi_arbitrage"
    assert rt["arb_type"] == "bucket"
    assert rt["arb_set_id"] == "set-abc"
    assert rt["edge_cents"] == 89.4
    assert rt["outcome_bet"] == "yes"


def test_compute_division_falls_back_for_tail_strategy():
    row = _baseline_row(_actor="kalshi_tail_price_arb", outcome=None, leg="no")
    rt = kr._compute_round_trip_row(row, {"status": "resolved", "result": "no"})
    assert rt["division"] == "kalshi_arbitrage"
    assert rt["arb_type"] == "tail"
    assert rt["outcome_bet"] == "no"


# ── resolver loop ──────────────────────────────────────────────────────


def test_resolve_pending_inserts_and_ignores_repeats(fresh_db):
    db_url, db_path = fresh_db

    _insert_audit_event(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {
            "order_id": "ord-1", "ticker": "KXFOO-1",
            "event_ticker": "KXFOO", "event_title": "Foo?",
            "category": "Climate", "outcome": "yes",
            "qty": 10.0, "limit_price": 0.30,
            "implied_prob_at_entry": 0.4, "llm_prob_estimate": 0.6,
            "divergence_pct": 20.0,
        },
    )
    broker = _StubKalshiBroker({
        "KXFOO-1": {"status": "resolved", "result": "yes",
                    "ticker": "KXFOO-1", "close_time": ""},
    })

    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["scanned"] == 1
    assert counts["resolved"] == 1

    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute("SELECT order_id, won, realized_pnl FROM kalshi_round_trips"))
    assert len(rows) == 1
    assert rows[0][0] == "ord-1" and rows[0][1] == 1
    assert rows[0][2] == pytest.approx(7.0)  # 10 * (1 - 0.3)

    # Re-run: row already in kalshi_round_trips → resolver skips via LEFT JOIN.
    # We should NOT call broker again (the fetch filters it out).
    broker.calls.clear()
    counts2 = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts2["scanned"] == 0
    assert broker.calls == []


def test_resolve_skips_pending_and_not_found(fresh_db):
    db_url, _ = fresh_db
    _insert_audit_event(
        db_url, "kalshi_temporal_bucket_arb", "would_have_placed",
        {"order_id": "ord-pending", "ticker": "KXP-1", "leg": "yes_KXP-1",
         "qty": 10.0, "limit_price": 0.20, "kalshi_arb_type": "temporal"},
    )
    _insert_audit_event(
        db_url, "kalshi_tail_price_arb", "would_have_placed",
        {"order_id": "ord-missing", "ticker": "KXM-1", "leg": "yes",
         "qty": 5.0, "limit_price": 0.05},
    )
    broker = _StubKalshiBroker({
        "KXP-1": {"status": "pending", "result": None, "ticker": "KXP-1", "close_time": ""},
        # KXM-1 not in dict → default not_found.
    })
    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["scanned"] == 2
    assert counts["pending"] == 1
    assert counts["not_found"] == 1
    assert counts["resolved"] == 0


def test_resolve_handles_broker_exception(fresh_db):
    db_url, _ = fresh_db
    _insert_audit_event(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "ord-err", "ticker": "KXE-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.50},
    )
    broker = _StubKalshiBroker({}, raise_on={"KXE-1"})
    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["errors"] == 1
    assert counts["resolved"] == 0


def test_resolve_void_writes_zero_pnl_row(fresh_db):
    db_url, db_path = fresh_db
    _insert_audit_event(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "ord-v", "ticker": "KXV-1", "outcome": "yes",
         "qty": 7.0, "limit_price": 0.40},
    )
    broker = _StubKalshiBroker({
        "KXV-1": {"status": "void", "result": "void", "ticker": "KXV-1", "close_time": ""},
    })
    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["void"] == 1
    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute(
            "SELECT market_result, won, realized_pnl FROM kalshi_round_trips"
        ))
    assert rows == [("void", 0, 0.0)]


# ── live copy placement booking (kalshi_copy_placed_live) ──────────────


def test_resolve_books_live_copy_placed_live_row(fresh_db):
    """Live copies emit kind='kalshi_copy_placed_live' (not 'would_have_placed')
    and carry fill_qty + fill_price (the outcome-leg cost after the kalshi_live
    FIX-1 YES->leg inversion). They must be scanned and booked into
    kalshi_round_trips on settlement with contract-correct PnL — a winning and a
    losing NO outcome. The wrong-units top-level qty/limit_price (the prod
    $163.84-bug fields) must be IGNORED in favor of fill_qty/fill_price."""
    db_url, db_path = fresh_db
    # NO 166 contracts @ leg-cost 0.013 (the corrected prod position).
    _insert_audit_event(
        db_url, "kalshi_copy_trader", "kalshi_copy_placed_live",
        {"order_id": "live-win", "ticker": "KXWIN-1", "outcome": "no",
         "side": "buy", "division": "kalshi_copy_trading",
         "fill_qty": 166.0, "fill_price": 0.013, "leg_priced": True,
         "qty": 163.84, "limit_price": 0.987},   # wrong-units — must be ignored
    )
    _insert_audit_event(
        db_url, "kalshi_copy_trader", "kalshi_copy_placed_live",
        {"order_id": "live-loss", "ticker": "KXLOSS-1", "outcome": "no",
         "side": "buy", "division": "kalshi_copy_trading",
         "fill_qty": 166.0, "fill_price": 0.013, "leg_priced": True,
         "qty": 163.84, "limit_price": 0.987},
    )
    broker = _StubKalshiBroker({
        "KXWIN-1": {"status": "resolved", "result": "no",
                    "ticker": "KXWIN-1", "close_time": ""},
        "KXLOSS-1": {"status": "resolved", "result": "yes",
                     "ticker": "KXLOSS-1", "close_time": ""},
    })
    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["resolved"] == 2, counts

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["order_id"]: r for r in conn.execute(
            "SELECT order_id, qty, entry_price, notional, won, realized_pnl, "
            "division, strategy, arb_type FROM kalshi_round_trips"
        )}
    assert set(rows) == {"live-win", "live-loss"}

    win = rows["live-win"]
    assert win["qty"] == pytest.approx(166.0)             # contracts, not USD 163.84
    assert win["entry_price"] == pytest.approx(0.013)     # leg cost, not 0.987
    assert win["won"] == 1
    assert win["realized_pnl"] == pytest.approx(166.0 * (1.0 - 0.013))  # NO wins
    assert win["notional"] == pytest.approx(166.0 * 0.013)
    assert win["division"] == "kalshi_copy_trading"
    assert win["strategy"] == "kalshi_copy_trader"
    assert win["arb_type"] == "copy_trade"

    loss = rows["live-loss"]
    assert loss["won"] == 0
    assert loss["realized_pnl"] == pytest.approx(-166.0 * 0.013)  # ≈ -2.16, not -163.84
    assert loss["notional"] == pytest.approx(166.0 * 0.013)


def test_pre_fix_live_copy_without_leg_priced_is_skipped(fresh_db):
    """A pre-FIX live copy row (no `leg_priced` flag) carries a YES-centric
    fill_price (e.g. NO @ 0.987) that would mis-book as a $163.84 phantom. Such
    rows must be SKIPPED entirely — not booked with the poisoned price — so the
    resolver never backfills the 4 pre-fix prod trades wrong."""
    db_url, db_path = fresh_db
    _insert_audit_event(
        db_url, "kalshi_copy_trader", "kalshi_copy_placed_live",
        {"order_id": "prefix-no", "ticker": "KXPRE-1", "outcome": "no",
         "side": "buy", "division": "kalshi_copy_trading",
         "fill_qty": 166.0, "fill_price": 0.987},   # YES-centric, no leg_priced flag
    )
    broker = _StubKalshiBroker({
        "KXPRE-1": {"status": "resolved", "result": "yes",
                    "ticker": "KXPRE-1", "close_time": ""},
    })
    asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM kalshi_round_trips").fetchone()[0]
    assert n == 0   # skipped, NOT booked as a -$163.84 phantom


# ── equity snapshot ────────────────────────────────────────────────────


def test_fetch_orders_past_expiration_first(fresh_db):
    """Past-expiration rows must be scanned before future-expiration rows
    of the same actor. Pre-fix `ORDER BY ts ASC` prioritized OLDEST audit
    rows — but for long-horizon bets, oldest-by-ts means longest-horizon,
    which means STILL PENDING. Past-expiration rows (which actually have
    a final result on Kalshi) were starved.
    """
    db_url, _ = fresh_db
    # kalshi_llm resolution is epoch-scoped to entry_ts >= 2026-07-07T16:40
    # (see _fetch_unresolved_orders), so all entries here are post-epoch; the
    # ordering-by-expiry (not audit-ts) invariant is asserted among them.
    # OLDER audit ts but FAR-FUTURE expiration (a long-horizon bet placed early):
    _insert_audit_event(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "old-future", "ticker": "KXFUTURE-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.30,
         "expires_at": "2027-12-31T00:00:00+00:00"},
        ts="2026-07-08T00:00:00+00:00",
    )
    # NEWER audit ts but NEAR-TERM expiration (a short-horizon bet placed later):
    _insert_audit_event(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "new-past", "ticker": "KXPAST-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.30,
         "expires_at": "2026-07-25T00:00:00+00:00"},
        ts="2026-07-20T00:00:00+00:00",
    )
    # NEWER audit ts with NO expires_at (e.g. legacy payload):
    _insert_audit_event(
        db_url, "kalshi_llm_arbitrage", "would_have_placed",
        {"order_id": "no-exp", "ticker": "KXNOEXP-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.30},
        ts="2026-07-15T00:00:00+00:00",
    )
    rows = kr._fetch_unresolved_orders(db_url, max_per_actor=10)
    order_ids = [r.get("order_id") for r in rows]
    # earliest-expiration first, then far-future, then no-expires_at --
    # ordered by expires_at ASC regardless of audit ts.
    assert order_ids == ["new-past", "old-future", "no-exp"], (
        f"Expected earliest-expiration first; got {order_ids}"
    )


def test_fetch_orders_falls_back_to_leg_date_when_no_expires_at(fresh_db):
    """Temporal/bucket arb proposals carry `leg_date` (the leg's resolution
    date) but NO `expires_at`. Before the COALESCE fix they all tied at
    (expires_at IS NULL) and fell back to `ts ASC`, so oldest-audit
    indefinite-horizon legs (mergers/IPOs that never settle) permanently
    occupied the per-actor budget and matured legs were never scanned --
    the kalshi_arbitrage temporal book booked 0 round-trips for ~2 months.
    The resolver must fall back to `leg_date` so past-dated (matured) legs
    sort first, DESPITE having a newer audit ts.
    """
    db_url, _ = fresh_db
    # OLDER audit ts, FAR-FUTURE leg_date (indefinite-horizon market):
    _insert_audit_event(
        db_url, "kalshi_temporal_bucket_arb", "would_have_placed",
        {"order_id": "temporal-future", "ticker": "KXMERGE-1",
         "leg": "no_early", "qty": 33.0, "limit_price": 0.03,
         "kalshi_arb_type": "temporal", "leg_date": "2027-12-31"},
        ts="2026-05-11T00:00:00+00:00",
    )
    # NEWER audit ts, PAST leg_date (a matured leg that should resolve now):
    _insert_audit_event(
        db_url, "kalshi_temporal_bucket_arb", "would_have_placed",
        {"order_id": "temporal-past", "ticker": "KXFDA-1",
         "leg": "yes_late", "qty": 33.0, "limit_price": 0.80,
         "kalshi_arb_type": "temporal", "leg_date": "2026-06-01"},
        ts="2026-05-20T00:00:00+00:00",
    )
    rows = kr._fetch_unresolved_orders(db_url, max_per_actor=10)
    order_ids = [r.get("order_id") for r in rows]
    # leg_date ASC: matured (2026-06-01) before indefinite (2027-12-31),
    # even though temporal-past has the NEWER audit ts.
    assert order_ids == ["temporal-past", "temporal-future"], (
        f"Expected leg_date ordering (matured first); got {order_ids}"
    )


def test_resolve_per_actor_budget_prevents_starvation(fresh_db):
    """A strategy with a large stuck-pending backlog must not starve
    newer/lower-volume strategies. Pre-fix, _fetch_unresolved_orders used
    a single `actor IN (...) ORDER BY ts ASC LIMIT N` query — when LLM had
    1700+ rows, kalshi_weather_arb + kalshi_crypto_arb never made the
    top-N cut. Per-actor budget gives each actor its own LIMIT.
    """
    db_url, db_path = fresh_db
    # 120 OLD llm rows (predates everything else) — bigger than any
    # plausible max_per_actor we'd set.
    for i in range(120):
        _insert_audit_event(
            db_url, "kalshi_llm_arbitrage", "would_have_placed",
            {"order_id": f"llm-{i}", "ticker": f"KXLLM-{i}", "outcome": "yes",
             "qty": 1.0, "limit_price": 0.40},
            ts=f"2026-05-01T00:00:{i:02d}+00:00",
        )
    # 2 fresh weather rows (timestamp newer than all llm rows).
    _insert_audit_event(
        db_url, "kalshi_weather_arb", "would_have_placed",
        {"order_id": "w-1", "ticker": "KXW-1", "outcome": "yes",
         "qty": 1.0, "limit_price": 0.50},
        ts="2026-05-15T14:33:00+00:00",
    )
    _insert_audit_event(
        db_url, "kalshi_crypto_arb", "would_have_placed",
        {"order_id": "c-1", "ticker": "KXC-1", "outcome": "no",
         "qty": 1.0, "limit_price": 0.50},
        ts="2026-05-15T14:33:01+00:00",
    )

    broker = _StubKalshiBroker({
        "KXW-1": {"status": "resolved", "result": "yes",
                  "ticker": "KXW-1", "close_time": ""},
        "KXC-1": {"status": "resolved", "result": "no",
                  "ticker": "KXC-1", "close_time": ""},
        # All llm tickers default to not_found → simulates stuck-pending.
    })
    # max_per_actor=10 caps llm at 10; weather + crypto get scanned too.
    # Pre-fix (single global ORDER BY ts ASC LIMIT N), only the oldest
    # llm rows would have been returned.
    counts = asyncio.run(
        kr.resolve_pending_round_trips(db_url, broker, max_per_actor=10)
    )
    assert counts["resolved"] == 2, (
        f"Both weather + crypto should resolve; got {counts}"
    )
    assert "KXW-1" in broker.calls
    assert "KXC-1" in broker.calls
    with sqlite3.connect(db_path) as conn:
        order_ids = {r[0] for r in conn.execute(
            "SELECT order_id FROM kalshi_round_trips"
        )}
    assert "w-1" in order_ids
    assert "c-1" in order_ids


def test_write_equity_snapshot_inserts_row(fresh_db):
    db_url, db_path = fresh_db
    snap = SimpleNamespace(
        equity=523.45, cash=499.00,
        positions=[SimpleNamespace(symbol="KXFOO-1", qty=10.0)],
    )
    broker = _StubKalshiBroker({}, snap=snap)
    ok = asyncio.run(kr.write_equity_snapshot(db_url, "kalshi_arbitrage", broker))
    assert ok is True
    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute(
            "SELECT division, equity, cash_usd, positions_value, n_positions "
            "FROM kalshi_equity_history"
        ))
    assert len(rows) == 1
    division, equity, cash, pos_val, n_pos = rows[0]
    assert division == "kalshi_arbitrage"
    assert equity == pytest.approx(523.45)
    assert cash == pytest.approx(499.00)
    assert pos_val == pytest.approx(24.45)
    assert n_pos == 1


def test_write_equity_snapshot_broker_error_returns_false(fresh_db):
    db_url, db_path = fresh_db

    class _Bad:
        async def snapshot(self):
            raise RuntimeError("API down")

    ok = asyncio.run(kr.write_equity_snapshot(db_url, "kalshi_arbitrage", _Bad()))
    assert ok is False
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM kalshi_equity_history").fetchone()[0]
    assert n == 0
