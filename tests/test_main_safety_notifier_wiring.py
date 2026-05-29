"""Integration test for the safety_notifier wiring in main.py.

Commit 7b of Stage-1 Session N+1. Asserts:

- The same TelegramChannel singleton wired into `bitunix_observer.
  telegram_channel` is ALSO wired into `data_exec.safety_notifier` —
  no parallel TelegramChannel instances (CLAUDE.md Phase-C principle).
- The duck-typed contract holds: safety_notifier.push(text, *,
  audit_path, audit_context) is awaitable and returns the
  confirmed-delivery bool from the underlying push() implementation.
- Safety-side consumers on the safety branch (mode-mismatch in
  place(), flatten_division()) can fire via data_exec.safety_notifier
  with confirmed-delivery semantics. We mock the safety trigger here
  because the actual consumer code lives on the safety branch, not
  this one; the test validates the WIRING SHAPE which is what commit
  7b owns.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.data_exec import DataExecAgent


# ─── singleton wiring ───────────────────────────────────────────────────


def test_safety_notifier_and_observer_telegram_channel_are_same_object():
    """The exact lines from main.py that wire the singleton:
        bitunix_observer.telegram_channel = channel
        data_exec.safety_notifier = channel
    Must result in IS-equal references — same object, not a copy or
    a re-wrapped instance. This is the Phase-C principle: one
    TelegramChannel per process. Parallel channels would mean
    duplicate Telegram clients, double-deliveries, and divergent
    delivery audits."""
    # Simulate the channel singleton
    channel = MagicMock()
    channel.push = AsyncMock(return_value=True)

    # Simulate the two consumers in main.py
    bitunix_observer = MagicMock()
    bitunix_observer.telegram_channel = None  # initial unwired
    data_exec = DataExecAgent(logger=MagicMock())

    # main.py:805 + main.py:813 wiring (commit 7b)
    bitunix_observer.telegram_channel = channel
    data_exec.safety_notifier = channel

    assert bitunix_observer.telegram_channel is data_exec.safety_notifier, (
        "safety_notifier and telegram_channel must reference the SAME "
        "channel object, not parallel TelegramChannel instances"
    )


# ─── duck-typed push() contract ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_safety_notifier_push_contract_matches_safety_branch_consumers():
    """The safety-branch consumers (data_exec.place()'s mode-mismatch
    handler + flatten_division) call:

        ok = await self.safety_notifier.push(
            text,
            audit_path="position_mode_mismatch",
            audit_context={...},
        )

    where `ok: bool` is the confirmed-delivery signal (True = 2xx +
    ok:true, False = drop/send-failed). The slot stored on
    DataExecAgent must accept this signature without further
    wrapping/translation."""
    channel = MagicMock()
    channel.push = AsyncMock(return_value=True)
    data_exec = DataExecAgent(logger=MagicMock(), safety_notifier=channel)

    # Simulate the safety-branch's call pattern
    ok = await data_exec.safety_notifier.push(
        "BitunixPositionModeMismatch raised",
        audit_path="position_mode_mismatch",
        audit_context={"order_id": "abc", "broker": "bitunix"},
    )
    assert ok is True
    channel.push.assert_awaited_once_with(
        "BitunixPositionModeMismatch raised",
        audit_path="position_mode_mismatch",
        audit_context={"order_id": "abc", "broker": "bitunix"},
    )


@pytest.mark.asyncio
async def test_safety_notifier_push_false_returned_to_caller():
    """When push() reports a failed delivery (False), the safety
    branch's consumer writes a telegram_notification_failed audit and
    DOES NOT block the safety re-raise. The wiring contract on this
    side is that .push() returns False on failure — we don't override
    that to True or raise."""
    channel = MagicMock()
    channel.push = AsyncMock(return_value=False)
    data_exec = DataExecAgent(logger=MagicMock(), safety_notifier=channel)

    ok = await data_exec.safety_notifier.push(
        "flatten_division failed positions remain",
        audit_path="flatten_account_failed",
        audit_context={"division": "bitunix_futures"},
    )
    assert ok is False, (
        "wiring must preserve push() bool semantics — safety consumer "
        "relies on False to write telegram_notification_failed audit"
    )


# ─── unwired tolerance (legacy callers) ─────────────────────────────────


def test_data_exec_without_safety_notifier_is_legal():
    """Pre-N+1 callers (test fixtures, unwired tools) construct
    DataExecAgent without safety_notifier; the slot is None. Safety
    consumers on the safety branch fail-soft when safety_notifier is
    None (audit + re-raise still happen; only the telegram side-effect
    skips). This is the test fixtures back-compat guarantee."""
    data_exec = DataExecAgent(logger=MagicMock())
    assert data_exec.safety_notifier is None


# ─── main.py wiring assertion via source grep ──────────────────────────


def test_main_py_wiring_lines_present():
    """Belt-and-suspenders: the main.py wiring at the post-channel-init
    site must literally write `data_exec.safety_notifier = channel`. A
    rename of the channel local or removal of the assignment line breaks
    the safety wiring silently (the slot still exists but stays None).
    This test fails LOUDLY if main.py drifts."""
    from pathlib import Path
    main_py = Path(__file__).resolve().parent.parent / "trading_corp" / "main.py"
    src = main_py.read_text(encoding="utf-8")
    assert "data_exec.safety_notifier = channel" in src, (
        "main.py must wire data_exec.safety_notifier to the TelegramChannel "
        "singleton (commit 7b of Stage-1 N+1). Found no such line; safety "
        "telegrams will drop silently on the safety branch's consumers."
    )
    # Adjacency check: the safety_notifier wiring sits right after the
    # telegram_channel wiring so they share the singleton scope.
    tc_idx = src.find("bitunix_observer.telegram_channel = channel")
    sn_idx = src.find("data_exec.safety_notifier = channel")
    assert tc_idx >= 0 and sn_idx >= 0
    assert sn_idx > tc_idx, (
        "data_exec.safety_notifier wiring must follow the "
        "bitunix_observer.telegram_channel wiring so they share scope "
        "without re-instantiating channel"
    )
    # No parallel TelegramChannel construction nearby (verify the
    # singleton claim isn't silently broken by a second client).
    n_constructions = src.count("TelegramChannel(")
    assert n_constructions == 1, (
        f"expected exactly ONE TelegramChannel(...) construction in main.py; "
        f"found {n_constructions}. Phase-C principle requires a singleton."
    )
