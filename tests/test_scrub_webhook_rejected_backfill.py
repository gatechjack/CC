"""Tests for scripts/scrub_webhook_rejected_secrets.py.

Uses tmp_path + sqlite3 directly — no app imports required.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Make the script importable without a full project install
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.scrub_webhook_rejected_secrets import run  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE audit_event ("
        "  id INTEGER PRIMARY KEY, "
        "  ts TEXT, "
        "  actor TEXT, "
        "  kind TEXT, "
        "  payload_json TEXT"
        ")"
    )
    conn.commit()
    return conn


def _insert_row(conn: sqlite3.Connection, row_id: int, kind: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO audit_event (id, ts, actor, kind, payload_json) VALUES (?, ?, ?, ?, ?)",
        (row_id, "2026-01-01T00:00:00", "test_actor", kind, json.dumps(payload)),
    )
    conn.commit()


def _fetch_snippet(conn: sqlite3.Connection, row_id: int) -> str:
    row = conn.execute(
        "SELECT payload_json FROM audit_event WHERE id = ?", (row_id,)
    ).fetchone()
    return json.loads(row[0])["raw_body_snippet"]


def _fetch_payload_json(conn: sqlite3.Connection, row_id: int) -> str:
    row = conn.execute(
        "SELECT payload_json FROM audit_event WHERE id = ?", (row_id,)
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_scrubs_secret_in_existing_row(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = _create_db(db)
    _insert_row(conn, 1, "webhook_rejected", {
        "strategy": "s", "division": "d", "reason": "bad",
        "client_ip": "1.2.3.4",
        "raw_body_snippet": '{"secret": "leaked", "action": "buy"}',
    })
    conn.close()

    counts = run(str(db))

    conn = sqlite3.connect(str(db))
    snippet = json.loads(
        conn.execute("SELECT payload_json FROM audit_event WHERE id = 1").fetchone()[0]
    )["raw_body_snippet"]
    conn.close()

    assert "leaked" not in snippet
    assert "***REDACTED***" in snippet
    assert counts["rows_changed"] == 1
    assert counts["rows_scanned"] == 1


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = _create_db(db)
    original_payload = {
        "strategy": "s", "division": "d", "reason": "bad",
        "client_ip": "1.2.3.4",
        "raw_body_snippet": '{"secret": "should-not-change"}',
    }
    _insert_row(conn, 1, "webhook_rejected", original_payload)
    conn.close()

    counts = run(str(db), dry_run=True)

    conn = sqlite3.connect(str(db))
    raw = conn.execute("SELECT payload_json FROM audit_event WHERE id = 1").fetchone()[0]
    conn.close()

    stored = json.loads(raw)["raw_body_snippet"]
    assert "should-not-change" in stored, "dry-run must not modify the DB"
    assert counts["rows_changed"] == 1  # would-change count still reported


def test_idempotent_on_already_scrubbed_row(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = _create_db(db)
    _insert_row(conn, 1, "webhook_rejected", {
        "strategy": "s", "division": "d", "reason": "bad",
        "client_ip": "1.2.3.4",
        "raw_body_snippet": '{"secret": "***REDACTED***", "action": "buy"}',
    })
    conn.close()

    counts = run(str(db))

    conn = sqlite3.connect(str(db))
    snippet = json.loads(
        conn.execute("SELECT payload_json FROM audit_event WHERE id = 1").fetchone()[0]
    )["raw_body_snippet"]
    conn.close()

    # Row must be unchanged and counted as already_clean
    assert counts["rows_already_clean"] == 1
    assert counts["rows_changed"] == 0
    assert "***REDACTED***" in snippet


def test_skips_non_webhook_rejected_rows(tmp_path: Path) -> None:
    """Rows with kind != 'webhook_rejected' must never be touched."""
    db = tmp_path / "test.db"
    conn = _create_db(db)
    _insert_row(conn, 1, "webhook_received", {
        "strategy": "s", "division": "d",
        "raw_body_snippet": '{"secret": "plaintext-safe"}',
    })
    conn.close()

    counts = run(str(db))

    conn = sqlite3.connect(str(db))
    snippet = json.loads(
        conn.execute("SELECT payload_json FROM audit_event WHERE id = 1").fetchone()[0]
    )["raw_body_snippet"]
    conn.close()

    assert "plaintext-safe" in snippet, "non-webhook_rejected row must remain untouched"
    assert counts["rows_scanned"] == 0  # query filters on kind, so this row is never seen


def test_skips_malformed_payload_json(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = _create_db(db)
    conn.execute(
        "INSERT INTO audit_event (id, ts, actor, kind, payload_json) VALUES (?, ?, ?, ?, ?)",
        (1, "2026-01-01T00:00:00", "actor", "webhook_rejected", "NOT VALID JSON {{{"),
    )
    conn.commit()
    conn.close()

    counts = run(str(db))

    assert counts["rows_skipped_bad_json"] == 1
    assert counts["rows_changed"] == 0

    # Original malformed value must still be intact
    conn = sqlite3.connect(str(db))
    raw = conn.execute("SELECT payload_json FROM audit_event WHERE id = 1").fetchone()[0]
    conn.close()
    assert raw == "NOT VALID JSON {{{"


def test_skips_row_with_no_snippet_field(tmp_path: Path) -> None:
    """Legacy webhook_rejected rows without raw_body_snippet are skipped silently."""
    db = tmp_path / "test.db"
    conn = _create_db(db)
    _insert_row(conn, 1, "webhook_rejected", {
        "strategy": "s", "division": "d", "reason": "bad",
        "client_ip": "1.2.3.4",
        # no raw_body_snippet key
    })
    conn.close()

    counts = run(str(db))

    assert counts["rows_skipped_no_snippet"] == 1
    assert counts["rows_changed"] == 0


def test_scrubs_webhook_secret_and_token_fields(tmp_path: Path) -> None:
    """All three field aliases (secret, webhook_secret, token) must be scrubbed."""
    db = tmp_path / "test.db"
    conn = _create_db(db)
    _insert_row(conn, 1, "webhook_rejected", {
        "strategy": "s", "division": "d", "reason": "bad",
        "client_ip": "1.2.3.4",
        "raw_body_snippet": (
            '{"webhook_secret": "abc123", "token": "tok456", "action": "sell"}'
        ),
    })
    conn.close()

    counts = run(str(db))

    conn = sqlite3.connect(str(db))
    snippet = json.loads(
        conn.execute("SELECT payload_json FROM audit_event WHERE id = 1").fetchone()[0]
    )["raw_body_snippet"]
    conn.close()

    assert "abc123" not in snippet
    assert "tok456" not in snippet
    assert snippet.count("***REDACTED***") == 2
    assert counts["rows_changed"] == 1
