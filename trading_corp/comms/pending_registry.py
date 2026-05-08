"""In-process registry of pending HITL approval Futures.

Phase B.1 of the HITL-in-app direction (Board, 2026-05-03). The web
POST `/approvals/{order_id}/decide` and TelegramChannel's inline-keyboard
callback both interact with this registry. The orchestrator
(`_run_order` in main.py) calls `registry.wait(req)` instead of
`channel.request_approval(req)`; TelegramChannel's role narrows to
"register a notifier (push the message) + handle inline-keyboard
callbacks → resolve the registry."

Single instance per process. Constructed in main.py at startup, passed
into TelegramChannel + WebDeps. Tests construct their own per case.

Audit chain (CLAUDE.md §1 — audit before every decision branch):

    pending_approval_added         (when wait() registers the entry)
        → board_decision_received   (when resolve() fires; tagged with source)
        → board_approved | board_rejected  (existing — written by graph nodes)

Restart semantics: the registry is in-process and lost on restart.
The LangGraph SqliteSaver persists the suspended thread state, so a
restart-recovery routine can scan the checkpointer for interrupted
threads and re-emit notifications + re-add entries on the next boot.
For B.1 we accept the gap — recovery is a B-v2 polish item (see
planning/hitl_in_app_design.md §3 + §9).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision

log = logging.getLogger(__name__)

Notifier = Callable[[ApprovalRequest], Awaitable[None]]


@dataclass
class PendingEntry:
    """Public-shape entry returned by `get_entry` / iterated by the
    index page. The Future is intentionally kept on the entry rather
    than in a parallel dict so resolve() can check `done()` atomically
    with the lookup."""
    request: ApprovalRequest
    future: asyncio.Future
    division: str | None = None
    pmcc_pair_id: str | None = None
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PendingApprovalRegistry:
    """In-process registry of approval Futures keyed by order_id.

    Construct ONE per process at startup (main.py); pass into both
    TelegramChannel and WebDeps. Tests construct their own per case.
    """

    def __init__(self, logger_agent: Any = None) -> None:
        # Held under self._lock for mutations; reads (list_pending, get)
        # snapshot the dict directly — Python dict reads are atomic for
        # the single-event-loop process model we run in.
        self._pending: dict[str, PendingEntry] = {}
        self._lock = asyncio.Lock()
        self._notifiers: list[Notifier] = []
        # LoggerAgent — None in tests that don't care about audit rows.
        # The registry is best-effort about audit writes (wraps in
        # try/except) so a temporarily-down audit DB doesn't block
        # approvals.
        self._logger = logger_agent

    # ── Notifier registration ────────────────────────────────────────

    def register_notifier(self, fn: Notifier) -> None:
        """Subscribe a notifier coroutine. Each notifier receives the
        ApprovalRequest when wait() registers an entry. Notifiers fire
        concurrently; an exception in one does NOT block others or
        fail the wait (handled by `_safe_notify`)."""
        self._notifiers.append(fn)

    # ── Orchestrator-side: wait for a decision ───────────────────────

    async def wait(
        self,
        req: ApprovalRequest,
        timeout_s: float = 3600.0,
    ) -> BoardDecision:
        """Register, fan out notifiers, block until resolved or timeout.

        Replaces `channel.request_approval(req)` in the orchestrator
        path. Returns the BoardDecision the registry was resolved with,
        or a synthetic reject on timeout.

        Audit:
          - `pending_approval_added` written BEFORE notifiers fire so
            that the dashboard `/approvals` page can recover pending
            state from audit rows even if every notifier fails.
          - `board_decision_received` written by `resolve()` — not
            here. On timeout, this method writes a synthetic one
            tagged source='timeout'.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        division = (req.detail or {}).get("division")
        pair_id = _extract_pair_id(req.detail)
        entry = PendingEntry(
            request=req, future=fut,
            division=division, pmcc_pair_id=pair_id,
        )

        async with self._lock:
            self._pending[req.order_id] = entry

        # Audit FIRST — even if every notifier fails, the row is in
        # the DB so the dashboard can recover.
        self._audit("pending_approval_added", {
            "order_id": req.order_id,
            "division": division,
            "summary": req.summary,
            "pmcc_pair_id": pair_id,
        })

        # Fire notifiers concurrently. _safe_notify wraps each so a bug
        # in one (e.g. Telegram timeout) doesn't block others or fail
        # the wait.
        if self._notifiers:
            await asyncio.gather(
                *(self._safe_notify(fn, req) for fn in self._notifiers),
            )

        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            decision = BoardDecision(
                decision="reject", reason="approval timeout",
            )
            self._audit("board_decision_received", {
                "order_id": req.order_id,
                "decision": decision.decision,
                "reason": decision.reason,
                "new_qty": decision.new_qty,
                "source": "timeout",
            })
            return decision
        finally:
            # Always clean up the entry — even if the wait raised
            # CancelledError (e.g. process shutdown). Without this the
            # dict would leak entries across restarts of the orchestrator.
            self._pending.pop(req.order_id, None)

    # ── Resolver-side: complete a decision ───────────────────────────

    def resolve(
        self,
        order_id: str,
        decision: BoardDecision,
        source: str,
        also_resolve_paired: bool = False,
    ) -> bool:
        """Fulfill the pending Future for `order_id`. First call wins;
        second call returns False so the caller can surface a 409
        Conflict (web) or "decision already recorded" (Telegram).

        `source` tags the audit row: 'telegram' / 'web' / 'cli' /
        'auto' / 'timeout'. Tagged so the audit log makes the channel
        attribution explicit.

        `also_resolve_paired` (B.3): when True AND the entry has a
        `pmcc_pair_id`, the matching sibling entry is resolved with the
        SAME decision in the same call. Each leg gets its own
        `board_decision_received` audit row tagged
        `paired_with=<sibling_order_id>` for traceability. Eliminates
        the "approve close, reject open → naked short" failure mode by
        making the paired decision atomic at the registry layer.

        Returns True on accept, False if no entry or already-resolved.
        For paired calls, return value reflects ONLY the primary
        order_id; if the sibling was also-resolved that's a bonus
        (logged) but doesn't change the return — the caller's order
        was the one they decided on.

        The audit row is written BEFORE `set_result` so it lands even
        if the awaiter races and observes the result first.
        """
        entry = self._pending.get(order_id)
        if entry is None:
            return False
        fut = entry.future
        if fut.done():
            return False

        sibling_id: str | None = None
        if also_resolve_paired and entry.pmcc_pair_id:
            sibling = self._find_sibling_entry(order_id, entry.pmcc_pair_id)
            if sibling is not None:
                sibling_id = sibling.request.order_id

        self._audit("board_decision_received", {
            "order_id": order_id,
            "decision": decision.decision,
            "reason": decision.reason,
            "new_qty": decision.new_qty,
            "source": source,
            "paired_with": sibling_id,
        })
        fut.set_result(decision)

        if sibling_id is not None:
            sibling = self._pending.get(sibling_id)
            if sibling is not None and not sibling.future.done():
                self._audit("board_decision_received", {
                    "order_id": sibling_id,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "new_qty": decision.new_qty,
                    "source": source,
                    "paired_with": order_id,
                })
                sibling.future.set_result(decision)
        return True

    def find_sibling(self, order_id: str) -> ApprovalRequest | None:
        """Look up the paired sibling of `order_id`. Returns None if the
        order isn't pending, has no pmcc_pair_id, or no matching sibling
        is currently in the registry. Used by the detail page to render
        both legs in one card when both are simultaneously pending."""
        entry = self._pending.get(order_id)
        if entry is None or not entry.pmcc_pair_id:
            return None
        sibling = self._find_sibling_entry(order_id, entry.pmcc_pair_id)
        return sibling.request if sibling is not None else None

    def _find_sibling_entry(
        self, order_id: str, pmcc_pair_id: str,
    ) -> PendingEntry | None:
        """Internal helper — find the OTHER entry with the same pair_id."""
        for oid, e in self._pending.items():
            if oid != order_id and e.pmcc_pair_id == pmcc_pair_id:
                return e
        return None

    # ── Read-only views (for the index/detail UIs) ───────────────────

    def list_pending(self) -> list[PendingEntry]:
        """Snapshot of pending entries for the `/approvals` index.
        Newest-first by added_at. Returns a fresh list — mutations
        don't affect the registry."""
        entries = list(self._pending.values())
        entries.sort(key=lambda e: e.added_at, reverse=True)
        return entries

    def get(self, order_id: str) -> ApprovalRequest | None:
        """Lookup the request for the detail page. None if not pending
        (e.g. already resolved or never registered)."""
        entry = self._pending.get(order_id)
        return entry.request if entry else None

    def get_entry(self, order_id: str) -> PendingEntry | None:
        """Lookup including added_at + division — for the detail page's
        header. Kept separate from `get()` to keep the public API
        small for callers that only need the request."""
        return self._pending.get(order_id)

    def pending_count(self) -> int:
        return len(self._pending)

    # ── Internals ────────────────────────────────────────────────────

    async def _safe_notify(self, fn: Notifier, req: ApprovalRequest) -> None:
        """Run a notifier with broad exception suppression. A bug in
        one notifier (e.g. Telegram network timeout) must not block
        the wait or prevent other notifiers from firing.
        """
        try:
            await fn(req)
        except Exception as e:
            log.warning(
                "notifier %r failed for order_id=%s: %s",
                getattr(fn, "__qualname__", repr(fn)), req.order_id, e,
            )

    def _audit(self, kind: str, payload: dict[str, Any]) -> None:
        """Write an audit row through the LoggerAgent. Best-effort —
        the registry must keep working even if the audit DB is
        temporarily unavailable.
        """
        if self._logger is None:
            return
        try:
            self._logger.log_event("hitl", kind, payload)
        except Exception as e:
            log.warning("audit write failed for %s: %s", kind, e)


def _extract_pair_id(detail: dict | None) -> str | None:
    """Pull `pmcc_pair_id` out of `ApprovalRequest.detail`.

    The graph (`graph/ceo_graph.py`) stores `order.to_db_row()` under
    `detail["order"]`; that row's `extra_json` is a JSON string holding
    `pmcc_pair_id` when the strategy emitted a paired roll. Defensive
    against missing/malformed shapes — None when in doubt.
    """
    if not isinstance(detail, dict):
        return None
    order = detail.get("order")
    if not isinstance(order, dict):
        return None
    extra_json = order.get("extra_json")
    if isinstance(extra_json, dict):
        # Some test paths skip JSON encoding.
        return extra_json.get("pmcc_pair_id")
    if not extra_json:
        # Another test shape: pre-decoded `extra` dict on the order row.
        extra = order.get("extra")
        if isinstance(extra, dict):
            return extra.get("pmcc_pair_id")
        return None
    try:
        import json as _json
        decoded = _json.loads(extra_json)
        if isinstance(decoded, dict):
            return decoded.get("pmcc_pair_id")
    except (TypeError, ValueError):
        pass
    return None
