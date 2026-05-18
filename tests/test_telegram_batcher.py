"""Tests for the TelegramBatcher.

Window collapse, bypass-tag immediate-fire, and "bypass does not reset
window" behavior. Uses a stub channel that records every push and a
zero/short batch window so tests don't actually sleep for real time.
"""
from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import AsyncMock

import pytest

from trading_corp.comms.telegram_batcher import TelegramBatcher


class _RecordingChannel:
    def __init__(self) -> None:
        self.sent: List[str] = []

    async def push(self, text: str) -> None:
        self.sent.append(text)


# ---------------------------------------------------------------------------
# Batch behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_message_sends_verbatim_after_window():
    """One message in the window → flush sends the original text, not the
    deeplink template."""
    ch = _RecordingChannel()
    b = TelegramBatcher(ch, batch_window_sec=0.05)
    await b.push("🟧 IC open proposed on SPY")
    await asyncio.sleep(0.2)
    assert ch.sent == ["🟧 IC open proposed on SPY"]


@pytest.mark.asyncio
async def test_five_proposals_in_window_collapse_to_one_notification():
    """Five push() calls within a single window → one batched flush."""
    ch = _RecordingChannel()
    b = TelegramBatcher(ch, batch_window_sec=0.2)
    for i in range(5):
        await b.push(f"🟧 proposal #{i}")
    # All 5 should still be pending — not flushed yet.
    assert b.pending_count == 5
    assert ch.sent == []
    # Wait past the window.
    await asyncio.sleep(0.35)
    assert len(ch.sent) == 1
    assert "5 pending approvals" in ch.sent[0]
    assert "https://" in ch.sent[0]      # deeplink present
    assert b.pending_count == 0


# ---------------------------------------------------------------------------
# Bypass tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_tag_fires_immediately():
    """A push tagged with a bypass tag sends immediately, no window wait."""
    ch = _RecordingChannel()
    b = TelegramBatcher(
        ch, batch_window_sec=10.0,            # long window
        bypass_tags=["catastrophic_stop", "circuit_breaker_auto_repause"],
    )
    await b.push("🚨 catastrophic stop fired",
                 tags=["catastrophic_stop"])
    # Immediate — no sleep.
    assert ch.sent == ["🚨 catastrophic stop fired"]
    assert b.pending_count == 0


@pytest.mark.asyncio
async def test_bypass_does_not_reset_batch_window():
    """A bypass mid-window doesn't restart the timer; queued non-bypass
    messages still flush at the original deadline."""
    ch = _RecordingChannel()
    b = TelegramBatcher(
        ch, batch_window_sec=0.2,
        bypass_tags=["catastrophic_stop"],
    )
    # Queue two normal messages — timer starts.
    await b.push("normal 1")
    await b.push("normal 2")
    # Fire a bypass mid-window.
    await asyncio.sleep(0.05)
    await b.push("🚨 cat stop", tags=["catastrophic_stop"])
    # Bypass already sent.
    assert ch.sent == ["🚨 cat stop"]
    assert b.pending_count == 2          # queue untouched
    # Wait for the rest of the original window.
    await asyncio.sleep(0.25)
    # Queue flushed → second send is the batched template.
    assert len(ch.sent) == 2
    assert "2 pending approvals" in ch.sent[1]


@pytest.mark.asyncio
async def test_non_bypass_tag_still_batches():
    """Tags that aren't in the bypass set don't trigger immediate send."""
    ch = _RecordingChannel()
    b = TelegramBatcher(
        ch, batch_window_sec=0.1,
        bypass_tags=["catastrophic_stop"],
    )
    await b.push("normal 1", tags=["open"])
    await b.push("normal 2", tags=["close_tested_side"])
    assert ch.sent == []                  # neither bypassed
    await asyncio.sleep(0.2)
    assert len(ch.sent) == 1
    assert "2 pending approvals" in ch.sent[0]


# ---------------------------------------------------------------------------
# Window separation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_after_flush_open_a_new_window():
    """After the first window flushes, a subsequent push opens a fresh
    window — doesn't merge into a single window."""
    ch = _RecordingChannel()
    b = TelegramBatcher(ch, batch_window_sec=0.1)
    await b.push("first window 1")
    await b.push("first window 2")
    await asyncio.sleep(0.2)
    assert len(ch.sent) == 1
    await b.push("second window 1")
    await asyncio.sleep(0.2)
    assert len(ch.sent) == 2
    # Second send is the lone message verbatim (count=1 path).
    assert ch.sent[1] == "second window 1"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_underlying_push_failure_is_swallowed():
    """If the underlying channel.push raises, the batcher logs and
    continues — it does not propagate the exception to callers."""
    ch = AsyncMock()
    ch.push.side_effect = RuntimeError("telegram is down")
    b = TelegramBatcher(ch, batch_window_sec=0.05)
    await b.push("will fail")
    await asyncio.sleep(0.15)
    # No raise; push was attempted.
    ch.push.assert_called()


@pytest.mark.asyncio
async def test_close_drains_pending():
    """Closing the batcher drains pending messages."""
    ch = _RecordingChannel()
    b = TelegramBatcher(ch, batch_window_sec=10.0)
    await b.push("pending 1")
    await b.push("pending 2")
    assert ch.sent == []
    await b.close()
    # On close, the pending queue should have been drained.
    assert len(ch.sent) == 1
    assert "2 pending approvals" in ch.sent[0]


@pytest.mark.asyncio
async def test_close_then_push_is_noop():
    ch = _RecordingChannel()
    b = TelegramBatcher(ch, batch_window_sec=0.05)
    await b.close()
    await b.push("late message")
    await asyncio.sleep(0.15)
    assert ch.sent == []
