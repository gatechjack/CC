"""Phase B of the would_have_placed enrichment (BACKLOG.md 2026-05-01).

Pins:
- paper_trade_record schema has the columns the replay job will read.
- PaperTradeRecord.from_order pulls Phase A trade-card fields out of
  order.extra and computes expected_loss / rr_ratio correctly.
- Legacy orders (no Phase A fields) still write a row, just with NULLs
  in trade-spec columns — the replay job will skip them.
- insert_paper_trade_record uses INSERT OR IGNORE so the backfill script
  never collides with the inline write-on-emit path.
- Backfill walks audit_event WHERE kind='would_have_placed' and joins
  to proposed_order.extra_json. Idempotent across runs.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from scripts.backfill_paper_trade_record import backfill
from trading_corp.persistence.db import (
    connect,
    init_db,
    insert_paper_trade_record,
    resolve_db_path,
)
from trading_corp.persistence.models import (
    AuditEvent,
    PaperTradeRecord,
    ProposedOrder,
)


EXPECTED_COLUMNS = {
    "order_id", "ts", "strategy", "division", "symbol", "side", "qty",
    "tier", "source_signal", "entry_reference_price", "stop_price",
    "tp_price", "tp_r_multiple", "expected_loss", "expected_gain",
    "rr_ratio", "max_hold_seconds", "result", "result_ts", "result_price",
    "actual_pnl_dollars", "actual_r_multiple", "bars_to_resolution",
    "extra_json",
}


def _columns(db_url: str, table: str) -> set[str]:
    path = resolve_db_path(db_url)
    with sqlite3.connect(path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


# ── schema pin ────────────────────────────────────────────────────────────


def test_schema_has_expected_columns(tmp_db):
    init_db(tmp_db)
    cols = _columns(tmp_db, "paper_trade_record")
    assert cols == EXPECTED_COLUMNS, f"unexpected columns: {cols ^ EXPECTED_COLUMNS}"


def test_schema_creates_indexes(tmp_db):
    init_db(tmp_db)
    path = resolve_db_path(tmp_db)
    with sqlite3.connect(path) as conn:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='paper_trade_record'"
        )}
    assert "ix_paper_trade_record_strategy_ts" in idx
    assert "ix_paper_trade_record_result" in idx


# ── from_order ────────────────────────────────────────────────────────────


def _full_phase_a_order() -> ProposedOrder:
    return ProposedOrder(
        strategy="lord_otter",
        symbol="BTC/USD",
        side="buy",
        qty=0.0125,
        rationale="diamond_3m",
        extra={
            "tier": "diamond",
            "source_signal": "bullish_diamond_3m",
            "entry_reference_price": 67420.0,
            "stop_price": 67150.0,
            "stop_basis": "trigger_bar_low",
            "stop_distance_pct": 0.004,
            "max_dollar_risk": 50.0,
            "take_profit_price": 68230.0,
            "tp_basis": "3R",
            "tp_r_multiple": 3.0,
            "tp_distance_pct": 0.012,
            "expected_gain_if_tp_hit": 150.0,
        },
    )


def test_from_order_full_phase_a_fields():
    order = _full_phase_a_order()
    rec = PaperTradeRecord.from_order(
        order, strategy="lord_otter", division="coinbase_spot",
        max_hold_seconds=86400,
    )
    assert rec.order_id == order.id
    assert rec.strategy == "lord_otter"
    assert rec.division == "coinbase_spot"
    assert rec.symbol == "BTC/USD"
    assert rec.tier == "diamond"
    assert rec.source_signal == "bullish_diamond_3m"
    assert rec.entry_reference_price == 67420.0
    assert rec.stop_price == 67150.0
    assert rec.tp_price == 68230.0
    assert rec.tp_r_multiple == 3.0
    assert rec.expected_loss == -50.0       # = -max_dollar_risk
    assert rec.expected_gain == 150.0
    assert rec.rr_ratio == 3.0              # 150 / 50
    assert rec.max_hold_seconds == 86400
    # Phase C fields untouched
    assert rec.result is None
    assert rec.result_ts is None


def test_from_order_legacy_no_extras_writes_nulls():
    """Order predating Phase A — no stop/TP fields. Should still build a
    record, with the trade-spec columns left as None."""
    order = ProposedOrder(
        strategy="lord_otter", symbol="BTC/USD", side="buy", qty=0.01,
    )
    rec = PaperTradeRecord.from_order(
        order, strategy="lord_otter", division="coinbase_spot",
        max_hold_seconds=86400,
    )
    assert rec.stop_price is None
    assert rec.tp_price is None
    assert rec.expected_loss is None
    assert rec.expected_gain is None
    assert rec.rr_ratio is None
    # Identity fields still populated
    assert rec.order_id == order.id
    assert rec.symbol == "BTC/USD"
    assert rec.qty == 0.01


def test_from_order_partial_phase_a_only_stop_no_tp():
    """Stop populated but no TP yet — common when TP yaml is absent for a
    tier. expected_loss should compute; rr_ratio stays None."""
    order = ProposedOrder(
        strategy="lord_otter", symbol="BTC/USD", side="buy", qty=0.01,
        extra={"tier": "solo_otter", "max_dollar_risk": 10.0,
               "stop_price": 67000.0},
    )
    rec = PaperTradeRecord.from_order(
        order, strategy="lord_otter", division="coinbase_spot",
        max_hold_seconds=86400,
    )
    assert rec.expected_loss == -10.0
    assert rec.expected_gain is None
    assert rec.rr_ratio is None


# ── insert_paper_trade_record ─────────────────────────────────────────────


def test_insert_writes_row_with_all_fields(tmp_db):
    init_db(tmp_db)
    rec = PaperTradeRecord.from_order(
        _full_phase_a_order(), strategy="lord_otter",
        division="coinbase_spot", max_hold_seconds=86400,
    )
    insert_paper_trade_record(rec.to_db_row(), db_url=tmp_db)

    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id = ?",
            (rec.order_id,),
        ).fetchone()
    assert row is not None
    assert row["strategy"] == "lord_otter"
    assert row["tier"] == "diamond"
    assert row["expected_loss"] == -50.0
    assert row["rr_ratio"] == pytest.approx(3.0)


def test_insert_is_idempotent_on_order_id(tmp_db):
    """Second insert with same order_id is a no-op (INSERT OR IGNORE).
    Backfill safety: re-running won't double-count or error."""
    init_db(tmp_db)
    rec = PaperTradeRecord.from_order(
        _full_phase_a_order(), strategy="lord_otter",
        division="coinbase_spot", max_hold_seconds=86400,
    )
    insert_paper_trade_record(rec.to_db_row(), db_url=tmp_db)
    insert_paper_trade_record(rec.to_db_row(), db_url=tmp_db)

    with connect(tmp_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_trade_record"
        ).fetchone()["n"]
    assert count == 1


