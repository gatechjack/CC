"""Tests for the PMCC approval-lifecycle reconciler (STEP 3 of the risk_approved-leak fix).

Covers: Fix A (audit-triggered reconciler), Fix B (boot checkpointer-thread recovery), the
canary, the shared writer's scope guard + idempotency, the standalone backfill (dry-run +
commit + idempotent + parity), and the S2 counter change (/pending reads the registry).
Each reproduces the POST-orphan STATE (deterministic) — not the non-deterministic db-lock /
restart timing — which is what the recovery consumes.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import timedelta

import pytest

from trading_corp.agents import pmcc_approval_reconciler as R
from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.risk import RiskAgent
from trading_corp.brokers.paper import PaperBroker
from trading_corp.comms.telegram_commands import TelegramCommands
from trading_corp.graph.ceo_graph import build_trade_graph
from trading_corp.persistence import db
from trading_corp.persistence.checkpointer import make_checkpointer
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.time import iso, now_utc

_ANY_MIN_TS = "2000-01-01T00:00:00+00:00"   # override so seeded rows never fall below the cutoff


# ── helpers ─────────────────────────────────────────────────────────────────
def _seed_stuck(logger, *, strategy="robinhood_pmcc", symbol="ASTS", side="sell",
                qty=1.0, minutes_old=200, status="risk_approved") -> ProposedOrder:
    o = ProposedOrder(
        strategy=strategy, symbol=symbol, side=side, qty=qty, order_type="limit",
        limit_price=5.0, status=status, risk_reason="ok",
        ts=iso(now_utc() - timedelta(minutes=minutes_old)),
    )
    logger.log_proposed_order(o)
    return o


def _seed_decision(logger, order_id, *, decision="reject", source="timeout") -> None:
    logger.log_event("hitl", "board_decision_received", {
        "order_id": order_id, "decision": decision, "reason": "approval timeout", "source": source,
    })


def _status(db_url, oid):
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT status, board_reason FROM proposed_order WHERE id=?", (oid,)
        ).fetchone()
    return (r["status"], r["board_reason"]) if r else (None, None)


def _kinds(db_url, oid):
    with db.connect(db_url) as conn:
        return [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE json_extract(payload_json,'$.order_id')=?", (oid,)
        ).fetchall()]


def _load_backfill():
    p = (pathlib.Path(__file__).resolve().parents[1] / "deploy"
         / "2026-07-08_pmcc_lifecycle_fix" / "backfill_pmcc_risk_approved.py")
    spec = importlib.util.spec_from_file_location("backfill_pmcc", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── shared writer: scope + idempotency ──────────────────────────────────────
def test_expire_writes_board_rejected_and_audits(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    o = _seed_stuck(logger)
    acted = R.expire_pmcc_approval(
        tmp_db, logger, o.id, audit_kind=R.KIND_RECONCILER, cause=R.CAUSE_RECONCILER,
        reason="unit test",
    )
    assert acted is True
    assert _status(tmp_db, o.id)[0] == "board_rejected"
    kinds = _kinds(tmp_db, o.id)
    assert "board_rejected" in kinds and R.KIND_RECONCILER in kinds


def test_expire_refuses_non_pmcc(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    o = _seed_stuck(logger, strategy="bitunix_futures", symbol="BTC")
    acted = R.expire_pmcc_approval(
        tmp_db, logger, o.id, audit_kind="x", cause="y", reason="z",
    )
    assert acted is False
    assert _status(tmp_db, o.id)[0] == "risk_approved"   # untouched — fence held


def test_expire_idempotent_on_terminal_row(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    o = _seed_stuck(logger)
    assert R.expire_pmcc_approval(tmp_db, logger, o.id, audit_kind=R.KIND_RECONCILER,
                                  cause=R.CAUSE_RECONCILER, reason="first") is True
    assert R.expire_pmcc_approval(tmp_db, logger, o.id, audit_kind=R.KIND_RECONCILER,
                                  cause=R.CAUSE_RECONCILER, reason="second") is False


# ── Fix A — periodic audit-triggered reconciler ──────────────────────────────
@pytest.mark.asyncio
async def test_reconciler_recovers_decision_recorded_orphan(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    o = _seed_stuck(logger)                       # risk_approved, 200min old
    _seed_decision(logger, o.id)                  # timeout-reject audit the resume never wrote back
    n = await R.reconcile_pmcc_approvals(tmp_db, logger, now=now_utc(), min_ts=_ANY_MIN_TS)
    assert n == 1
    assert _status(tmp_db, o.id)[0] == "board_rejected"
    assert R.KIND_RECONCILER in _kinds(tmp_db, o.id)
    # idempotent
    assert await R.reconcile_pmcc_approvals(tmp_db, logger, now=now_utc(), min_ts=_ANY_MIN_TS) == 0


@pytest.mark.asyncio
async def test_reconciler_no_false_positives(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    fresh = _seed_stuck(logger, symbol="RIOT", minutes_old=10)          # too young
    nodecision = _seed_stuck(logger, symbol="CIFR")                     # no decision audit
    nonpmcc = _seed_stuck(logger, strategy="bitunix_futures", symbol="BTC")
    approved = _seed_stuck(logger, symbol="OPEN")                       # recorded APPROVE, stuck
    _seed_decision(logger, approved.id, decision="approve", source="web")
    n = await R.reconcile_pmcc_approvals(tmp_db, logger, now=now_utc(), min_ts=_ANY_MIN_TS)
    assert n == 0
    for o in (fresh, nodecision, nonpmcc, approved):
        assert _status(tmp_db, o.id)[0] == "risk_approved"   # none auto-board_rejected


@pytest.mark.asyncio
async def test_reconciler_respects_cutoff(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    o = _seed_stuck(logger)
    _seed_decision(logger, o.id)
    # cutoff in the future → the pre-existing row is left for the backfill, untouched.
    future_cut = iso(now_utc() + timedelta(days=1))
    n = await R.reconcile_pmcc_approvals(tmp_db, logger, now=now_utc(), min_ts=future_cut)
    assert n == 0
    assert _status(tmp_db, o.id)[0] == "risk_approved"


# ── Fix B — boot checkpointer-thread recovery ────────────────────────────────
async def _drive_to_interrupt(tmp_db, ckpt_path, order):
    """Process 1: run the real graph to the approval interrupt (leaves the thread suspended
    in the AsyncSqliteSaver at `ckpt_path` + the row at risk_approved in `tmp_db`), then 'die'.

    NOTE: prod shares ONE sqlite file between the checkpointer and the app rows — that shared
    write transaction is exactly what causes the `database is locked` collision this fix
    recovers from (main.py:1097). Here the checkpointer is on a SEPARATE file so we can set up
    the orphan STATE deterministically (recovery-under-lock is not what these tests exercise)."""
    logger = LoggerAgent(tmp_db)
    de = DataExecAgent(logger)
    de.register_broker("default", PaperBroker(account="paper-test", starting_equity=100_000.0))
    await de.connect_all()
    risk = RiskAgent(narrator_enabled=False)   # default global caps; a $500 order clears them
    async with make_checkpointer(ckpt_path) as saver:
        graph = build_trade_graph(risk, de, logger, checkpointer=saver)
        state = {
            "proposed_order": order.to_db_row() | {"extra": order.extra},
            "division": "robinhood_pmcc", "regime": "uptrend",
            "strategy_state": {"strategy": "robinhood_pmcc", "halted": False},
            "account": {"account": "paper-test", "equity": 100_000.0, "peak_equity": 100_000.0},
        }
        result = await graph.ainvoke(state, config={"configurable": {"thread_id": order.id}})
        assert "__interrupt__" in result, f"expected interrupt; got {list(result)}"


@pytest.mark.asyncio
async def test_boot_recovery_expires_suspended_thread(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    order = ProposedOrder(strategy="robinhood_pmcc", symbol="SPY", side="buy",
                          qty=1, order_type="limit", limit_price=500.0)
    ckpt = db.resolve_db_path(tmp_db).with_name("ckpt.db")
    await _drive_to_interrupt(tmp_db, ckpt, order)

    # Row is risk_approved with NO recorded decision (manifestation B).
    assert _status(tmp_db, order.id)[0] == "risk_approved"
    assert R._latest_board_decision(tmp_db, order.id) is None

    config = {"configurable": {"thread_id": order.id}}
    async with make_checkpointer(ckpt) as saver2:   # "restart" — fresh saver, same ckpt file
        assert await saver2.aget_tuple(config) is not None   # thread survived the restart
        n = await R.recover_orphaned_pmcc_threads_on_boot(tmp_db, logger, saver2, now=now_utc())
        assert n == 1
        assert await saver2.aget_tuple(config) is None       # thread cleared

    status, reason = _status(tmp_db, order.id)
    assert status == "board_rejected"
    assert "boot recovery" in (reason or "")
    kinds = _kinds(tmp_db, order.id)
    assert R.KIND_BOOT in kinds and "board_rejected" in kinds

    # idempotent — a second boot recovers nothing.
    async with make_checkpointer(ckpt) as saver3:
        assert await R.recover_orphaned_pmcc_threads_on_boot(tmp_db, logger, saver3, now=now_utc()) == 0


@pytest.mark.asyncio
async def test_boot_recovery_skips_decision_recorded(tmp_db):
    """Label integrity: a suspended-thread row that ALSO has a recorded decision is
    manifestation A (backfill/reconciler territory), NOT boot-recovery's."""
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    order = ProposedOrder(strategy="robinhood_pmcc", symbol="SPY", side="buy",
                          qty=1, order_type="limit", limit_price=500.0)
    ckpt = db.resolve_db_path(tmp_db).with_name("ckpt.db")
    await _drive_to_interrupt(tmp_db, ckpt, order)
    _seed_decision(logger, order.id)   # now it has a decision audit → boot-recovery must skip it

    async with make_checkpointer(ckpt) as saver2:
        n = await R.recover_orphaned_pmcc_threads_on_boot(tmp_db, logger, saver2, now=now_utc())
        assert n == 0
    assert _status(tmp_db, order.id)[0] == "risk_approved"   # left for Fix A / backfill


