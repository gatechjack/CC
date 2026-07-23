"""Execution-engine Telegram alert (comms/exec_alert.py): classifier, dedupe
window, and failure-isolation. No real orders, no real sends."""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.comms import exec_alert as ea
from trading_corp.comms.exec_alert import (
    ExecOutcome,
    _should_send,
    classify,
    emit_exec_alert,
    first_line,
)


def _o(tier, symbol="TSLA", strategy="robinhood_pmcc", reason="test"):
    return ExecOutcome(tier=tier, symbol=symbol, strategy=strategy, reason=reason)


# ── classifier: each tier → correct tier + first line ────────────────────────

def test_classify_all_tiers_first_line():
    cases = {
        "FILLED":    ("\U0001F7E2", "FILLED"),
        "ABORTED":   ("\U0001F7E1", "ABORTED"),
        "NO_FILL":   ("\U0001F7E0", "NO FILL"),
        "EXEC_FAIL": ("\U0001F534", "EXEC FAIL"),
        "NAKED_LEG": ("\U0001F6A8", "NAKED LEG"),
    }
    for tier, (glyph, kw) in cases.items():
        tier_out, fl = classify(_o(tier, symbol="TSLA",
                                   strategy="robinhood_pmcc", reason="r"))
        assert tier_out == tier
        assert fl == f"{glyph} {kw} — TSLA robinhood_pmcc — r"


def test_classify_unknown_tier_fails_loud_to_exec_fail():
    tier, fl = classify(_o("WHATEVER"))
    assert tier == "EXEC_FAIL"
    assert fl.startswith("\U0001F534 EXEC FAIL")


def test_position_changed_defaults_by_tier():
    assert _o("FILLED").changed is True
    assert _o("NAKED_LEG").changed is True
    assert _o("ABORTED").changed is False
    assert _o("NO_FILL").changed is False
    assert ExecOutcome("ABORTED", "X", "s", "r", position_changed=True).changed is True


# ── dedupe window ────────────────────────────────────────────────────────────

def test_dedupe_collapses_identical_aborted_within_window():
    ea.reset_dedupe()
    o = _o("ABORTED", symbol="OPEN", reason="no_liquid_weekly")
    assert _should_send(o, now=1000.0) is True            # first → send
    assert _should_send(o, now=1000.0 + 60) is False      # within 15m → collapsed
    assert _should_send(o, now=1000.0 + 60) is False      # still collapsed
    assert _should_send(o, now=1000.0 + 901) is True      # past window → send again


def test_dedupe_bypassed_for_filled_execfail_nakedleg():
    ea.reset_dedupe()
    for tier in ("FILLED", "EXEC_FAIL", "NAKED_LEG"):
        o = _o(tier, reason="x")
        assert _should_send(o, now=5.0) is True
        assert _should_send(o, now=5.0) is True           # always send


def test_dedupe_distinct_reason_not_collapsed():
    ea.reset_dedupe()
    a = _o("ABORTED", symbol="OPEN", reason="no_liquid (considered=19)")
    b = _o("ABORTED", symbol="OPEN", reason="no_liquid (considered=12)")
    assert _should_send(a, now=1.0) is True
    assert _should_send(b, now=1.0) is True               # different reason → sends


# ── emit end-to-end (sync path) + dedupe via emit ────────────────────────────

def test_emit_sends_and_dedupes():
    sent = []

    async def rec(text, chat_id=None):
        sent.append(text)
        return True

    ea.set_exec_alert_sender(rec)
    ea.reset_dedupe()
    try:
        emit_exec_alert(_o("ABORTED", symbol="OPEN", reason="no_liquid"))
        emit_exec_alert(_o("ABORTED", symbol="OPEN", reason="no_liquid"))   # deduped
        ea.flush_for_test()
        assert len(sent) == 1
        emit_exec_alert(_o("FILLED", symbol="OPEN", reason="filled 1.29"))
        emit_exec_alert(_o("FILLED", symbol="OPEN", reason="filled 1.29"))  # bypass dedupe
        ea.flush_for_test()
        assert len(sent) == 3
        # first line is the phone preview
        assert sent[0].splitlines()[0].startswith("\U0001F7E1 ABORTED — OPEN")
    finally:
        ea.set_exec_alert_sender(None)


