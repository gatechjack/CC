"""HITL interrupt helpers for LangGraph.

Wraps `langgraph.types.interrupt(...)` so callers can pause a graph at a
Board approval gate and resume with a typed `BoardDecision`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Decision = Literal["approve", "reject", "modify"]


@dataclass
class ApprovalRequest:
    order_id: str
    summary: str            # one-line summary for Telegram/CLI
    detail: dict[str, Any] # full order + risk verdict for the UI


@dataclass
class BoardDecision:
    decision: Decision
    reason: str = ""
    new_qty: float | None = None  # only used when decision='modify'


def request_board_approval(req: ApprovalRequest) -> BoardDecision:
    """Pause the graph until the Board responds. The runtime serializes the
    state to the checkpointer; the graph resumes when `Command(resume=...)`
    is invoked with a dict matching BoardDecision fields.
    """
    from langgraph.types import interrupt  # type: ignore

    payload = interrupt({
        "kind": "approval_request",
        "order_id": req.order_id,
        "summary": req.summary,
        "detail": req.detail,
    })

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Board approval resume payload must be a dict, got {type(payload).__name__}"
        )

    decision = payload.get("decision", "reject")
    if decision not in ("approve", "reject", "modify"):
        raise ValueError(f"Unknown decision: {decision!r}")
    return BoardDecision(
        decision=decision,
        reason=payload.get("reason", ""),
        new_qty=payload.get("new_qty"),
    )
