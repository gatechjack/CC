"""Tests for the HITL + tasty_options monitoring data functions.

Post 2026-07-07 tile reorg these back the HITL merge into the Pending
Approvals stat card and the tasty_options activation tile on the IC live
page (they previously fed the retired /partials/stage1-monitoring row).
These tests pin:
  • hitl_activity_24h — pending count, board decisions in window,
    autonomous-live invariant (must be 0 in Stage 1)
  • tasty_activation_status — broker session inference + scanner-tick
    placeholder (Fork #4 anomaly: scanner kind not currently emitted)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trading_corp.persistence import db as _db
from trading_corp.web import data as wd


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "hitl_tasty.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url


def _ins(db_url, *, actor, kind, payload=None, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?,?,?,?)",
            (ts, actor, kind, json.dumps(payload or {})),
        )


# ── HITL: pending ────────────────────────────────────────────────────────

def test_hitl_pending_zero_when_registry_none(fresh_db):
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["pending"] == 0


def test_hitl_pending_reads_pending_count(fresh_db):
    registry = SimpleNamespace(pending_count=lambda: 3)
    result = wd.hitl_activity_24h(fresh_db, pending_registry=registry)
    assert result["pending"] == 3


def test_hitl_pending_swallows_registry_exception(fresh_db):
    """A buggy registry must not crash the monitoring tile."""
    def _raise():
        raise RuntimeError("registry exploded")
    registry = SimpleNamespace(pending_count=_raise)
    result = wd.hitl_activity_24h(fresh_db, pending_registry=registry)
    assert result["pending"] == 0


# ── HITL: board decisions in 24h ──────────────────────────────────────────

def test_hitl_board_decisions_counted(fresh_db):
    _ins(fresh_db, actor="board", kind="board_approved")
    _ins(fresh_db, actor="board", kind="board_approved")
    _ins(fresh_db, actor="board", kind="board_rejected")
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["approved_24h"] == 2
    assert result["rejected_24h"] == 1


def test_hitl_board_decisions_window(fresh_db):
    """Decisions older than 24h must NOT count."""
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    inside = (now - timedelta(hours=12)).isoformat()
    outside = (now - timedelta(hours=25)).isoformat()
    _ins(fresh_db, actor="board", kind="board_approved", ts=inside)
    _ins(fresh_db, actor="board", kind="board_approved", ts=outside)
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None, now=now)
    assert result["approved_24h"] == 1


def test_hitl_board_decisions_actor_filter(fresh_db):
    """Only actor='board' rows count — other actors' kind collisions
    (if any) must be excluded."""
    _ins(fresh_db, actor="not_the_board", kind="board_approved")
    _ins(fresh_db, actor="board", kind="board_approved")
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["approved_24h"] == 1


# ── HITL: autonomous-live invariant ───────────────────────────────────────

def test_hitl_autonomous_live_zero_by_default(fresh_db):
    """Empty DB → autonomous_live_24h = 0 → severity = green."""
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["autonomous_live_24h"] == 0
    assert result["severity"] == "green"


def test_hitl_autonomous_live_excludes_hitl_required(fresh_db):
    """live_order_placed with hitl_gate='required' (HITL'd) must NOT
    count — Stage-1 invariant is specifically about HITL bypass."""
    _ins(
        fresh_db, actor="bitunix_futures", kind="live_order_placed",
        payload={
            "execution_mode": "live",
            "hitl_gate": "required",
            "symbol": "BTC",
        },
    )
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["autonomous_live_24h"] == 0
    assert result["severity"] == "green"


def test_hitl_autonomous_live_counts_monitor_mode(fresh_db):
    """live_order_placed with hitl_gate='monitor_mode' (HITL bypassed
    after first-N cap) is the invariant-breaking row."""
    _ins(
        fresh_db, actor="bitunix_futures", kind="live_order_placed",
        payload={
            "execution_mode": "live",
            "hitl_gate": "monitor_mode",
            "symbol": "BTC",
        },
    )
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["autonomous_live_24h"] == 1
    assert result["severity"] == "red"


def test_hitl_autonomous_live_window(fresh_db):
    """Autonomous live orders older than 24h must NOT count."""
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    outside = (now - timedelta(hours=25)).isoformat()
    _ins(
        fresh_db, actor="bitunix_futures", kind="live_order_placed",
        payload={"hitl_gate": "monitor_mode"},
        ts=outside,
    )
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None, now=now)
    assert result["autonomous_live_24h"] == 0


def test_hitl_autonomous_live_actor_filter(fresh_db):
    """Only actor='bitunix_futures' counts — other actors' live_order_placed
    are out of Stage-1 scope."""
    _ins(
        fresh_db, actor="some_other_division", kind="live_order_placed",
        payload={"hitl_gate": "monitor_mode"},
    )
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None)
    assert result["autonomous_live_24h"] == 0


# ── tasty_options activation ──────────────────────────────────────────────

def test_tasty_activation_unwired_when_brokers_map_none():
    result = wd.tasty_activation_status(None)
    assert result["session"] == "unwired"
    assert result["broker_name"] == "—"
    assert result["scanner_tick_rate"] is None


def test_tasty_activation_unwired_when_no_tasty_broker_in_map():
    """brokers_map has only non-tasty brokers."""
    brokers = {"bitunix_futures": SimpleNamespace(_connected=True)}
    result = wd.tasty_activation_status(brokers)
    assert result["session"] == "unwired"


def test_tasty_activation_connected_when_connected():
    tasty_broker = SimpleNamespace(_connected=True, name="TastytradeBroker")
    brokers = {"tasty_options": tasty_broker}
    result = wd.tasty_activation_status(brokers)
    assert result["session"] == "connected"
    assert result["broker_name"] == "TastytradeBroker"


def test_tasty_activation_disconnected_when_not_connected():
    tasty_broker = SimpleNamespace(_connected=False, name="TastytradeBroker")
    brokers = {"tasty_options": tasty_broker}
    result = wd.tasty_activation_status(brokers)
    assert result["session"] == "disconnected"


def test_tasty_activation_finds_broker_by_prefix():
    """Falls back to scanning for tasty* slugs if the canonical key
    isn't present (defensive for slug-naming churn)."""
    tasty_broker = SimpleNamespace(_connected=True, name="TastytradeBroker")
    brokers = {"tasty_options_paper": tasty_broker}
    result = wd.tasty_activation_status(brokers)
    assert result["session"] == "connected"


def test_tasty_activation_scanner_tick_always_none_until_audit_kind_lands():
    """Fork #4 anomaly: scanner doesn't emit per-cycle audit. Pin that
    the return is None — when the BACKLOG item lands, this test breaks
    deliberately and tells the next session to wire the rate read."""
    tasty_broker = SimpleNamespace(_connected=True, name="TT")
    brokers = {"tasty_options": tasty_broker}
    result = wd.tasty_activation_status(brokers)
    assert result["scanner_tick_rate"] is None


# ── Return shape ──────────────────────────────────────────────────────────

def test_hitl_since_iso_is_24h_before_now(fresh_db):
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = wd.hitl_activity_24h(fresh_db, pending_registry=None, now=now)
    expected = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    assert result["since_iso"] == expected
