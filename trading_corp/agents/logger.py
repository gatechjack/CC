"""Logger / Auditor Agent — non-LLM, writes structured audit + journal rows.

Every meaningful event in the system flows through this agent so the SQLite
DB is the single source of truth for compliance, debugging, and EOD review.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from trading_corp.persistence import db
from trading_corp.persistence.models import AuditEvent, ProposedOrder
from trading_corp.utils.time import iso, now_utc, trading_day

log = logging.getLogger(__name__)


class LoggerAgent:
    def __init__(self, db_url: str = "sqlite:///data/trading_corp.db") -> None:
        self.db_url = db_url

    def log_event(self, actor: str, kind: str, payload: dict[str, Any]) -> int | None:
        """Insert an audit_event row. Returns the new row id (best-effort
        — None if SQLite's lastrowid isn't available, which shouldn't
        happen for a successful INSERT but the audit path must never
        raise on read-back). Phase 1f's debate_audit_row_id needs the id
        to tag products that join the debate row."""
        evt = AuditEvent(actor=actor, kind=kind, payload=payload)
        row_id: int | None = None
        with db.connect(self.db_url) as conn:
            cur = conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(:ts,:actor,:kind,:payload_json)",
                evt.to_db_row(),
            )
            try:
                row_id = int(cur.lastrowid) if cur.lastrowid else None
            except Exception:
                row_id = None
        log.info("[audit] %s/%s %s", actor, kind, _short(payload))
        return row_id

    def log_proposed_order(self, order: ProposedOrder) -> None:
        with db.connect(self.db_url) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO proposed_order
                   (id, ts, strategy, symbol, side, qty, order_type, limit_price,
                    rationale, status, risk_reason, board_reason, fill_price, fill_ts, extra_json)
                   VALUES(:id,:ts,:strategy,:symbol,:side,:qty,:order_type,:limit_price,
                          :rationale,:status,:risk_reason,:board_reason,:fill_price,:fill_ts,:extra_json)""",
                order.to_db_row(),
            )

    def log_brief(self, kind: str, body_md: str) -> None:
        """kind: 'morning' or 'eod_debate'."""
        with db.connect(self.db_url) as conn:
            conn.execute(
                """INSERT INTO daily_brief(trading_day, kind, body_md, created_ts)
                   VALUES(?,?,?,?)""",
                (str(trading_day()), kind, body_md, iso(now_utc())),
            )

    def recent_events(self, limit: int = 50) -> list[dict]:
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT ts, actor, kind, payload_json FROM audit_event ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "ts": r["ts"], "actor": r["actor"], "kind": r["kind"],
                "payload": json.loads(r["payload_json"]),
            })
        return out

    def events_since(self, ts_iso: str, limit: int = 5000) -> list[dict]:
        """Date-scoped audit fetch for multi-day windows that would
        overflow recent_events()'s default limit. Returns newest-first.
        Used by the PMCC research-as-consultant validation view, which
        needs the full observation period (≥3 days) regardless of how
        many other audit rows landed in the same window."""
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT id, ts, actor, kind, payload_json FROM audit_event "
                "WHERE ts >= ? ORDER BY id DESC LIMIT ?",
                (ts_iso, limit),
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"], "ts": r["ts"], "actor": r["actor"],
                "kind": r["kind"], "payload": json.loads(r["payload_json"]),
            })
        return out


def _short(d: dict) -> str:
    s = json.dumps(d, default=str)
    return s if len(s) <= 200 else s[:197] + "..."
