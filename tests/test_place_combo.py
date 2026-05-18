"""Tests for DataExecAgent.place_combo (step 7 of the IC build).

Uses the `tmp_db` fixture from conftest.py to write to a real SQLite
file so we can verify the audit_event + position rows that place_combo
writes. The broker is a MagicMock — place_multi_leg returns whatever
the test pre-arranges. Cohesion validation and broker-layer
implementation are covered separately in test_robinhood_multi_leg.py and
test_paper_multi_leg.py; here we focus on the dispatch/persistence
glue in DataExecAgent.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent, ProposedOrder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_schema(db_url: str) -> None:
    """Run the SCHEMA DDL so audit_event + position + proposed_order exist."""
    from trading_corp.persistence.db import SCHEMA
    with db.connect(db_url) as conn:
        conn.executescript(SCHEMA)


def _agent(db_url: str, *, broker: MagicMock) -> DataExecAgent:
    logger = LoggerAgent(db_url=db_url)
    a = DataExecAgent(logger=logger)
    a.register_broker("robinhood_joint", broker)
    return a


def _leg(
    *,
    role: str,
    side: str,
    strike: float,
    option_type: str,
    limit_price: float,
    combo_id: str = "combo-1",
    net_limit: float = 1.20,
    direction: str = "credit",
    underlying: str = "SPY",
    expiration: str = "2026-06-19",
    effect: str = "open",
    qty: int = 1,
) -> ProposedOrder:
    return ProposedOrder(
        strategy="robinhood_joint_iron_condor",
        symbol=underlying,
        side=side,    # type: ignore[arg-type]
        qty=float(qty),
        order_type="limit",
        limit_price=limit_price,
        extra={
            "is_option": True,
            "is_multi_leg": True,
            "combo_id": combo_id,
            "combo_role": role,
            "combo_direction": direction,
            "net_limit_price": net_limit,
            "underlying": underlying,
            "expiration": expiration,
            "strike": strike,
            "option_type": option_type,
            "position_effect": effect,
            "ratio_quantity": 1,
        },
    )


def _standard_ic_legs(combo_id: str = "combo-1",
                      net_limit: float = 1.20) -> list[ProposedOrder]:
    return [
        _leg(role="short_put",  side="sell", option_type="put",
             strike=430.0, limit_price=0.55, combo_id=combo_id, net_limit=net_limit),
        _leg(role="long_put",   side="buy",  option_type="put",
             strike=427.0, limit_price=0.20, combo_id=combo_id, net_limit=net_limit),
        _leg(role="short_call", side="sell", option_type="call",
             strike=470.0, limit_price=0.65, combo_id=combo_id, net_limit=net_limit),
        _leg(role="long_call",  side="buy",  option_type="call",
             strike=473.0, limit_price=0.20, combo_id=combo_id, net_limit=net_limit),
    ]


def _fake_fills(orders: list[ProposedOrder],
                prices: tuple[float, float, float, float]) -> list[FillEvent]:
    return [
        FillEvent(
            order_id=o.id,
            symbol=o.symbol,
            side=o.side,
            qty=float(o.qty),
            price=p,
            ts="2026-05-17T15:00:00",
            venue="robinhood",
        )
        for o, p in zip(orders, prices)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_combo_happy_path_emits_fill_and_persists_positions(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()
    fills = _fake_fills(legs, (0.55, 0.20, 0.65, 0.20))

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=fills)

    a = _agent(tmp_db, broker=broker)
    out = await a.place_combo(legs, division="robinhood_joint")

    # Returns the broker's fills, unmodified.
    assert out == fills
    broker.place_multi_leg.assert_awaited_once_with(legs)

    # All 4 orders marked filled.
    assert all(o.status == "filled" for o in legs)
    assert [o.fill_price for o in legs] == [0.55, 0.20, 0.65, 0.20]

    # Audit event written.
    events = a.logger.recent_events(limit=10)
    combo_filled = [e for e in events if e["kind"] == "combo_filled"]
    assert len(combo_filled) == 1
    payload = combo_filled[0]["payload"]
    assert payload["combo_id"] == "combo-1"
    assert payload["strategy"] == "robinhood_joint_iron_condor"
    assert payload["division"] == "robinhood_joint"
    assert payload["direction"] == "credit"
    assert payload["leg_count"] == 4
    assert payload["net_limit_price"] == 1.20

    # net_actual = (0.55 + 0.65) - (0.20 + 0.20) = 0.80.
    assert payload["net_actual"] == pytest.approx(0.80)
    assert payload["actual_vs_limit_slippage_dollars"] == pytest.approx(0.40)

    # Leg payload covers all 4 with correct roles.
    roles = sorted([l["combo_role"] for l in payload["legs"]])
    assert roles == ["long_call", "long_put", "short_call", "short_put"]

    # Position rows persisted — 4 rows, all tagged with combo_id.
    with db.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT account, symbol, qty, avg_price, opened_ts, extra_json "
            "FROM position ORDER BY id"
        ).fetchall()
    assert len(rows) == 4
    for r in rows:
        ex = json.loads(r["extra_json"])
        assert ex["combo_id"] == "combo-1"
        assert ex["is_combo_leg"] is True
        assert ex["strategy"] == "robinhood_joint_iron_condor"
        assert ex["division"] == "robinhood_joint"
        assert r["account"] == "robinhood_joint"
    # Signed qty matches buy/sell.
    qty_by_role = {
        json.loads(r["extra_json"])["combo_role"]: r["qty"]
        for r in rows
    }
    assert qty_by_role["short_put"]  == -1.0  # sell → negative
    assert qty_by_role["long_put"]   ==  1.0
    assert qty_by_role["short_call"] == -1.0
    assert qty_by_role["long_call"]  ==  1.0


@pytest.mark.asyncio
async def test_place_combo_unfilled_emits_audit_and_no_positions(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=[])

    a = _agent(tmp_db, broker=broker)
    out = await a.place_combo(legs, division="robinhood_joint")

    assert out == []
    # Orders left in "proposed" status — no fill_price.
    assert all(o.status == "proposed" for o in legs)
    assert all(o.fill_price is None for o in legs)

    # Audit event with combo_unfilled kind.
    events = a.logger.recent_events(limit=10)
    unfilled = [e for e in events if e["kind"] == "combo_unfilled"]
    assert len(unfilled) == 1
    payload = unfilled[0]["payload"]
    assert payload["combo_id"] == "combo-1"
    assert payload["strategy"] == "robinhood_joint_iron_condor"
    assert payload["division"] == "robinhood_joint"
    assert payload["direction"] == "credit"
    assert payload["net_limit_price"] == 1.20
    assert payload["leg_count"] == 4
    assert "reason" in payload

    # No combo_filled audit, no position rows.
    assert not any(e["kind"] == "combo_filled" for e in events)
    with db.connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM position").fetchone()["c"]
    assert n == 0


@pytest.mark.asyncio
async def test_place_combo_rejects_mixed_combo_ids_before_broker_call(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()
    legs[2].extra["combo_id"] = "intruder"

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=[])  # would succeed if reached

    a = _agent(tmp_db, broker=broker)
    with pytest.raises(ValueError, match="mixed/missing combo_ids"):
        await a.place_combo(legs, division="robinhood_joint")

    # Broker MUST NOT have been called.
    broker.place_multi_leg.assert_not_called()
    # No audit, no position rows.
    with db.connect(tmp_db) as conn:
        n_audit = conn.execute("SELECT COUNT(*) AS c FROM audit_event").fetchone()["c"]
        n_pos = conn.execute("SELECT COUNT(*) AS c FROM position").fetchone()["c"]
    assert n_audit == 0
    assert n_pos == 0


@pytest.mark.asyncio
async def test_place_combo_rejects_missing_combo_id(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()
    del legs[1].extra["combo_id"]

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=[])

    a = _agent(tmp_db, broker=broker)
    with pytest.raises(ValueError, match="mixed/missing combo_ids"):
        await a.place_combo(legs, division="robinhood_joint")
    broker.place_multi_leg.assert_not_called()


@pytest.mark.asyncio
async def test_place_combo_empty_input_returns_empty_without_broker(tmp_db):
    _ensure_schema(tmp_db)
    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=[])
    a = _agent(tmp_db, broker=broker)
    out = await a.place_combo([], division="robinhood_joint")
    assert out == []
    broker.place_multi_leg.assert_not_called()


@pytest.mark.asyncio
async def test_place_combo_raises_when_no_broker_registered(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()
    logger = LoggerAgent(db_url=tmp_db)
    a = DataExecAgent(logger=logger)
    # No register_broker call.
    with pytest.raises(RuntimeError, match="No broker registered"):
        await a.place_combo(legs, division="robinhood_joint")


@pytest.mark.asyncio
async def test_place_combo_raises_when_broker_returns_wrong_leg_count(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()
    # Only 2 fills returned for 4 legs.
    half_fills = _fake_fills(legs[:2], (0.55, 0.20))

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=half_fills)
    a = _agent(tmp_db, broker=broker)
    with pytest.raises(RuntimeError, match="returned 2 fills for 4 legs"):
        await a.place_combo(legs, division="robinhood_joint")


@pytest.mark.asyncio
async def test_place_combo_debit_direction_actual_calc(tmp_db):
    """For debit combos, actual is -cashflow (debit-as-positive)."""
    _ensure_schema(tmp_db)
    # Two-leg debit close: buy short for 0.30, sell long for 0.10.
    legs = [
        _leg(role="short_put", side="buy",  option_type="put",
             strike=430.0, limit_price=0.30, direction="debit",
             net_limit=0.40, effect="close", combo_id="debit-1"),
        _leg(role="long_put",  side="sell", option_type="put",
             strike=427.0, limit_price=0.10, direction="debit",
             net_limit=0.40, effect="close", combo_id="debit-1"),
    ]
    fills = _fake_fills(legs, (0.30, 0.10))

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=fills)
    a = _agent(tmp_db, broker=broker)
    await a.place_combo(legs, division="robinhood_joint")

    events = a.logger.recent_events(limit=5)
    combo_filled = [e for e in events if e["kind"] == "combo_filled"][0]
    payload = combo_filled["payload"]
    # cashflow = 0.10 - 0.30 = -0.20. actual = -cashflow = 0.20.
    assert payload["direction"] == "debit"
    assert payload["net_actual"] == pytest.approx(0.20)
    assert payload["actual_vs_limit_slippage_dollars"] == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_place_combo_dry_run_skips_broker_and_returns_synthetic_fills(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()

    broker = MagicMock()
    broker.name = "robinhood"
    broker.place_multi_leg = AsyncMock(return_value=[])  # should not be called
    logger = LoggerAgent(db_url=tmp_db)
    a = DataExecAgent(logger=logger, dry_run=True)
    a.register_broker("robinhood_joint", broker)

    fills = await a.place_combo(legs, division="robinhood_joint")
    assert len(fills) == 4
    assert all(f.venue == "robinhood:dry-run" for f in fills)
    broker.place_multi_leg.assert_not_called()

    events = a.logger.recent_events(limit=5)
    assert any(e["kind"] == "dry_run_skip_combo" for e in events)
    # No real fill audit + no position rows.
    assert not any(e["kind"] == "combo_filled" for e in events)
    with db.connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM position").fetchone()["c"]
    assert n == 0


@pytest.mark.asyncio
async def test_place_combo_proposed_order_rows_written_on_fill(tmp_db):
    _ensure_schema(tmp_db)
    legs = _standard_ic_legs()
    fills = _fake_fills(legs, (0.55, 0.20, 0.65, 0.20))

    broker = MagicMock()
    broker.place_multi_leg = AsyncMock(return_value=fills)
    a = _agent(tmp_db, broker=broker)
    await a.place_combo(legs, division="robinhood_joint")

    with db.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT id, status, fill_price FROM proposed_order ORDER BY id"
        ).fetchall()
    assert len(rows) == 4
    assert all(r["status"] == "filled" for r in rows)
    assert sorted(r["fill_price"] for r in rows) == [0.20, 0.20, 0.55, 0.65]
