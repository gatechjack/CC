"""Tests for PendingApprovalRegistry — Phase B.1 of HITL-in-app.

Pins the contract used by the orchestrator (calls `wait`) and the two
resolver surfaces (TelegramChannel callback + web POST handler — both
call `resolve`). Tests exercise the registry directly; integration with
TelegramChannel + web routes is covered separately.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.comms.pending_registry import PendingApprovalRegistry
from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision
from trading_corp.persistence import db


# ── Fixtures ─────────────────────────────────────────────────────────────


def _req(order_id: str = "ord-1", summary: str = "ROLL · MSTR",
         division: str | None = "robinhood_pmcc") -> ApprovalRequest:
    detail = {"division": division} if division else {}
    return ApprovalRequest(order_id=order_id, summary=summary, detail=detail)


@pytest.fixture
def registry_no_log() -> PendingApprovalRegistry:
    return PendingApprovalRegistry(logger_agent=None)


@pytest.fixture
def registry_with_log(tmp_db) -> tuple[PendingApprovalRegistry, str]:
    db.init_db(tmp_db)
    reg = PendingApprovalRegistry(logger_agent=LoggerAgent(tmp_db))
    return reg, tmp_db


def _audit_kinds(db_url: str) -> list[tuple[str, dict]]:
    """Return (kind, payload) pairs for all audit_event rows, oldest-first."""
    sqlite_path = db_url.replace("sqlite:///", "")
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rs = conn.execute(
            "SELECT kind, payload_json FROM audit_event ORDER BY id ASC"
        ).fetchall()
    return [(r["kind"], json.loads(r["payload_json"])) for r in rs]


# ── wait + resolve happy path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_blocks_until_resolve_and_returns_decision(registry_no_log):
    reg = registry_no_log
    req = _req("ord-A")
    decision = BoardDecision(decision="approve", reason="ok")

    async def _resolver_after_delay():
        await asyncio.sleep(0.01)
        assert reg.resolve("ord-A", decision, source="web") is True

    resolver_task = asyncio.create_task(_resolver_after_delay())
    out = await reg.wait(req, timeout_s=2.0)
    await resolver_task

    assert out.decision == "approve"
    assert out.reason == "ok"
    # Entry cleaned up after wait returns.
    assert reg.pending_count() == 0
    assert reg.get("ord-A") is None


@pytest.mark.asyncio
async def test_wait_timeout_returns_synthetic_reject(registry_no_log):
    reg = registry_no_log
    out = await reg.wait(_req("ord-T"), timeout_s=0.05)
    assert out.decision == "reject"
    assert "timeout" in out.reason.lower()
    assert reg.pending_count() == 0


# ── resolve idempotency + error cases ────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_returns_false_on_unknown_order_id(registry_no_log):
    reg = registry_no_log
    decision = BoardDecision(decision="approve")
    assert reg.resolve("never-registered", decision, source="web") is False


@pytest.mark.asyncio
async def test_resolve_idempotency_second_call_returns_false(registry_no_log):
    reg = registry_no_log
    req = _req("ord-IDP")
    first = BoardDecision(decision="approve", reason="first")
    second = BoardDecision(decision="reject", reason="second")

    async def _race():
        await asyncio.sleep(0.005)
        assert reg.resolve("ord-IDP", first, source="telegram") is True
        # Second call: entry already resolved (Future done) → False.
        assert reg.resolve("ord-IDP", second, source="web") is False

    race = asyncio.create_task(_race())
    out = await reg.wait(req, timeout_s=2.0)
    await race
    assert out.decision == "approve"
    assert out.reason == "first"


# ── Audit row writes ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_writes_pending_approval_added_audit(registry_with_log):
    reg, db_url = registry_with_log
    req = _req("ord-AUD-1", summary="OPEN · NVDA")
    decision = BoardDecision(decision="approve", reason="ok")

    async def _resolver():
        await asyncio.sleep(0.005)
        reg.resolve("ord-AUD-1", decision, source="web")

    asyncio.create_task(_resolver())
    await reg.wait(req, timeout_s=2.0)

    rows = _audit_kinds(db_url)
    kinds = [k for k, _ in rows]
    assert "pending_approval_added" in kinds
    added = next(p for k, p in rows if k == "pending_approval_added")
    assert added["order_id"] == "ord-AUD-1"
    assert added["summary"] == "OPEN · NVDA"
    assert added["division"] == "robinhood_pmcc"


@pytest.mark.asyncio
async def test_resolve_writes_board_decision_received_with_source(
    registry_with_log,
):
    reg, db_url = registry_with_log
    req = _req("ord-AUD-2")
    decision = BoardDecision(
        decision="approve", reason="board ok", new_qty=None,
    )

    async def _resolver():
        await asyncio.sleep(0.005)
        reg.resolve("ord-AUD-2", decision, source="telegram")

    asyncio.create_task(_resolver())
    await reg.wait(req, timeout_s=2.0)

    rows = _audit_kinds(db_url)
    rcvd = next(
        (p for k, p in rows if k == "board_decision_received"), None,
    )
    assert rcvd is not None
    assert rcvd["order_id"] == "ord-AUD-2"
    assert rcvd["decision"] == "approve"
    assert rcvd["reason"] == "board ok"
    assert rcvd["source"] == "telegram"


@pytest.mark.asyncio
async def test_wait_timeout_writes_board_decision_received_source_timeout(
    registry_with_log,
):
    reg, db_url = registry_with_log
    out = await reg.wait(_req("ord-AUD-3"), timeout_s=0.05)
    assert out.decision == "reject"

    rows = _audit_kinds(db_url)
    timeout_row = next(
        (p for k, p in rows if k == "board_decision_received"), None,
    )
    assert timeout_row is not None
    assert timeout_row["source"] == "timeout"
    assert timeout_row["decision"] == "reject"


# ── Notifier fan-out ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_notifier_fan_out_all_called(registry_no_log):
    reg = registry_no_log
    seen: list[str] = []

    async def n1(req):
        seen.append(f"n1:{req.order_id}")

    async def n2(req):
        seen.append(f"n2:{req.order_id}")

    reg.register_notifier(n1)
    reg.register_notifier(n2)

    req = _req("ord-N")
    decision = BoardDecision(decision="approve")

    async def _resolver():
        await asyncio.sleep(0.005)
        reg.resolve("ord-N", decision, source="web")

    asyncio.create_task(_resolver())
    await reg.wait(req, timeout_s=2.0)
    assert "n1:ord-N" in seen
    assert "n2:ord-N" in seen


@pytest.mark.asyncio
async def test_notifier_exception_does_not_block_others(registry_no_log):
    reg = registry_no_log
    seen: list[str] = []

    async def bad(req):
        raise RuntimeError("notifier-boom")

    async def good(req):
        seen.append(req.order_id)

    reg.register_notifier(bad)
    reg.register_notifier(good)

    req = _req("ord-EXC")
    async def _resolver():
        await asyncio.sleep(0.005)
        reg.resolve("ord-EXC", BoardDecision(decision="approve"), source="web")

    asyncio.create_task(_resolver())
    out = await reg.wait(req, timeout_s=2.0)
    assert out.decision == "approve"
    assert "ord-EXC" in seen


# ── Read-only views ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_pending_returns_entries_newest_first(registry_no_log):
    reg = registry_no_log
    waits: list[asyncio.Task] = []

    for i in range(3):
        async def _w(idx=i):
            await reg.wait(_req(f"ord-L-{idx}"), timeout_s=2.0)
        waits.append(asyncio.create_task(_w()))
        # Ensure each wait registers + has a distinct added_at.
        await asyncio.sleep(0.002)

    # Give the last wait time to register before snapshotting.
    await asyncio.sleep(0.01)
    entries = reg.list_pending()
    assert len(entries) == 3
    # Newest-first: ord-L-2 should be first.
    ids = [e.request.order_id for e in entries]
    assert ids == ["ord-L-2", "ord-L-1", "ord-L-0"]

    # Cleanup so the test doesn't leak waits.
    for i in range(3):
        reg.resolve(f"ord-L-{i}", BoardDecision(decision="reject"), source="test")
    for w in waits:
        await w


@pytest.mark.asyncio
async def test_get_returns_request_or_none(registry_no_log):
    reg = registry_no_log
    assert reg.get("nope") is None
    assert reg.get_entry("nope") is None

    req = _req("ord-G")
    async def _w():
        await reg.wait(req, timeout_s=2.0)
    w = asyncio.create_task(_w())
    await asyncio.sleep(0.005)

    out = reg.get("ord-G")
    assert out is not None
    assert out.order_id == "ord-G"

    entry = reg.get_entry("ord-G")
    assert entry is not None
    assert entry.request.order_id == "ord-G"
    assert entry.division == "robinhood_pmcc"

    reg.resolve("ord-G", BoardDecision(decision="reject"), source="test")
    await w


# ── B.3 — pair-coalescing semantics ─────────────────────────────────────


def _paired_req(order_id: str, pair_id: str, action: str = "roll_short_call_close",
                side: str = "buy") -> ApprovalRequest:
    """ApprovalRequest with a fully-shaped detail dict carrying
    pmcc_pair_id in the order's extra_json (mirrors what graph/ceo_graph
    produces from a PMCC scout-emitted ProposedOrder)."""
    extra = {
        "is_option": True, "underlying": "MSTR", "strike": 170.0,
        "option_type": "call", "expiration": "2026-05-09",
        "action": action, "pmcc_pair_id": pair_id,
    }
    return ApprovalRequest(
        order_id=order_id,
        summary=f"ROLL · MSTR · {action}",
        detail={
            "order": {
                "id": order_id, "symbol": "MSTR", "side": side, "qty": 1.0,
                "extra_json": json.dumps(extra),
            },
            "division": "robinhood_pmcc",
            "risk_verdict": {"verdict": "approve", "reason": "ok"},
        },
    )


@pytest.mark.asyncio
async def test_pmcc_pair_id_extracted_into_entry(registry_no_log):
    reg = registry_no_log
    req = _paired_req("ord-CLOSE", "pair-X")

    async def _w():
        await reg.wait(req, timeout_s=2.0)
    w = asyncio.create_task(_w())
    await asyncio.sleep(0.005)

    e = reg.get_entry("ord-CLOSE")
    assert e is not None
    assert e.pmcc_pair_id == "pair-X"

    reg.resolve("ord-CLOSE", BoardDecision(decision="reject"), source="test")
    await w


@pytest.mark.asyncio
async def test_find_sibling_returns_other_leg_with_same_pair_id(registry_no_log):
    reg = registry_no_log
    close_req = _paired_req("ord-CLOSE", "pair-Y", action="roll_short_call_close", side="buy")
    open_req = _paired_req("ord-OPEN", "pair-Y", action="roll_short_call_open", side="sell")

    tasks = [
        asyncio.create_task(reg.wait(close_req, timeout_s=2.0)),
        asyncio.create_task(reg.wait(open_req, timeout_s=2.0)),
    ]
    await asyncio.sleep(0.01)

    # From the close leg's POV, the sibling is the open leg.
    sib = reg.find_sibling("ord-CLOSE")
    assert sib is not None
    assert sib.order_id == "ord-OPEN"
    # And vice versa.
    sib2 = reg.find_sibling("ord-OPEN")
    assert sib2 is not None
    assert sib2.order_id == "ord-CLOSE"

    # Cleanup.
    for oid in ("ord-CLOSE", "ord-OPEN"):
        reg.resolve(oid, BoardDecision(decision="reject"), source="test")
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_find_sibling_none_when_solo(registry_no_log):
    reg = registry_no_log
    req = _paired_req("ord-SOLO", "pair-Z")

    t = asyncio.create_task(reg.wait(req, timeout_s=2.0))
    await asyncio.sleep(0.005)
    assert reg.find_sibling("ord-SOLO") is None
    reg.resolve("ord-SOLO", BoardDecision(decision="reject"), source="test")
    await t


@pytest.mark.asyncio
async def test_find_sibling_none_when_no_pair_id(registry_no_log):
    reg = registry_no_log
    # Plain (non-paired) request.
    req = ApprovalRequest(
        order_id="ord-NOPAIR", summary="solo", detail={"division": "x"},
    )
    t = asyncio.create_task(reg.wait(req, timeout_s=2.0))
    await asyncio.sleep(0.005)
    assert reg.find_sibling("ord-NOPAIR") is None
    reg.resolve("ord-NOPAIR", BoardDecision(decision="reject"), source="test")
    await t


@pytest.mark.asyncio
async def test_resolve_also_paired_resolves_both_atomically(registry_no_log):
    reg = registry_no_log
    close_req = _paired_req("ord-PCLOSE", "pair-A", action="roll_short_call_close", side="buy")
    open_req = _paired_req("ord-POPEN", "pair-A", action="roll_short_call_open", side="sell")

    tasks = {
        "close": asyncio.create_task(reg.wait(close_req, timeout_s=2.0)),
        "open": asyncio.create_task(reg.wait(open_req, timeout_s=2.0)),
    }
    await asyncio.sleep(0.01)

    decision = BoardDecision(decision="approve", reason="board ok")
    accepted = reg.resolve(
        "ord-PCLOSE", decision, source="web", also_resolve_paired=True,
    )
    assert accepted is True

    out_close = await tasks["close"]
    out_open = await tasks["open"]
    assert out_close.decision == "approve"
    assert out_open.decision == "approve"
    assert out_open.reason == "board ok"


@pytest.mark.asyncio
async def test_resolve_also_paired_writes_paired_with_audit(registry_with_log):
    reg, db_url = registry_with_log
    close_req = _paired_req("ord-AC", "pair-AUD", action="roll_short_call_close", side="buy")
    open_req = _paired_req("ord-AO", "pair-AUD", action="roll_short_call_open", side="sell")

    tasks = [
        asyncio.create_task(reg.wait(close_req, timeout_s=2.0)),
        asyncio.create_task(reg.wait(open_req, timeout_s=2.0)),
    ]
    await asyncio.sleep(0.01)

    reg.resolve(
        "ord-AC", BoardDecision(decision="approve"), source="web",
        also_resolve_paired=True,
    )
    await asyncio.gather(*tasks)

    rows = _audit_kinds(db_url)
    decision_rows = [p for k, p in rows if k == "board_decision_received"]
    # Two decision rows — one per leg, each tagged with paired_with.
    assert len(decision_rows) == 2
    by_oid = {r["order_id"]: r for r in decision_rows}
    assert by_oid["ord-AC"]["paired_with"] == "ord-AO"
    assert by_oid["ord-AO"]["paired_with"] == "ord-AC"


@pytest.mark.asyncio
async def test_resolve_also_paired_no_op_when_no_sibling(registry_no_log):
    reg = registry_no_log
    req = _paired_req("ord-LONELY", "pair-LONELY")

    t = asyncio.create_task(reg.wait(req, timeout_s=2.0))
    await asyncio.sleep(0.005)

    accepted = reg.resolve(
        "ord-LONELY", BoardDecision(decision="approve"), source="web",
        also_resolve_paired=True,
    )
    assert accepted is True
    out = await t
    assert out.decision == "approve"
