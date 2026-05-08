#!/usr/bin/env python3
"""One-shot backfill: replay historical `would_have_placed` audit events
into the `paper_trade_record` table.

Phase B of the would_have_placed enrichment (BACKLOG.md 2026-05-01).
Idempotent — INSERT OR IGNORE on order_id, so running twice is a no-op.

Strategy:
1. Walk audit_event WHERE kind = 'would_have_placed'.
2. For each, look up the matching proposed_order row to pull `extra_json`
   (the source of truth for tier, stop, TP, etc — Phase A fields).
3. Build a PaperTradeRecord and insert. Legacy rows predating Phase A
   will have NULL trade-spec columns; that's expected and the replay
   job will skip them.

Usage:
    python scripts/backfill_paper_trade_record.py
    python scripts/backfill_paper_trade_record.py --db sqlite:///data/trading_corp.db
    python scripts/backfill_paper_trade_record.py --dry-run

Run once after deploying the schema migration. Future emissions are
written inline by the webhook path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trading_corp.persistence import db as _db
from trading_corp.persistence.models import ProposedOrder, PaperTradeRecord


# Strategy → max_hold_seconds defaults at backfill time. We don't try to
# read strategies.yaml here because backfilled rows may pre-date the
# config field; using today's value is "good enough" and the replay job
# never alters past trades' frozen value.
DEFAULT_MAX_HOLD = {
    "lord_otter": 86400,
    "market_cypher": 604800,
}


def _iter_would_have_placed(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT ts, actor, payload_json FROM audit_event "
        "WHERE kind = 'would_have_placed' "
        "ORDER BY ts ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def _load_order(conn, order_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, ts, strategy, symbol, side, qty, order_type, limit_price, "
        "       rationale, status, risk_reason, board_reason, fill_price, "
        "       fill_ts, extra_json "
        "FROM proposed_order WHERE id = ?",
        (order_id,),
    ).fetchone()
    return dict(row) if row else None


def _record_from_audit(audit_row: dict, order_row: dict | None) -> PaperTradeRecord | None:
    """Build a PaperTradeRecord from an audit_event payload + proposed_order.

    Uses proposed_order.extra_json (Phase A canonical home). Falls back to
    audit payload fields when the order row is missing — but the spec
    fields (stop, TP, etc.) won't be present in that case and will write
    as NULL.
    """
    payload = json.loads(audit_row["payload_json"] or "{}")
    order_id = payload.get("order_id")
    if not order_id:
        return None
    strategy = payload.get("strategy") or audit_row["actor"]
    division = payload.get("division") or "unknown"
    symbol = payload.get("symbol")
    side = payload.get("side")
    qty = payload.get("qty")

    extra: dict = {}
    if order_row and order_row.get("extra_json"):
        try:
            extra = json.loads(order_row["extra_json"]) or {}
        except json.JSONDecodeError:
            extra = {}

    # Synthesize a ProposedOrder shape for from_order(); we only need the
    # fields it reads.
    fake = ProposedOrder(
        strategy=strategy,
        symbol=symbol or "",
        side=side or "buy",
        qty=float(qty or 0),
        extra=extra,
    )
    fake.id = order_id
    fake.ts = (order_row.get("ts") if order_row else None) or audit_row["ts"]

    return PaperTradeRecord.from_order(
        fake,
        strategy=strategy,
        division=division,
        max_hold_seconds=DEFAULT_MAX_HOLD.get(strategy),
    )


def backfill(db_url: str, *, dry_run: bool = False) -> dict:
    """Returns counts: {seen, inserted, skipped_no_order_id}."""
    _db.init_db(db_url)
    seen = inserted = skipped = 0
    with _db.connect(db_url) as conn:
        audits = _iter_would_have_placed(conn)
        for a in audits:
            seen += 1
            payload = json.loads(a["payload_json"] or "{}")
            order_id = payload.get("order_id")
            if not order_id:
                skipped += 1
                continue
            order_row = _load_order(conn, order_id)
            rec = _record_from_audit(a, order_row)
            if rec is None:
                skipped += 1
                continue
            if dry_run:
                continue
            row = rec.to_db_row()
            cols = list(row.keys())
            placeholders = ",".join("?" for _ in cols)
            conn.execute(
                f"INSERT OR IGNORE INTO paper_trade_record ({','.join(cols)}) "
                f"VALUES ({placeholders})",
                [row[c] for c in cols],
            )
            if conn.total_changes > inserted:
                inserted = conn.total_changes
    return {"seen": seen, "inserted": inserted, "skipped_no_order_id": skipped}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="sqlite:///data/trading_corp.db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    counts = backfill(args.db, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "WROTE"
    print(
        f"{mode}: scanned={counts['seen']} inserted={counts['inserted']} "
        f"skipped_no_order_id={counts['skipped_no_order_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
