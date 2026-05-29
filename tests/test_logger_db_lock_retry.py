"""Tests for LoggerAgent.log_event() retry-with-backoff + file fallback.

Tests are written test-first (RED before implementation).

(a) Transient lock succeeds on retry — no raise, no fallback file.
(b) Exhausted retries → fallback JSONL written, returns None, no raise.
(c) Non-lock OperationalError re-raises.

Patching strategy: since sqlite3.Connection.execute is read-only in Python
3.14+, we patch db.connect at the module level to return a fake context
manager whose yielded connection raises on demand via a mock.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from trading_corp.agents import logger as logger_mod
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db


# ── helpers ──────────────────────────────────────────────────────────────

def _fallback_path(db_url: str) -> Path:
    """Derive the fallback JSONL path from the db_url (mirrors implementation)."""
    return db.resolve_db_path(db_url).parent / "audit_event_write_failed.jsonl"


def _make_patched_connect(real_connect, lock_error, fail_count: int):
    """Return a patched connect() whose conn.execute raises lock_error for the
    first `fail_count` INSERT-into-audit_event calls, then passes through.

    Because sqlite3.Connection.execute is read-only, we wrap the entire
    context manager with a fake connection proxy (MagicMock) that delegates
    all non-INSERT calls to the real connection and raises on INSERT.
    """
    insert_call_count = [0]

    @contextmanager
    def patched_connect(db_url: str):
        with real_connect(db_url) as real_conn:
            proxy = MagicMock(wraps=real_conn)

            def tracked_execute(sql, *args, **kwargs):
                if "INSERT INTO audit_event" in sql:
                    insert_call_count[0] += 1
                    if insert_call_count[0] <= fail_count:
                        raise lock_error
                # Route to real connection
                return real_conn.execute(sql, *args, **kwargs)

            proxy.execute = tracked_execute
            yield proxy

    return patched_connect


# ── (a) transient lock: retries then succeeds ────────────────────────────

def test_transient_lock_succeeds_on_retry(tmp_db, tmp_path, monkeypatch):
    """INSERT fails with 'database is locked' twice, then succeeds.

    Assertions:
    - log_event returns an integer row_id (not None)
    - The row IS present in audit_event
    - The fallback file is NOT created
    - time.sleep was called with jittered delays (len == 2 retries)
    """
    init_db(tmp_db)
    agent = LoggerAgent(tmp_db)

    # Patch retry delays to tiny values so the test runs fast.
    monkeypatch.setattr(logger_mod, "_DB_LOCK_RETRY_DELAYS_SEC", (0.001, 0.001, 0.001))

    sleep_calls: list[float] = []
    monkeypatch.setattr(logger_mod.time, "sleep", lambda s: sleep_calls.append(s))

    lock_error = sqlite3.OperationalError("database is locked")
    real_connect = db.connect
    monkeypatch.setattr(
        logger_mod.db, "connect",
        _make_patched_connect(real_connect, lock_error, fail_count=2),
    )

    row_id = agent.log_event("test_actor", "test_kind", {"key": "value"})

    assert row_id is not None, "Expected row_id, got None"
    assert isinstance(row_id, int)

    # Row should be in DB
    with db.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT id, actor, kind FROM audit_event WHERE actor = 'test_actor'"
        ).fetchall()
    assert len(rows) == 1, f"Expected 1 row, found {len(rows)}"

    # No fallback file
    fallback = _fallback_path(tmp_db)
    assert not fallback.exists(), "Fallback file should not be created on eventual success"

    # sleep was called for each retry (2 retries before 3rd attempt succeeded)
    assert len(sleep_calls) == 2, f"Expected 2 sleeps, got {len(sleep_calls)}: {sleep_calls}"
    for s in sleep_calls:
        assert s > 0


# ── (b) exhausted retries → fallback, returns None, no raise ─────────────

def test_exhausted_retries_writes_fallback_returns_none(tmp_db, monkeypatch):
    """INSERT always raises 'database is locked'.

    Assertions:
    - log_event returns None (does NOT raise)
    - The row is NOT present in audit_event
    - The fallback JSONL file exists with one line
    - The line's payload == the original payload dict
    """
    init_db(tmp_db)
    agent = LoggerAgent(tmp_db)

    monkeypatch.setattr(logger_mod, "_DB_LOCK_RETRY_DELAYS_SEC", (0.001, 0.001, 0.001))
    monkeypatch.setattr(logger_mod.time, "sleep", lambda s: None)

    # fail_count > len(delays) ensures it ALWAYS fails within the retry budget
    lock_error = sqlite3.OperationalError("database is locked")
    real_connect = db.connect
    monkeypatch.setattr(
        logger_mod.db, "connect",
        _make_patched_connect(real_connect, lock_error, fail_count=999),
    )

    payload = {"key": "exhaust_test", "num": 42}
    result = agent.log_event("actor_x", "kind_y", payload)

    assert result is None, f"Expected None, got {result!r}"

    # Row must NOT be in DB
    with db.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT id FROM audit_event WHERE actor = 'actor_x'"
        ).fetchall()
    assert len(rows) == 0, f"Row should not exist; found {len(rows)}"

    # Fallback file must exist with 1 valid JSON line
    fallback = _fallback_path(tmp_db)
    assert fallback.exists(), "Fallback JSONL file should be created"

    lines = [ln.strip() for ln in fallback.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"Expected 1 fallback line, got {len(lines)}"

    entry = json.loads(lines[0])
    assert entry["actor"] == "actor_x"
    assert entry["kind"] == "kind_y"
    assert entry["payload"] == payload
    assert "ts" in entry
    assert "error" in entry
    assert "attempts" in entry


# ── (c) non-lock OperationalError re-raises ──────────────────────────────

def test_non_lock_operational_error_reraises(tmp_db, monkeypatch):
    """An OperationalError NOT containing 'database is locked' must propagate."""
    init_db(tmp_db)
    agent = LoggerAgent(tmp_db)

    monkeypatch.setattr(logger_mod, "_DB_LOCK_RETRY_DELAYS_SEC", (0.001,))
    monkeypatch.setattr(logger_mod.time, "sleep", lambda s: None)

    other_error = sqlite3.OperationalError("no such table: audit_event")
    insert_called = [False]
    # Capture real_connect BEFORE patching to avoid recursion.
    real_connect = db.connect

    @contextmanager
    def raises_other_error(db_url: str):
        with real_connect(db_url) as real_conn:
            proxy = MagicMock(wraps=real_conn)

            def bad_execute(sql, *args, **kwargs):
                if "INSERT INTO audit_event" in sql:
                    insert_called[0] = True
                    raise other_error
                return real_conn.execute(sql, *args, **kwargs)

            proxy.execute = bad_execute
            yield proxy

    monkeypatch.setattr(logger_mod.db, "connect", raises_other_error)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        agent.log_event("actor", "kind", {"x": 1})

    assert insert_called[0], "execute should have been called"
