"""Logger / Auditor Agent — non-LLM, writes structured audit + journal rows.

Every meaningful event in the system flows through this agent so the SQLite
DB is the single source of truth for compliance, debugging, and EOD review.
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from trading_corp.persistence import db
from trading_corp.persistence.models import AuditEvent, ProposedOrder
from trading_corp.utils.time import iso, now_utc, trading_day

log = logging.getLogger(__name__)

# Retry delay schedule for "database is locked" OperationalErrors.
# Each value is the BASE sleep before that retry attempt; actual sleep is
# `delay * (0.5 + random.random())` (jitter).  3 entries → up to 4 total
# attempts (1 initial + 3 retries).  Tests monkeypatch this to near-zero.
_DB_LOCK_RETRY_DELAYS_SEC: tuple[float, ...] = (0.1, 0.3, 0.7)


def _write_audit_fallback(
    db_url: str,
    actor: str,
    kind: str,
    payload: dict[str, Any],
    error: Exception,
    attempts: int,
) -> None:
    """Append one JSON line to the file-based fallback next to the DB.

    This helper must never raise — all exceptions are caught and logged.
    """
    try:
        fallback_path = db.resolve_db_path(db_url).parent / "audit_event_write_failed.jsonl"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "kind": kind,
            "payload": payload,
            "error": str(error),
            "attempts": attempts,
        }
        line = json.dumps(entry, separators=(",", ":"), default=str) + "\n"
        with fallback_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:  # pragma: no cover
        log.error("[audit] _write_audit_fallback: could not write fallback file: %s", exc)


class LoggerAgent:
    def __init__(self, db_url: str = "sqlite:///data/trading_corp.db") -> None:
        self.db_url = db_url

    def log_event(self, actor: str, kind: str, payload: dict[str, Any]) -> int | None:
        """Insert an audit_event row. Returns the new row id on success, or
        None if all retries exhausted (fallback file written instead).

        - On transient 'database is locked': retries up to len(_DB_LOCK_RETRY_DELAYS_SEC)
          times with jittered backoff.  Never raises on lock errors.
        - On any other OperationalError: re-raises (preserves existing behavior
          for genuine bugs like missing tables).
        - NEVER logs or returns success when the row did not land.
        """
        evt = AuditEvent(actor=actor, kind=kind, payload=payload)
        insert_sql = (
            "INSERT INTO audit_event(ts, actor, kind, payload_json) "
            "VALUES(:ts,:actor,:kind,:payload_json)"
        )
        db_row = evt.to_db_row()

        attempt = 0  # 0 = initial attempt; 1..N = retries

        while True:
            try:
                with db.connect(self.db_url) as conn:
                    cur = conn.execute(insert_sql, db_row)
                    try:
                        row_id: int | None = int(cur.lastrowid) if cur.lastrowid else None
                    except Exception:
                        row_id = None

                # Row confirmed inserted.
                if attempt == 0:
                    log.info("[audit] %s/%s %s", actor, kind, _short(payload))
                else:
                    log.warning(
                        "[audit] log_event succeeded after %d retries: %s/%s",
                        attempt, actor, kind,
                    )
                return row_id

            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    # Not a lock error — propagate immediately.
                    raise

                if attempt >= len(_DB_LOCK_RETRY_DELAYS_SEC):
                    # All retries exhausted — fall back to file.
                    total_attempts = attempt + 1
                    log.error(
                        "[audit] log_event FAILED after %d attempts (database locked): "
                        "%s/%s — writing to fallback file",
                        total_attempts, actor, kind,
                    )
                    _write_audit_fallback(
                        self.db_url, actor, kind, payload, exc, total_attempts,
                    )
                    return None

                delay = _DB_LOCK_RETRY_DELAYS_SEC[attempt] * (0.5 + random.random())
                log.warning(
                    "[audit] log_event: database locked on attempt %d/%d; "
                    "sleeping %.3fs before retry: %s/%s",
                    attempt + 1,
                    len(_DB_LOCK_RETRY_DELAYS_SEC) + 1,
                    delay,
                    actor,
                    kind,
                )
                time.sleep(delay)
                attempt += 1

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
