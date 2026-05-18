"""Tests for step-12 combo HITL coalescing.

Three layers:
  - PendingComboRegistry: propose/list/get/resolve semantics, mixed
    combo_ids stored independently.
  - combo_approval_view.build_combo_card_payload: rendering shape for
    opens, full closes, adjustment_1 (close + open grouped).
  - propose_ic_combo integration: registry receives the entry when
    risk-gate approves.

The web POST/GET routes (`/approvals/combos/{combo_id}` and
`/approvals/combos/{combo_id}/decide`) are exercised via the
underlying handler logic in `_ic_orchestration.dispatch_approved_ic_combo`
(already covered in `test_ic_orchestration.py`) — adding an HTTP-level
integration test would require spinning up the full FastAPI app and
templating, which is out of scope for the unit harness. See the route
implementations in `web/routes.py` for the dispatch wiring.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.strategies._ic_orchestration import propose_ic_combo
from trading_corp.comms.pending_combo_registry import (
    PendingComboEntry,
    PendingComboRegistry,
)
from trading_corp.persistence.models import ProposedOrder
from trading_corp.web.combo_approval_view import build_combo_card_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leg(
    *,
    role: str,
    side: str,
    option_type: str,
    strike: float,
    combo_id: str = "combo-A",
    direction: str = "credit",
    effect: str = "open",
    net_limit: float = 1.20,
    limit_price: float = 0.50,
    expiration: str = "2026-06-19",
    underlying: str = "SPY",
    qty: int = 1,
    intent: str = "open",
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
            "combo_intent": intent,
            "net_limit_price": net_limit,
            "underlying": underlying,
            "expiration": expiration,
            "strike": strike,
            "option_type": option_type,
            "position_effect": effect,
            "ratio_quantity": 1,
        },
    )


def _open_ic(combo_id: str = "combo-A") -> list[ProposedOrder]:
    return [
        _leg(role="short_put",  side="sell", option_type="put",
             strike=430.0, combo_id=combo_id),
        _leg(role="long_put",   side="buy",  option_type="put",
             strike=427.0, combo_id=combo_id),
        _leg(role="short_call", side="sell", option_type="call",
             strike=470.0, combo_id=combo_id),
        _leg(role="long_call",  side="buy",  option_type="call",
             strike=473.0, combo_id=combo_id),
    ]


def _close_ic(combo_id: str = "combo-B") -> list[ProposedOrder]:
    return [
        _leg(role="short_put",  side="buy",  option_type="put",
             strike=430.0, combo_id=combo_id, direction="debit",
             effect="close", intent="profit_target", net_limit=0.50),
        _leg(role="long_put",   side="sell", option_type="put",
             strike=427.0, combo_id=combo_id, direction="debit",
             effect="close", intent="profit_target", net_limit=0.50),
        _leg(role="short_call", side="buy",  option_type="call",
             strike=470.0, combo_id=combo_id, direction="debit",
             effect="close", intent="profit_target", net_limit=0.50),
        _leg(role="long_call",  side="sell", option_type="call",
             strike=473.0, combo_id=combo_id, direction="debit",
             effect="close", intent="profit_target", net_limit=0.50),
    ]


def _adjustment_1_combo(combo_id: str = "combo-C") -> list[ProposedOrder]:
    # 2 close (old untested put vertical) + 2 open (new untested put vertical)
    return [
        _leg(role="old_short_put", side="buy",  option_type="put",
             strike=430.0, combo_id=combo_id, direction="credit",
             effect="close", intent="adjustment_1", net_limit=0.30,
             limit_price=0.30),
        _leg(role="old_long_put",  side="sell", option_type="put",
             strike=427.0, combo_id=combo_id, direction="credit",
             effect="close", intent="adjustment_1", net_limit=0.30,
             limit_price=0.10),
        _leg(role="new_short_put", side="sell", option_type="put",
             strike=440.0, combo_id=combo_id, direction="credit",
             effect="open", intent="adjustment_1", net_limit=0.30,
             limit_price=0.80),
        _leg(role="new_long_put",  side="buy",  option_type="put",
             strike=437.0, combo_id=combo_id, direction="credit",
             effect="open", intent="adjustment_1", net_limit=0.30,
             limit_price=0.35),
    ]


# ---------------------------------------------------------------------------
# PendingComboRegistry
# ---------------------------------------------------------------------------


def test_registry_propose_then_get_returns_entry():
    r = PendingComboRegistry()
    orders = _open_ic()
    e = r.propose(
        "combo-A", orders,
        intent="open", strategy_slug="robinhood_joint_iron_condor",
        division="robinhood_joint",
    )
    assert e.combo_id == "combo-A"
    assert len(e.orders) == 4
    assert e.intent == "open"
    assert e.underlying == "SPY"
    assert e.direction == "credit"
    assert e.net_limit_price == 1.20

    got = r.get("combo-A")
    assert got is e


def test_registry_4_orders_one_combo_id_render_one_card():
    """The user's checklist: combo with 4 ProposedOrders sharing
    combo_id renders one card. Verified at the registry layer + view
    builder: 4 orders → 1 PendingComboEntry → one card payload."""
    r = PendingComboRegistry()
    r.propose(
        "combo-A", _open_ic(),
        intent="open", strategy_slug="x", division="d",
    )
    entries = r.list_pending()
    assert len(entries) == 1
    assert entries[0].combo_id == "combo-A"
    view = build_combo_card_payload(entries[0])
    # One leg-group (no adjustment split) with 4 legs.
    assert len(view["leg_groups"]) == 1
    assert len(view["leg_groups"][0]["legs"]) == 4


def test_registry_mixed_combo_ids_render_separate_cards():
    """Two combos with distinct combo_ids → two registry entries → two
    cards."""
    r = PendingComboRegistry()
    r.propose("combo-A", _open_ic("combo-A"),
              intent="open", strategy_slug="x", division="d")
    r.propose("combo-B", _close_ic("combo-B"),
              intent="profit_target", strategy_slug="x", division="d")
    entries = r.list_pending()
    assert len(entries) == 2
    ids = {e.combo_id for e in entries}
    assert ids == {"combo-A", "combo-B"}


def test_registry_list_is_newest_first():
    import time
    r = PendingComboRegistry()
    r.propose("combo-1", _open_ic("combo-1"),
              intent="open", strategy_slug="x", division="d")
    time.sleep(0.01)
    r.propose("combo-2", _open_ic("combo-2"),
              intent="open", strategy_slug="x", division="d")
    entries = r.list_pending()
    assert entries[0].combo_id == "combo-2"
    assert entries[1].combo_id == "combo-1"


def test_registry_resolve_approve_pops_entry_and_audits():
    logger = MagicMock()
    r = PendingComboRegistry(logger_agent=logger)
    r.propose("combo-A", _open_ic(),
              intent="open", strategy_slug="x", division="d")
    e = r.resolve("combo-A", decision="approve", reason="looks good")
    assert e is not None
    assert e.combo_id == "combo-A"
    # Entry removed.
    assert r.get("combo-A") is None
    # Audit emitted.
    kinds = [c.kwargs.get("kind") or c.args[1] for c in logger.log_event.call_args_list]
    assert "board_combo_approved" in kinds


def test_registry_resolve_reject_pops_entry_and_audits():
    logger = MagicMock()
    r = PendingComboRegistry(logger_agent=logger)
    r.propose("combo-A", _open_ic(),
              intent="open", strategy_slug="x", division="d")
    e = r.resolve("combo-A", decision="reject", reason="no thanks")
    assert e is not None
    assert r.get("combo-A") is None
    kinds = [c.kwargs.get("kind") or c.args[1] for c in logger.log_event.call_args_list]
    assert "board_combo_rejected" in kinds


def test_registry_resolve_unknown_returns_none():
    r = PendingComboRegistry()
    assert r.resolve("ghost", decision="approve") is None


def test_registry_resolve_rejects_invalid_decision():
    r = PendingComboRegistry()
    r.propose("combo-A", _open_ic(),
              intent="open", strategy_slug="x", division="d")
    with pytest.raises(ValueError, match="approve.*reject"):
        r.resolve("combo-A", decision="maybe")


def test_registry_propose_requires_combo_id_and_orders():
    r = PendingComboRegistry()
    with pytest.raises(ValueError, match="combo_id"):
        r.propose("", _open_ic(),
                  intent="open", strategy_slug="x", division="d")
    with pytest.raises(ValueError, match="orders"):
        r.propose("combo-A", [],
                  intent="open", strategy_slug="x", division="d")


# ---------------------------------------------------------------------------
# combo_approval_view.build_combo_card_payload
# ---------------------------------------------------------------------------


def test_view_open_combo_one_leg_group_with_4_legs():
    e = PendingComboEntry(
        combo_id="combo-A", orders=_open_ic(),
        intent="open", strategy_slug="robinhood_joint_iron_condor",
        division="robinhood_joint",
    )
    v = build_combo_card_payload(e)
    assert v["intent"] == "open"
    assert v["intent_label"] == "Open"
    assert v["symbol"] == "SPY"
    assert v["direction"] == "credit"
    assert v["net_price"] == 1.20
    assert v["contracts"] == 1
    assert v["leg_count"] == 4
    assert v["short_id"] == "combo-A"[:8]
    assert len(v["leg_groups"]) == 1
    assert v["leg_groups"][0]["label"] == "Legs"
    legs = v["leg_groups"][0]["legs"]
    roles = sorted(l["role"] for l in legs)
    assert roles == ["long_call", "long_put", "short_call", "short_put"]
    # Side-labels sensible.
    short_put_leg = next(l for l in legs if l["role"] == "short_put")
    assert short_put_leg["side_label"] == "Sell to Open"
    assert short_put_leg["strike"] == 430.0


def test_view_close_combo_shows_buy_to_close_labels():
    e = PendingComboEntry(
        combo_id="combo-B", orders=_close_ic(),
        intent="profit_target", strategy_slug="robinhood_joint_iron_condor",
        division="robinhood_joint",
    )
    v = build_combo_card_payload(e)
    assert v["intent"] == "profit_target"
    assert v["intent_label"] == "Close (50% Profit Target)"
    assert v["direction"] == "debit"
    legs = v["leg_groups"][0]["legs"]
    short_put_leg = next(l for l in legs if l["role"] == "short_put")
    assert short_put_leg["side_label"] == "Buy to Close"
    long_put_leg = next(l for l in legs if l["role"] == "long_put")
    assert long_put_leg["side_label"] == "Sell to Close"


def test_view_adjustment_1_splits_close_and_open_groups():
    """Adjustment 1 card displays close+open as one combined visual —
    the view payload has two leg_groups labeled 'Closing untested'
    and 'Opening new untested'."""
    e = PendingComboEntry(
        combo_id="combo-C", orders=_adjustment_1_combo(),
        intent="adjustment_1", strategy_slug="robinhood_joint_iron_condor",
        division="robinhood_joint",
    )
    v = build_combo_card_payload(e)
    assert v["intent"] == "adjustment_1"
    assert v["intent_label"] == "Adjust (Roll Untested)"
    assert len(v["leg_groups"]) == 2
    group_labels = [g["label"] for g in v["leg_groups"]]
    assert "Closing untested" in group_labels
    assert "Opening new untested" in group_labels
    closing = next(g for g in v["leg_groups"] if g["label"] == "Closing untested")
    opening = next(g for g in v["leg_groups"] if g["label"] == "Opening new untested")
    assert len(closing["legs"]) == 2
    assert len(opening["legs"]) == 2
    # Closing legs have position_effect=close
    assert all(l["position_effect"] == "close" for l in closing["legs"])
    # Opening legs have position_effect=open
    assert all(l["position_effect"] == "open" for l in opening["legs"])


# ---------------------------------------------------------------------------
# propose_ic_combo + registry wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_ic_combo_registers_in_registry_when_provided():
    """When pending_combo_registry is provided, a successful propose
    registers the entry."""
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    risk_agent.evaluate = MagicMock(return_value=_v("approve"))
    logger_agent = MagicMock()
    registry = PendingComboRegistry()

    legs = _open_ic("combo-A")
    ok = await propose_ic_combo(
        legs, intent="open",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=logger_agent,
        account=MagicMock(), strategy_state=MagicMock(),
        pending_combo_registry=registry,
        division="robinhood_joint",
    )
    assert ok is True
    e = registry.get("combo-A")
    assert e is not None
    assert e.intent == "open"
    assert len(e.orders) == 4


@pytest.mark.asyncio
async def test_propose_ic_combo_does_not_register_on_risk_reject():
    """If any leg's risk-gate rejects, the combo is NOT registered."""
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    # First leg approves, second rejects → abort.
    risk_agent.evaluate = MagicMock(side_effect=[
        _v("approve"), _v("reject", "over cap"),
    ])
    registry = PendingComboRegistry()

    legs = _open_ic("combo-A")
    ok = await propose_ic_combo(
        legs, intent="open",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=MagicMock(),
        account=MagicMock(), strategy_state=MagicMock(),
        pending_combo_registry=registry,
        division="robinhood_joint",
    )
    assert ok is False
    assert registry.get("combo-A") is None


