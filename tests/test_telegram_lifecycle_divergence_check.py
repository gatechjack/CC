"""Tests for scripts/telegram_lifecycle_divergence_check.py.

Covers:
- N resolutions + M<N success rows → divergence_detected row written
  with divergence=N-M.
- Equal counts → no divergence row written.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.persistence import db as _db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS audit_event (
    id           INTEGER PRIMARY KEY,
    ts           TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trade_record (
    order_id              TEXT PRIMARY KEY,
    ts                    TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    division              TEXT NOT NULL,
    symbol                TEXT NOT NULL,
    side                  TEXT NOT NULL,
    qty                   REAL NOT NULL,
    result                TEXT,
    result_ts             TEXT
);
"""


def _make_db(tmp_path: Path) -> str:
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_CREATE_TABLES)
    conn.commit()
    conn.close()
    return f"sqlite:///{path}"


def _now_ts() -> str:
    """Current UTC timestamp as ISO string — always within any reasonable window."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _insert_resolutions(db_url: str, n: int) -> None:
    """Insert N paper_trade_record rows with result IS NOT NULL and result_ts = now."""
    from trading_corp.persistence.db import resolve_db_path
    ts = _now_ts()
    path = resolve_db_path(db_url)
    conn = sqlite3.connect(str(path))
    for i in range(n):
        conn.execute(
            "INSERT INTO paper_trade_record "
            "(order_id, ts, strategy, division, symbol, side, qty, result, result_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"order-{i}",
                ts,
                "bitunix_pa",
                "bitunix_futures",
                "BTCUSDT",
                "buy",
                1.0,
                "win",
                ts,
            ),
        )
    conn.commit()
    conn.close()


def _insert_success_rows(db_url: str, m: int) -> None:
    """Insert M telegram_notification_success rows with path='lifecycle_close_out'."""
    from trading_corp.persistence.db import resolve_db_path
    ts = _now_ts()
    path = resolve_db_path(db_url)
    conn = sqlite3.connect(str(path))
    for i in range(m):
        payload = json.dumps({"path": "lifecycle_close_out", "ok": True})
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
            (ts, "telegram_channel", "telegram_notification_success", payload),
        )
    conn.commit()
    conn.close()


def _read_divergence_rows(db_url: str) -> list[dict]:
    from trading_corp.persistence.db import resolve_db_path
    path = resolve_db_path(db_url)
    conn = sqlite3.connect(str(path))
    rows = conn.execute(
        "SELECT payload_json FROM audit_event "
        "WHERE kind = 'telegram_lifecycle_divergence_detected'"
    ).fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Test 1: divergence detected
# ---------------------------------------------------------------------------


def test_divergence_detected_writes_audit_row(tmp_path: Path):
    """3 resolutions, 1 success row → divergence=2 row written."""
    import sys
    from pathlib import Path as _Path
    _repo = str(_Path(__file__).resolve().parent.parent)
    if _repo not in sys.path:
        sys.path.insert(0, _repo)

    db_url = _make_db(tmp_path)
    _insert_resolutions(db_url, 3)
    _insert_success_rows(db_url, 1)

    from scripts.telegram_lifecycle_divergence_check import run_check
    run_check(db_url=db_url, hours=24)

    rows = _read_divergence_rows(db_url)
    assert len(rows) == 1
    payload = rows[0]
    assert payload["divergence"] == 2
    assert payload["n_resolutions"] == 3
    assert payload["n_success_close_out"] == 1


# ---------------------------------------------------------------------------
# Test 2: no divergence
# ---------------------------------------------------------------------------


def test_no_divergence_does_not_write_audit_row(tmp_path: Path):
    """2 resolutions, 2 success rows → no divergence row."""
    import sys
    from pathlib import Path as _Path
    _repo = str(_Path(__file__).resolve().parent.parent)
    if _repo not in sys.path:
        sys.path.insert(0, _repo)

    db_url = _make_db(tmp_path)
    _insert_resolutions(db_url, 2)
    _insert_success_rows(db_url, 2)

    from scripts.telegram_lifecycle_divergence_check import run_check
    run_check(db_url=db_url, hours=24)

    rows = _read_divergence_rows(db_url)
    assert len(rows) == 0
