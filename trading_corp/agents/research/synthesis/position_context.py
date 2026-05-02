"""Synthesize a `PositionContext` from a single symbol's expert reports.

Phase 1d. Returned to the requesting division for internal consumption
(Lord Otter / Market Cypher pre-trade situational awareness). Audit row
is written regardless of whether the caller surfaces the result — see
design §3.5 PositionContext docstring.

Synthesis math:
  - Single symbol; reports come from the engagement's registered
    expert set per the (position_context, asset_class) registry row,
    which today is [macro, sentiment].
  - Refused experts (data_sufficiency=False) flow into risk_flags
    as observability gaps rather than being silently dropped.
  - confidence_score = mean of valid experts' confidence_score; 0.0
    when all experts refused.

LLM narration: when ANTHROPIC_API_KEY is set, the synthesis LLM
produces macro_summary + sentiment_summary + risk_flags. When
unavailable, all three are built deterministically from the
structured expert summaries.

See planning/research_firm_design.md §3.5, §1.3 (PositionContext row),
Phase 1d.
"""
from __future__ import annotations

import json
import logging
import re

from trading_corp.agents.research.cost import (
    cost_for_anthropic_usage, model_for_role,
)
from trading_corp.agents.research.schemas import (
    EngagementSpec, ExpertReport, PositionContext, PositionContextScope,
)

log = logging.getLogger(__name__)


async def synthesize_position_context(
    *,
    spec: EngagementSpec,
    reports: list[ExpertReport],
    expert_audit_row_ids: list[int],
    debate_outcome: dict | None = None,
) -> tuple[PositionContext, float]:
    """Return (position_context, llm_dollars).

    `reports` is the flat list of ExpertReports for the single symbol
    in scope (one per registered role; refusals included).

    Phase 1f: when `debate_outcome` is provided (gate fired on
    macro+sentiment disagreement), the judge synthesis surfaces as an
    extra `risk_flags` entry so the consuming division (Otter/Cypher)
    sees that the macro+sentiment view was contested. PositionContext
    has no debate_audit_row_id field per design — the row is joinable
    via engagement_id from the dashboard.
    """
    if not isinstance(spec.scope, PositionContextScope):
        raise ValueError(
            f"synthesize_position_context called with non-PositionContextScope "
            f"({type(spec.scope).__name__})"
        )
    scope = spec.scope
    symbol = scope.symbol

    valid = [r for r in reports if r.data_sufficiency]
    refused = [r for r in reports if not r.data_sufficiency]

    macro_valid = _by_role(valid, "macro")
    sentiment_valid = _by_role(valid, "sentiment")
    macro_refused = _by_role(refused, "macro")
    sentiment_refused = _by_role(refused, "sentiment")

    deterministic_macro = _det_role_summary(
        symbol, "macro", macro_valid, macro_refused,
    )
    deterministic_sentiment = _det_role_summary(
        symbol, "sentiment", sentiment_valid, sentiment_refused,
    )
    deterministic_flags = _det_risk_flags(valid, refused)

    llm_cost = 0.0
    narrated_macro: str | None = None
    narrated_sentiment: str | None = None
    narrated_flags: list[str] | None = None
    narrated = await _narrate_position_context_if_available(
        spec, symbol, valid, refused,
    )
    if narrated is not None:
        (narrated_macro, narrated_sentiment, narrated_flags), llm_cost = narrated

    macro_summary = narrated_macro or deterministic_macro
    sentiment_summary = narrated_sentiment or deterministic_sentiment
    risk_flags = list(narrated_flags if narrated_flags else deterministic_flags)

    # Phase 1f: when the debate gate fired, surface the judge's
    # synthesis as an extra risk_flags entry so the consuming division
    # sees that the macro+sentiment view was contested. The full
    # bull/bear/judge content lives in audit log; the flag just signals
    # "look at the audit row for the debate" with a one-line summary.
    if debate_outcome and debate_outcome.get("synthesis"):
        risk_flags.insert(
            0,
            f"debate fired: {debate_outcome.get('synthesis', '')}",
        )

    confidence_score = (
        sum(r.confidence_score for r in valid) / len(valid)
        if valid
        else 0.0
    )

    pc = PositionContext(
        engagement_id=spec.engagement_id,
        requesting_division=spec.requesting_division,
        symbol=symbol,
        time_horizon_hours=scope.time_horizon_hours,
        macro_summary=macro_summary,
        sentiment_summary=sentiment_summary,
        risk_flags=risk_flags,
        confidence_score=confidence_score,
        expert_audit_row_ids=list(expert_audit_row_ids),
    )
    return pc, llm_cost


