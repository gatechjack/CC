"""Synthesize a `CandidateRecommendation` from per-symbol expert reports.

Takes the engagement spec + a list of `(symbol, [ExpertReport])` rows
and produces a typed `CandidateRecommendation` with a list of
`Candidate` rows, each with thesis + conviction + fit_rationale +
fit_score.

Synthesis math:
  - For each symbol: aggregate confidence_score across NON-REFUSED
    experts → fit_score component (refused experts excluded — they
    don't get a 0 weight).
  - Aggregate directional_lean — bullish-skewed symbols score higher.
  - fit_score is in [0, 1] — bullish + confident scores high.
  - conviction is categorical — derived from fit_score with three bands.
  - Top `n_candidates` by fit_score become the candidates list (filtered
    to fit_score > 0; symbols in scope.current_holdings excluded).

LLM narration: when ANTHROPIC_API_KEY is set, the synthesis LLM produces
a 1-paragraph thesis + fit_rationale per candidate (one batched call,
JSON return). When unavailable, both are built deterministically from
the structured expert summaries.

See planning/research_firm_design.md §3.5, §5.
"""
from __future__ import annotations

import logging

from trading_corp.agents.research.cost import (
    cost_for_anthropic_usage, model_for_role,
)
from trading_corp.agents.research.schemas import (
    Candidate, CandidateRecommendation, CandidateScope, EngagementSpec,
    ExpertReport,
)

log = logging.getLogger(__name__)


async def synthesize_candidate_recommendation(
    *,
    spec: EngagementSpec,
    reports_by_symbol: dict[str, list[ExpertReport]],
    expert_audit_row_ids: list[int],
) -> tuple[CandidateRecommendation, float]:
    """Return (recommendation, llm_dollars).

    `reports_by_symbol` maps each candidate symbol to the registered
    experts' ExpertReports for it (some may be refusals from stub
    experts in Phase 1a-1).
    """
    if not isinstance(spec.scope, CandidateScope):
        raise ValueError(
            f"synthesize_candidate_recommendation called with non-CandidateScope "
            f"({type(spec.scope).__name__})"
        )

    scope = spec.scope
    held = {s.upper() for s in scope.current_holdings}

    # Compute fit scores deterministically.
    fit_scores: dict[str, float] = {}
    deterministic_thesis: dict[str, str] = {}
    deterministic_fit_rationale: dict[str, str] = {}
    for symbol, reports in reports_by_symbol.items():
        if symbol.upper() in held:
            # Layer 2 will reject if synth tries to recommend a held name;
            # skip here so we don't waste a slot.
            continue
        valid = [r for r in reports if r.data_sufficiency]
        if not valid:
            fit_scores[symbol] = 0.0
            deterministic_thesis[symbol] = (
                f"All experts refused for {symbol} — no signal."
            )
            deterministic_fit_rationale[symbol] = (
                "Mandate fit indeterminate without expert signal."
            )
            continue
        mean_conf = sum(r.confidence_score for r in valid) / len(valid)
        leans = [r.directional_lean for r in valid if r.directional_lean]
        bull = sum(1 for l in leans if l == "bullish")
        bear = sum(1 for l in leans if l == "bearish")
        if leans:
            alignment = (bull - bear) / len(leans)   # [-1, 1]
        else:
            alignment = 0.0
        fit_scores[symbol] = round(max(0.0, mean_conf * (0.5 + 0.5 * alignment)), 4)
        deterministic_thesis[symbol] = _det_thesis(symbol, valid)
        deterministic_fit_rationale[symbol] = _det_fit_rationale(symbol, valid, scope.mandate)

    # Pick top-N.
    ranked = sorted(fit_scores.items(), key=lambda kv: kv[1], reverse=True)
    accepted = [s for s, _ in ranked[: scope.n_candidates] if fit_scores[s] > 0]

    # Optional LLM narration — one call shared across accepted symbols.
    llm_cost = 0.0
    narrated_thesis: dict[str, str] = {}
    narrated_fit_rationale: dict[str, str] = {}
    if accepted:
        narrated = await _narrate_candidates_if_available(
            spec, accepted, reports_by_symbol,
        )
        if narrated is not None:
            (narrated_thesis, narrated_fit_rationale), llm_cost = narrated

    candidates: list[Candidate] = []
    for sym in accepted:
        thesis = narrated_thesis.get(sym) or deterministic_thesis.get(sym, "")
        fit_rationale = (
            narrated_fit_rationale.get(sym)
            or deterministic_fit_rationale.get(sym, "")
        )
        candidates.append(Candidate(
            symbol=sym,
            thesis=thesis,
            conviction=_conviction_from_fit_score(fit_scores[sym]),
            fit_rationale=fit_rationale,
            fit_score=fit_scores[sym],
        ))

    rec = CandidateRecommendation(
        engagement_id=spec.engagement_id,
        requesting_division=spec.requesting_division,
        asset_class=spec.asset_class,
        candidates=candidates,
        expert_audit_row_ids=list(expert_audit_row_ids),
        debate_audit_row_id=None,
    )
    return rec, llm_cost


