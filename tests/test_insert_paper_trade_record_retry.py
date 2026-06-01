"""Tests for Decision 6.2 — DB-lock retry on `insert_paper_trade_record`.

Session B Commit 2 of N+2 Phase 3. Validates:

- Happy path (no lock contention) writes the row in one attempt.
- Transient 'database is locked' OperationalError triggers a jittered
  retry per the `_DB_LOCK_RETRY_DELAYS_SEC = (0.1, 0.3, 0.7)` schedule
  (same as `LoggerAgent.log_event`).
- Retry exhaustion (4 total attempts) re-raises the OperationalError
  for the caller's existing try/except to handle.
- Non-lock OperationalErrors (e.g. schema drift) propagate immediately.
- INSERT OR IGNORE idempotency holds: a row that landed on an earlier
  attempt remains a no-op on retry (no duplicate-PK error).
- Existing happy-path callers (Path C, paper-mode helper) don't regress.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_corp.persistence import db
from trading_corp.persistence.db import (
    _DB_LOCK_RETRY_DELAYS_SEC,
    insert_paper_trade_record,
)


# ─── fixtures ───────────────────────────────────────────────────────────


def _record(order_id: str = "ord-1") -> dict:
    """Minimal valid paper_trade_record row."""
    return {
        "order_id": order_id,
        "ts": "2026-06-01T10:00:00+00:00",
        "strategy": "bitunix_futures",
        "division": "bitunix_futures",
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 0.001,
        "entry_reference_price": 80_000.0,
        "stop_price": 79_500.0,
        "tp_price": 81_000.0,
        "max_hold_seconds": 7200,
        "result": None,
        "extra_json": None,
    }


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "retry.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


@pytest.fixture(autouse=True)
def _instant_retry(monkeypatch):
    """Monkeypatch the retry delays to near-zero so tests stay fast."""
    monkeypatch.setattr(
        db, "_DB_LOCK_RETRY_DELAYS_SEC",
        (0.001, 0.001, 0.001),
    )


# ─── happy path ─────────────────────────────────────────────────────────


def test_happy_path_single_attempt(db_url):
    insert_paper_trade_record(_record("ord-h1"), db_url=db_url)
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT order_id FROM paper_trade_record WHERE order_id=?",
            ("ord-h1",),
        ).fetchone()
    assert row["order_id"] == "ord-h1"


def test_insert_or_ignore_duplicate_no_error(db_url):
    """Pre-existing INSERT OR IGNORE semantics preserved by the retry
    wrapper — calling twice for the same order_id is a no-op."""
    insert_paper_trade_record(_record("ord-dup"), db_url=db_url)
    insert_paper_trade_record(_record("ord-dup"), db_url=db_url)
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) c FROM paper_trade_record WHERE order_id=?",
            ("ord-dup",),
        ).fetchone()
    assert rows["c"] == 1


# ─── retry on transient lock ────────────────────────────────────────────


class _CountingConnect:
    """Patches db.connect to raise OperationalError(database is locked)
    for the first N attempts, then delegate to the real connect."""

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.attempts = 0
        self._real_connect = db.connect

    def __call__(self, db_url):
        self.attempts += 1
        if self.attempts <= self.fail_count:
            # Mimic the SQLite lock error shape
            raise sqlite3.OperationalError("database is locked")
        return self._real_connect(db_url)


def test_retries_on_transient_lock_then_succeeds(db_url, monkeypatch):
    """1 transient lock → succeeds on retry (attempt 2)."""
    counter = _CountingConnect(fail_count=1)
    monkeypatch.setattr(db, "connect", counter)

    insert_paper_trade_record(_record("ord-r1"), db_url=db_url)

    assert counter.attempts == 2, (
        "expected 1 lock + 1 successful retry = 2 total attempts"
    )
    # Verify the row actually landed
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT order_id FROM paper_trade_record WHERE order_id=?",
            ("ord-r1",),
        ).fetchone()
    assert row["order_id"] == "ord-r1"


def test_retries_up_to_schedule_length(db_url, monkeypatch):
    """3 transient locks → succeeds on attempt 4 (matches the schedule
    length of 3 retries after the initial attempt)."""
    counter = _CountingConnect(
        fail_count=len(_DB_LOCK_RETRY_DELAYS_SEC),  # 3
    )
    monkeypatch.setattr(db, "connect", counter)

    insert_paper_trade_record(_record("ord-r3"), db_url=db_url)

    assert counter.attempts == len(_DB_LOCK_RETRY_DELAYS_SEC) + 1  # 4


# ─── retry exhaustion ───────────────────────────────────────────────────


def test_exhausted_retries_reraise_operational_error(db_url, monkeypatch):
    """More transient locks than the schedule allows → re-raise so the
    caller's try/except (already present at Path C, _record_placement_outcome)
    can handle it."""
    counter = _CountingConnect(fail_count=99)  # never succeeds
    monkeypatch.setattr(db, "connect", counter)

    with pytest.raises(sqlite3.OperationalError) as exc_info:
        insert_paper_trade_record(_record("ord-x"), db_url=db_url)

    assert "database is locked" in str(exc_info.value).lower()
    # Exactly 4 attempts before re-raise (1 initial + 3 retries)
    assert counter.attempts == len(_DB_LOCK_RETRY_DELAYS_SEC) + 1


# ─── non-lock errors propagate immediately ──────────────────────────────


def test_non_lock_operational_error_propagates_immediately(
    db_url, monkeypatch,
):
    """A real bug (e.g. schema drift, missing table) must not be
    retry-masked. The retry layer keys on the 'database is locked'
    substring; other OperationalErrors re-raise on the first attempt."""

    real_connect = db.connect
    attempts = {"n": 0}

    def _flaky(db_url):
        attempts["n"] += 1
        # Mimic a non-lock OperationalError (e.g. no such table).
        raise sqlite3.OperationalError("no such table: paper_trade_record")

    monkeypatch.setattr(db, "connect", _flaky)

    with pytest.raises(sqlite3.OperationalError) as exc_info:
        insert_paper_trade_record(_record("ord-n"), db_url=db_url)

    assert "no such table" in str(exc_info.value).lower()
    assert attempts["n"] == 1, (
        "non-lock OperationalError must not be retried"
    )


# ─── retry counter resets between calls ─────────────────────────────────


def test_retry_counter_isolated_per_call(db_url, monkeypatch):
    """Two successive calls each get their own retry budget — no
    cross-call counter pollution."""

    # First call: 2 locks, then succeed.
    counter_a = _CountingConnect(fail_count=2)
    monkeypatch.setattr(db, "connect", counter_a)
    insert_paper_trade_record(_record("ord-a"), db_url=db_url)
    assert counter_a.attempts == 3

    # Second call: 3 locks then succeed (fresh budget).
    counter_b = _CountingConnect(fail_count=3)
    monkeypatch.setattr(db, "connect", counter_b)
    insert_paper_trade_record(_record("ord-b"), db_url=db_url)
    assert counter_b.attempts == 4
