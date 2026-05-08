"""Manual research-firm replay (BACKLOG.md 2026-05-02).

Pins:
- synthesize_order_from_payload reconstructs a ProposedOrder shape from
  a TV webhook payload, including side inference from signal name.
- Bear-leaning signal names ('bear', 'top', 'red_diamond', etc.) map to
  side='sell'; everything else defaults to 'buy'.
- replay_signal_research routes through the existing
  consult_research_for_trade_confirmation and writes a
  research_replay_completed audit row tagged with the source audit id.
- Failure modes (bad payload, consult exception) write a
  research_replay_failed audit row and return a stub ConsultResult —
  never raises.
"""
from __future__ import annotations

import json

import pytest

from trading_corp.agents.research.signal_replay import (
    _infer_side,
    replay_signal_research,
    synthesize_order_from_payload,
)


# ── side inference ──────────────────────────────────────────────────


@pytest.mark.parametrize("signal,expected", [
    ("pink_box_bear", "sell"),
    ("cvd_bear_flip", "sell"),
    ("money_bag_top", "sell"),
    ("spoon_bear", "sell"),
    ("mc_a_red_diamond", "sell"),
    ("mc_b_sell_circle", "sell"),
    ("pink_box_bull", "buy"),
    ("cvd_bull_flip", "buy"),
    ("spoon_bull", "buy"),
    ("money_bag", "buy"),         # bull tier
    ("unknown_signal", "buy"),    # default
    ("", "buy"),
])
def test_infer_side(signal: str, expected: str):
    assert _infer_side(signal) == expected


# ── synthesize_order_from_payload ──────────────────────────────────


def test_synthesize_pulls_signal_symbol_price_into_extra():
    payload = {
        "strategy": "lord_otter", "division": "coinbase_spot",
        "signal": "money_bag_top", "ticker": "BTCUSD",
        "symbol": "BTC/USD", "price": 78411.5,
        "time": "2026-05-02T14:51:00Z", "interval": "3",
    }
    order = synthesize_order_from_payload(payload, audit_event_id=42)

    assert order.strategy == "lord_otter"
    assert order.symbol == "BTC/USD"
    assert order.side == "sell"           # money_bag_top → bear → sell
    assert order.qty == 0.01              # placeholder
    assert order.extra["synthetic"] is True
    assert order.extra["synthetic_audit_event_id"] == 42
    assert order.extra["source_signal"] == "money_bag_top"
    assert order.extra["entry_reference_price"] == 78411.5
    assert order.extra["alert_time"] == "2026-05-02T14:51:00Z"
    assert order.extra["alert_interval"] == "3"
    assert order.extra["tier"] == "replay_synth"


def test_synthesize_handles_missing_optional_fields():
    """A minimal payload (just signal + symbol) still synthesizes."""
    order = synthesize_order_from_payload(
        {"signal": "spoon_bull", "symbol": "BTC/USD"},
    )
    assert order.symbol == "BTC/USD"
    assert order.side == "buy"
    assert "entry_reference_price" not in order.extra
    assert order.extra["source_signal"] == "spoon_bull"


def test_synthesize_falls_back_to_ticker_when_symbol_missing():
    """Some webhook payloads use 'ticker' (TV's native field) without
    'symbol'. The synthesizer should still produce a usable order."""
    order = synthesize_order_from_payload(
        {"signal": "pink_box_bear", "ticker": "BTCUSD"},
    )
    assert order.symbol == "BTCUSD"
    assert order.side == "sell"


# ── replay_signal_research end-to-end ──────────────────────────────


class _FakeLoggerAgent:
    def __init__(self):
        self.events: list[dict] = []

    def log_event(self, *, actor, kind, payload):
        self.events.append({"actor": actor, "kind": kind, "payload": payload})


