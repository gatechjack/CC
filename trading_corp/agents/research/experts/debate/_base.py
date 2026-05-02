"""Shared internals for bull / bear debaters (Phase 1f).

Bull and bear are symmetric: same expert-report context, same prompt
shape, only the stance differs. Factor the LLM call out so the public
`run_bull` / `run_bear` modules stay one-screen.
"""
from __future__ import annotations

import logging
from typing import Iterable, Literal

from trading_corp.agents.research.cost import (
    cost_for_anthropic_usage, model_for_role,
)
from trading_corp.agents.research.schemas import ExpertReport

log = logging.getLogger(__name__)

Stance = Literal["bull", "bear"]


async def run_debater(
    stance: Stance,
    *,
    symbol: str,
    invoked_reason: str,
    reports: Iterable[ExpertReport],
) -> tuple[str, float]:
    """Generate a one-paragraph argument for the given stance.

    Returns (argument_text, llm_dollars). When the LLM is unavailable,
    returns a deterministic placeholder string built from the structured
    expert summaries — the debate still produces SOMETHING for the judge
    to score so the audit trail is complete.

    The prompt is intentionally adversarial: each debater sees the same
    inputs but is constrained to argue ONE side. The judge scores
    quality, not which side is correct.
    """
    valid = [r for r in reports if r.data_sufficiency]
    refused = [r for r in reports if not r.data_sufficiency]

    deterministic = _det_argument(stance, symbol, valid, refused, invoked_reason)

    narrated = await _narrate_argument_if_available(
        stance, symbol, invoked_reason, valid, refused,
    )
    if narrated is None:
        return deterministic, 0.0
    text, cost = narrated
    return (text or deterministic), cost


# ── Deterministic fallback ────────────────────────────────────────────


def _det_argument(
    stance: Stance,
    symbol: str,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
    invoked_reason: str,
) -> str:
    """Build a structured argument from expert summaries when no LLM is
    available. Conservative — only cites the experts whose lean aligns
    with the stance, and surfaces refusals as observability gaps."""
    target_lean = "bullish" if stance == "bull" else "bearish"
    aligned = [r for r in valid if r.directional_lean == target_lean]
    counter = [
        r for r in valid
        if r.directional_lean and r.directional_lean != target_lean
    ]

    bits: list[str] = [f"[stance={stance}] {symbol}: debate invoked because "
                       f"{invoked_reason}."]
    if aligned:
        bits.append("Supporting evidence:")
        for r in aligned:
            bits.append(
                f"- {r.role} (conf={r.confidence_score:.2f}): {r.summary[:140]}"
            )
    elif valid:
        bits.append(
            f"No experts directly support a {stance} read; arguing the "
            f"{stance} case would require interpreting neutral or "
            f"opposing-lean signals favorably."
        )
    if counter:
        bits.append("Counter-leaning experts (for the judge to weigh):")
        for r in counter:
            bits.append(
                f"- {r.role} (conf={r.confidence_score:.2f}, "
                f"lean={r.directional_lean}): {r.summary[:140]}"
            )
    if refused:
        bits.append("Observability gaps:")
        for r in refused:
            bits.append(
                f"- {r.role}: {r.refusal_reason or 'no data'}"
            )
    return "\n".join(bits)


# ── LLM narration ─────────────────────────────────────────────────────


async def _narrate_argument_if_available(
    stance: Stance,
    symbol: str,
    invoked_reason: str,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
) -> tuple[str, float] | None:
    """Best-effort LLM narration. Returns (text, cost) or None on any
    failure. Tests bypass via the no-API-key environment."""
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
    except Exception:
        return None

    rep_lines: list[str] = []
    for r in valid:
        rep_lines.append(
            f"  - [{r.role} | lean={r.directional_lean} | "
            f"conf={r.confidence_score:.2f}] {r.summary}"
        )
    for r in refused:
        rep_lines.append(f"  - [{r.role} REFUSED] {r.refusal_reason}")

    target_lean = "bullish" if stance == "bull" else "bearish"
    instruction = (
        f"You are arguing the {stance} case for {symbol}. Construct the "
        f"strongest 4-6 sentence {stance} argument you can from the "
        f"expert reports below. Cite specific experts. Do NOT hedge; "
        f"assume the role of an advocate for the {target_lean} thesis. "
        f"The judge will score quality, not whether you are correct."
    )

    prompt = (
        f"You are a debate participant on a research desk. "
        f"Debate invoked because: {invoked_reason}\n\n"
        f"{instruction}\n\n"
        f"Expert reports for {symbol}:\n" + "\n".join(rep_lines) + "\n\n"
        f"Reply with ONLY the argument text. No preamble, no JSON, no "
        f"meta-commentary about the prompt."
    )

    try:
        chat = build_chat_model("research_expert", max_tokens=600)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_expert"), usage)
        if not text:
            return None
        return text, cost
    except Exception as e:
        log.debug("debate %s narration failed: %s", stance, e)
        return None
