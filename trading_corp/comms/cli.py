"""CLI Board channel — stdout for push, stdin for approvals.

Used when Telegram is not configured (no bot token / chat id) or in tests.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from trading_corp.comms.base import BoardChannel
from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision

log = logging.getLogger(__name__)


class CLIChannel(BoardChannel):
    name = "cli"

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        sys.stdout.write("[CEO] CLI channel online. Talk freely; type 'help' for commands.\n")
        sys.stdout.flush()

    async def stop(self) -> None: ...

    async def push(self, text: str) -> None:
        async with self._lock:
            sys.stdout.write(f"[CEO] {text}\n")
            sys.stdout.flush()

    async def request_approval(
        self, req: ApprovalRequest, timeout_s: float = 3600.0,
    ) -> BoardDecision:
        async with self._lock:
            sys.stdout.write("\n=== APPROVAL REQUESTED ===\n")
            sys.stdout.write(f"Order:  {req.summary}\n")
            sys.stdout.write(f"ID:     {req.order_id}\n")
            sys.stdout.write("Type:   approve | reject | modify <new_qty>\n> ")
            sys.stdout.flush()

        # Read line in a thread to avoid blocking the loop.
        try:
            line = await asyncio.wait_for(
                asyncio.to_thread(sys.stdin.readline),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning("CLI approval timed out for order %s", req.order_id)
            return BoardDecision(decision="reject", reason="cli timeout")

        line = (line or "").strip().lower()
        if line.startswith("approve"):
            return BoardDecision(decision="approve", reason="via cli")
        if line.startswith("modify"):
            try:
                new_qty = float(line.split()[1])
                return BoardDecision(decision="modify", reason="via cli", new_qty=new_qty)
            except (IndexError, ValueError):
                return BoardDecision(decision="reject", reason="invalid modify input")
        return BoardDecision(decision="reject", reason="via cli")
