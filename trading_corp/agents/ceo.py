"""CEO Agent — the user-facing orchestrator.

The CEO doesn't trade itself. It:
  - Receives messages from the Board (you)
  - Routes work to division/shared agents
  - Produces the daily morning brief
  - Hosts the EOD debate group chat
  - Surfaces approval requests via Telegram/CLI
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


CEO_SYSTEM_PROMPT = """\
You are the CEO of an AI-Powered Trading Corporation. The Board (the user) is
your sole boss. Be professional, data-driven, and proactive. Your divisions
are: Robinhood PMCC, Fidelity Options, Crypto Futures. Your shared agents are:
Trend/Regime, Risk, Backtesting, Portfolio Manager, Data&Execution, Logger.

Critical rules you must NEVER bypass:
  1. The system defaults to PAPER mode every startup.
  2. Every live order requires Board approval until per-strategy auto-exec is
     explicitly unlocked. Risk caps are enforced deterministically — you don't
     override the Risk Agent.
  3. New strategies cannot deploy until the Backtesting Agent passes them.
  4. You speak in concise, busy-CEO language. Bullets over prose.

When the Board asks for status, summarize: regime, accounts/equity, any halts,
pending approvals, and recent fills. Always end with a one-line "Next action".
"""


@dataclass
class MorningBrief:
    body_md: str
    regime: str
    total_equity: float


class CEOAgent:
    def __init__(self) -> None:
        self._chat = None

    def _ensure_chat(self):
        if self._chat is None:
            from trading_corp.agents.llm import build_chat_model, is_llm_available
            if not is_llm_available():
                return None
            self._chat = build_chat_model("ceo", max_tokens=1500)
        return self._chat

    async def morning_brief(
        self,
        regime: str,
        portfolio_snapshot,
        pending_approvals: int,
        recent_events: list[dict],
    ) -> MorningBrief:
        # Deterministic skeleton always works; LLM enriches it when available.
        accounts_md = "\n".join(
            f"  - {a['division']} ({a['broker']}, paper={a['paper']}): "
            f"equity=${a['equity']:,.2f} cash=${a['cash']:,.2f} positions={len(a['positions'])}"
            for a in portfolio_snapshot.accounts
        ) or "  (no broker accounts registered)"
        events_md = "\n".join(
            f"  - {e['ts']} {e['actor']}/{e['kind']}" for e in recent_events[:8]
        ) or "  (no recent events)"
        body = (
            f"# Morning Brief\n\n"
            f"**Regime:** {regime}\n"
            f"**Total equity:** ${portfolio_snapshot.total_equity:,.2f} "
            f"(gross exposure ${portfolio_snapshot.gross_exposure:,.2f}, "
            f"net ${portfolio_snapshot.net_exposure:,.2f})\n"
            f"**Pending approvals:** {pending_approvals}\n\n"
            f"## Accounts\n{accounts_md}\n\n"
            f"## Recent events\n{events_md}\n\n"
            f"## Next action\n"
            f"Awaiting Board direction. Use /approve, /reject, /halt <strategy>, "
            f"or chat freely.\n"
        )
        # Optional LLM refinement
        chat = self._ensure_chat()
        if chat is not None:
            try:
                from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
                resp = await chat.ainvoke([
                    SystemMessage(content=CEO_SYSTEM_PROMPT),
                    HumanMessage(content=(
                        "Polish this morning brief. Keep all numbers exact; "
                        "tighten language; add one short forward-looking note. "
                        "Return Markdown only.\n\n" + body
                    )),
                ])
                content = getattr(resp, "content", None)
                if content:
                    body = content
            except Exception as e:
                log.warning("CEO morning_brief LLM refine failed: %s", e)
        return MorningBrief(
            body_md=body,
            regime=regime,
            total_equity=portfolio_snapshot.total_equity,
        )

    async def reply_to_board(self, user_msg: str, context_md: str) -> str:
        chat = self._ensure_chat()
        if chat is None:
            return (
                "CEO is in deterministic mode (no ANTHROPIC_API_KEY). "
                "Context:\n" + context_md + "\nYou said: " + user_msg
            )
        try:
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
            resp = await chat.ainvoke([
                SystemMessage(content=CEO_SYSTEM_PROMPT),
                HumanMessage(content=f"Context:\n{context_md}\n\nBoard said:\n{user_msg}"),
            ])
            return getattr(resp, "content", "") or "(empty)"
        except Exception as e:
            log.warning("CEO reply LLM call failed: %s", e)
            return f"(CEO LLM call failed: {e})"
