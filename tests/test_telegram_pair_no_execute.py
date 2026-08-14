"""FORK 1 (2026-07-30): the Telegram /pair Approve/execute keyboard is retired —
Telegram is notification-only (CLAUDE.md). /pair renders NO `approve:` button, and
the approve callback handler has NO order path (it redirects to the dashboard)."""
from __future__ import annotations

import types

import pytest

from trading_corp.comms.telegram_commands import TelegramCommands


def _analysis(action="roll_short"):
    return types.SimpleNamespace(
        action=action, urgency="elevated", confidence=0.8,
        summary="short breached, roll up-and-out", rationale="ITM by 3%",
        warnings=[], target_delta=0.30, target_dte=7,
    )


class _StubBroker:
    paper = True


class _StubPMCC:
    def __init__(self, analysis):
        self._a = analysis
        self.analyze_calls = 0
        self.propose_calls = 0

    async def analyze_symbol(self, broker, sym, regime="unknown"):
        self.analyze_calls += 1
        return self._a

    async def build_trade_recommendation(self, broker, sym, analysis, *,
                                         preview=False, prebuilt_orders=None):
        return None   # the informational message still renders with rec=None

    async def propose_orders_for_pair(self, broker, sym, analysis, *, preview=False):
        self.propose_calls += 1
        raise AssertionError("Telegram must never build orders for dispatch")


def _deps(pmcc):
    async def _boom_place(*a, **k):
        raise AssertionError("Telegram must never place an order")
    data_exec = types.SimpleNamespace(
        brokers={"robinhood_pmcc": _StubBroker()}, place=_boom_place)
    return types.SimpleNamespace(
        data_exec=data_exec, pmcc_agent=pmcc,
        trend_agent=types.SimpleNamespace(
            read=lambda: types.SimpleNamespace(regime="neutral")),
        logger_agent=types.SimpleNamespace(
            log_event=lambda **k: None, log_proposed_order=lambda o: None),
        risk_agent=None, db_url=None,
    )


def _callbacks(keyboard):
    if keyboard is None:
        return []
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_pair_render_has_no_approve_button():
    pmcc = _StubPMCC(_analysis("roll_short"))
    cmds = TelegramCommands(_deps(pmcc))
    text, keyboard = await cmds.pair("AAPL")
    cbs = _callbacks(keyboard)
    assert not any(c.startswith("approve:") for c in cbs), cbs   # execute button gone
    assert any(c.startswith("defer:") for c in cbs)              # non-dispatch control stays
    assert pmcc.propose_calls == 0                               # no order build on render


@pytest.mark.asyncio
async def test_execute_pair_handler_is_neutralized():
    pmcc = _StubPMCC(_analysis("roll_short"))
    cmds = TelegramCommands(_deps(pmcc))
    text, keyboard = await cmds.execute_pair("AAPL")
    assert "dashboard" in text.lower()
    assert "notification-only" in text.lower()
    assert pmcc.analyze_calls == 0 and pmcc.propose_calls == 0   # no order path at all


@pytest.mark.asyncio
async def test_approve_callback_routes_to_redirect_not_dispatch():
    pmcc = _StubPMCC(_analysis("roll_short"))
    cmds = TelegramCommands(_deps(pmcc))
    text, _keyboard = await cmds.handle_callback("approve:AAPL")
    assert "dashboard" in text.lower()                           # stale button degrades safely
    assert pmcc.propose_calls == 0