def _conviction_from_fit_score(fit: float) -> str:
    """Map fit_score → categorical conviction. Phase 1a-1 keeps these
    bands simple; Phase 1c may re-tune once real experts ship signal."""
    if fit >= 0.6:
        return "high"
    if fit >= 0.3:
        return "medium"
    return "low"


def _det_thesis(symbol: str, reports: list[ExpertReport]) -> str:
    bits = []
    for r in reports:
        if r.data_sufficiency:
            bits.append(f"[{r.role}] {r.summary}")
    if not bits:
        return f"{symbol}: no expert signal."
    return f"{symbol}: " + " | ".join(bits)


def _det_fit_rationale(
    symbol: str, reports: list[ExpertReport], mandate: dict,
) -> str:
    """Deterministic fallback when LLM narration unavailable. Surfaces
    the mandate keys + any matching evidence from expert reports."""
    mandate_keys = ", ".join(sorted(mandate.keys())) if mandate else "(none)"
    lean_summary = []
    for r in reports:
        if r.data_sufficiency and r.directional_lean:
            lean_summary.append(f"{r.role}={r.directional_lean}")
    leans = ", ".join(lean_summary) if lean_summary else "no directional reads"
    return (
        f"{symbol}: mandate keys checked = [{mandate_keys}]. "
        f"Expert leans: {leans}."
    )


async def _narrate_candidates_if_available(
    spec: EngagementSpec,
    accepted: list[str],
    reports_by_symbol: dict[str, list[ExpertReport]],
) -> tuple[tuple[dict[str, str], dict[str, str]], float] | None:
    """Best-effort batched LLM narration for thesis + fit_rationale.

    Returns ((thesis_map, fit_rationale_map), total_dollars) or None on
    any failure. Tests bypass this via no-API-key in conftest.
    """
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
    except Exception:
        return None

    if not isinstance(spec.scope, CandidateScope):
        return None
    scope = spec.scope

    blocks = []
    for sym in accepted:
        reports = reports_by_symbol.get(sym, [])
        rep_lines = []
        for r in reports:
            if r.data_sufficiency:
                rep_lines.append(
                    f"  - [{r.role} | lean={r.directional_lean} | "
                    f"conf={r.confidence_score:.2f}] {r.summary}"
                )
            else:
                rep_lines.append(f"  - [{r.role} REFUSED] {r.refusal_reason}")
        blocks.append(f"{sym}:\n" + "\n".join(rep_lines))

    mandate_str = ", ".join(f"{k}={v!r}" for k, v in scope.mandate.items())
    prompt = (
        f"You are a portfolio analyst on a research desk. The requesting "
        f"division is `{spec.requesting_division}` (asset_class="
        f"{spec.asset_class}). Recommended candidates below.\n\n"
        f"DIVISION MANDATE (verbatim from strategies.yaml):\n{mandate_str}\n\n"
        f"For EACH symbol below, write TWO short fields:\n"
        f"  - 'thesis' (3-5 sentences): the read on the name right now.\n"
        f"  - 'fit_rationale' (2-3 sentences): how this symbol matches "
        f"the mandate above. Cite specific mandate keys.\n\n"
        f"Treat refused expert dimensions as unobserved (do not invent data).\n\n"
        f"Return a single JSON object: "
        f'{{"<symbol>": {{"thesis": "...", "fit_rationale": "..."}}, ...}}. '
        f"No prose outside the JSON.\n\n"
        f"Symbols and expert reports:\n\n" + "\n\n".join(blocks)
    )

    try:
        chat = build_chat_model("research_synthesis", max_tokens=2000)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_synthesis"), usage)

        import json
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return ({}, {}), cost
        try:
            parsed = json.loads(match.group(0))
            if not isinstance(parsed, dict):
                return ({}, {}), cost
            thesis_map: dict[str, str] = {}
            fit_map: dict[str, str] = {}
            for k, v in parsed.items():
                if isinstance(v, dict):
                    thesis_map[str(k)] = str(v.get("thesis", ""))
                    fit_map[str(k)] = str(v.get("fit_rationale", ""))
            return (thesis_map, fit_map), cost
        except json.JSONDecodeError as e:
            log.debug("candidate narration JSON parse failed: %s", e)
            return ({}, {}), cost
    except Exception as e:
        log.debug("candidate narration LLM call failed: %s", e)
        return None
