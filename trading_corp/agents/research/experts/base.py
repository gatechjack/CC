"""Expert protocol — the unit-of-skill interface for research firm experts.

See planning/research_firm_design.md §5.1.

Experts have NO access to broker creds, `data_exec`, `Broker` instances,
or pre-built `WebDeps`. The `context` dict varies by product type but
is always JSON-safe; absent fields are graceful-degrade.

The `analyze` callable returns `(ExpertReport, llm_dollars_spent)`. A
report with `data_sufficiency=False` MUST set `refusal_reason` (Pydantic
enforces).
"""
from __future__ import annotations

from typing import Callable, Protocol

from trading_corp.agents.research.schemas import ExpertReport


class Expert(Protocol):
    role: str

    async def analyze(
        self,
        *,
        engagement_id: str,
        symbol: str,                   # may be "" for whole-engagement-level work
        context: dict,                 # asset_class, mandate, time_horizon, etc.
        on_data_fetch: Callable[..., None] | None = None,
    ) -> tuple[ExpertReport, float]:   # (report, llm_dollars)
        ...
