"""Portfolio Manager Agent.

Aggregates positions across all registered brokers, computes total exposure
and a simple correlation snapshot, and produces a short LLM-narrated
rebalance suggestion when called for the morning brief / EOD debate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from trading_corp.agents.data_exec import DataExecAgent

log = logging.getLogger(__name__)


@dataclass
class PortfolioSnapshot:
    total_equity: float
    total_buying_power: float
    accounts: list[dict]
    gross_exposure: float
    net_exposure: float


class PortfolioAgent:
    def __init__(self, data_exec: DataExecAgent) -> None:
        self.data_exec = data_exec
        self._chat = None

    async def snapshot(self) -> PortfolioSnapshot:
        accounts: list[dict] = []
        gross = 0.0
        net = 0.0
        total_eq = 0.0
        total_bp = 0.0
        for div, broker in self.data_exec.brokers.items():
            try:
                snap = await broker.snapshot()
            except Exception as e:
                log.warning("Portfolio: snapshot failed for %s: %s", div, e)
                continue
            total_eq += snap.equity
            total_bp += snap.buying_power
            pos_summary = []
            for pos in snap.positions:
                is_option = " " in pos.symbol or "#" in pos.symbol
                try:
                    mark = pos.avg_price if is_option else await broker.quote(pos.symbol)
                except Exception:
                    mark = pos.avg_price
                value = pos.qty * mark
                gross += abs(value)
                net += value
                pos_summary.append({
                    "symbol": pos.symbol, "qty": pos.qty,
                    "avg_price": pos.avg_price, "mark": mark, "value": value,
                })
            accounts.append({
                "division": div, "broker": broker.name, "paper": broker.paper,
                "equity": snap.equity, "cash": snap.cash, "positions": pos_summary,
            })
        return PortfolioSnapshot(
            total_equity=total_eq,
            total_buying_power=total_bp,
            accounts=accounts,
            gross_exposure=gross,
            net_exposure=net,
        )

    async def rebalance_suggestion(self, snapshot: PortfolioSnapshot) -> str:
        """LLM-generated suggestion. Returns empty string if LLM unavailable."""
        try:
            if self._chat is None:
                from trading_corp.agents.llm import build_chat_model, is_llm_available
                if not is_llm_available():
                    return ""
                self._chat = build_chat_model("portfolio", max_tokens=400)
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
            sys = SystemMessage(content=(
                "You are the Portfolio Manager for a multi-strategy trading corp. "
                "Given the snapshot, produce 3-5 bullet recommendations focused on "
                "concentration, correlation, and net exposure. Be terse."
            ))
            user = HumanMessage(content=str(snapshot))
            resp = await self._chat.ainvoke([sys, user])
            return getattr(resp, "content", "") or ""
        except Exception as e:
            log.warning("Portfolio LLM suggestion failed: %s", e)
            return ""
