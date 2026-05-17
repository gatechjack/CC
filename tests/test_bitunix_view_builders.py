"""Smoke tests for trade-plan PR 6 view builders.

Covers `build_bitunix_pa_view` + `build_bitunix_decision_flow_view`:
  - Missing-observer / missing-config fallthroughs return the right shape
    (None vs empty {"flows": []}).
  - Counts aggregation over `pa_validation_decision` audit rows.
  - Signal-keyed ±60s join between score-decided + PA + HTF audits.

Filed per the PR 6 "missing test coverage" followup. View builders
are read-only against the audit_event table; tests stand up an
in-memory SQLite DB, insert canned audit rows, and assert structure.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.persistence import db
from trading_corp.web.data import (
    build_bitunix_decision_flow_view,
    build_bitunix_pa_view,
)


def _ts(offset_seconds: int = 0) -> str:
    return (
        datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
        + timedelta(seconds=offset_seconds)
    ).isoformat()


def _insert_audit(conn, kind: str, payload: dict, ts: str) -> None:
    conn.execute(
        "INSERT INTO audit_event (ts, actor, kind, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (ts, "bitunix_futures", kind, json.dumps(payload)),
    )


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'test_view_builders.db'}"
    db.init_db(url)
    return url


# ─── build_bitunix_pa_view ─────────────────────────────────────────────


def test_pa_view_returns_none_when_no_pa_config(db_url: str):
    """Observer wired but without pa_config → builder returns None so
    the template `{% if view.bitunix_pa %}` guard hides the panel."""
    deps = SimpleNamespace(bitunix_observer=SimpleNamespace(pa_config=None))
    assert build_bitunix_pa_view(db_url, deps) is None


def test_pa_view_returns_none_when_observer_missing(db_url: str):
    deps = SimpleNamespace(bitunix_observer=None)
    assert build_bitunix_pa_view(db_url, deps) is None


def test_pa_view_empty_state_when_no_audit_rows(db_url: str):
    deps = SimpleNamespace(
        bitunix_observer=SimpleNamespace(
            pa_config=SimpleNamespace(enabled=True)
        )
    )
    view = build_bitunix_pa_view(db_url, deps)
    assert view is not None
    assert view["enabled"] is True
    assert view["latest"] is None
    assert view["recent"] == []
    assert view["counts"] == {"pass": 0, "reject": 0, "rush_fall": 0}


def test_pa_view_counts_pass_reject_rush_fall(db_url: str):
    """Three audits: 1 PASS, 1 REJECT, 1 PASS-with-rush_fall. Counts
    should aggregate independently (rush_fall counted alongside the
    decision, not exclusive)."""
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "PASS",
                "passed": ["vwap", "volume", "structure"],
                "failed": [],
                "rush_fall_triggered": False,
                "mode": "shadow",
                "trigger_signal": "otter_3m_pump",
            },
            _ts(0),
        )
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "REJECT",
                "passed": ["vwap"],
                "failed": ["volume", "structure"],
                "rush_fall_triggered": False,
                "mode": "shadow",
                "trigger_signal": "otter_15m_dump",
            },
            _ts(60),
        )
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "PASS",
                "passed": ["vwap", "volume", "structure"],
                "failed": [],
                "rush_fall_triggered": True,
                "mode": "shadow",
                "trigger_signal": "otter_3m_pump",
            },
            _ts(120),
        )

    deps = SimpleNamespace(
        bitunix_observer=SimpleNamespace(
            pa_config=SimpleNamespace(enabled=True)
        )
    )
    view = build_bitunix_pa_view(db_url, deps)

    assert view["counts"] == {"pass": 2, "reject": 1, "rush_fall": 1}
    assert view["latest"]["decision"] == "PASS"  # newest by ts DESC
    assert view["latest"]["rush_fall_triggered"] is True
    assert len(view["recent"]) == 3


# ─── build_bitunix_decision_flow_view ──────────────────────────────────


def test_decision_flow_returns_none_when_observer_missing(db_url: str):
    deps = SimpleNamespace(bitunix_observer=None)
    assert build_bitunix_decision_flow_view(db_url, deps) is None


def test_decision_flow_empty_when_no_score_audits(db_url: str):
    """Observer wired but no score-decided rows → empty flows list."""
    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert view == {"flows": []}


def test_decision_flow_joins_pa_and_htf_within_60s_window(db_url: str):
    """Score row at t=0; PA row at t=+10s same signal; HTF row at
    t=-20s same signal. Both should attach to the flow."""
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "bitunix_score_decided",
            {
                "tier": "PREMIUM",
                "side": "buy",
                "net_score": 12,
                "trigger_signal": "otter_3m_pump",
                "outcome": "placed",
            },
            _ts(0),
        )
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "PASS",
                "passed": ["vwap", "volume", "structure"],
                "failed": [],
                "rush_fall_triggered": False,
                "mode": "shadow",
                "trigger_signal": "otter_3m_pump",
            },
            _ts(10),
        )
        _insert_audit(
            conn,
            "htf_gate_decision",
            {
                "regime": "BULL",
                "size_multiplier": 1.0,
                "permission_reason": "regime-aligned",
                "mode": "shadow",
                "trigger_signal": "otter_3m_pump",
            },
            _ts(-20),
        )

    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert len(view["flows"]) == 1
    f = view["flows"][0]
    assert f["score"]["tier"] == "PREMIUM"
    assert f["pa"]["decision"] == "PASS"
    assert f["htf"]["regime"] == "BULL"
    assert f["htf"]["size_multiplier"] == 1.0
    assert f["outcome"] == "placed"
    assert f["alert_tf"] == "3m"  # heuristic parse of otter_3m_pump


def test_decision_flow_skips_pa_outside_60s_window(db_url: str):
    """Score at t=0; PA at t=+120s same signal → outside window, PA
    should be None on the flow."""
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "bitunix_score_decided",
            {
                "tier": "STANDARD",
                "side": "sell",
                "net_score": -7,
                "trigger_signal": "mc_b_dump",
                "outcome": "skipped_pa_reject",
            },
            _ts(0),
        )
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "REJECT",
                "passed": [],
                "failed": ["volume"],
                "rush_fall_triggered": False,
                "mode": "shadow",
                "trigger_signal": "mc_b_dump",
            },
            _ts(120),  # outside the ±60s window
        )

    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert len(view["flows"]) == 1
    assert view["flows"][0]["pa"] is None
    assert view["flows"][0]["htf"] is None


def test_decision_flow_skips_pa_on_signal_mismatch(db_url: str):
    """Score with trigger='otter_3m_pump'; PA with trigger='mc_b_dump'
    at the same ts → signal mismatch, PA should be None."""
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "bitunix_score_decided",
            {
                "tier": "PREMIUM",
                "side": "buy",
                "net_score": 11,
                "trigger_signal": "otter_3m_pump",
                "outcome": "placed",
            },
            _ts(0),
        )
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "REJECT",
                "passed": [],
                "failed": ["vwap"],
                "rush_fall_triggered": False,
                "mode": "shadow",
                "trigger_signal": "mc_b_dump",  # different signal
            },
            _ts(5),
        )

    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert view["flows"][0]["pa"] is None


def test_decision_flow_caps_at_5_score_rows(db_url: str):
    """Build 8 score audits; the view should return the most recent 5."""
    with db.connect(db_url) as conn:
        for i in range(8):
            _insert_audit(
                conn,
                "bitunix_score_decided",
                {
                    "tier": "WEAK",
                    "side": "buy",
                    "net_score": i,
                    "trigger_signal": f"sig_{i}",
                    "outcome": "placed",
                },
                _ts(i * 10),
            )
    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert len(view["flows"]) == 5
    # Newest first (ts DESC) → sig_7, sig_6, ..., sig_3
    assert view["flows"][0]["trigger_signal"] == "sig_7"
    assert view["flows"][-1]["trigger_signal"] == "sig_3"


# ─── build_bitunix_pending_pa_view (deferred-fire dashboard #1) ────────


from trading_corp.web.data import build_bitunix_pending_pa_view


def test_pending_pa_view_returns_none_when_observer_missing():
    deps = SimpleNamespace(bitunix_observer=None)
    assert build_bitunix_pending_pa_view(deps) is None


def test_pending_pa_view_empty_state_when_nothing_cached(db_url: str):
    """Observer wired but no cached payload → cached=False with default
    fields so the template renders 'no signal pending' without errors."""
    deps = SimpleNamespace(
        db_url=db_url,
        bitunix_observer=SimpleNamespace(
            _pending_pa_payload=None,
            _pending_pa_side=None,
            _pending_pa_cached_at_ts=None,
        ),
    )
    view = build_bitunix_pending_pa_view(deps)
    assert view == {
        "cached": False,
        "side": None,
        "signal": None,
        "cached_at": None,
        "cached_at_et": None,
        "bars_waited": 0,
        "seconds_waited": 0,
        "last_failed": [],
        "last_pa_decision_reason": None,
    }


def test_pending_pa_view_populated_when_cache_set(db_url: str):
    """Cache populated + an existing pa_validation_decision REJECT for the
    same signal → view returns side/bars_waited/last_failed enrichment."""
    cached_at = datetime.now(timezone.utc) - timedelta(minutes=12)  # 4 bars
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "pa_validation_decision",
            {
                "decision": "REJECT",
                "passed": ["vwap_alignment"],
                "failed": ["volume_confirmation", "structure_alignment"],
                "rush_fall_triggered": False,
                "mode": "enforce",
                "trigger_signal": "mc_a_red_diamond",
                "reason": "REJECT: require_all (passed 1/3)",
            },
            cached_at.isoformat(),
        )
    deps = SimpleNamespace(
        db_url=db_url,
        bitunix_observer=SimpleNamespace(
            _pending_pa_payload={
                "signal": "mc_a_red_diamond", "symbol": "BTCUSDT",
            },
            _pending_pa_side="sell",
            _pending_pa_cached_at_ts=cached_at,
        ),
    )
    view = build_bitunix_pending_pa_view(deps)
    assert view["cached"] is True
    assert view["side"] == "sell"
    assert view["signal"] == "mc_a_red_diamond"
    assert view["bars_waited"] >= 3  # 12 min wait ~ 4 bars (allow for drift)
    assert view["seconds_waited"] >= 700
    assert view["last_failed"] == [
        "volume_confirmation", "structure_alignment",
    ]
    assert "require_all" in (view["last_pa_decision_reason"] or "")


# ─── PA view extension: redeem/expired aggregates (dashboard #2) ────────


def test_pa_view_redeem_counts_zero_when_no_audit_rows(db_url: str):
    deps = SimpleNamespace(
        bitunix_observer=SimpleNamespace(pa_config=SimpleNamespace(enabled=True))
    )
    view = build_bitunix_pa_view(db_url, deps)
    assert view["redeem_counts"] == {
        "redeemed_24h": 0,
        "expired_score_decay_24h": 0,
        "expired_opposite_side_24h": 0,
    }
    assert view["recent_redeems"] == []
    assert view["recent_expired"] == []


def test_pa_view_redeem_counts_24h_window(db_url: str):
    """3 redeemed + 2 expired (score_decay) + 1 expired (opposite_side)
    in the last 24h. One stale row (40h old) must NOT count."""
    now = datetime.now(timezone.utc)
    with db.connect(db_url) as conn:
        for i in range(3):
            _insert_audit(
                conn,
                "pa_validation_redeem",
                {
                    "trigger_signal": "mc_a_red_diamond",
                    "bars_waited": i + 1,
                    "seconds_waited": (i + 1) * 180,
                    "final_side": "sell",
                    "final_tier": "STANDARD",
                    "order_id": f"OID-{i}" if i < 2 else None,
                },
                (now - timedelta(minutes=10 + i)).isoformat(),
            )
        for i in range(2):
            _insert_audit(
                conn,
                "pa_validation_expired",
                {
                    "trigger_signal": "spoon_bear",
                    "reason": "score_decay",
                    "bars_waited": 5,
                    "seconds_waited": 900,
                    "cached_side": "sell",
                },
                (now - timedelta(minutes=20 + i)).isoformat(),
            )
        _insert_audit(
            conn,
            "pa_validation_expired",
            {
                "trigger_signal": "spoon_bull",
                "reason": "opposite_side",
                "bars_waited": 2,
                "seconds_waited": 360,
                "cached_side": "buy",
            },
            (now - timedelta(minutes=30)).isoformat(),
        )
        # Stale row beyond 24h window — MUST NOT count
        _insert_audit(
            conn,
            "pa_validation_redeem",
            {
                "trigger_signal": "ancient_signal",
                "bars_waited": 99,
                "seconds_waited": 99 * 180,
                "final_side": "sell",
                "final_tier": "STANDARD",
                "order_id": "OID-ancient",
            },
            (now - timedelta(hours=40)).isoformat(),
        )

    deps = SimpleNamespace(
        bitunix_observer=SimpleNamespace(pa_config=SimpleNamespace(enabled=True))
    )
    view = build_bitunix_pa_view(db_url, deps)
    # The 24h-windowed COUNT must exclude the 40h-old row
    assert view["redeem_counts"] == {
        "redeemed_24h": 3,
        "expired_score_decay_24h": 2,
        "expired_opposite_side_24h": 1,
    }
    # `recent_redeems` is age-agnostic last-5 (so operators still see
    # recent activity past the 24h cutoff). 4 redeems inserted total
    # (3 fresh + 1 ancient) → all 4 surface here.
    assert len(view["recent_redeems"]) == 4
    # ORDER BY id DESC → ancient_signal (inserted last) is first
    assert view["recent_redeems"][0]["signal"] == "ancient_signal"
    assert view["recent_redeems"][-1]["signal"] == "mc_a_red_diamond"
    assert view["recent_redeems"][-1]["bars_waited"] == 1
    assert view["recent_redeems"][-1]["order_id"] == "OID-0"
    # Expired side: 3 inserted total (2 score_decay + 1 opposite_side).
    # ORDER BY id DESC → opposite_side (inserted last) is first.
    assert len(view["recent_expired"]) == 3
    assert view["recent_expired"][0]["reason"] == "opposite_side"


# ─── Decision flow extension: redemption marker (dashboard #3) ──────────


def test_decision_flow_marks_redeemed_when_source_matches(db_url: str):
    """A score-decided row with trigger_source='bar_tick_redeem' AND a
    matching pa_validation_redeem audit row → flow.redeemed=True with
    bars_waited populated from the redeem row."""
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "bitunix_score_decided",
            {
                "tier": "STANDARD",
                "side": "sell",
                "net_score": -7,
                "trigger_signal": "mc_a_red_diamond",
                "trigger_source": "bar_tick_redeem",
                "outcome": "placed",
            },
            _ts(0),
        )
        _insert_audit(
            conn,
            "pa_validation_redeem",
            {
                "trigger_signal": "mc_a_red_diamond",
                "bars_waited": 4,
                "seconds_waited": 720,
                "final_side": "sell",
                "final_tier": "STANDARD",
            },
            _ts(5),
        )
    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert len(view["flows"]) == 1
    f = view["flows"][0]
    assert f["redeemed"] is True
    assert f["redeem"] == {"bars_waited": 4, "seconds_waited": 720}


def test_decision_flow_not_redeemed_for_normal_tv_source(db_url: str):
    """A score-decided row with trigger_source='lord_otter' → flow.redeemed
    must be False even if a pa_validation_redeem row exists for a different
    signal/window."""
    with db.connect(db_url) as conn:
        _insert_audit(
            conn,
            "bitunix_score_decided",
            {
                "tier": "STANDARD",
                "side": "sell",
                "net_score": -7,
                "trigger_signal": "mc_a_red_diamond",
                "trigger_source": "lord_otter",
                "outcome": "placed",
            },
            _ts(0),
        )
    deps = SimpleNamespace(bitunix_observer=SimpleNamespace())
    view = build_bitunix_decision_flow_view(db_url, deps)
    assert len(view["flows"]) == 1
    assert view["flows"][0]["redeemed"] is False
    assert view["flows"][0]["redeem"] is None