def test_per_tier_toggle_off_suppresses():
    sent = []

    async def rec(text, chat_id=None):
        sent.append(text)
        return True

    ea.set_exec_alert_sender(rec)
    ea.reset_dedupe()
    ea.configure(tiers={"ABORTED": False})
    try:
        emit_exec_alert(_o("ABORTED", reason="x"))
        ea.flush_for_test()
        assert sent == []                                  # toggled off
        emit_exec_alert(_o("FILLED", reason="y"))
        ea.flush_for_test()
        assert len(sent) == 1                              # other tiers unaffected
    finally:
        ea.configure(tiers={"ABORTED": True})
        ea.set_exec_alert_sender(None)


# ── failure isolation: a send that RAISES must not break the execution path ──

def test_send_failure_is_isolated():
    attempted = []

    async def boom(text, chat_id=None):
        attempted.append(text)
        raise RuntimeError("telegram unreachable")

    ea.set_exec_alert_sender(boom)
    ea.reset_dedupe()
    try:
        # Must NOT raise — if this line raised, a trade path would break.
        emit_exec_alert(_o("EXEC_FAIL", reason="broker reject"))
        ea.flush_for_test()
        assert attempted, "sender should have been attempted"
    finally:
        ea.set_exec_alert_sender(None)


def test_emit_with_no_sender_is_safe():
    ea.set_exec_alert_sender(None)
    ea.reset_dedupe()
    emit_exec_alert(_o("FILLED", reason="no sender wired"))   # no raise


# ── fix #1: user-initiated dispatches bypass dedupe ──────────────────────────

def test_user_initiated_nofill_both_send_autonomous_deduped():
    ea.reset_dedupe()
    o = _o("NO_FILL", symbol="TSLA", reason="did not fill (limit not marketable)")
    # USER-initiated: two identical NO FILLs in-window → BOTH send (guaranteed ping).
    assert _should_send(o, now=1.0, origin="user") is True
    assert _should_send(o, now=1.0 + 60, origin="user") is True
    # AUTONOMOUS: identical repeats → second deduped.
    ea.reset_dedupe()
    assert _should_send(o, now=1.0, origin="autonomous") is True
    assert _should_send(o, now=1.0 + 60, origin="autonomous") is False


def test_emit_resolves_user_origin_from_outcome():
    sent = []

    async def rec(text, chat_id=None):
        sent.append(text)
        return True

    ea.set_exec_alert_sender(rec)
    ea.reset_dedupe()
    try:
        emit_exec_alert(ExecOutcome("NO_FILL", "TSLA", "robinhood_pmcc",
                                    "did not fill", origin="user"))
        emit_exec_alert(ExecOutcome("NO_FILL", "TSLA", "robinhood_pmcc",
                                    "did not fill", origin="user"))
        ea.flush_for_test()
        assert len(sent) == 2                              # user → both send
    finally:
        ea.set_exec_alert_sender(None)


def test_user_dispatch_contextmanager_bypasses_dedupe():
    sent = []

    async def rec(text, chat_id=None):
        sent.append(text)
        return True

    ea.set_exec_alert_sender(rec)
    ea.reset_dedupe()
    try:
        with ea.user_dispatch():                           # marks the context "user"
            emit_exec_alert(_o("NO_FILL", symbol="OPEN", reason="rested"))
            emit_exec_alert(_o("NO_FILL", symbol="OPEN", reason="rested"))
        ea.flush_for_test()
        assert len(sent) == 2                              # both send inside user scope
    finally:
        ea.set_exec_alert_sender(None)


# ── fix #3: fire-and-forget hardening ────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_reference_held_until_done():
    sent = []

    async def rec(text, chat_id=None):
        sent.append(text)
        return True

    ea.set_exec_alert_sender(rec)
    ea.reset_dedupe()
    ea._pending_tasks.clear()
    try:
        emit_exec_alert(_o("FILLED", reason="held"))       # running loop → create_task
        assert len(ea._pending_tasks) == 1                 # reference held (not GC-able)
        await asyncio.sleep(0.05)                          # let it complete
        assert len(ea._pending_tasks) == 0                 # discarded on done
        assert len(sent) == 1                              # and it actually sent
    finally:
        ea.set_exec_alert_sender(None)


def test_emit_no_running_loop_sends_via_thread():
    sent = []

    async def rec(text, chat_id=None):
        sent.append(text)
        return True

    ea.set_exec_alert_sender(rec)
    ea.reset_dedupe()
    try:
        # No running loop here → the thread fallback must still attempt the send.
        emit_exec_alert(_o("EXEC_FAIL", reason="no-loop path"))
        ea.flush_for_test(timeout=3)
        assert len(sent) == 1
    finally:
        ea.set_exec_alert_sender(None)
