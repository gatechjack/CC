"""LangGraph state schema — the global TypedDict passed between nodes."""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

Mode = Literal["PAPER", "LIVE"]
Regime = Literal["uptrend", "downtrend", "chop", "unknown"]


class CorpState(TypedDict, total=False):
    # Conversation history with the Board (CEO is the user-facing agent)
    messages: Annotated[list, add_messages]

    # Operational mode (PAPER default; LIVE only with --live + confirmation)
    mode: Mode

    # Latest regime read from the Trend Agent
    regime: Regime
    regime_confidence: float

    # Snapshot of accounts (account_name -> AccountState dict)
    accounts: dict[str, dict[str, Any]]

    # Strategy halt flags (strategy_name -> StrategyState dict)
    strategies: dict[str, dict[str, Any]]

    # In-flight proposed order awaiting risk/board review
    proposed_order: dict[str, Any] | None

    # Risk Agent verdict for the in-flight order
    risk_verdict: dict[str, Any] | None

    # Board verdict (set by interrupt resume)
    board_decision: dict[str, Any] | None

    # Most-recent brief (morning or EOD)
    last_brief_md: str | None

    # Human-readable status pushed to comms layer
    status_line: str | None
