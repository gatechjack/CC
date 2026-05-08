"""Tests for the /approvals* HTTP routes — Phase B.1 of HITL-in-app.

Pins the contract between the web surface and the PendingApprovalRegistry:
- GET /approvals → index (empty + populated states)
- GET /approvals/{order_id} → detail (404 when not pending)
- POST /approvals/{order_id}/decide → resolves registry; 409 on second
  call; 400 on bad decision; modify deferred to B.2
"""
from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trading_corp.comms.pending_registry import PendingApprovalRegistry
from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision
from trading_corp.persistence import db
from trading_corp.web import data as web_data
from trading_corp.web.app import WebDeps, create_app


# ── Stub command-center snapshot ─────────────────────────────────────────
# build_command_center pulls divisions config + broker snapshots + market
# data. None of that is what we're testing here; replace it with a stub
# so the test stays scoped to the /approvals* routes themselves.


@dataclass
class _StubSnap:
    mode: str = "PAPER"
    dry_run: bool = False
    regime: str = "neutral"
    vix: float = 15.0
    health: Any = None
    equity_curve: list = None  # noqa: RUF012

    def __post_init__(self):
        if self.health is None:
            self.health = types.SimpleNamespace(
                brokers=[], scheduler=types.SimpleNamespace(last_run=None),
            )
        if self.equity_curve is None:
            self.equity_curve = []


async def _stub_build_command_center(deps):
    return _StubSnap(mode=deps.mode)


@pytest.fixture
def stub_snap(monkeypatch):
    monkeypatch.setattr(
        web_data, "build_command_center", _stub_build_command_center,
    )


# ── Test deps + app construction ─────────────────────────────────────────


def _build_deps(tmp_db: str, registry: PendingApprovalRegistry | None) -> WebDeps:
    return WebDeps(
        db_url=tmp_db,
        db_path=tmp_db.replace("sqlite:///", ""),
        mode="PAPER",
        logger_agent=None,
        data_exec=None,
        trend_agent=None,
        portfolio=None,
        pmcc_agent=None,
        fidelity_agent=None,
        paper_broker=None,
        secrets=None,
        risk_agent=None,
        pending_registry=registry,
    )


@pytest.fixture
def client_with_registry(tmp_db, stub_snap):
    db.init_db(tmp_db)
    reg = PendingApprovalRegistry(logger_agent=None)
    deps = _build_deps(tmp_db, reg)
    app = create_app(deps)
    return TestClient(app), reg


@pytest.fixture
def client_without_registry(tmp_db, stub_snap):
    db.init_db(tmp_db)
    deps = _build_deps(tmp_db, registry=None)
    app = create_app(deps)
    return TestClient(app)


def _add_pending(reg: PendingApprovalRegistry, req: ApprovalRequest) -> asyncio.Task:
    """Helper — registers `req` into the registry by spawning a wait task.
    Returns the task so the test can resolve+await it cleanly."""
    loop = asyncio.new_event_loop()
    # Run wait synchronously in a thread so the test loop isn't required.
    raise NotImplementedError(
        "Don't use this helper — use the in-test loop directly."
    )


# ── GET /approvals — index ──────────────────────────────────────────────


def test_approvals_index_empty_state(client_with_registry):
    client, _ = client_with_registry
    r = client.get("/approvals")
    assert r.status_code == 200
    assert "No approvals pending" in r.text


def test_approvals_index_registry_unavailable(client_without_registry):
    r = client_without_registry.get("/approvals")
    assert r.status_code == 200
    assert "registry is not wired" in r.text.lower() or \
        "registry not wired" in r.text.lower()


def test_approvals_index_populated(client_with_registry):
    client, reg = client_with_registry
    # Seed registry by registering a Future under a fake event loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    req = ApprovalRequest(
        order_id="ord-IDX-1",
        summary="ROLL · MSTR",
        detail={"division": "robinhood_pmcc"},
    )
    fut = loop.create_future()
    from trading_corp.comms.pending_registry import PendingEntry
    reg._pending["ord-IDX-1"] = PendingEntry(
        request=req, future=fut, division="robinhood_pmcc",
    )

    r = client.get("/approvals")
    assert r.status_code == 200
    body = r.text
    # Index renders the row's headline (summary text), division, and a
    # truncated order_id.
    assert "ROLL · MSTR" in body
    assert "robinhood_pmcc" in body
    assert "ord-IDX-1"[:12] in body
    loop.close()