@pytest.mark.asyncio
async def test_replay_signal_writes_completed_audit(monkeypatch):
    """Happy path: monkeypatch consult to return a confirm verdict;
    verify the completed audit row is written and the result returned."""
    from trading_corp.agents.research import signal_replay
    from trading_corp.agents.research.trade_confirmation_consult import ConsultResult

    async def fake_consult(*, order, payload, research_firm, logger_agent,
                           division_slug, asset_class, account_equity=None,
                           timeout_s=None):
        return ConsultResult(
            decision="proceed", order=order,
            verdict_kind="confirm", confirmation=None,
            rationale="research firm sees no concerns with the signal setup",
        )
    monkeypatch.setattr(signal_replay, "consult_research_for_trade_confirmation", fake_consult)

    audit_row = {
        "id": 1234,
        "ts": "2026-05-02T14:54:00+00:00",
        "actor": "lord_otter",
        "kind": "alert_ignored",
        "payload_json": json.dumps({
            "strategy": "lord_otter", "division": "coinbase_spot",
            "signal": "money_bag_top", "symbol": "BTC/USD",
            "price": 78411.5,
        }),
    }
    logger = _FakeLoggerAgent()

    result = await replay_signal_research(
        audit_row, research_firm=object(), logger_agent=logger,
    )

    assert result.verdict_kind == "confirm"
    assert result.decision == "proceed"
    assert any(e["kind"] == "research_replay_completed" for e in logger.events)
    completed = next(e for e in logger.events if e["kind"] == "research_replay_completed")
    assert completed["payload"]["source_audit_event_id"] == 1234
    assert completed["payload"]["verdict_kind"] == "confirm"
    assert completed["payload"]["signal"] == "money_bag_top"


@pytest.mark.asyncio
async def test_replay_signal_handles_bad_payload_gracefully():
    """Unparseable payload_json → research_replay_failed audit row, stub
    error ConsultResult, no exception raised."""
    audit_row = {
        "id": 999,
        "kind": "alert_ignored",
        "payload_json": "this is not valid json {",
    }
    logger = _FakeLoggerAgent()

    result = await replay_signal_research(
        audit_row, research_firm=object(), logger_agent=logger,
    )

    assert result.verdict_kind == "error"
    assert result.decision == "skip"
    assert any(e["kind"] == "research_replay_failed" for e in logger.events)


@pytest.mark.asyncio
async def test_replay_signal_handles_consult_exception(monkeypatch):
    """If the consult itself raises, we catch it, write the failure
    audit row, and return a stub error ConsultResult."""
    from trading_corp.agents.research import signal_replay

    async def boom(**kw):
        raise RuntimeError("Anthropic API is down")
    monkeypatch.setattr(signal_replay, "consult_research_for_trade_confirmation", boom)

    audit_row = {
        "id": 5,
        "kind": "alert_ignored",
        "payload_json": json.dumps({
            "signal": "money_bag_top", "symbol": "BTC/USD", "price": 78000,
        }),
    }
    logger = _FakeLoggerAgent()

    result = await replay_signal_research(
        audit_row, research_firm=object(), logger_agent=logger,
    )

    assert result.verdict_kind == "error"
    assert "Anthropic API is down" in result.rationale
    assert any(e["kind"] == "research_replay_failed" for e in logger.events)


@pytest.mark.asyncio
async def test_replay_signal_no_research_firm_returns_no_research(monkeypatch):
    """When research_firm=None (test envs / partial wiring), the consult
    helper returns no_research without writing engagement rows. Replay
    surface should still write its own completed audit so the dashboard
    knows we tried."""
    from trading_corp.agents.research import signal_replay
    from trading_corp.agents.research.trade_confirmation_consult import ConsultResult

    async def no_research_consult(**kw):
        return ConsultResult(
            decision="proceed", order=kw["order"],
            verdict_kind="no_research", confirmation=None,
            rationale="research_firm not wired; proceeding without consult",
        )
    monkeypatch.setattr(signal_replay, "consult_research_for_trade_confirmation", no_research_consult)

    audit_row = {
        "id": 7,
        "kind": "alert_ignored",
        "payload_json": json.dumps({"signal": "spoon_bull", "symbol": "BTC/USD"}),
    }
    logger = _FakeLoggerAgent()

    result = await replay_signal_research(
        audit_row, research_firm=None, logger_agent=logger,
    )

    assert result.verdict_kind == "no_research"
    assert any(e["kind"] == "research_replay_completed" for e in logger.events)
