"""Tests for the step-14 paper-run tooling.

Two scripts:
  - `scripts/ic_paper_run_readiness.py` — pre-kickoff sanity check.
  - `scripts/ic_daily_digest.py` — daily ops digest.

Tests run against synthetic data via the tmp_db fixture. The readiness
script needs the real production config files (it's verifying those
configs are valid), so we just run it as-is and assert the BLOCK checks
all pass against the production env that ship with the repo.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_corp.persistence import db
from trading_corp.scripts.ic_daily_digest import render_digest
from trading_corp.scripts.ic_paper_run_readiness import (
    ReadinessReport,
    _format_report,
    run_readiness_checks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_schema(db_url: str) -> None:
    from trading_corp.persistence.db import SCHEMA
    with db.connect(db_url) as conn:
        conn.executescript(SCHEMA)


def _insert_audit(db_url: str, *, ts: str, kind: str,
                  actor: str = "robinhood_joint_iron_condor",
                  payload: dict | None = None) -> None:
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event(ts, actor, kind, payload_json) "
            "VALUES(?,?,?,?)",
            (ts, actor, kind, json.dumps(payload or {})),
        )


def _write_open_ic_state(db_url: str, *, combo_id: str = "open-1",
                        symbol: str = "SPY",
                        scan_telemetry: dict | None = None) -> None:
    state = {
        "open_ics": {
            combo_id: {
                "symbol": symbol, "expiration": "2026-06-19",
                "credit_at_entry": 1.20, "ivr_at_entry": 45.0,
                "contracts": 1, "adjustment_count": 0,
                "opened_ts": "2026-05-15T15:00:00",
            },
        },
        "circuit_breaker": {
            "consecutive_losses": 1, "recent_pnl": [-0.30],
            "paused_until": None, "drawdown_hwm": None,
        },
        "scan_telemetry": scan_telemetry or {},
    }
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=db_url)


# ---------------------------------------------------------------------------
# Readiness check
# ---------------------------------------------------------------------------


def test_readiness_check_all_blocking_pass_on_production_config():
    """Against the repo's actual production config, every blocking check
    must pass. Soft VIX check is skipped here (no network in the test
    sandbox)."""
    report = run_readiness_checks(skip_network=True)
    failed_blocking = [c for c in report.checks if not c.ok and c.blocking]
    assert failed_blocking == [], (
        "blocking readiness checks failed:\n" +
        "\n".join(f"  [{c.name}] {c.detail}" for c in failed_blocking)
    )
    assert report.all_blocking_passed is True


def test_readiness_check_reports_block_status_correctly():
    """The CLI output formatter labels every BLOCK row + emits the READY
    status when all blockings pass."""
    report = run_readiness_checks(skip_network=True)
    text = _format_report(report)
    assert "Iron Condor - Paper-Run Readiness Check" in text
    assert "[BLOCK]" in text
    # All passed → READY status
    assert "READY" in text
    assert "NOT READY" not in text


def test_readiness_check_handles_db_path_override(tmp_path):
    """A non-production db_url makes the agent_state + audit_event
    checks fall to a fresh-empty database — still passes the schema
    checks because the checks construct the schema on-demand if
    missing."""
    from trading_corp.persistence.db import SCHEMA
    test_db = f"sqlite:///{(tmp_path / 'probe.db').as_posix()}"
    # Pre-create schema so the checks find audit_event + agent_state.
    with db.connect(test_db) as conn:
        conn.executescript(SCHEMA)
    report = run_readiness_checks(db_url=test_db, skip_network=True)
    assert report.all_blocking_passed is True


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------


def test_digest_empty_db_renders_zero_activity(tmp_db):
    _ensure_schema(tmp_db)
    text = render_digest(day=date(2026, 5, 17), db_url=tmp_db)
    # Always contains the header + day.
    assert "IC Daily Digest" in text
    assert "2026-05-17" in text
    # Zero counts.
    assert "`combo_proposed` | 0" in text
    assert "`ic_lifecycle_closed` | 0" in text
    # Empty open + closed sections.
    assert "_No open ICs._" in text
    assert "_No combo closes today._" in text
    assert "_No symbols filtered today" in text


def test_digest_renders_todays_combo_activity(tmp_db):
    _ensure_schema(tmp_db)
    day = date(2026, 5, 17)
    today_ts = "2026-05-17T15:30:00+00:00"
    yesterday_ts = "2026-05-16T15:30:00+00:00"

    # 3 audit events today + 1 yesterday (excluded).
    _insert_audit(tmp_db, ts=today_ts, kind="combo_proposed",
                  payload={"combo_id": "c1"})
    _insert_audit(tmp_db, ts=today_ts, kind="board_combo_approved",
                  actor="pending_combo_registry",
                  payload={"combo_id": "c1"})
    _insert_audit(tmp_db, ts=today_ts, kind="combo_filled",
                  actor="data_exec",
                  payload={"combo_id": "c1"})
    _insert_audit(tmp_db, ts=yesterday_ts, kind="combo_proposed",
                  payload={"combo_id": "old"})

    text = render_digest(day=day, db_url=tmp_db)
    # Today's counts.
    assert "`combo_proposed` | 1" in text
    assert "`board_combo_approved` | 1" in text
    assert "`combo_filled` | 1" in text


def test_digest_shows_open_ics_from_agent_state(tmp_db):
    _ensure_schema(tmp_db)
    _write_open_ic_state(tmp_db, combo_id="abc12345-ic", symbol="SPY")
    text = render_digest(day=date(2026, 5, 17), db_url=tmp_db)
    assert "Open ICs (1)" in text
    assert "abc12345" in text       # short_id rendered
    assert "SPY" in text


def test_digest_shows_closed_combo_and_realized_pnl(tmp_db):
    _ensure_schema(tmp_db)
    day = date(2026, 5, 17)
    _insert_audit(
        tmp_db, ts="2026-05-17T16:00:00+00:00",
        kind="ic_lifecycle_closed",
        payload={
            "combo_id": "closed-abc12345",
            "symbol": "SPY",
            "ivr_at_entry": 45.0,
            "adjustment_count": 0,
            "realized_pnl_dollars": 55.0,
            "close_kind": "profit_target",
        },
    )
    text = render_digest(day=day, db_url=tmp_db)
    assert "Closed today (1)" in text
    assert "closed-a" in text
    assert "profit_target" in text
    assert "$55.00" in text
    assert "Realized P&L today: $55.00" in text


def test_digest_circuit_breaker_paused_warning(tmp_db):
    _ensure_schema(tmp_db)
    paused_until = (
        datetime.now(timezone.utc) + timedelta(days=2)
    ).isoformat(timespec="seconds")
    state = {
        "open_ics": {},
        "circuit_breaker": {
            "consecutive_losses": 3, "recent_pnl": [-0.50, -0.30, -0.80],
            "paused_until": paused_until, "drawdown_hwm": None,
        },
        "scan_telemetry": {},
    }
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    text = render_digest(day=date(2026, 5, 17), db_url=tmp_db)
    assert "PAUSED until" in text
    assert "consecutive_losses: 3" in text


def test_digest_scan_filter_table(tmp_db):
    _ensure_schema(tmp_db)
    _write_open_ic_state(
        tmp_db, scan_telemetry={
            "2026-05-17": {
                "SPY": {"total": 3, "by_reason": {
                    "ivr_below_30": 2, "vix_above_30": 1,
                }},
                "QQQ": {"total": 1, "by_reason": {
                    "ex_dividend_window": 1,
                }},
            },
        },
    )
    text = render_digest(day=date(2026, 5, 17), db_url=tmp_db)
    assert "Total filtered passes today: **4**" in text
    assert "`ivr_below_30` | 2" in text
    assert "`vix_above_30` | 1" in text
    assert "`ex_dividend_window` | 1" in text


def test_digest_slippage_today_and_cumulative(tmp_db):
    _ensure_schema(tmp_db)
    day = date(2026, 5, 17)
    today_ts = "2026-05-17T15:00:00+00:00"
    yesterday_ts = "2026-05-16T15:00:00+00:00"
    # 1 fill today, 1 yesterday → today=1, cumulative=2
    _insert_audit(
        tmp_db, ts=today_ts, kind="combo_filled", actor="data_exec",
        payload={
            "combo_id": "c1", "strategy": "robinhood_joint_iron_condor",
            "division": "robinhood_joint",
            "direction": "credit",
            "net_limit_price": 1.00, "net_actual": 1.20,
            "actual_vs_limit_slippage_dollars": 0.20,
            "leg_count": 4, "legs": [],
        },
    )
    _insert_audit(
        tmp_db, ts=yesterday_ts, kind="combo_filled", actor="data_exec",
        payload={
            "combo_id": "c0", "strategy": "robinhood_joint_iron_condor",
            "division": "robinhood_joint",
            "direction": "credit",
            "net_limit_price": 1.00, "net_actual": 1.05,
            "actual_vs_limit_slippage_dollars": 0.05,
            "leg_count": 4, "legs": [],
        },
    )
    text = render_digest(day=day, db_url=tmp_db)
    assert "Today: **1** combo fills" in text
    assert "Cumulative: **2** combo fills" in text


def test_digest_30_day_window_with_realistic_volume(tmp_db):
    """End-to-end smoke: 30 days of synthetic combo activity. Digest
    renders without crashing; key sections populate; cumulative numbers
    line up."""
    _ensure_schema(tmp_db)
    start = date(2026, 4, 1)
    # 10 wins + 5 losses across 30 days.
    for i in range(10):
        ts = (datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(days=i))
        _insert_audit(
            tmp_db, ts=ts.isoformat(),
            kind="ic_lifecycle_closed",
            payload={
                "combo_id": f"win-{i}", "symbol": "SPY",
                "ivr_at_entry": 35.0 + i, "adjustment_count": 0,
                "realized_pnl_dollars": 50.0 + i,
                "close_kind": "profit_target",
            },
        )
    for i in range(5):
        ts = (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(days=i))
        _insert_audit(
            tmp_db, ts=ts.isoformat(),
            kind="ic_lifecycle_closed",
            payload={
                "combo_id": f"loss-{i}", "symbol": "SPY",
                "ivr_at_entry": 50.0 + i, "adjustment_count": 1,
                "realized_pnl_dollars": -120.0 - i,
                "close_kind": "hard_stop",
            },
        )
    text = render_digest(day=date(2026, 4, 30), db_url=tmp_db)
    # Adjustment outcome reflects 10 unadjusted (winners) + 5 adjusted (losers).
    # The "Adjusted vs unadjusted" table renders cleanly.
    assert "Adjusted | Unadjusted" in text
    assert "Count | 5 | 10 |" in text
    assert "Win rate | 0.0% | 100.0%" in text
    # IVR bucket section reflects entries split across buckets 30-40 and 40-50.
    assert "IVR-bucketed win rate" in text
    # Open ICs is 0 (we only inserted close events, no agent_state.open_ics).
    assert "Open ICs (0)" in text