# ── GET /approvals/{order_id} — detail ──────────────────────────────────


def test_approval_detail_not_found_returns_404(client_with_registry):
    client, _ = client_with_registry
    r = client.get("/approvals/does-not-exist")
    assert r.status_code == 404


def test_approval_detail_renders_structured_view(client_with_registry):
    """B.2 — detail page renders the structured view (headline + legs +
    risk + raw detail). Replaces the B.1 'render req.summary as <pre>'
    test now that the structured renderer is wired."""
    client, reg = client_with_registry
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Realistic detail shape — mirrors what graph/ceo_graph.py produces.
    order_row = {
        "id": "ord-DET-1",
        "symbol": "NVDA",
        "side": "sell",
        "qty": 1.0,
        "limit_price": 5.50,
        "extra_json": '{"is_option": true, "underlying": "NVDA", '
                      '"strike": 150.0, "option_type": "call", '
                      '"expiration": "2026-05-09", "dte": 7, '
                      '"action": "open_short_call", '
                      '"position_effect": "open"}',
        "rationale": "weekly $150C",
    }
    req = ApprovalRequest(
        order_id="ord-DET-1",
        summary="📤 *SELL CALL TO OPEN* · `NVDA` · robinhood_pmcc",
        detail={
            "division": "robinhood_pmcc",
            "order": order_row,
            "risk_verdict": {"verdict": "approve", "reason": "within caps"},
        },
    )
    fut = loop.create_future()
    from trading_corp.comms.pending_registry import PendingEntry
    reg._pending["ord-DET-1"] = PendingEntry(
        request=req, future=fut, division="robinhood_pmcc",
    )

    r = client.get("/approvals/ord-DET-1")
    assert r.status_code == 200
    body = r.text
    # Headline action label + symbol from the structured view.
    assert "SELL CALL TO OPEN" in body
    assert "NVDA" in body
    assert "robinhood_pmcc" in body
    # Risk verdict block.
    assert "within caps" in body
    # Approve / Reject / Modify buttons.
    assert "Approve" in body
    assert "Reject" in body
    assert "Modify" in body
    # Raw detail debug block contains the JSON.
    assert "&#34;order&#34;" in body or '"order"' in body
    loop.close()


def test_approval_detail_404_when_no_registry(client_without_registry):
    r = client_without_registry.get("/approvals/anything")
    assert r.status_code == 404


# ── POST /approvals/{order_id}/decide ────────────────────────────────────


