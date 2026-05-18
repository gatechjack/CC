"""Telegram notification batcher.

Coalesces notifications inside a configurable time window so the Board
doesn't receive a flood of pings when a strategy emits multiple
proposals back-to-back. The web-app HITL queue always reflects truth;
batching only affects Telegram ping density.

High-severity audit events — tagged at the call site via the `tags`
kwarg — bypass the batcher and ping immediately. Per the iron-condor
strategy config, the bypass tags are:

  - circuit_breaker_auto_repause   (≥ 15% drawdown auto-repause)
  - catastrophic_stop              (portfolio -10% session loss close-all)
  - startup_catchup                (overdue exits fired at startup)
  - late_dte_force_close           (DTE < 7 gamma-risk forced close)

Bypass sends do NOT reset the batch window — pending non-bypass
notifications still flush when the timer naturally expires. They also
do NOT touch the queue, so they don't get rolled into the next batch.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Protocol

log = logging.getLogger(__name__)


class _PushableChannel(Protocol):
    async def push(self, text: str) -> None: ...


class TelegramBatcher:
    """Window-based batcher in front of a `BoardChannel`-like push target.

    Single instance per strategy. Schedule a flush asyncio task when the
    first non-bypass message arrives; subsequent messages within the
    window just append to the queue. On flush:

      - 0 pending: no-op.
      - 1 pending: send the original message verbatim (no template).
      - 2+ pending: collapse to `deeplink_template.format(count=N)`.

    Construction is sync-safe; `push` is async because the underlying
    channel's push is async.
    """

    DEFAULT_DEEPLINK = (
        "📋 {count} pending approvals — open web app at "
        "https://trading.jacksumner.com/approvals"
    )

    def __init__(
        self,
        channel: _PushableChannel,
        *,
        batch_window_sec: float = 60.0,
        bypass_tags: Iterable[str] = (),
        deeplink_template: str = DEFAULT_DEEPLINK,
    ) -> None:
        self._channel = channel
        self._window = float(batch_window_sec)
        self._bypass_tags = frozenset(bypass_tags)
        self._deeplink_template = deeplink_template
        self._pending: list[str] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def bypass_tags(self) -> frozenset[str]:
        return self._bypass_tags

    async def push(
        self, message: str, *, tags: Iterable[str] = (),
    ) -> None:
        """Queue `message` for batched send unless any of `tags` is a
        bypass tag, in which case send immediately and skip the queue.
        """
        if self._closed:
            log.debug("TelegramBatcher: push after close — dropped: %s", message)
            return

        tag_set = set(tags)
        if tag_set & self._bypass_tags:
            # Bypass path: send now, do NOT touch the queue or window.
            try:
                await self._channel.push(message)
            except Exception as e:
                log.warning("TelegramBatcher bypass push failed: %s", e)
            return

        async with self._lock:
            self._pending.append(message)
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(self._window)
            await self._flush_now()
        except asyncio.CancelledError:
            # Drain remaining on cancel — keeps test/teardown clean.
            await self._flush_now()
            raise

    async def _flush_now(self) -> None:
        async with self._lock:
            if not self._pending:
                return
            messages = list(self._pending)
            self._pending.clear()
            self._flush_task = None
        if len(messages) == 1:
            payload = messages[0]
        else:
            payload = self._deeplink_template.format(count=len(messages))
        try:
            await self._channel.push(payload)
        except Exception as e:
            log.warning("TelegramBatcher batched-flush failed: %s", e)

    async def close(self) -> None:
        """Cancel any pending flush task. Drains pending messages first
        via the cancel handler in `_delayed_flush`."""
        self._closed = True
        task = self._flush_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        # Defensive final drain (shouldn't fire — cancel handler already did).
        await self._flush_now()
