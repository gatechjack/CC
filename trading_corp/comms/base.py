"""Common abstractions for Board communication channels."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision


class BoardChannel(ABC):
    """A channel that can push messages to the Board and request approvals."""

    name: str = "base"

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def push(self, text: str) -> None: ...

    @abstractmethod
    async def request_approval(
        self, req: ApprovalRequest, timeout_s: float = 3600.0,
    ) -> BoardDecision: ...

    async def wait_for_shutdown_signal(self) -> None:
        """Block until the channel decides the process should shut down.

        Default implementation: never returns. Subclasses (TelegramChannel)
        override this to return when an unrecoverable error like a polling
        Conflict is detected. The main idle loop races a sleep against this
        coroutine to exit promptly without busy-spinning the logs.
        """
        await asyncio.Event().wait()  # never set
