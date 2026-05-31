"""Tests for the Stage-1 paper-mode filter on the trade-flow rail.

Pins the contract between `trade_flow(stage1_only=)` and the WHERE clause:
when on, only bitunix_futures rows where payload.execution_mode is 'paper'
(or absent) pass through. When off, the rail is byte-identical to its
pre-toggle behavior.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web import data as wd


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "stage1_filter.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url


def _ins_audit(db_url, actor, kind, payload, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?,?,?,?)",
            (ts, actor, kind, json.dumps(payload)),
        )


# ── Filter off (default) ──────────────────────────────────────────────────

def test_default_returns_all_kinds(fresh_db):
    """Filter off: all trade-flow kinds across actors should surface."""
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed", {"symbol": "BTC"})
    _ins_audit(fresh_db, "pmcc", "scan_order_result", {"symbol": "SPY"})
    _ins_audit(fresh_db, "kalshi_llm_arbitrage", "fill", {"symbol": "KX"})
    rows = wd.trade_flow(fresh_db, limit=20)
    kinds = [r["kind"] for r in rows]
    assert "would_have_placed" in kinds
    assert "scan_order_result" in kinds
    assert "fill" in kinds


def test_default_excludes_live_order_kinds(fresh_db):
    """Filter off: legacy behavior — live_order_placed/rejected are NOT in
    the default kinds list (it predates Stage-1; only would_have_placed
    was the bitunix decision row on home rail).
    """
    _ins_audit(fresh_db, "bitunix_futures", "live_order_placed", {})
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed", {})
    rows = wd.trade_flow(fresh_db, limit=20)
    kinds = [r["kind"] for r in rows]
    assert "would_have_placed" in kinds
    assert "live_order_placed" not in kinds


# ── Filter on (stage1_only=True) ──────────────────────────────────────────

def test_stage1_includes_bitunix_would_have_placed(fresh_db):
    """Paper rows omit execution_mode by convention — must still pass."""
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed", {"symbol": "BTC"})
    rows = wd.trade_flow(fresh_db, limit=20, stage1_only=True)
    assert len(rows) == 1
    assert rows[0]["kind"] == "would_have_placed"
    assert rows[0]["actor"] == "bitunix_futures"


def test_stage1_includes_bitunix_paper_explicit(fresh_db):
    """Rows that explicitly stamp execution_mode='paper' pass."""
    _ins_audit(
        fresh_db, "bitunix_futures", "would_have_placed",
        {"symbol": "ETH", "execution_mode": "paper"},
    )
    rows = wd.trade_flow(fresh_db, limit=20, stage1_only=True)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH"


def test_stage1_excludes_bitunix_live(fresh_db):
    """Live-path rows (execution_mode='live') must be filtered out."""
    _ins_audit(
        fresh_db, "bitunix_futures", "live_order_placed",
        {"symbol": "BTC", "execution_mode": "live"},
    )
    rows = wd.trade_flow(fresh_db, limit=20, stage1_only=True)
    assert rows == []


def test_stage1_excludes_non_bitunix_actors(fresh_db):
    """PMCC, IC, kalshi, polymarket rows must be filtered out."""
    _ins_audit(fresh_db, "pmcc", "scan_order_result", {"symbol": "SPY"})
    _ins_audit(fresh_db, "fidelity", "scan_order_result", {"symbol": "IWM"})
    _ins_audit(fresh_db, "kalshi_llm_arbitrage", "fill", {"symbol": "KX"})
    _ins_audit(fresh_db, "polymarket_arbitrage", "would_have_placed", {})
    rows = wd.trade_flow(fresh_db, limit=20, stage1_only=True)
    assert rows == []


def test_stage1_mixed_dataset_returns_only_bitunix_paper(fresh_db):
    """Realistic mixed dataset: bitunix paper + bitunix live + other
    actors. Only the bitunix paper rows should pass through.
    """
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed",
               {"symbol": "BTC"})  # paper (no exec_mode)
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed",
               {"symbol": "ETH", "execution_mode": "paper"})  # paper explicit
    _ins_audit(fresh_db, "bitunix_futures", "live_order_placed",
               {"symbol": "BTC", "execution_mode": "live"})  # live → out
    _ins_audit(fresh_db, "pmcc", "scan_order_result",
               {"symbol": "SPY"})  # other actor → out
    _ins_audit(fresh_db, "kalshi_llm_arbitrage", "fill",
               {"symbol": "KX"})  # other actor → out

    rows = wd.trade_flow(fresh_db, limit=20, stage1_only=True)
    assert len(rows) == 2
    symbols = sorted(r["symbol"] for r in rows)
    assert symbols == ["BTC", "ETH"]


def test_stage1_empty_dataset(fresh_db):
    """No bitunix paper activity → empty list (template renders the
    'no Stage-1 activity yet' empty-state branch)."""
    _ins_audit(fresh_db, "pmcc", "scan_order_result", {})
    rows = wd.trade_flow(fresh_db, limit=20, stage1_only=True)
    assert rows == []


def test_stage1_respects_limit(fresh_db):
    """The LIMIT clause applies post-filter."""
    for i in range(25):
        _ins_audit(
            fresh_db, "bitunix_futures", "would_have_placed",
            {"symbol": f"BTC-{i:02d}"},
        )
    rows = wd.trade_flow(fresh_db, limit=5, stage1_only=True)
    assert len(rows) == 5


def test_stage1_ordering_descending_by_id(fresh_db):
    """Most-recent-first — matches the existing rail ordering."""
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed",
               {"symbol": "FIRST"})
    _ins_audit(fresh_db, "bitunix_futures", "would_have_placed",
               {"symbol": "SECOND"})
    rows = wd.trade_flow(fresh_db, limit=5, stage1_only=True)
    assert rows[0]["symbol"] == "SECOND"
    assert rows[1]["symbol"] == "FIRST"
