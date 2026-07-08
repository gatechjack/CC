"""PMCC approval-lifecycle reconciler — external recovery for orphaned `risk_approved` rows.

STEP 3 of the PMCC lifecycle-leak fix. Design + forensic:
`reports/2026-07-08_pmcc_risk_approved_forensic.md` + `..._lifecycle_fix_design.md`.

Root cause (STEP 1): `proposed_order.status` for HITL-gated PMCC orders is advanced out of
`risk_approved` ONLY as a live side-effect of `_run_order`'s in-process resume loop, with no
external recovery. Two manifestations, both strand the row at `risk_approved`:
  (A) resume-after-decision fails (checkpointer `database is locked`, main.py:1097): a
      `board_decision_received` audit exists but the row was never written back.
  (B) the up-to-1h approval wait is cancelled by a restart: the LangGraph thread is left
      suspended in the checkpointer with no recorded decision and no boot recovery.

This module adds the missing external recovery path — ALL scoped strictly to
`strategy='robinhood_pmcc'` (fence held; the coupling is division-agnostic — see the design
doc's adjacent-findings + the end-of-session memory):

  * `expire_pmcc_approval()`         — shared, idempotent status writer. Every recovery source
                                       lands the row at 'board_rejected' (Board steer #1); the
                                       manifestation is distinguished ONLY in the audit trail.
  * `reconcile_pmcc_approvals()`     — Fix A: periodic audit-triggered sweep (manifestation A).
  * `recover_orphaned_pmcc_threads_on_boot()` — Fix B: boot checkpointer-thread recovery (B).
  * `pmcc_orphan_canary()`           — emits `pmcc_orphan_detected` if either fix regresses.
  * `run_pmcc_approval_reconcile_loop()` — the periodic background loop (reconcile → canary).

Audit kinds (per Board steer — a 6-month regression check can distinguish source):
  Fix A  -> pmcc_orphan_reconciler_recovered  (cause=decision_recorded_status_stuck)
  Fix B  -> pmcc_orphan_boot_recovered        (cause=wait_cancelled_by_restart)
  backfill (standalone script, parity) -> pmcc_orphan_backfilled (cause=resume_write_failed_or_registry_race)
  canary -> pmcc_orphan_detected
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)

# ── Scope + thresholds (tunable module constants; rationale in the design doc §0) ─────────
STRATEGY = "robinhood_pmcc"            # fence: every query filters on this
APPROVAL_TIMEOUT_S = 3600              # existing registry.wait timeout (the auto-reject deadline)
RECONCILE_GRACE_MIN = 90              # timeout(60)+30 buffer; past this a stuck row is definitively
                                      # orphaned (a live pending order would already have timed-out
                                      # -> board_rejected at 60m), so recovery can't race a live wait.
RECONCILE_INTERVAL_S = 300           # 5-min loop cadence (matches _scheduled_pead_reconcile_loop)
CANARY_DETECT_MIN = 180              # canary alarm threshold; ABOVE grace+interval so a healthy
                                      # (reconciled-at-90) system always reads 0 -> true tripwire.
# Deploy-date cutoff: the PERIODIC reconciler + canary act ONLY on post-deploy orphans, leaving
# the pre-existing residue to the operator-authorized backfill (design fork #2). Boot-recovery
# has NO cutoff (it must clear pre-existing suspended threads). Harmless once the backfill ran.
RECONCILE_MIN_TS = "2026-07-08T00:00:00+00:00"

# Audit kinds + causes.
AUDIT_ACTOR = "pmcc_reconciler"
KIND_RECONCILER = "pmcc_orphan_reconciler_recovered"
KIND_BOOT = "pmcc_orphan_boot_recovered"
KIND_BACKFILL = "pmcc_orphan_backfilled"           # used by the standalone backfill (parity)
KIND_DETECTED = "pmcc_orphan_detected"
CAUSE_RECONCILER = "decision_recorded_status_stuck"
CAUSE_BOOT = "wait_cancelled_by_restart"
CAUSE_BACKFILL = "resume_write_failed_or_registry_race"

_TERMINAL_STATUS = "board_rejected"     # Board steer #1 — same terminal row status for all
_ROW_COLS = (
    "id, ts, strategy, symbol, side, qty, order_type, limit_price, rationale, status, "
    "risk_reason, board_reason, fill_price, fill_ts, extra_json, execution_mode"
)


# ── small helpers ─────────────────────────────────────────────────────────────────────────
def _parse_ts(ts: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _elapsed_s(ts: str | None, now: datetime) -> int | None:
    dt = _parse_ts(ts)
    return int((now - dt).total_seconds()) if dt is not None else None


def _order_from_row(r: dict) -> ProposedOrder:
    """Reconstruct a ProposedOrder from a full proposed_order row (no from_db helper exists)."""
    extra: dict = {}
    if r.get("extra_json"):
        try:
            extra = json.loads(r["extra_json"]) or {}
        except (TypeError, ValueError):
            extra = {}
    return ProposedOrder(
        strategy=r["strategy"], symbol=r["symbol"], side=r["side"], qty=r["qty"],
        order_type=r.get("order_type") or "market", limit_price=r.get("limit_price"),
        rationale=r.get("rationale") or "", extra=extra,
        id=r["id"], ts=r["ts"], status=r["status"],
        risk_reason=r.get("risk_reason"), board_reason=r.get("board_reason"),
        fill_price=r.get("fill_price"), fill_ts=r.get("fill_ts"),
        execution_mode=r.get("execution_mode"),
    )


def _stuck_rows(
    db_url: str, *, now: datetime, older_than_min: float | None, min_ts: str | None = None,
) -> list[dict]:
    """PMCC rows still at status='risk_approved'. `older_than_min` bounds by age (None = no
    age floor — boot recovery, where every suspended thread is provably orphaned).
    `min_ts` optionally bounds below by the deploy cutoff (periodic reconciler/canary)."""
    sql = (
        f"SELECT {_ROW_COLS} FROM proposed_order "
        "WHERE strategy=? AND status='risk_approved'"
    )
    params: list[Any] = [STRATEGY]
    if older_than_min is not None:
        sql += " AND ts < ?"
        params.append(iso(now - timedelta(minutes=older_than_min)))
    if min_ts is not None:
        sql += " AND ts >= ?"
        params.append(min_ts)
    sql += " ORDER BY ts"
    with db.connect(db_url) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _latest_board_decision(db_url: str, order_id: str) -> dict | None:
    """Latest `hitl/board_decision_received` audit payload for order_id, or None."""
    sql = (
        "SELECT payload_json FROM audit_event "
        "WHERE actor='hitl' AND kind='board_decision_received' "
        "AND json_extract(payload_json,'$.order_id')=? ORDER BY ts DESC LIMIT 1"
    )
    with db.connect(db_url) as conn:
        row = conn.execute(sql, (order_id,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None


async def _aget_tuple_safe(saver: Any, order_id: str) -> Any:
    try:
        return await saver.aget_tuple({"configurable": {"thread_id": order_id}})
    except Exception as e:  # pragma: no cover - defensive
        log.warning("pmcc: aget_tuple failed for %s: %s", order_id, e)
        return None


async def _adelete_thread_safe(saver: Any, order_id: str) -> bool:
    """Best-effort delete of a suspended checkpointer thread. Never raises."""
    try:
        await saver.adelete_thread(order_id)
        return True
    except AttributeError:
        log.warning(
            "pmcc: checkpointer has no adelete_thread; thread %s left "
            "(row already board_rejected)", order_id,
        )
        return False
    except Exception as e:  # pragma: no cover - defensive
        log.warning("pmcc: adelete_thread failed for %s: %s", order_id, e)
        return False


# ── shared idempotent writer (Fix A + Fix B; backfill mirrors this by documented parity) ────
def expire_pmcc_approval(
    db_url: str,
    logger: LoggerAgent,
    order_id: str,
    *,
    audit_kind: str,
    cause: str,
    reason: str,
    decision: str = "reject",
    evidence: dict | None = None,
    now: datetime | None = None,
) -> bool:
    """Idempotently advance ONE orphaned PMCC `risk_approved` row to `board_rejected`.

    Returns True iff it acted (the row was still `risk_approved`). No-op (False) otherwise —
    so re-running any caller is safe. Thread cleanup (Fix B) is done by the async caller AFTER
    this returns True (this writer is deliberately sync: it's the shared, easily-tested core).

    Writes mirror `end_rejected_node` (status + board_reason + a `board/board_rejected` audit)
    and ADD the manifestation-specific `audit_kind` (+ cause + evidence) recovery audit.
    Refuses any row whose strategy != robinhood_pmcc (fence).
    """
    now = now or now_utc()
    with db.connect(db_url) as conn:
        row = conn.execute(
            f"SELECT {_ROW_COLS} FROM proposed_order WHERE id=?", (order_id,)
        ).fetchone()
    if row is None:
        return False
    r = dict(row)
    if r["strategy"] != STRATEGY:
        log.warning(
            "expire_pmcc_approval: refusing non-pmcc order %s (strategy=%s)",
            order_id, r["strategy"],
        )
        return False
    if r["status"] != "risk_approved":
        return False  # idempotent no-op (already terminal)

    order = _order_from_row(r)
    order.status = _TERMINAL_STATUS
    order.board_reason = reason
    logger.log_proposed_order(order)

    # Parity audit: the same board/board_rejected row a healthy end_rejected_node writes,
    # so downstream board-decision consumers + the healthy-path tally stay consistent.
    logger.log_event(actor="board", kind="board_rejected", payload={
        "order_id": order_id, "reason": reason, "recovered_by": audit_kind,
    })
    # Manifestation-specific recovery audit (distinct kind + cause) for the regression check.
    logger.log_event(actor=AUDIT_ACTOR, kind=audit_kind, payload={
        "order_id": order_id, "strategy": STRATEGY, "division": STRATEGY,
        "cause": cause, "reason": reason, "decision": decision,
        "row_ts": r["ts"], "recovered_ts": iso(now),
        "wait_elapsed_s": _elapsed_s(r["ts"], now), "evidence": evidence or {},
    })
    return True


# ── Fix A — periodic audit-triggered reconciler (manifestation A) ───────────────────────────
async def reconcile_pmcc_approvals(
    db_url: str, logger: LoggerAgent, *, saver: Any = None,
    now: datetime | None = None, min_ts: str | None = RECONCILE_MIN_TS,
) -> int:
    """Recover manifestation-A orphans: PMCC rows stuck at `risk_approved` past the grace
    window that HAVE a recorded `board_decision_received` (reject) audit the resume failed to
    write back. Only REJECT decisions are auto-applied — a recorded approve/modify that never
    executed is left for the canary to surface to a human (never silently board_reject an
    approved order). Post-cutoff only (`min_ts`) so it never pre-empts the authorized backfill.
    """
    now = now or now_utc()
    recovered = 0
    for r in _stuck_rows(db_url, now=now, older_than_min=RECONCILE_GRACE_MIN, min_ts=min_ts):
        oid = r["id"]
        decision = _latest_board_decision(db_url, oid)
        if decision is None:
            continue  # no recorded decision -> not manifestation A (boot-recovery/canary)
        dec = (decision.get("decision") or "").lower()
        if dec != "reject":
            log.warning(
                "pmcc reconciler: skipping %s — recorded decision=%r (not reject); "
                "canary will flag", oid, dec,
            )
            continue
        acted = expire_pmcc_approval(
            db_url, logger, oid,
            audit_kind=KIND_RECONCILER, cause=CAUSE_RECONCILER,
            reason=f"recovered from recorded board decision (reject, source={decision.get('source')})",
            decision="reject", now=now,
            evidence={
                "board_decision_received_source": decision.get("source"),
                "board_decision_received_reason": decision.get("reason"),
            },
        )
        if acted:
            recovered += 1
            if saver is not None:
                await _adelete_thread_safe(saver, oid)  # clear any lingering suspended thread
    return recovered


# ── Fix B — boot checkpointer-thread recovery (manifestation B) ─────────────────────────────
async def recover_orphaned_pmcc_threads_on_boot(
    db_url: str, logger: LoggerAgent, saver: Any, *, now: datetime | None = None,
) -> int:
    """Recover manifestation-B orphans at startup: PMCC rows still `risk_approved` whose
    LangGraph thread is still suspended in the checkpointer, with NO recorded decision (the
    wait was cancelled by a restart before the 1h timeout could fire).

    Invariant on return: no suspended LangGraph thread for a `robinhood_pmcc` order at
    `risk_approved` with no recorded board decision remains — each is `board_rejected` and its
    checkpointer thread deleted. NO deploy-date cutoff (boot recovery must clear pre-existing
    suspended threads too — the standalone SQL backfill cannot clear checkpointer threads).
    Rows WITH a recorded decision (manifestation A) are excluded for audit-label integrity.

    Safe at boot: it runs BEFORE the scheduler starts any new approval waits, so every thread
    in the checkpointer is from a prior process lifetime = orphaned. A `risk_approved` row is
    the ONLY state whose graph pauses at the approval interrupt, so `aget_tuple != None` for
    such a row ⟹ suspended-at-approval (no age floor needed).
    """
    now = now or now_utc()
    if saver is None:
        return 0
    recovered = 0
    for r in _stuck_rows(db_url, now=now, older_than_min=None):
        oid = r["id"]
        if _latest_board_decision(db_url, oid) is not None:
            continue  # manifestation A — not boot-recovery's job (label integrity)
        tup = await _aget_tuple_safe(saver, oid)
        if tup is None:
            continue  # threadless — backfill / canary territory
        elapsed = _elapsed_s(r["ts"], now)
        acted = expire_pmcc_approval(
            db_url, logger, oid,
            audit_kind=KIND_BOOT, cause=CAUSE_BOOT,
            reason="approval window expired — boot recovery (wait cancelled by restart)",
            decision="reject", now=now,
            evidence={"thread_found": True, "elapsed_s": elapsed},
        )
        cleared = await _adelete_thread_safe(saver, oid)
        if acted:
            recovered += 1
            log.info(
                "pmcc boot-recovery: id=%s strategy=%s wait_elapsed_s=%s thread_cleared=%s",
                oid, r["strategy"], elapsed, cleared,
            )
    if recovered:
        log.info("pmcc boot-recovery: expired %d orphaned suspended-thread approval(s)", recovered)
    return recovered


# ── canary — regression tripwire ────────────────────────────────────────────────────────────
def pmcc_orphan_canary(
    db_url: str, logger: LoggerAgent, *, now: datetime | None = None,
    min_ts: str | None = RECONCILE_MIN_TS,
) -> int:
    """Emit `pmcc_orphan_detected` iff any post-cutoff PMCC row is still `risk_approved` past
    `CANARY_DETECT_MIN` — which, in a healthy system, never happens (the reconciler acts at
    90m). A non-zero count means the reconciler regressed or a new failure mode appeared.
    Detects/alarms only; never fixes. Returns the count."""
    now = now or now_utc()
    cutoff = iso(now - timedelta(minutes=CANARY_DETECT_MIN))
    sql = (
        "SELECT id, ts FROM proposed_order "
        "WHERE strategy=? AND status='risk_approved' AND ts < ?"
    )
    params: list[Any] = [STRATEGY, cutoff]
    if min_ts is not None:
        sql += " AND ts >= ?"
        params.append(min_ts)
    sql += " ORDER BY ts"
    with db.connect(db_url) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not rows:
        return 0
    logger.log_event(actor=AUDIT_ACTOR, kind=KIND_DETECTED, payload={
        "strategy": STRATEGY, "division": STRATEGY,
        "count": len(rows), "oldest_ts": rows[0]["ts"],
        "threshold_min": CANARY_DETECT_MIN,
        "sample_ids": [r["id"] for r in rows[:10]],
    })
    log.warning(
        "pmcc canary: %d risk_approved row(s) older than %dmin despite the reconciler "
        "— pmcc_orphan_detected", len(rows), CANARY_DETECT_MIN,
    )
    return len(rows)


# ── periodic background loop (Fix A + canary) ───────────────────────────────────────────────
async def run_pmcc_approval_reconcile_loop(
    db_url: str, logger: LoggerAgent, saver: Any, *, interval_s: float = RECONCILE_INTERVAL_S,
) -> None:
    """Background task: each tick runs Fix A then the canary, in isolated try/excepts, then
    sleeps `interval_s`. Mirrors `_scheduled_pead_reconcile_loop`. CancelledError exits clean."""
    log.info(
        "pmcc approval reconcile loop starting (interval=%ss grace=%smin canary=%smin)",
        interval_s, RECONCILE_GRACE_MIN, CANARY_DETECT_MIN,
    )
    while True:
        try:
            n = await reconcile_pmcc_approvals(db_url, logger, saver=saver)
            if n:
                log.info("pmcc reconciler: recovered %d orphaned approval(s)", n)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.exception("pmcc reconcile tick failed: %s", e)
        try:
            pmcc_orphan_canary(db_url, logger)
        except Exception as e:
            log.exception("pmcc canary tick failed: %s", e)
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return