def _seed_pending(reg: PendingApprovalRegistry, order_id: str):
    """Inject a pending entry directly into the registry (bypassing wait
    so the test stays synchronous). Returns the Future for assertion."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    req = ApprovalRequest(
        order_id=order_id,
        summary=f"test summary for {order_id}",
        detail={"division": "robinhood_pmcc"},
    )
    fut = loop.create_future()
    from trading_corp.comms.pending_registry import PendingEntry
    reg._pending[order_id] = PendingEntry(
        request=req, future=fut, division="robinhood_pmcc",
    )
    return loop, fut


def test_decide_approve_resolves_registry(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-A")
    r = client.post(
        "/approvals/ord-A/decide",
        data={"decision": "approve"},
    )
    assert r.status_code == 200, r.text
    assert "APPROVE" in r.text
    assert fut.done()
    out = fut.result()
    assert out.decision == "approve"
    loop.close()


def test_decide_reject_resolves_registry(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-R")
    r = client.post(
        "/approvals/ord-R/decide",
        data={"decision": "reject", "reason": "bad fill price"},
    )
    assert r.status_code == 200
    assert fut.done()
    out = fut.result()
    assert out.decision == "reject"
    assert out.reason == "bad fill price"
    loop.close()


def test_decide_409_when_already_resolved(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-DUP")
    # First call: 200.
    r1 = client.post(
        "/approvals/ord-DUP/decide", data={"decision": "approve"},
    )
    assert r1.status_code == 200
    # Second call: 409 (entry already resolved + popped, but resolve
    # also returns False on done-Future before pop).
    r2 = client.post(
        "/approvals/ord-DUP/decide", data={"decision": "reject"},
    )
    assert r2.status_code == 409
    loop.close()


def test_decide_400_on_invalid_decision(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-BAD")
    r = client.post(
        "/approvals/ord-BAD/decide", data={"decision": "modify"},
    )
    # Modify is deferred to B.2.
    assert r.status_code == 400
    loop.close()


def test_decide_400_on_unknown_decision(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-UNK")
    r = client.post(
        "/approvals/ord-UNK/decide", data={"decision": "yolo"},
    )
    assert r.status_code == 400
    loop.close()


def test_decide_404_when_no_registry(client_without_registry):
    r = client_without_registry.post(
        "/approvals/whatever/decide", data={"decision": "approve"},
    )
    assert r.status_code == 404


def test_decide_accepts_json_body(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-JSON")
    r = client.post(
        "/approvals/ord-JSON/decide",
        json={"decision": "approve", "reason": "via API"},
    )
    assert r.status_code == 200
    assert fut.done()
    out = fut.result()
    assert out.decision == "approve"
    assert out.reason == "via API"
    loop.close()


# ── B.2 — modify support ────────────────────────────────────────────────


def test_decide_modify_with_new_qty_resolves_registry(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-MOD")
    r = client.post(
        "/approvals/ord-MOD/decide",
        data={"decision": "modify", "new_qty": "2.5", "reason": "smaller"},
    )
    assert r.status_code == 200, r.text
    assert "MODIFY" in r.text
    assert "qty=2.5" in r.text
    assert fut.done()
    out = fut.result()
    assert out.decision == "modify"
    assert out.new_qty == 2.5
    assert out.reason == "smaller"
    loop.close()


def test_decide_modify_missing_both_fields_400(client_with_registry):
    """B.5 — modify with no fields requires at least one of
    new_qty / new_limit_price. Error message updated when B.5 added
    new_limit_price as a valid alternative to new_qty."""
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-MODE1")
    r = client.post(
        "/approvals/ord-MODE1/decide", data={"decision": "modify"},
    )
    assert r.status_code == 400
    assert "at least one" in r.text
    loop.close()


def test_decide_modify_zero_qty_400(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-MODE2")
    r = client.post(
        "/approvals/ord-MODE2/decide",
        data={"decision": "modify", "new_qty": "0"},
    )
    assert r.status_code == 400
    assert "must be > 0" in r.text
    loop.close()


def test_decide_modify_negative_qty_400(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-MODE3")
    r = client.post(
        "/approvals/ord-MODE3/decide",
        data={"decision": "modify", "new_qty": "-1.5"},
    )
    assert r.status_code == 400
    loop.close()


def test_decide_modify_non_numeric_qty_400(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-MODE4")
    r = client.post(
        "/approvals/ord-MODE4/decide",
        data={"decision": "modify", "new_qty": "abc"},
    )
    assert r.status_code == 400
    loop.close()


def test_decide_modify_via_json(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-MODJSON")
    r = client.post(
        "/approvals/ord-MODJSON/decide",
        json={"decision": "modify", "new_qty": 3, "reason": "via API"},
    )
    assert r.status_code == 200
    out = fut.result()
    assert out.decision == "modify"
    assert out.new_qty == 3.0
    loop.close()


# ── B.3 — paired entries in index + detail ──────────────────────────────


def _seed_paired(reg, close_id: str, open_id: str, pair_id: str = "pair-T"):
    """Inject TWO paired entries into the registry for the same pmcc_pair_id."""
    import json
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    from trading_corp.comms.pending_registry import PendingEntry

    def _req(oid, action, side):
        extra = {
            "is_option": True, "underlying": "MSTR", "strike": 170.0,
            "option_type": "call", "expiration": "2026-05-09",
            "action": action, "pmcc_pair_id": pair_id,
            "mark_per_share": 5.0,
        }
        return ApprovalRequest(
            order_id=oid,
            summary=f"📤 *{action.upper()}* · `MSTR` · robinhood_pmcc",
            detail={
                "order": {
                    "id": oid, "symbol": "MSTR", "side": side, "qty": 1.0,
                    "limit_price": 5.0, "extra_json": json.dumps(extra),
                },
                "division": "robinhood_pmcc",
                "risk_verdict": {"verdict": "approve", "reason": "ok"},
            },
        )

    close_req = _req(close_id, "roll_short_call_close", "buy")
    open_req = _req(open_id, "roll_short_call_open", "sell")
    futs = (loop.create_future(), loop.create_future())
    reg._pending[close_id] = PendingEntry(
        request=close_req, future=futs[0],
        division="robinhood_pmcc", pmcc_pair_id=pair_id,
    )
    reg._pending[open_id] = PendingEntry(
        request=open_req, future=futs[1],
        division="robinhood_pmcc", pmcc_pair_id=pair_id,
    )
    return loop, futs


def test_index_coalesces_paired_entries_into_one_row(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_paired(reg, "ord-CR1", "ord-OR1", pair_id="pair-IDX")
    r = client.get("/approvals")
    assert r.status_code == 200
    body = r.text
    # One row, "paired" badge, combined headline.
    assert "paired" in body.lower()
    assert "ROLL · MSTR · close + open" in body
    # Both legs counted somewhere; pair id visible.
    assert "2 legs" in body
    assert "pair-IDX" in body
    loop.close()


def test_paired_detail_renders_both_legs_and_net(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_paired(reg, "ord-CD1", "ord-OD1", pair_id="pair-DET")
    # Loading either leg should render the paired card.
    r = client.get("/approvals/ord-CD1")
    assert r.status_code == 200
    body = r.text
    # Both buy and sell labels present; paired-roll badge; sibling order id link.
    assert "paired roll" in body.lower()
    assert "Approve both" in body
    assert "Reject both" in body
    # The hidden also_resolve_paired field is set.
    assert 'name="also_resolve_paired"' in body
    assert 'value="true"' in body
    loop.close()


def test_paired_decide_also_resolves_sibling(client_with_registry):
    client, reg = client_with_registry
    loop, futs = _seed_paired(reg, "ord-CP1", "ord-OP1", pair_id="pair-DEC")
    close_fut, open_fut = futs

    r = client.post(
        "/approvals/ord-CP1/decide",
        data={"decision": "approve", "also_resolve_paired": "true"},
    )
    assert r.status_code == 200
    assert "both legs resolved" in r.text
    assert close_fut.done()
    assert open_fut.done()
    assert close_fut.result().decision == "approve"
    assert open_fut.result().decision == "approve"
    loop.close()


def test_paired_decide_without_flag_only_resolves_one_leg(client_with_registry):
    """Sanity: when also_resolve_paired is omitted, only the targeted
    leg is resolved (no automatic coalescing on the resolve side — only
    the UI form opts in)."""
    client, reg = client_with_registry
    loop, futs = _seed_paired(reg, "ord-CO1", "ord-OO1", pair_id="pair-NOPAIR")
    close_fut, open_fut = futs

    r = client.post(
        "/approvals/ord-CO1/decide",
        data={"decision": "approve"},   # no also_resolve_paired
    )
    assert r.status_code == 200
    assert close_fut.done()
    assert not open_fut.done()
    loop.close()


# ── B.5 — new_limit_price + quick-modify presets ────────────────────────


def test_decide_modify_with_new_limit_price_only(client_with_registry):
    """B.5 — modify with only new_limit_price (no new_qty) is valid."""
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-LIM1")
    r = client.post(
        "/approvals/ord-LIM1/decide",
        data={"decision": "modify", "new_limit_price": "5.25"},
    )
    assert r.status_code == 200, r.text
    assert "limit=$5.25" in r.text
    out = fut.result()
    assert out.decision == "modify"
    assert out.new_qty is None
    assert out.new_limit_price == 5.25
    loop.close()


def test_decide_modify_with_both_new_qty_and_new_limit(client_with_registry):
    """B.5 — modify with BOTH fields applies both atomically."""
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-BOTH")
    r = client.post(
        "/approvals/ord-BOTH/decide",
        data={"decision": "modify", "new_qty": "3", "new_limit_price": "10.0"},
    )
    assert r.status_code == 200
    assert "qty=3" in r.text
    assert "limit=$10.00" in r.text
    out = fut.result()
    assert out.new_qty == 3.0
    assert out.new_limit_price == 10.0
    loop.close()


def test_decide_modify_neither_field_400(client_with_registry):
    """B.5 — modify with neither new_qty nor new_limit_price is rejected."""
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-NEITHER")
    r = client.post(
        "/approvals/ord-NEITHER/decide",
        data={"decision": "modify"},
    )
    assert r.status_code == 400
    assert "at least one" in r.text
    loop.close()


def test_decide_modify_zero_new_limit_400(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-LIMZ")
    r = client.post(
        "/approvals/ord-LIMZ/decide",
        data={"decision": "modify", "new_limit_price": "0"},
    )
    assert r.status_code == 400
    assert "must be > 0" in r.text
    loop.close()


def test_decide_modify_non_numeric_new_limit_400(client_with_registry):
    client, reg = client_with_registry
    loop, _ = _seed_pending(reg, "ord-LIMS")
    r = client.post(
        "/approvals/ord-LIMS/decide",
        data={"decision": "modify", "new_limit_price": "abc"},
    )
    assert r.status_code == 400
    loop.close()


def test_decide_modify_via_json_with_new_limit_price(client_with_registry):
    client, reg = client_with_registry
    loop, fut = _seed_pending(reg, "ord-LIMJ")
    r = client.post(
        "/approvals/ord-LIMJ/decide",
        json={"decision": "modify", "new_limit_price": 7.5},
    )
    assert r.status_code == 200
    out = fut.result()
    assert out.new_limit_price == 7.5
    loop.close()


def test_detail_template_renders_quick_modify_buttons(client_with_registry):
    """Smoke: the detail page surfaces the four quick-modify presets
    when the order has both qty and a limit_price (mark)."""
    client, reg = client_with_registry
    import json as _json
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    extra = {
        "is_option": True, "underlying": "NVDA", "strike": 150.0,
        "option_type": "call", "expiration": "2026-05-09",
        "action": "open_short_call", "mark_per_share": 5.50,
    }
    req = ApprovalRequest(
        order_id="ord-PRESET",
        summary="📤 *SELL CALL TO OPEN* · `NVDA` · robinhood_pmcc",
        detail={
            "order": {
                "id": "ord-PRESET", "symbol": "NVDA", "side": "sell",
                "qty": 2.0, "limit_price": 5.50,
                "extra_json": _json.dumps(extra),
            },
            "division": "robinhood_pmcc",
            "risk_verdict": {"verdict": "approve", "reason": "ok"},
        },
    )
    fut = loop.create_future()
    from trading_corp.comms.pending_registry import PendingEntry
    reg._pending["ord-PRESET"] = PendingEntry(
        request=req, future=fut, division="robinhood_pmcc",
    )

    r = client.get("/approvals/ord-PRESET")
    assert r.status_code == 200
    body = r.text
    # All four presets render with computed values.
    assert "½× size" in body
    assert "2× size" in body
    assert "limit −5%" in body
    assert "limit +5%" in body
    assert 'data-preset-kind="qty-half"' in body
    assert 'data-preset-kind="qty-double"' in body
    assert 'data-preset-kind="limit-down"' in body
    assert 'data-preset-kind="limit-up"' in body
    # Computed preset values: qty=2.0 → ½ = 1, ×2 = 4. mark=5.50 → -5% = $5.22, +5% = $5.78
    assert 'data-preset-value="1"' in body
    assert 'data-preset-value="4"' in body
    assert 'data-preset-value="5.22"' in body
    assert 'data-preset-value="5.78"' in body
    # The new_limit_price input field appears since the order has a price.
    assert 'name="new_limit_price"' in body
    loop.close()


def test_detail_template_disables_limit_buttons_when_no_price(client_with_registry):
    """Smoke: when an order has no limit_price (market order without
    mark), the limit-modify buttons are disabled."""
    client, reg = client_with_registry
    import json as _json
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Stock market order with no limit_price.
    req = ApprovalRequest(
        order_id="ord-NOPRICE",
        summary="market order",
        detail={
            "order": {
                "id": "ord-NOPRICE", "symbol": "AAPL", "side": "buy",
                "qty": 100, "limit_price": None,
                "order_type": "market", "extra_json": "{}",
            },
            "division": "default",
            "risk_verdict": {"verdict": "approve", "reason": "ok"},
        },
    )
    fut = loop.create_future()
    from trading_corp.comms.pending_registry import PendingEntry
    reg._pending["ord-NOPRICE"] = PendingEntry(
        request=req, future=fut, division="default",
    )

    r = client.get("/approvals/ord-NOPRICE")
    assert r.status_code == 200
    body = r.text
    # Limit buttons present-but-disabled.
    assert 'data-preset-kind="limit-down"' in body
    assert 'data-preset-kind="limit-up"' in body
    # Look for disabled attribute near the limit-down button.
    import re
    m = re.search(
        r'data-preset-kind="limit-down"[^>]*?disabled', body,
    )
    assert m is not None, "limit-down preset should be disabled when no price"
    loop.close()