# ── Deterministic helpers ────────────────────────────────────────────────


def _by_role(reports: list[ExpertReport], role: str) -> list[ExpertReport]:
    return [r for r in reports if r.role == role]


def _det_role_summary(
    symbol: str,
    role: str,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
) -> str:
    if valid:
        return " | ".join(r.summary for r in valid)
    if refused:
        reasons = "; ".join(
            r.refusal_reason or "no reason given" for r in refused
        )
        return f"{symbol}: {role} refused — {reasons}"
    return f"{symbol}: no {role} report."


def _det_risk_flags(
    valid: list[ExpertReport],
    refused: list[ExpertReport],
) -> list[str]:
    flags: list[str] = []
    for r in valid:
        if r.directional_lean == "bearish":
            flags.append(
                f"{r.role}: bearish (conf={r.confidence_score:.2f}) — {r.summary}"
            )
    for r in refused:
        flags.append(
            f"{r.role}: data unavailable — {r.refusal_reason or 'no reason given'}"
        )
    return flags


# ── LLM narration ────────────────────────────────────────────────────────


async def _narrate_position_context_if_available(
    spec: EngagementSpec,
    symbol: str,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
) -> tuple[tuple[str, str, list[str]], float] | None:
    """Best-effort LLM narration. Returns ((macro, sentiment, flags), cost)
    or None on any failure. Tests bypass via no-API-key."""
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
    except Exception:
        return None
    if not isinstance(spec.scope, PositionContextScope):
        return None

    rep_lines: list[str] = []
    for r in valid:
        rep_lines.append(
            f"  - [{r.role} | lean={r.directional_lean} | "
            f"conf={r.confidence_score:.2f}] {r.summary}"
        )
    for r in refused:
        rep_lines.append(f"  - [{r.role} REFUSED] {r.refusal_reason}")

    horizon = spec.scope.time_horizon_hours
    division = spec.requesting_division

    prompt = (
        f"You are an analyst on a research desk. The {division} division is "
        f"holding {symbol} (asset_class={spec.asset_class}) and wants a "
        f"situational picture for the next {horizon} hour(s). "
        f"This is a *context* read, NOT a buy/sell recommendation.\n\n"
        f"Return a single JSON object with exactly these keys:\n"
        f'  {{"macro_summary": "...", "sentiment_summary": "...", '
        f'"risk_flags": ["...", "..."]}}\n\n'
        f"Rules:\n"
        f"- macro_summary: 1-3 sentences synthesizing the macro read for "
        f"{symbol} over the next {horizon}h.\n"
        f"- sentiment_summary: 1-3 sentences synthesizing the sentiment read.\n"
        f"- risk_flags: short bullet items — bearish leans, refused data "
        f"dimensions, or any near-term hazards. Empty list is valid.\n"
        f"- Treat refused expert dimensions as unobserved — surface the gap "
        f"as a risk_flag; do not invent data.\n"
        f"- No prose outside the JSON.\n\n"
        f"Expert reports for {symbol}:\n" + "\n".join(rep_lines)
    )

    try:
        chat = build_chat_model("research_synthesis", max_tokens=1000)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_synthesis"), usage)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            log.debug("position_context narration JSON parse failed: %s", e)
            return None
        if not isinstance(parsed, dict):
            return None
        macro_s = str(parsed.get("macro_summary") or "").strip()
        sent_s = str(parsed.get("sentiment_summary") or "").strip()
        flags_raw = parsed.get("risk_flags") or []
        flags = [str(x).strip() for x in flags_raw if str(x).strip()]
        if not macro_s and not sent_s:
            return None
        return (macro_s, sent_s, flags), cost
    except Exception as e:
        log.debug("position_context narration LLM call failed: %s", e)
        return None
