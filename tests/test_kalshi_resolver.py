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


# ── equity snapshot ────────────────────────────────────────────────────


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