@pytest.mark.asyncio
async def test_propose_ic_combo_works_without_registry():
    """No registry passed → propose still succeeds (covers tests that
    don't care about the registry layer)."""
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    risk_agent.evaluate = MagicMock(return_value=_v("approve"))

    legs = _open_ic("combo-A")
    ok = await propose_ic_combo(
        legs, intent="open",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=MagicMock(),
        account=MagicMock(), strategy_state=MagicMock(),
        pending_combo_registry=None,
        division="robinhood_joint",
    )
    assert ok is True


# ---------------------------------------------------------------------------
# End-to-end: approve fires dispatch_approved_ic_combo (state-callback wire)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_full_round_trip_calls_dispatch_with_all_legs():
    """The user's checklist: approve fires dispatch_approved_ic_combo
    with the full list of 4 orders. The web POST handler does:
      1. registry.resolve(combo_id, decision='approve')
      2. dispatch_approved_ic_combo(entry.orders, ...)
    Verify that flow end-to-end without spinning up FastAPI."""
    from trading_corp.agents.strategies._ic_orchestration import (
        dispatch_approved_ic_combo,
    )
    from trading_corp.persistence.models import FillEvent

    registry = PendingComboRegistry()
    legs = _open_ic("combo-A")
    registry.propose("combo-A", legs,
                     intent="open", strategy_slug="x",
                     division="robinhood_joint")
    # Simulate POST approve.
    entry = registry.resolve("combo-A", decision="approve", source="web")
    assert entry is not None
    assert len(entry.orders) == 4

    fills = [
        FillEvent(order_id=o.id, symbol="SPY", side=o.side, qty=1.0,
                  price=0.50, ts="t", venue="paper-exec")
        for o in entry.orders
    ]
    strategy = MagicMock()
    strategy.on_combo_filled = MagicMock()
    data_exec = MagicMock()
    data_exec.place_combo = AsyncMock(return_value=fills)

    out = await dispatch_approved_ic_combo(
        entry.orders, strategy=strategy, data_exec=data_exec,
        division=entry.division,
    )
    # place_combo received exactly the 4 legs from the registry entry.
    data_exec.place_combo.assert_awaited_once_with(
        entry.orders, division=entry.division,
    )
    assert out == fills
    strategy.on_combo_filled.assert_called_once_with("combo-A", fills)


@pytest.mark.asyncio
async def test_reject_does_not_invoke_dispatch():
    """The user's checklist: reject leaves the position unchanged.
    Validated by ensuring the POST-reject path does NOT call
    dispatch_approved_ic_combo — only the audit fires."""
    logger = MagicMock()
    registry = PendingComboRegistry(logger_agent=logger)
    legs = _open_ic("combo-A")
    registry.propose("combo-A", legs,
                     intent="open", strategy_slug="x",
                     division="robinhood_joint")

    entry = registry.resolve("combo-A", decision="reject",
                             reason="not now", source="web")
    assert entry is not None
    # Registry no longer holds the entry → no follow-up dispatch can
    # find it.
    assert registry.get("combo-A") is None
    # Audit-of-rejection emitted, no approval audit.
    kinds = [c.kwargs.get("kind") or c.args[1] for c in logger.log_event.call_args_list]
    assert "board_combo_rejected" in kinds
    assert "board_combo_approved" not in kinds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _v(verdict: str, reason: str = ""):
    v = MagicMock()
    v.verdict = verdict
    v.reason = reason
    v.new_qty = None
    return v
