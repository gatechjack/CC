"""Tests for the Gate (a) REST resilience 24h tile.

Pins:
  • the audit-kind mapping (the request named rest_retry /
    stuck_order_timeout but the actual emit sites use
    rest_request_retried + stuck_order_cancelled +
    stuck_order_cancel_failed — see web/data.py constants)
  • the 24h window boundary (anything older than 24h is excluded)
  • the severity ladder (green / yellow / red) including the
    rest_request_retried > 10 escalation
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web import data as wd


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "gate_a.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url


def _ins(db_url, kind, *, ts=None, actor="bitunix_broker", payload=None):
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?,?,?,?)",
            (ts, actor, kind, json.dumps(payload or {})),
        )


# ── Kind taxonomy constants ───────────────────────────────────────────────

def test_constants_match_actual_emit_sites():
    """Pins the kind strings against the actual emit sites in
    trading_corp/brokers/bitunix.py and trading_corp/agents/data_exec.py.
    If those emit sites rename a kind, these tests must update too."""
    assert wd.GATE_A_KIND_REST_RETRIED == "rest_request_retried"
    assert wd.GATE_A_KIND_SNAPSHOT_STALE == "snapshot_stale_halt"
    assert wd.GATE_A_KIND_STUCK_CANCELLED == "stuck_order_cancelled"
    assert wd.GATE_A_KIND_STUCK_CANCEL_FAILED == "stuck_order_cancel_failed"
    assert set(wd.GATE_A_KINDS) == {
        "rest_request_retried", "snapshot_stale_halt",
        "stuck_order_cancelled", "stuck_order_cancel_failed",
    }


# ── Counts ────────────────────────────────────────────────────────────────

def test_empty_db_returns_all_zero(fresh_db):
    result = wd.gate_a_resilience_24h(fresh_db)
    assert result["total"] == 0
    for k in wd.GATE_A_KINDS:
        assert result["by_kind"][k] == 0
    assert result["severity"] == "green"


def test_counts_each_kind_independently(fresh_db):
    _ins(fresh_db, "rest_request_retried")
    _ins(fresh_db, "rest_request_retried")
    _ins(fresh_db, "snapshot_stale_halt", actor="data_exec")
    _ins(fresh_db, "stuck_order_cancelled")
    _ins(fresh_db, "stuck_order_cancel_failed")
    result = wd.gate_a_resilience_24h(fresh_db)
    assert result["by_kind"]["rest_request_retried"] == 2
    assert result["by_kind"]["snapshot_stale_halt"] == 1
    assert result["by_kind"]["stuck_order_cancelled"] == 1
    assert result["by_kind"]["stuck_order_cancel_failed"] == 1
    assert result["total"] == 5


def test_excludes_rows_older_than_24h(fresh_db):
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    inside_window = (now - timedelta(hours=23, minutes=59)).isoformat()
    outside_window = (now - timedelta(hours=24, minutes=1)).isoformat()
    _ins(fresh_db, "rest_request_retried", ts=inside_window)
    _ins(fresh_db, "rest_request_retried", ts=outside_window)
    result = wd.gate_a_resilience_24h(fresh_db, now=now)
    assert result["by_kind"]["rest_request_retried"] == 1
    assert result["total"] == 1


def test_excludes_non_gate_a_kinds(fresh_db):
    """Other audit kinds in the same window do not count."""
    _ins(fresh_db, "fill", payload={"symbol": "BTC"})
    _ins(fresh_db, "would_have_placed", actor="bitunix_futures",
         payload={"symbol": "BTC"})
    _ins(fresh_db, "board_approved", actor="board")
    result = wd.gate_a_resilience_24h(fresh_db)
    assert result["total"] == 0
    assert result["severity"] == "green"


# ── Severity ladder ───────────────────────────────────────────────────────

def test_severity_green_when_zero(fresh_db):
    result = wd.gate_a_resilience_24h(fresh_db)
    assert result["severity"] == "green"


def test_severity_yellow_for_isolated_rest_retry(fresh_db):
    _ins(fresh_db, "rest_request_retried")
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "yellow"


def test_severity_yellow_for_stuck_cancelled(fresh_db):
    """Stuck order that DID get cancelled — transient, handled."""
    _ins(fresh_db, "stuck_order_cancelled")
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "yellow"


def test_severity_red_for_snapshot_stale(fresh_db):
    """System-protective halt — must surface in red."""
    _ins(fresh_db, "snapshot_stale_halt", actor="data_exec")
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "red"


def test_severity_red_for_cancel_failed(fresh_db):
    """Stuck order cancel itself failed — order may still rest at venue."""
    _ins(fresh_db, "stuck_order_cancel_failed")
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "red"


def test_severity_red_when_rest_retries_exceed_threshold(fresh_db):
    """Sustained API churn (>10 retries in 24h) escalates to red."""
    for _ in range(11):
        _ins(fresh_db, "rest_request_retried")
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "red"


def test_severity_yellow_at_rest_retries_threshold(fresh_db):
    """Boundary: exactly 10 rest retries stays yellow (escalation is > 10)."""
    for _ in range(10):
        _ins(fresh_db, "rest_request_retried")
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "yellow"


def test_severity_red_dominates_mixed_signals(fresh_db):
    """Yellow signals + a single red signal → red overall."""
    _ins(fresh_db, "rest_request_retried")  # yellow
    _ins(fresh_db, "stuck_order_cancelled")  # yellow
    _ins(fresh_db, "snapshot_stale_halt", actor="data_exec")  # red
    assert wd.gate_a_resilience_24h(fresh_db)["severity"] == "red"


# ── Return shape ──────────────────────────────────────────────────────────

def test_since_iso_is_24h_before_now(fresh_db):
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = wd.gate_a_resilience_24h(fresh_db, now=now)
    expected_since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    assert result["since_iso"] == expected_since


def test_by_kind_always_has_all_four_keys(fresh_db):
    """Template iterates over all four kinds — they must always be present
    in the dict, even when zero."""
    result = wd.gate_a_resilience_24h(fresh_db)
    assert set(result["by_kind"].keys()) == set(wd.GATE_A_KINDS)
