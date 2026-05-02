"""Judge — scores both bull and bear arguments on quality (Phase 1f).

Critical rule (CLAUDE.md §1, design §3.4 docstring):
  Judge scores QUALITY only — never produces a verdict. Synthesis is
  what synthesizes. The judge's job is to produce comparable
  evidence_quality / logical_consistency / falsifiability scores so
  the synthesis prompt can weight the bull and bear cases.

Why Opus: argument-quality scoring is Opus territory (per Q9 /
config/agents.yaml `research_judge` role). It only fires when the gate
triggers, which the design says is rare — cost stays bounded.

Returns a `DebateOutcome` carrying bull_case + bear_case + both
JudgeScores + a short synthesis string. The synthesis string is the
judge's narrated tie-up — NOT a verdict; closer to "where the
arguments converge or diverge on facts" — and can be referenced by
the product synthesizer downstream.
"""
from __future__ import annotations

import json
import logging
import re

from trading_corp.agents.research.cost import (
    cost_for_anthropic_usage, model_for_role,
)
from trading_corp.agents.research.schemas import (
    DebateOutcome, JudgeScore,
)

log = logging.getLogger(__name__)


async def run_judge(
    *,
    engagement_id: str,
    symbol: str,
    invoked_reason: str,
    bull_case: str,
    bear_case: str,
) -> tuple[DebateOutcome, float]:
    """Score the two arguments. Returns (DebateOutcome, llm_dollars).

    LLM is best-effort: when ANTHROPIC_API_KEY is absent we fall back
    to deterministic placeholder scores (0.5 across the board) so the
    audit trail is still complete and the synthesis prompt can still
    structure around a DebateOutcome.
    """
    deterministic = _det_outcome(
        engagement_id=engagement_id,
        symbol=symbol,
        invoked_reason=invoked_reason,
        bull_case=bull_case,
        bear_case=bear_case,
    )

    narrated = await _narrate_judge_if_available(
        symbol=symbol,
        invoked_reason=invoked_reason,
        bull_case=bull_case,
        bear_case=bear_case,
    )
    if narrated is None:
        return deterministic, 0.0

    parsed, cost = narrated
    bull_score = _coerce_judge_score(parsed.get("bull_score"))
    bear_score = _coerce_judge_score(parsed.get("bear_score"))
    synthesis = (parsed.get("synthesis") or "").strip()
    if bull_score is None or bear_score is None or not synthesis:
        # Partial parse — fall back to deterministic but still attribute
        # the cost we paid for the LLM call.
        return deterministic, cost

    return (
        DebateOutcome(
            engagement_id=engagement_id,
            symbol=symbol,
            invoked_reason=invoked_reason,
            bull_case=bull_case,
            bear_case=bear_case,
            judge_bull_score=bull_score,
            judge_bear_score=bear_score,
            synthesis=synthesis,
        ),
        cost,
    )


# ── Deterministic fallback ────────────────────────────────────────────


def _det_outcome(
    *,
    engagement_id: str,
    symbol: str,
    invoked_reason: str,
    bull_case: str,
    bear_case: str,
) -> DebateOutcome:
    """0.5/0.5/0.5 across both sides + a structured synthesis string.
    Caller can reference this in the audit row even when the LLM is
    unavailable so the trail is complete."""
    return DebateOutcome(
        engagement_id=engagement_id,
        symbol=symbol,
        invoked_reason=invoked_reason,
        bull_case=bull_case,
        bear_case=bear_case,
        judge_bull_score=JudgeScore(
            evidence_quality=0.5,
            logical_consistency=0.5,
            falsifiability=0.5,
            notes="(no LLM available; placeholder score)",
        ),
        judge_bear_score=JudgeScore(
            evidence_quality=0.5,
            logical_consistency=0.5,
            falsifiability=0.5,
            notes="(no LLM available; placeholder score)",
        ),
        synthesis=(
            f"Debate on {symbol} ran without judge LLM; both cases "
            f"recorded for audit but not scored. invoked_reason: "
            f"{invoked_reason}"
        ),
    )


def _coerce_judge_score(raw: object) -> JudgeScore | None:
    """Defensive: validate one half of the LLM response into a
    JudgeScore. Returns None on any validation failure so the caller
    can fall back to deterministic."""
    if not isinstance(raw, dict):
        return None
    try:
        return JudgeScore.model_validate(raw)
    except Exception as e:
        log.debug("judge: JudgeScore validation failed: %s", e)
        return None


# ── LLM narration ─────────────────────────────────────────────────────


async def _narrate_judge_if_available(
    *,
    symbol: str,
    invoked_reason: str,
    bull_case: str,
    bear_case: str,
) -> tuple[dict, float] | None:
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
    except Exception:
        return None

    prompt = (
        f"You are a debate judge on a research desk. Two debaters have "
        f"argued opposing cases for {symbol}. Score BOTH arguments on "
        f"three dimensions, each in [0.0, 1.0]:\n"
        f"  - evidence_quality: how well-grounded in cited expert data\n"
        f"  - logical_consistency: internal coherence of the argument\n"
        f"  - falsifiability: degree to which the argument makes claims "
        f"that could be tested\n\n"
        f"You do NOT produce a verdict. The synthesis layer downstream "
        f"decides what to do with the cases. Your job is comparable "
        f"quality scores + a short synthesis string capturing where the "
        f"two cases agree on facts and where they genuinely disagree.\n\n"
        f"Debate invoked because: {invoked_reason}\n\n"
        f"BULL CASE:\n{bull_case}\n\n"
        f"BEAR CASE:\n{bear_case}\n\n"
        f"Return a single JSON object with exactly these keys:\n"
        f'  {{"bull_score": {{"evidence_quality": 0.0-1.0, '
        f'"logical_consistency": 0.0-1.0, "falsifiability": 0.0-1.0, '
        f'"notes": "..."}}, "bear_score": {{... same shape ...}}, '
        f'"synthesis": "1-2 sentence neutral summary of where the cases '
        f'converge / diverge on facts"}}\n\n'
        f"No prose outside the JSON. No verdict, no recommendation."
    )

    try:
        chat = build_chat_model("research_judge", max_tokens=900)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_judge"), usage)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            log.debug("judge: JSON parse failed: %s", e)
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed, cost
    except Exception as e:
        log.debug("judge LLM call failed: %s", e)
        return None
