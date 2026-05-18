"""Tests for the Iron Condor live trades view.

Two layers:
  1. Unit tests for each ic_live_view query function against synthetic
     data (mirrors the test_ic_telemetry.py pattern).
  2. End-to-end route test: load /telemetry/iron_condor against an
     empty database (proves empty-state rendering) and against a
     synthetic populated database (proves data binding).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_corp.agents import ic_live_view
from trading_corp.agents.ic_live_view import (
    _cached_get_option_greeks,
    _greeks_cache,
    open_positions_detail,
    pending_combos_view,
    recent_activity,
    recent_closed_combos,
    strategy_health,
    todays_scan_results,
)
from trading_corp.comms.pending_combo_registry import PendingComboRegistry
from trading_corp.persistence import db
from trading_corp.persistence.models import ProposedOrder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_schema(db_url: str) -> None:
    from trading_corp.persistence.db import SCHEMA
    with db.connect(db_url) as conn:
        conn.executescript(SCHEMA)


def _insert_audit(db_url, *, ts, kind, actor="robinhood_joint_iron_condor",
                  payload=None):
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event(ts, actor, kind, payload_json) "
            "VALUES(?,?,?,?)",
            (ts, actor, kind, json.dumps(payload or {})),
        )


def _make_open_ic_state(combo_id="abc12345-ic", **overrides):
    base = {
        "symbol": "SPY",
        "expiration": "2026-06-19",
        "contracts": 1,
        "wing_width": 3.0,
        "credit_at_entry": 1.20,
        "dte_at_entry": 45,
        "ivr_at_entry": 45.0,
        "max_loss_per_contract": 180.0,
        "short_put_strike": 430.0,
        "long_put_strike": 427.0,
        "short_call_strike": 470.0,
        "long_call_strike": 473.0,
        "short_put_option_id": "OPT-SP",
        "long_put_option_id": "OPT-LP",
        "short_call_option_id": "OPT-SC",
        "long_call_option_id": "OPT-LC",
        "short_put_delta_at_entry": -0.16,
        "short_call_delta_at_entry": 0.16,
        "long_put_delta_at_entry": -0.08,
        "long_call_delta_at_entry": 0.08,
        "adjustment_count": 0,
        "opened_ts": "2026-05-15T15:00:00",
        "session_start_mark": 0.0,
        "session_start_date": "2026-05-15",
    }
    base.update(overrides)
    return {combo_id: base}


def _make_state(open_ics=None, circuit_breaker=None, scan_telemetry=None):
    return {
        "open_ics": open_ics or {},
        "circuit_breaker": circuit_breaker or {
            "consecutive_losses": 0, "recent_pnl": [],
            "paused_until": None, "drawdown_hwm": None,
        },
        "scan_telemetry": scan_telemetry or {},
    }


def _fake_broker(*, spot=450.0, greeks_by_id=None):
    b = MagicMock()
    b.quote = AsyncMock(return_value=spot)

    g = greeks_by_id or {}
    async def _gk(opt_id):
        return g.get(opt_id, {
            "delta": None, "gamma": None, "theta": None,
            "vega": None, "iv": None, "mark_price": None,
        })
    b.get_option_greeks = _gk
    return b


@pytest.fixture(autouse=True)
def _clear_greeks_cache():
    _greeks_cache.clear()
    yield
    _greeks_cache.clear()


# ---------------------------------------------------------------------------
# _cached_get_option_greeks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_greeks_cache_returns_first_call_then_caches():
    broker = MagicMock()
    broker.get_option_greeks = AsyncMock(return_value={"delta": 0.25})
    g1 = await _cached_get_option_greeks(broker, "opt-1")
    g2 = await _cached_get_option_greeks(broker, "opt-1")
    assert g1["delta"] == 0.25
    assert g2["delta"] == 0.25
    broker.get_option_greeks.assert_called_once()


@pytest.mark.asyncio
async def test_greeks_cache_handles_broker_exception():
    broker = MagicMock()
    broker.get_option_greeks = AsyncMock(side_effect=RuntimeError("boom"))
    g = await _cached_get_option_greeks(broker, "opt-1")
    # Empty shape, no raise.
    assert g["delta"] is None
    assert g["mark_price"] is None


@pytest.mark.asyncio
async def test_greeks_cache_handles_none_option_id_or_broker():
    assert (await _cached_get_option_greeks(MagicMock(), ""))["delta"] is None
    assert (await _cached_get_option_greeks(None, "opt-1"))["delta"] is None


# ---------------------------------------------------------------------------
# Section 1: open_positions_detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_positions_detail_empty_state(tmp_db):
    _ensure_schema(tmp_db)
    out = await open_positions_detail(broker=None, db_url=tmp_db)
    assert out == []


@pytest.mark.asyncio
async def test_open_positions_detail_renders_full_view(tmp_db):
    _ensure_schema(tmp_db)
    open_ics = _make_open_ic_state()
    state = _make_state(open_ics=open_ics)
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    # Greeks: short call has drifted to Δ 0.32 → tested side = "call".
    greeks = {
        "OPT-SP": {"delta": -0.18, "mark_price": 0.30},
        "OPT-LP": {"delta": -0.05, "mark_price": 0.10},
        "OPT-SC": {"delta": 0.32, "mark_price": 1.20},     # tested
        "OPT-LC": {"delta": 0.10, "mark_price": 0.30},
    }
    broker = _fake_broker(spot=465.0, greeks_by_id=greeks)
    out = await open_positions_detail(broker=broker, db_url=tmp_db)
    assert len(out) == 1
    p = out[0]
    assert p["symbol"] == "SPY"
    assert p["short_id"] == "abc12345"
    assert p["adjustment_count"] == 0
    assert p["contracts"] == 1
    assert p["ivr_at_entry"] == 45.0
    assert p["credit_at_entry_per_share"] == 1.20
    # Close cost = 1.20 + 0.30 - 0.30 - 0.10 = 1.10/share
    # P&L = credit - close = 1.20 - 1.10 = 0.10/share = $10
    assert p["current_close_cost_per_share"] == pytest.approx(1.10)
    assert p["pnl_per_share"] == pytest.approx(0.10)
    assert p["pnl_dollars"] == pytest.approx(10.0)
    assert p["pnl_pct_of_credit"] == pytest.approx(8.333, abs=0.01)
    # Tested side = call (0.32 vs entry 0.16 = +0.16 drift).
    assert p["tested_side"] == "call"
    # Cadence: worst |Δ| = 0.32 → 300s.
    assert p["position_manager_cadence_sec"] == 300
    # Spot 465 — short call 470 is 5$ away or 1.08%
    assert p["short_call_distance_dollars"] == pytest.approx(-5.0)
    assert p["short_call_distance_pct"] == pytest.approx(-1.075, abs=0.01)
    # Distance to 50% profit: 0.60 - 0.10 = 0.50/sh
    assert p["distance_to_profit_per_share"] == pytest.approx(0.50)
    # Legs include current_delta.
    sc_leg = next(l for l in p["legs"] if l["role"] == "short_call")
    assert sc_leg["current_delta"] == 0.32


@pytest.mark.asyncio
async def test_open_positions_detail_handles_missing_greeks(tmp_db):
    """Broker returns None marks → P&L stays None, no crash."""
    _ensure_schema(tmp_db)
    state = _make_state(open_ics=_make_open_ic_state())
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    broker = _fake_broker(greeks_by_id={})    # all-None greeks
    out = await open_positions_detail(broker=broker, db_url=tmp_db)
    p = out[0]
    assert p["pnl_dollars"] is None
    assert p["tested_side"] == "neither"      # missing data → neither
    assert p["position_manager_cadence_sec"] == 1800   # default idle


@pytest.mark.asyncio
async def test_open_positions_detail_tested_side_identifies_call():
    """Direct check of _identify_tested_side_for_view replicated logic."""
    side = ic_live_view._identify_tested_side_for_view(
        sc_entry=0.16, sp_entry=-0.16, sc_cur=0.32, sp_cur=-0.18, band=0.05,
    )
    assert side == "call"


# ---------------------------------------------------------------------------
# Section 2: recent_activity
# ---------------------------------------------------------------------------


def test_recent_activity_filters_to_strategy(tmp_db):
    _ensure_schema(tmp_db)
    _insert_audit(tmp_db, ts="2026-05-17T15:00:00", kind="combo_proposed",
                  payload={"combo_id": "c1", "strategy": "robinhood_joint_iron_condor"})
    _insert_audit(tmp_db, ts="2026-05-17T15:01:00", kind="combo_filled",
                  actor="data_exec",
                  payload={"combo_id": "c1", "strategy": "robinhood_joint_iron_condor"})
    # An unrelated event (no strategy reference) shouldn't appear.
    _insert_audit(tmp_db, ts="2026-05-17T15:02:00", kind="unrelated",
                  actor="someone_else", payload={"foo": "bar"})
    out = recent_activity(db_url=tmp_db, limit=10)
    kinds = [e["kind"] for e in out]
    assert "combo_proposed" in kinds
    assert "combo_filled" in kinds
    assert "unrelated" not in kinds


def test_recent_activity_orders_newest_first(tmp_db):
    _ensure_schema(tmp_db)
    _insert_audit(tmp_db, ts="2026-05-17T15:00:00", kind="combo_proposed",
                  payload={"strategy": "robinhood_joint_iron_condor"})
    _insert_audit(tmp_db, ts="2026-05-17T16:00:00", kind="combo_filled",
                  payload={"strategy": "robinhood_joint_iron_condor"})
    out = recent_activity(db_url=tmp_db)
    assert out[0]["ts"] == "2026-05-17T16:00:00"
    assert out[1]["ts"] == "2026-05-17T15:00:00"


def test_recent_activity_extracts_severity(tmp_db):
    _ensure_schema(tmp_db)
    _insert_audit(tmp_db, ts="2026-05-17T15:00:00", kind="late_dte_force_close",
                  payload={"strategy": "robinhood_joint_iron_condor",
                           "audit_severity": "warning"})
    out = recent_activity(db_url=tmp_db)
    assert out[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# Section 3: pending_combos_view
# ---------------------------------------------------------------------------


def _leg(role, side, strike, otype):
    return ProposedOrder(
        strategy="robinhood_joint_iron_condor",
        symbol="SPY", side=side, qty=1.0,
        order_type="limit", limit_price=0.50,
        extra={
            "is_option": True, "is_multi_leg": True,
            "combo_id": "test-combo", "combo_role": role,
            "combo_direction": "credit", "combo_intent": "open",
            "net_limit_price": 1.20, "underlying": "SPY",
            "expiration": "2026-06-19", "strike": strike,
            "option_type": otype, "position_effect": "open",
            "ratio_quantity": 1,
        },
    )


def test_pending_combos_view_empty_when_no_registry():
    out = pending_combos_view(registry=None)
    assert out["entries"] == []


def test_pending_combos_view_renders_entries():
    r = PendingComboRegistry()
    legs = [
        _leg("short_put", "sell", 430.0, "put"),
        _leg("long_put", "buy", 427.0, "put"),
        _leg("short_call", "sell", 470.0, "call"),
        _leg("long_call", "buy", 473.0, "call"),
    ]
    r.propose("test-combo", legs,
              intent="open", strategy_slug="robinhood_joint_iron_condor",
              division="robinhood_joint")
    out = pending_combos_view(registry=r)
    assert len(out["entries"]) == 1
    e = out["entries"][0]
    assert e["combo_id"] == "test-combo"
    assert e["short_id"] == "test-com"
    assert e["intent"] == "open"
    assert e["symbol"] == "SPY"
    assert e["direction"] == "credit"
    assert e["net_limit_price"] == 1.20
    assert e["contracts"] == 1
    assert len(e["legs"]) == 4
    assert e["detail_url"] == "/approvals/combos/test-combo"


def test_pending_combos_view_includes_batcher_queue():
    r = PendingComboRegistry()
    batcher = MagicMock()
    batcher.pending_count = 3
    out = pending_combos_view(registry=r, batcher=batcher)
    assert out["telegram_queue"]["pending_count"] == 3


# ---------------------------------------------------------------------------
# Section 4: todays_scan_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_todays_scan_results_empty_state(tmp_db):
    _ensure_schema(tmp_db)
    with patch(
        "trading_corp.agents.ic_live_view.calc_iv_rank",
        new=AsyncMock(return_value=0.40),
    ), patch(
        "trading_corp.agents.ic_live_view.calc_atm_iv",
        new=AsyncMock(return_value=0.20),
    ):
        out = await todays_scan_results(
            broker=None, db_url=tmp_db,
            universe=["SPY"], day=date(2026, 5, 17),
        )
    assert len(out) == 1
    row = out[0]
    assert row["symbol"] == "SPY"
    assert row["scanned"] is False
    assert row["filtered_total"] == 0
    assert row["combos_proposed"] == 0
    assert row["current_ivr_pct"] == 40.0
    assert row["current_atm_iv_45dte"] == 0.20


@pytest.mark.asyncio
async def test_todays_scan_results_reflects_filter_counters(tmp_db):
    _ensure_schema(tmp_db)
    state = _make_state(
        scan_telemetry={
            "2026-05-17": {
                "SPY": {"total": 1, "by_reason": {"ivr_below_30": 1}},
            },
        },
    )
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    with patch(
        "trading_corp.agents.ic_live_view.calc_iv_rank",
        new=AsyncMock(return_value=0.20),
    ), patch(
        "trading_corp.agents.ic_live_view.calc_atm_iv",
        new=AsyncMock(return_value=0.20),
    ):
        out = await todays_scan_results(
            broker=None, db_url=tmp_db,
            universe=["SPY"], day=date(2026, 5, 17),
        )
    row = out[0]
    assert row["scanned"] is True
    assert row["filtered_total"] == 1
    assert row["filter_reasons"] == {"ivr_below_30": 1}


# ---------------------------------------------------------------------------
# Section 5: strategy_health
# ---------------------------------------------------------------------------


def test_strategy_health_empty_state_reports_safely(tmp_db):
    _ensure_schema(tmp_db)
    health = strategy_health(
        ic_strategy=None, ic_division=None,
        pending_combo_registry=None, telegram_batcher=None,
        db_url=tmp_db,
    )
    assert health["enabled"] is None
    assert health["circuit_breaker"]["consecutive_losses"] == 0
    assert health["circuit_breaker"]["is_paused"] is False
    assert health["wiring"]["ic_strategy_attached"] is False
    assert health["state_consistency"]["open_ics_in_agent_state"] == 0


def test_strategy_health_reports_paused_circuit_breaker(tmp_db):
    _ensure_schema(tmp_db)
    paused_until = (
        datetime.now(timezone.utc) + timedelta(days=2)
    ).isoformat(timespec="seconds")
    state = _make_state(
        circuit_breaker={
            "consecutive_losses": 3, "recent_pnl": [-0.5, -0.3, -0.8],
            "paused_until": paused_until, "drawdown_hwm": 5000.0,
        },
    )
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    health = strategy_health(
        ic_strategy=None, ic_division=None,
        pending_combo_registry=None, telegram_batcher=None,
        db_url=tmp_db,
    )
    assert health["circuit_breaker"]["consecutive_losses"] == 3
    assert health["circuit_breaker"]["is_paused"] is True


def test_strategy_health_detects_state_consistency_mismatch(tmp_db):
    """1 open IC in agent_state but no matching open leg in position
    table → state_consistency.agrees=False."""
    _ensure_schema(tmp_db)
    state = _make_state(open_ics=_make_open_ic_state())
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    health = strategy_health(
        ic_strategy=None, ic_division=None,
        pending_combo_registry=None, telegram_batcher=None,
        db_url=tmp_db,
    )
    assert health["state_consistency"]["open_ics_in_agent_state"] == 1
    assert health["state_consistency"]["distinct_open_combos_in_position_table"] == 0
    assert health["state_consistency"]["agrees"] is False


def test_strategy_health_reports_wiring_attachments(tmp_db):
    _ensure_schema(tmp_db)
    strategy = MagicMock()
    strategy.enabled = True
    strategy.auto_execute = False
    division = MagicMock()
    division.has_strategy = True
    registry = PendingComboRegistry()
    batcher = MagicMock()
    health = strategy_health(
        ic_strategy=strategy, ic_division=division,
        pending_combo_registry=registry, telegram_batcher=batcher,
        db_url=tmp_db,
    )
    assert health["enabled"] is True
    assert health["auto_execute"] is False
    assert health["wiring"]["ic_strategy_attached"] is True
    assert health["wiring"]["division_has_strategy"] is True


# ---------------------------------------------------------------------------
# Section 6: recent_closed_combos
# ---------------------------------------------------------------------------


def test_recent_closed_combos_empty(tmp_db):
    _ensure_schema(tmp_db)
    out = recent_closed_combos(db_url=tmp_db)
    assert out == []


def test_recent_closed_combos_renders_rows(tmp_db):
    _ensure_schema(tmp_db)
    _insert_audit(
        tmp_db, ts="2026-05-17T15:00:00",
        kind="ic_lifecycle_closed",
        payload={
            "combo_id": "closed-aaa11122", "symbol": "SPY",
            "ivr_at_entry": 45.0, "dte_at_entry": 45,
            "credit_at_entry": 1.20, "adjustment_count": 0,
            "realized_pnl_dollars": 60.0,
            "realized_pnl_per_share": 0.60,
            "contracts": 1, "close_kind": "profit_target",
        },
    )
    out = recent_closed_combos(db_url=tmp_db)
    assert len(out) == 1
    c = out[0]
    assert c["short_id"] == "closed-a"
    assert c["realized_pnl_dollars"] == 60.0
    assert c["realized_pct_of_credit"] == pytest.approx(50.0)
    assert c["close_kind"] == "profit_target"


# ---------------------------------------------------------------------------
# End-to-end route test
# ---------------------------------------------------------------------------


def _build_test_app(*, tmp_db):
    """Construct a minimal app whose deps point at the test database."""
    from trading_corp.agents.logger import LoggerAgent
    from trading_corp.web.app import WebDeps, create_app

    logger = LoggerAgent(db_url=tmp_db)
    data_exec = MagicMock()
    data_exec.brokers = {}     # no broker registered → falls back to paper_broker
    paper_broker = MagicMock()
    paper_broker.quote = AsyncMock(return_value=450.0)

    async def _gk(opt_id):
        return {"delta": 0.20, "mark_price": 0.50,
                "gamma": 0.01, "theta": -0.02, "vega": 0.10, "iv": 0.25}
    paper_broker.get_option_greeks = _gk

    pending_registry = MagicMock()
    pending_registry.list_pending = MagicMock(return_value=[])

    combo_registry = PendingComboRegistry()
    deps = WebDeps(
        db_url=tmp_db, db_path=tmp_db, mode="PAPER",
        logger_agent=logger, data_exec=data_exec,
        trend_agent=None, portfolio=None, pmcc_agent=None,
        fidelity_agent=None, paper_broker=paper_broker,
        secrets=None, pending_registry=pending_registry,
        pending_combo_registry=combo_registry,
    )
    app = create_app(deps)
    return app, deps, combo_registry


@pytest.mark.asyncio
async def test_iron_condor_live_route_empty_state(tmp_db):
    """Empty DB → page renders without 500."""
    _ensure_schema(tmp_db)
    from fastapi.testclient import TestClient
    with patch(
        "trading_corp.agents.ic_live_view.calc_iv_rank",
        new=AsyncMock(return_value=0.40),
    ), patch(
        "trading_corp.agents.ic_live_view.calc_atm_iv",
        new=AsyncMock(return_value=0.20),
    ):
        app, _deps, _registry = _build_test_app(tmp_db=tmp_db)
        client = TestClient(app)
        resp = client.get("/telemetry/iron_condor")
    assert resp.status_code == 200
    html = resp.text
    # All 6 section headers present.
    assert "Strategy Health" in html
    assert "Open Positions" in html
    assert "Pending Approvals" in html
    assert "Recent Activity" in html
    assert "Today's Scan Results" in html
    assert "Last 10 Closed Combos" in html
    # Empty states.
    assert "No open ICs" in html
    assert "No combos awaiting Board approval" in html
    assert "No audit activity yet" in html
    assert "No closed combos yet" in html


@pytest.mark.asyncio
async def test_iron_condor_live_route_populated_db(tmp_db):
    """Populated synthetic DB → all sections bind data without 500."""
    _ensure_schema(tmp_db)
    # Open IC
    state = _make_state(open_ics=_make_open_ic_state())
    db.set_agent_state("robinhood_joint_iron_condor", "state",
                       state, db_url=tmp_db)
    # Audit events
    _insert_audit(tmp_db, ts="2026-05-17T15:00:00",
                  kind="combo_proposed",
                  payload={"combo_id": "c1", "symbol": "SPY",
                           "strategy": "robinhood_joint_iron_condor"})
    _insert_audit(tmp_db, ts="2026-05-17T15:30:00",
                  kind="ic_lifecycle_closed",
                  payload={"combo_id": "closed-c1", "symbol": "QQQ",
                           "ivr_at_entry": 38.0, "credit_at_entry": 1.20,
                           "adjustment_count": 0, "contracts": 1,
                           "realized_pnl_dollars": 55.0,
                           "realized_pnl_per_share": 0.55,
                           "close_kind": "profit_target"})

    from fastapi.testclient import TestClient
    with patch(
        "trading_corp.agents.ic_live_view.calc_iv_rank",
        new=AsyncMock(return_value=0.45),
    ), patch(
        "trading_corp.agents.ic_live_view.calc_atm_iv",
        new=AsyncMock(return_value=0.22),
    ):
        app, deps, registry = _build_test_app(tmp_db=tmp_db)
        # Add one pending combo so section 3 renders an entry.
        legs = [_leg("short_put", "sell", 430.0, "put"),
                _leg("long_put", "buy", 427.0, "put"),
                _leg("short_call", "sell", 470.0, "call"),
                _leg("long_call", "buy", 473.0, "call")]
        registry.propose("pending-abc12345", legs,
                          intent="open",
                          strategy_slug="robinhood_joint_iron_condor",
                          division="robinhood_joint")
        client = TestClient(app)
        resp = client.get("/telemetry/iron_condor")
    assert resp.status_code == 200
    html = resp.text
    # Section 1 — open IC short_id rendered.
    assert "abc12345" in html
    # Section 2 — at least one event row.
    assert "combo_proposed" in html
    # Section 3 — pending combo present.
    assert "pending-" in html
    # Section 4 — symbol rows.
    assert ">SPY<" in html
    # Section 6 — closed combo present.
    assert "closed-c" in html
    assert "profit_target" in html


@pytest.mark.asyncio
async def test_iron_condor_live_partial_returns_only_live_sections(tmp_db):
    """The htmx partial returns just sections 1, 3, 5 (no Section 2/4/6)."""
    _ensure_schema(tmp_db)
    from fastapi.testclient import TestClient
    with patch(
        "trading_corp.agents.ic_live_view.calc_iv_rank",
        new=AsyncMock(return_value=0.40),
    ), patch(
        "trading_corp.agents.ic_live_view.calc_atm_iv",
        new=AsyncMock(return_value=0.20),
    ):
        app, _deps, _registry = _build_test_app(tmp_db=tmp_db)
        client = TestClient(app)
        resp = client.get("/telemetry/iron_condor/partials/live")
    assert resp.status_code == 200
    html = resp.text
    # Live sections present.
    assert "Strategy Health" in html
    assert "Open Positions" in html
    assert "Pending Approvals" in html
    # Static sections NOT present (they live in the main view only).
    assert "Today's Scan Results" not in html
    assert "Recent Activity" not in html
    assert "Last 10 Closed Combos" not in html
