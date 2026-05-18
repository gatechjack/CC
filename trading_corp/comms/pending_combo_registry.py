"""In-process registry of pending HITL combo approvals.

Phase IC1 (2026-05-17). Sibling to `PendingApprovalRegistry`. Combos
are atomic at the broker — one approval click authorizes all 4 legs
together — so they don't fit the per-order `wait()` model used for
single-leg flows. Instead the IC strategy's `manage()`/`scan()` loops
propose combos here (non-blocking); the web app reads the registry to
render approval cards and resolves entries on Board click.

Audit chain:

    combo_proposed                    (written by orchestration layer)
       → registry.propose()           (this module)
       → board_combo_approved         (when resolve(approve) fires)
          → data_exec.place_combo()    (orchestration's place call)
          → strategy.on_combo_filled  (state-callback contract)
       OR
       → board_combo_rejected         (when resolve(reject) fires)

The registry is in-process and lost on restart. That's intentional for
v1 — a missed approval just means the strategy re-proposes on the next
manage() tick if conditions still hold. Persistent recovery is a
post-v1 polish item.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


@dataclass
class PendingComboEntry:
    combo_id: str
    orders: list[ProposedOrder]
    intent: str                              # "open"|"close"|"adjustment_1"|...
    strategy_slug: str
    division: str
    added_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def underlying(self) -> str:
        if not self.orders:
            return ""
        return (self.orders[0].extra or {}).get("underlying") or self.orders[0].symbol

    @property
    def direction(self) -> str:
        if not self.orders:
            return ""
        return (self.orders[0].extra or {}).get("combo_direction") or ""

    @property
    def net_limit_price(self) -> float | None:
        if not self.orders:
            return None
        v = (self.orders[0].extra or {}).get("net_limit_price")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None


class PendingComboRegistry:
    """In-process registry of pending IC combos keyed by combo_id.

    Construct ONE per process at startup (main.py); pass into WebDeps
    so the /approvals routes can read + resolve.
    """

    def __init__(self, logger_agent: Any = None) -> None:
        # Threading.Lock (not asyncio.Lock) so the web POST handler can
        # call resolve() without awaiting; the same lock guards the
        # orchestration-side propose() calls. Operations are O(1) so
        # holding the lock is cheap.
        self._lock = Lock()
        self._pending: dict[str, PendingComboEntry] = {}
        self._logger = logger_agent

    # ── Orchestration side ──────────────────────────────────────────

    def propose(
        self,
        combo_id: str,
        orders: list[ProposedOrder],
        *,
        intent: str,
        strategy_slug: str,
        division: str,
    ) -> PendingComboEntry:
        """Register a combo for HITL approval. Replaces any prior entry
        with the same combo_id (the strategy is expected to use a fresh
        UUID per proposal, so collision means a programming bug)."""
        if not combo_id:
            raise ValueError("combo_id is required")
        if not orders:
            raise ValueError("orders must be a non-empty list")
        entry = PendingComboEntry(
            combo_id=combo_id, orders=orders, intent=intent,
            strategy_slug=strategy_slug, division=division,
        )
        with self._lock:
            if combo_id in self._pending:
                log.warning(
                    "PendingComboRegistry: overwriting existing entry %s "
                    "(strategy %s, intent %s)",
                    combo_id, strategy_slug, intent,
                )
            self._pending[combo_id] = entry
        return entry

    # ── Web read side ──────────────────────────────────────────────

    def list_pending(self) -> list[PendingComboEntry]:
        """Snapshot of all pending entries, newest-first."""
        with self._lock:
            entries = list(self._pending.values())
        entries.sort(key=lambda e: e.added_at, reverse=True)
        return entries

    def get(self, combo_id: str) -> PendingComboEntry | None:
        with self._lock:
            return self._pending.get(combo_id)

    # ── Web POST side ──────────────────────────────────────────────

    def resolve(
        self,
        combo_id: str,
        *,
        decision: str,
        reason: str = "",
        source: str = "web",
    ) -> PendingComboEntry | None:
        """Remove and return the entry on resolve. Returns None if the
        combo was already resolved (or never registered).

        `decision` must be "approve" or "reject". The caller is
        responsible for the post-resolve action — `dispatch_approved_ic_combo`
        for approve, audit-only for reject. The registry just owns the
        bookkeeping.
        """
        if decision not in ("approve", "reject"):
            raise ValueError(f"decision must be 'approve' or 'reject', got {decision!r}")
        with self._lock:
            entry = self._pending.pop(combo_id, None)
        if entry is None:
            return None
        kind = "board_combo_approved" if decision == "approve" else "board_combo_rejected"
        self._audit(kind, {
            "combo_id": combo_id,
            "strategy": entry.strategy_slug,
            "division": entry.division,
            "intent": entry.intent,
            "leg_count": len(entry.orders),
            "source": source,
            "reason": reason,
        })
        return entry

    # ── Audit helper ───────────────────────────────────────────────

    def _audit(self, kind: str, payload: dict) -> None:
        if self._logger is None:
            return
        try:
            self._logger.log_event(actor="pending_combo_registry",
                                   kind=kind, payload=payload)
        except Exception as e:
            log.warning("combo audit write failed for %s: %s", kind, e)
