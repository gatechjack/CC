"""Bull debater — generates the strongest bullish argument from the
expert-report panel for one symbol. Phase 1f."""
from __future__ import annotations

from typing import Iterable

from trading_corp.agents.research.experts.debate._base import run_debater
from trading_corp.agents.research.schemas import ExpertReport


async def run_bull(
    *,
    symbol: str,
    invoked_reason: str,
    reports: Iterable[ExpertReport],
) -> tuple[str, float]:
    """Return (bull_case_text, llm_dollars)."""
    return await run_debater(
        "bull",
        symbol=symbol,
        invoked_reason=invoked_reason,
        reports=reports,
    )
