"""Bull / bear / judge debate round (Phase 1f, design §2.1 DEBATE_ROUND).

Fires only when `debate_gate.evaluate_debate_gate(...)` says fire.
Bull and bear are LLM-narrated argument generators (Sonnet); judge is
Opus and scores BOTH arguments deterministically against three quality
axes — never produces a verdict (CLAUDE.md §1: deterministic-then-narrate;
synthesis synthesizes).
"""
from trading_corp.agents.research.experts.debate.bull import run_bull
from trading_corp.agents.research.experts.debate.bear import run_bear
from trading_corp.agents.research.experts.debate.judge import run_judge

__all__ = ["run_bull", "run_bear", "run_judge"]
