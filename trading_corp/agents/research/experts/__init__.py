"""Research firm experts (v3 — renamed from `analysts/` per Q1).

Each expert implements the `Expert` protocol declared in `base.py` and
registers in the `EXPERT_REGISTRY` map keyed by `(product_type,
asset_class)`. Experts have NO access to broker creds, `data_exec`, or
`Broker` instances — read-only data-source toolbox only.

Shipped roster:
  - technical (real, yfinance, Phase 1a-1) — 50/200d MAs, RSI(14), ATR
  - macro (real, MacroCalendar+VIX+earnings, Phase 1a-1)
  - fundamental (real, yfinance .info, Phase 1c) — equity-only, refuses
    on non-equity symbols
  - sentiment (real, yfinance .recommendations + .news, Phase 1c) —
    analyst-driven (NOT crowd); refuses on crypto symbols

`stub_expert_report` remains for graceful fallback when an engagement
references a role whose Expert instance isn't wired into deps (e.g.
test fixtures that intentionally only inject 1-2 fake experts).
"""
from trading_corp.agents.research.experts._stub import stub_expert_report
from trading_corp.agents.research.experts.base import Expert
from trading_corp.agents.research.experts.fundamental import FundamentalExpert
from trading_corp.agents.research.experts.macro import MacroExpert
from trading_corp.agents.research.experts.registry import (
    EXPERT_REGISTRY, experts_for,
)
from trading_corp.agents.research.experts.sentiment import SentimentExpert
from trading_corp.agents.research.experts.technical import TechnicalExpert

__all__ = [
    "Expert",
    "EXPERT_REGISTRY",
    "experts_for",
    "stub_expert_report",
    "TechnicalExpert",
    "MacroExpert",
    "FundamentalExpert",
    "SentimentExpert",
]
