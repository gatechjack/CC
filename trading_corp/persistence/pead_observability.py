"""Persistence helpers for the `robinhood_pead` operations dashboard's
observability surfaces: data-feed health (tri-state) + the scan-rejection tally.

Tables (`data_feed_status`, `scan_evaluation`) are defined in
`persistence/db.py` SCHEMA. Writers: the EODHD earnings adapter upserts feed
status; the Phase-2 PEAD scan logs per-candidate evaluations. Readers: the
dashboard's `build_pead_view`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from trading_corp.persistence.db import connect

_FEED_STATES = ("live", "degraded", "down")
_DEFAULT_DB = "sqlite:///data/trading_corp.db"


# ── data_feed_status (Stage-0 safety strip) ────────────────────────────────

def upsert_feed_status(
    feed_name: str,
    status: str,
    *,
    ok: bool = False,
    detail: str | None = None,
    db_url: str = _DEFAULT_DB,
) -> None:
    """Upsert a feed's tri-state status. `ok=True` (a successful contact) also
    refreshes `last_ok_ts`; on a non-ok write the prior `last_ok_ts` is kept so
    the dashboard can show "down · last seen Xm ago". `status` is coerced to
    'down' if not one of live/degraded/down."""
    if status not in _FEED_STATES:
        status = "down"
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_url) as conn:
        if ok:
            conn.execute(
                "INSERT INTO data_feed_status "
                "(feed_name, status, last_ok_ts, last_check_ts, detail) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(feed_name) DO UPDATE SET status=excluded.status, "
                "last_ok_ts=excluded.last_ok_ts, last_check_ts=excluded.last_check_ts, "
                "detail=excluded.detail",
                (feed_name, status, now, now, detail),
            )
        else:
            conn.execute(
                "INSERT INTO data_feed_status "
                "(feed_name, status, last_ok_ts, last_check_ts, detail) "
                "VALUES (?, ?, NULL, ?, ?) "
                "ON CONFLICT(feed_name) DO UPDATE SET status=excluded.status, "
                "last_check_ts=excluded.last_check_ts, detail=excluded.detail",
                (feed_name, status, now, detail),
            )


def load_feed_status(db_url: str = _DEFAULT_DB) -> dict[str, dict]:
    """Return {feed_name: {status, last_ok_ts, last_check_ts, detail}} for all feeds."""
    with connect(db_url) as conn:
        rows = conn.execute(
            "SELECT feed_name, status, last_ok_ts, last_check_ts, detail "
            "FROM data_feed_status"
        ).fetchall()
    return {
        r["feed_name"]: {
            "status": r["status"], "last_ok_ts": r["last_ok_ts"],
            "last_check_ts": r["last_check_ts"], "detail": r["detail"],
        }
        for r in rows
    }


# ── scan_evaluation (Stage 1-3 "dropped this scan · why" tally) ─────────────

def insert_scan_evaluation(
    session_ts: str,
    ticker: str,
    verdict: str,
    *,
    reason_code: str | None = None,
    metrics: dict | None = None,
    db_url: str = _DEFAULT_DB,
) -> None:
    """Log one evaluated candidate. `verdict` is 'passed' | 'rejected';
    `reason_code` is set on rejection (mapped from pead_signal's screen reasons)."""
    now = datetime.now(timezone.utc).isoformat()
    metrics_json = (
        json.dumps(metrics, separators=(",", ":"), default=str)
        if metrics is not None else None
    )
    with connect(db_url) as conn:
        conn.execute(
            "INSERT INTO scan_evaluation "
            "(session_ts, ticker, verdict, reason_code, metrics_json, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_ts, ticker, verdict, reason_code, metrics_json, now),
        )


def latest_scan_session(db_url: str = _DEFAULT_DB) -> str | None:
    with connect(db_url) as conn:
        row = conn.execute(
            "SELECT session_ts FROM scan_evaluation ORDER BY session_ts DESC LIMIT 1"
        ).fetchone()
    return row["session_ts"] if row else None


def scan_rejection_tally(
    session_ts: str | None = None, db_url: str = _DEFAULT_DB
) -> dict:
    """Aggregate the latest (or given) scan session into the funnel tally.

    Returns {session_ts, scanned, qualified, rejected, by_reason{code:count}}.
    Invariant (the dashboard relies on it): scanned − qualified == rejected ==
    sum(by_reason.values())."""
    if session_ts is None:
        session_ts = latest_scan_session(db_url)
    if session_ts is None:
        return {"session_ts": None, "scanned": 0, "qualified": 0,
                "rejected": 0, "by_reason": {}}
    with connect(db_url) as conn:
        rows = conn.execute(
            "SELECT verdict, reason_code, COUNT(*) AS n FROM scan_evaluation "
            "WHERE session_ts = ? GROUP BY verdict, reason_code",
            (session_ts,),
        ).fetchall()
    scanned = qualified = rejected = 0
    by_reason: dict[str, int] = {}
    for r in rows:
        n = int(r["n"])
        scanned += n
        if r["verdict"] == "passed":
            qualified += n
        else:
            rejected += n
            code = r["reason_code"] or "unknown"
            by_reason[code] = by_reason.get(code, 0) + n
    return {"session_ts": session_ts, "scanned": scanned, "qualified": qualified,
            "rejected": rejected, "by_reason": by_reason}