# ── backfill ──────────────────────────────────────────────────────────────


def _seed_audit_and_order(db_url: str, order: ProposedOrder, *,
                          strategy: str, division: str) -> None:
    """Mimic the production write path: proposed_order row + would_have_placed
    audit_event row."""
    with connect(db_url) as conn:
        po_row = order.to_db_row()
        cols = list(po_row.keys())
        conn.execute(
            f"INSERT INTO proposed_order ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            [po_row[c] for c in cols],
        )
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        audit = AuditEvent(
            actor=strategy, kind="would_have_placed",
            payload={
                "strategy": strategy, "division": division,
                "order_id": order.id, "symbol": order.symbol,
                "side": order.side, "qty": order.qty,
                "tier": (order.extra or {}).get("tier"),
            },
            ts=ts,
        )
        ar = audit.to_db_row()
        conn.execute(
            f"INSERT INTO audit_event ({','.join(ar.keys())}) "
            f"VALUES ({','.join('?' for _ in ar)})",
            list(ar.values()),
        )


def test_backfill_walks_audit_into_paper_trade_record(tmp_db):
    init_db(tmp_db)
    order = _full_phase_a_order()
    _seed_audit_and_order(tmp_db, order, strategy="lord_otter",
                          division="coinbase_spot")

    counts = backfill(tmp_db)

    assert counts["seen"] == 1
    assert counts["inserted"] == 1
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id = ?",
            (order.id,),
        ).fetchone()
    assert row is not None
    assert row["tier"] == "diamond"
    assert row["expected_loss"] == -50.0
    assert row["max_hold_seconds"] == 86400  # default applied per strategy


def test_backfill_is_idempotent(tmp_db):
    init_db(tmp_db)
    order = _full_phase_a_order()
    _seed_audit_and_order(tmp_db, order, strategy="lord_otter",
                          division="coinbase_spot")

    backfill(tmp_db)
    counts2 = backfill(tmp_db)

    # Row exists exactly once after second run.
    with connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) n FROM paper_trade_record").fetchone()["n"]
    assert n == 1
    # Second pass still walks the audit row but inserts nothing new.
    assert counts2["seen"] == 1


def test_backfill_legacy_order_writes_row_with_null_specs(tmp_db):
    """Pre-Phase-A audit/order pair (no extras) still produces a row,
    just with NULL trade-spec columns — the replay job will skip it."""
    init_db(tmp_db)
    order = ProposedOrder(
        strategy="market_cypher", symbol="BTC/USD", side="buy", qty=0.5,
    )
    _seed_audit_and_order(tmp_db, order, strategy="market_cypher",
                          division="coinbase_spot")

    backfill(tmp_db)

    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id = ?",
            (order.id,),
        ).fetchone()
    assert row is not None
    assert row["stop_price"] is None
    assert row["tp_price"] is None
    assert row["expected_loss"] is None
    assert row["max_hold_seconds"] == 604800  # cypher default


def test_backfill_dry_run_does_not_write(tmp_db):
    init_db(tmp_db)
    order = _full_phase_a_order()
    _seed_audit_and_order(tmp_db, order, strategy="lord_otter",
                          division="coinbase_spot")

    backfill(tmp_db, dry_run=True)

    with connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) n FROM paper_trade_record").fetchone()["n"]
    assert n == 0