# ── canary ───────────────────────────────────────────────────────────────────
def test_canary_emits_when_orphan_past_threshold(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    _seed_stuck(logger, minutes_old=200)   # > 180
    n = R.pmcc_orphan_canary(tmp_db, logger, now=now_utc(), min_ts=_ANY_MIN_TS)
    assert n == 1
    with db.connect(tmp_db) as conn:
        c = conn.execute(
            "SELECT COUNT(*) c FROM audit_event WHERE kind=?", (R.KIND_DETECTED,)
        ).fetchone()["c"]
    assert c == 1


def test_canary_silent_when_within_grace(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    _seed_stuck(logger, minutes_old=30)    # < 180 → the reconciler window, not an alarm
    assert R.pmcc_orphan_canary(tmp_db, logger, now=now_utc(), min_ts=_ANY_MIN_TS) == 0


# ── backfill (standalone) ─────────────────────────────────────────────────────
def test_backfill_dryrun_then_commit_idempotent(tmp_db, monkeypatch, capsys, tmp_path):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    a1 = _seed_stuck(logger, symbol="ASTS")
    a2 = _seed_stuck(logger, symbol="CIFR", side="buy")
    b1 = _seed_stuck(logger, symbol="OPEN")
    fresh = _seed_stuck(logger, symbol="RIOT", side="buy", minutes_old=10)
    nonpmcc = _seed_stuck(logger, strategy="bitunix_futures", symbol="BTC", side="buy")
    _seed_decision(logger, a1.id)
    _seed_decision(logger, a2.id)
    mod = _load_backfill()

    # dry-run: reports the 3 (a1,a2,b1); fresh excluded by age, nonpmcc by strategy.
    monkeypatch.setattr("sys.argv", ["backfill", "--db-url", tmp_db])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "would touch 3 row(s): 2 cause=A" in out
    with db.connect(tmp_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM proposed_order WHERE status='board_rejected'"
        ).fetchone()["c"] == 0                     # dry-run changed nothing

    # commit
    outfile = str(tmp_path / "ids.txt")
    monkeypatch.setattr("sys.argv", ["backfill", "--db-url", tmp_db, "--commit", "--out", outfile])
    assert mod.main() == 0
    with db.connect(tmp_db) as conn:
        rej = {r["id"] for r in conn.execute(
            "SELECT id FROM proposed_order WHERE status='board_rejected'").fetchall()}
        assert rej == {a1.id, a2.id, b1.id}
        assert _status(tmp_db, fresh.id)[0] == "risk_approved"
        assert _status(tmp_db, nonpmcc.id)[0] == "risk_approved"
        nkind = conn.execute(
            "SELECT COUNT(*) c FROM audit_event WHERE kind=?", (mod.KIND_BACKFILL,)
        ).fetchone()["c"]
        assert nkind == 3

    # idempotent — re-commit touches nothing new.
    monkeypatch.setattr("sys.argv", ["backfill", "--db-url", tmp_db, "--commit", "--out", outfile])
    assert mod.main() == 0
    with db.connect(tmp_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM proposed_order WHERE status='board_rejected'"
        ).fetchone()["c"] == 3


def test_backfill_constants_parity_with_module():
    mod = _load_backfill()
    assert mod.STRATEGY == R.STRATEGY
    assert mod.TERMINAL == R._TERMINAL_STATUS
    assert mod.KIND_BACKFILL == R.KIND_BACKFILL
    assert mod.CAUSE_A == R.CAUSE_BACKFILL      # decision-recorded cause
    assert mod.CAUSE_B == R.CAUSE_BOOT          # no-decision cause


# ── S2 counter: /pending reads the registry, not the DB residue ──────────────
class _StubReq:
    def __init__(self, oid, order):
        self.order_id = oid
        self.detail = {"order": order}
        self.summary = ""


class _StubEntry:
    def __init__(self, oid, order):
        self.request = _StubReq(oid, order)
        self.added_at = now_utc()


class _StubRegistry:
    def __init__(self, entries):
        self._entries = entries

    def list_pending(self):
        return self._entries


class _StubDeps:
    def __init__(self, registry, db_url):
        self.pending_registry = registry
        self.db_url = db_url
        self.logger_agent = None


@pytest.mark.asyncio
async def test_pending_command_reads_registry_ignoring_db_residue(tmp_db):
    db.init_db(tmp_db)
    logger = LoggerAgent(tmp_db)
    # DB residue: a stuck risk_approved row the OLD query would have counted.
    _seed_stuck(logger, symbol="ASTS")
    # empty registry → /pending must report none despite the residue.
    tc_empty = TelegramCommands(_StubDeps(_StubRegistry([]), tmp_db))
    assert "No pending approvals" in await tc_empty.pending()
    # one live registry entry → /pending renders it.
    entry = _StubEntry("abcdef123456", {"symbol": "CIFR", "side": "buy", "qty": 2, "strategy": "robinhood_pmcc"})
    tc_one = TelegramCommands(_StubDeps(_StubRegistry([entry]), tmp_db))
    body = await tc_one.pending()
    assert "CIFR" in body and "(1)" in body
