"""Tests for scripts/replay_audit_event_write_failed.py

Scenario:
- Fallback JSONL has 2 rows: one already in audit_event (duplicate), one new.
- After replay: new row inserted, duplicate skipped, stats correct.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.persistence import db
from trading_corp.persistence.db import init_db

# Make sure the scripts dir is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fallback_path(db_url: str) -> Path:
    return db.resolve_db_path(db_url).parent / "audit_event_write_failed.jsonl"


def _insert_audit_event(conn, ts: str, actor: str, kind: str, payload: dict) -> int:
    payload_json = json.dumps(payload, separators=(",", ":"))
    cur = conn.execute(
        "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
        (ts, actor, kind, payload_json),
    )
    return cur.lastrowid


def test_replay_inserts_new_skips_duplicate(tmp_db, tmp_path):
    """Run replay with 2 fallback rows (1 pre-existing, 1 new).

    Expected:
    - scanned == 2
    - inserted == 1
    - skipped_existing == 1
    """
    import importlib.util, importlib

    init_db(tmp_db)

    # Insert the "already present" row into audit_event
    ts_existing = "2026-05-28T01:00:00+00:00"
    actor_existing = "test_actor_existing"
    kind_existing = "test_kind"
    payload_existing = {"marker": "pre_existing"}

    with db.connect(tmp_db) as conn:
        _insert_audit_event(conn, ts_existing, actor_existing, kind_existing, payload_existing)

    # Build the fallback JSONL with 2 rows
    fallback_file = _fallback_path(tmp_db)
    fallback_file.parent.mkdir(parents=True, exist_ok=True)

    ts_new = "2026-05-28T02:00:00+00:00"
    actor_new = "test_actor_new"
    kind_new = "test_kind"
    payload_new = {"marker": "new_row"}

    with fallback_file.open("w", encoding="utf-8") as f:
        # Row 1: duplicate (matches existing row)
        f.write(json.dumps({
            "ts": ts_existing,
            "actor": actor_existing,
            "kind": kind_existing,
            "payload": payload_existing,
            "error": "database is locked",
            "attempts": 4,
        }) + "\n")
        # Row 2: new
        f.write(json.dumps({
            "ts": ts_new,
            "actor": actor_new,
            "kind": kind_new,
            "payload": payload_new,
            "error": "database is locked",
            "attempts": 4,
        }) + "\n")

    # Import and run the replay script
    spec = importlib.util.spec_from_file_location(
        "replay_audit_event_write_failed",
        ROOT / "scripts" / "replay_audit_event_write_failed.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    stats = module.replay(db_url=tmp_db)

    assert stats["scanned"] == 2, f"Expected scanned=2, got {stats}"
    assert stats["inserted"] == 1, f"Expected inserted=1, got {stats}"
    assert stats["skipped_existing"] == 1, f"Expected skipped_existing=1, got {stats}"

    # Verify the new row is actually in audit_event
    with db.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT ts, actor, kind, payload_json FROM audit_event WHERE actor = ?",
            (actor_new,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["actor"] == actor_new
    assert json.loads(rows[0]["payload_json"]) == payload_new

    # Fallback file should be drained (renamed or emptied) after full replay
    # Implementation choice: file renamed to .replayed-<ts> when fully drained.
    # Either the original file is gone or it's empty.
    if fallback_file.exists():
        remaining = [l for l in fallback_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(remaining) == 0, f"Fallback file should be empty after full drain; has {len(remaining)} lines"


def test_replay_dry_run_does_not_insert(tmp_db, tmp_path):
    """--dry-run: scans and counts but does not insert."""
    import importlib.util

    init_db(tmp_db)

    fallback_file = _fallback_path(tmp_db)
    fallback_file.parent.mkdir(parents=True, exist_ok=True)

    with fallback_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": "2026-05-28T03:00:00+00:00",
            "actor": "dry_run_actor",
            "kind": "dry_run_kind",
            "payload": {"x": 1},
            "error": "database is locked",
            "attempts": 4,
        }) + "\n")

    spec = importlib.util.spec_from_file_location(
        "replay_audit_event_write_failed",
        ROOT / "scripts" / "replay_audit_event_write_failed.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    stats = module.replay(db_url=tmp_db, dry_run=True)

    assert stats["scanned"] == 1
    assert stats["inserted"] == 0

    with db.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT id FROM audit_event WHERE actor = 'dry_run_actor'"
        ).fetchall()
    assert len(rows) == 0, "dry-run must not insert"
