"""Synthesize a `Thesis` from a single symbol's expert reports.

Phase 1b. Board ad-hoc only — no division consumes Thesis in production
flow. Mirrors `candidate.py`'s shape but operates on one symbol.

Synthesis math:
  - Single symbol; reports come from the engagement's registered
    expert set per the (thesis, asset_class) registry row.
  - Refused experts (data_sufficiency=False) flow into key_risks
    as observability gaps rather than being silently dropped.
  - earnings_window_clear: deterministic via yfinance get_next_earnings
    (7-day cutoff — matches CandidateScope's default earnings_buffer).
    Failures default to True; the Thesis surface is exploratory, not
    a trade gate.

LLM narration: when ANTHROPIC_API_KEY is set, the synthesis LLM
produces summary + key_drivers + key_risks. When unavailable, all
three are built deterministically from the structured expert summaries.

See planning/research_firm_design.md §3.5, §1.3 (Thesis row), Phase 1b.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from trading_corp.agents.research.cost import (
    cost_for_anthropic_usage, model_for_role,
)
from trading_corp.agents.research.schemas import (
    EngagementSpec, ExpertReport, Thesis, ThesisScope,
)

log = logging.getLogger(__name__)


# Same default as CandidateScope.earnings_buffer_days. Thesis is
# exploratory so we don't expose this as a Scope field — but if the
# Board wants a different window, the deterministic value lives in
# the synthesized Thesis (key_risks calls it out) and the LLM
# narration can override the wording.
_THESIS_EARNINGS_BUFFER_DAYS = 7


async def synthesize_thesis(
    *,
    spec: EngagementSpec,
    reports: list[ExpertReport],
    expert_audit_row_ids: list[int],
    debate_outcome: dict | None = None,
    debate_audit_row_id: int | None = None,
) -> tuple[Thesis, float]:
    """Return (thesis, llm_dollars).

    `reports` is the flat list of ExpertReports for the single symbol
    in scope (one per registered role; refusals included).

    Phase 1f: when the debate gate fired, `debate_outcome` is a
    DebateOutcome.model_dump() with bull_case/bear_case/judge scores/
    synthesis. Synthesis surfaces the judge's synthesis line in
    key_drivers/key_risks; the audit row id is tagged on the product.
    """
    if not isinstance(spec.scope, ThesisScope):
        raise ValueError(
            f"synthesize_thesis called with non-ThesisScope "
            f"({type(spec.scope).__name__})"
        )
    scope = spec.scope
    symbol = scope.symbol

    valid = [r for r in reports if r.data_sufficiency]
    refused = [r for r in reports if not r.data_sufficiency]

    earnings_clear = _compute_earnings_window_clear(symbol)

    deterministic_summary = _det_summary(symbol, valid, refused)
    deterministic_drivers = _det_key_drivers(valid)
    deterministic_risks = _det_key_risks(valid, refused, earnings_clear)

    # Optional LLM narration — single call (single symbol).
    llm_cost = 0.0
    narrated_summary: str | None = None
    narrated_drivers: list[str] | None = None
    narrated_risks: list[str] | None = None
    narrated = await _narrate_thesis_if_available(
        spec, symbol, valid, refused, earnings_clear, debate_outcome,
    )
    if narrated is not None:
        (narrated_summary, narrated_drivers, narrated_risks), llm_cost = narrated

    summary = narrated_summary or deterministic_summary
    key_drivers = list(
        narrated_drivers if narrated_drivers else deterministic_drivers
    )
    key_risks = list(
        narrated_risks if narrated_risks else deterministic_risks
    )

    # Phase 1f: ALWAYS surface the debate synthesis as a key_drivers
    # entry when the gate fired, regardless of whether LLM narration
    # also incorporated debate context into other drivers. This is
    # metadata for the dashboard / consumers that the debate ran —
    # consistency with PositionContext's risk_flags surface, and
    # tests can pin the marker without depending on LLM output.
    if debate_outcome and debate_outcome.get("synthesis"):
        key_drivers.insert(
            0,
            f"debate (gate fired): {debate_outcome.get('synthesis', '')}",
        )

    thesis = Thesis(
        engagement_id=spec.engagement_id,
        symbol=symbol,
        summary=summary,
        key_drivers=key_drivers,
        key_risks=key_risks,
        earnings_window_clear=earnings_clear,
        expert_audit_row_ids=list(expert_audit_row_ids),
        debate_audit_row_id=debate_audit_row_id,
    )
    return thesis, llm_cost


# ── Deterministic helpers ────────────────────────────────────────────────


def _compute_earnings_window_clear(symbol: str) -> bool:
    """True if the next earnings is at least _THESIS_EARNINGS_BUFFER_DAYS
    away (or unknown). Failures default to True — the Thesis surface is
    exploratory, not a trade gate."""
    try:
        from trading_corp.utils.market_data import get_next_earnings
        next_earn = get_next_earnings(symbol)
    except Exception as e:
        log.debug("thesis: earnings lookup failed for %s: %s", symbol, e)
        return True
    if next_earn is None:
        return True
    try:
        now = datetime.now(timezone.utc)
        days = (next_earn - now).total_seconds() / 86400.0
        return days >= _THESIS_EARNINGS_BUFFER_DAYS
    except Exception:
        return True


def _det_summary(
    symbol: str,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
) -> str:
    if not valid and not refused:
        return f"{symbol}: no expert reports."
    if not valid:
        roles = ", ".join(sorted({r.role for r in refused}))
        return f"{symbol}: all experts refused ({roles}). No signal."
    bits = [f"[{r.role}] {r.summary}" for r in valid]
    return f"{symbol}: " + " | ".join(bits)


def _det_key_drivers(valid: list[ExpertReport]) -> list[str]:
    """Bullish + neutral leans become drivers; cite the role for traceability."""
    drivers: list[str] = []
    for r in valid:
        if r.directional_lean in ("bullish", "neutral"):
            drivers.append(
                f"{r.role}: {r.directional_lean} (conf={r.confidence_score:.2f}) — {r.summary}"
            )
    if not drivers and valid:
        # All bearish or no leans — surface the strongest-confidence read so
        # the Thesis still has SOMETHING actionable in the drivers slot.
        strongest = max(valid, key=lambda r: r.confidence_score)
        drivers.append(
            f"{strongest.role}: strongest signal (conf={strongest.confidence_score:.2f}) — {strongest.summary}"
        )
    return drivers or ["No directional drivers from expert reports."]


def _det_key_risks(
    valid: list[ExpertReport],
    refused: list[ExpertReport],
    earnings_clear: bool,
) -> list[str]:
    risks: list[str] = []
    for r in valid:
        if r.directional_lean == "bearish":
            risks.append(
                f"{r.role}: bearish (conf={r.confidence_score:.2f}) — {r.summary}"
            )
    for r in refused:
        risks.append(
            f"{r.role}: data unavailable — {r.refusal_reason or 'no reason given'}"
        )
    if not earnings_clear:
        risks.append(
            f"earnings within {_THESIS_EARNINGS_BUFFER_DAYS}d — event risk pending"
        )
    return risks or ["No bearish flags from expert reports."]


# ── LLM narration ────────────────────────────────────────────────────────


async def _narrate_thesis_if_available(
    spec: EngagementSpec,
    symbol: str,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
    earnings_clear: bool,
    debate_outcome: dict | None = None,
) -> tuple[tuple[str, list[str], list[str]], float] | None:
    """Best-effort LLM narration. Returns ((summary, drivers, risks), cost)
    or None on any failure. Tests bypass via no-API-key.

    When `debate_outcome` is provided, the prompt includes the judge's
    synthesis line + the two quality scores so the LLM can incorporate
    debate context into the narrated drivers/risks. Per Phase 1f
    decision, only the judge synthesis is included (not the full
    bull/bear texts) — those are recorded in the audit log; what's
    actionable for the narrative is the synthesis."""
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
    except Exception:
        return None
    if not isinstance(spec.scope, ThesisScope):
        return None

    rep_lines: list[str] = []
    for r in valid:
        rep_lines.append(
            f"  - [{r.role} | lean={r.directional_lean} | "
            f"conf={r.confidence_score:.2f}] {r.summary}"
        )
    for r in refused:
        rep_lines.append(f"  - [{r.role} REFUSED] {r.refusal_reason}")

    earnings_blurb = (
        "no earnings within the next 7 days"
        if earnings_clear
        else "earnings event within the next 7 days — event risk active"
    )

    depth_line = (
        "Write a deeper read (5-7 sentences in summary)."
        if spec.scope.depth == "deep"
        else "Write a standard read (3-4 sentences in summary)."
    )

    debate_block = _format_debate_block(debate_outcome)

    prompt = (
        f"You are an analyst on a research desk. The Board has asked for a "
        f"thesis on `{symbol}` (asset_class={spec.asset_class}). "
        f"Calendar: {earnings_blurb}.\n\n"
        f"{depth_line}\n\n"
        f"{debate_block}"
        f"Return a single JSON object with exactly these keys:\n"
        f'  {{"summary": "...", "key_drivers": ["...", "..."], '
        f'"key_risks": ["...", "..."]}}\n\n'
        f"Rules:\n"
        f"- summary: a paragraph synthesizing the read on {symbol} right now.\n"
        f"- key_drivers: 2-5 short bullet items (the bull case for {symbol}).\n"
        f"- key_risks: 2-5 short bullet items (what could go wrong / what's missing).\n"
        f"- Treat refused expert dimensions as unobserved — surface the gap "
        f"as a risk; do not invent data.\n"
        f"- No prose outside the JSON.\n\n"
        f"Expert reports for {symbol}:\n" + "\n".join(rep_lines)
    )

    try:
        chat = build_chat_model("research_synthesis", max_tokens=1500)
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
            log.debug("thesis narration JSON parse failed: %s", e)
            return None
        if not isinstance(parsed, dict):
            return None
        summary = str(parsed.get("summary") or "").strip()
        drivers_raw = parsed.get("key_drivers") or []
        risks_raw = parsed.get("key_risks") or []
        drivers = [str(x).strip() for x in drivers_raw if str(x).strip()]
        risks = [str(x).strip() for x in risks_raw if str(x).strip()]
        if not summary:
            # Empty narrated summary is no better than the deterministic one.
            return None
        return (summary, drivers, risks), cost
    except Exception as e:
        log.debug("thesis narration LLM call failed: %s", e)
        return None


def _format_debate_block(debate_outcome: dict | None) -> str:
    """Format the debate-context block for inclusion in the synthesis
    prompt. Returns a trailing-newline string when debate fired,
    empty string when it didn't. Per Phase 1f decision, only the judge
    synthesis line + the two quality scores are included — full
    bull/bear texts live in the audit log."""
    if not debate_outcome:
        return ""
    bull = debate_outcome.get("judge_bull_score") or {}
    bear = debate_outcome.get("judge_bear_score") or {}
    return (
        "Debate-gate fired (variance/disagreement among experts). "
        "Judge synthesis (NOT a verdict — neutral summary of where the "
        "bull/bear cases converge or diverge):\n"
        f"  {debate_outcome.get('synthesis', '')}\n"
        f"Judge quality scores — "
        f"bull: evidence={bull.get('evidence_quality', 0):.2f} "
        f"logic={bull.get('logical_consistency', 0):.2f} "
        f"falsifiability={bull.get('falsifiability', 0):.2f}; "
        f"bear: evidence={bear.get('evidence_quality', 0):.2f} "
        f"logic={bear.get('logical_consistency', 0):.2f} "
        f"falsifiability={bear.get('falsifiability', 0):.2f}.\n"
        "Incorporate this into your read; treat the judge's synthesis "
        "as factually neutral.\n\n"
    )
