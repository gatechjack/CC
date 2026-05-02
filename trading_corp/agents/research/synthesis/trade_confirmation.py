"""Synthesize a `TradeConfirmation` from a single symbol's expert reports.

Phase 1e. The TradeConfirmation surface is consumed inline by Otter /
Cypher webhook handlers between tier classification and `_build_order`.
Latency-critical (Phase 1e cap: $0.30/$0.75 cost, ~8s timeout per Q11);
verdict is *advice*, not a *gate* — the existing risk gate + HITL flow
remains the safety net.

Synthesis math:
  - Single symbol; reports come from the engagement's registered
    expert set per the (trade_confirmation, asset_class) registry row:
    [technical, fundamental, macro] for equity / option,
    [technical, macro, sentiment] for crypto_spot.
  - Verdict ternary per design §3.5 / Q2 (confirm | push_back | conditional).
    Conditional verdicts MUST carry suggested_modifications; the schema's
    model_validator enforces this and synthesis defends-in-depth.

Verdict logic:
  - LLM available: model returns verdict + rationale + risks_flagged + optional
    SuggestedModifications (size_pct_equity / entry_price / side / rationale).
    Synthesis sanity-checks the response and falls back deterministically
    on parse failure.
  - LLM unavailable: deterministic fallback only emits confirm or push_back
    (never conditional) because conditional requires a synthesized
    SuggestedModifications.rationale that we won't fabricate. Heuristic:
    push_back when ALL valid experts lean bearish; otherwise confirm.

Refused experts (data_sufficiency=False) flow into risks_flagged as
observability gaps rather than being silently dropped.

See planning/research_firm_design.md §3.5, §1.3 (TradeConfirmation row),
Phase 1e.
"""
from __future__ import annotations

import json
import logging
import re

from trading_corp.agents.research.cost import (
    cost_for_anthropic_usage, model_for_role,
)
from trading_corp.agents.research.schemas import (
    EngagementSpec, ExpertReport, SuggestedModifications,
    TradeConfirmation, TradeConfirmationScope,
)

log = logging.getLogger(__name__)


async def synthesize_trade_confirmation(
    *,
    spec: EngagementSpec,
    reports: list[ExpertReport],
    expert_audit_row_ids: list[int],
) -> tuple[TradeConfirmation, float]:
    """Return (trade_confirmation, llm_dollars).

    `reports` is the flat list of ExpertReports for the single symbol
    in scope (one per registered role; refusals included).
    """
    if not isinstance(spec.scope, TradeConfirmationScope):
        raise ValueError(
            f"synthesize_trade_confirmation called with non-TradeConfirmationScope "
            f"({type(spec.scope).__name__})"
        )
    scope = spec.scope
    proposed_action = dict(scope.proposed_action or {})

    valid = [r for r in reports if r.data_sufficiency]
    refused = [r for r in reports if not r.data_sufficiency]

    deterministic = _deterministic_verdict(valid, refused, proposed_action)

    llm_cost = 0.0
    llm_result = await _narrate_trade_confirmation_if_available(
        spec, proposed_action, valid, refused,
    )

    if llm_result is not None:
        narrated, llm_cost = llm_result
        verdict = narrated["verdict"]
        rationale = narrated["rationale"] or deterministic["rationale"]
        risks_flagged = narrated["risks_flagged"] or deterministic["risks_flagged"]
        suggested_modifications = narrated["suggested_modifications"]
    else:
        verdict = deterministic["verdict"]
        rationale = deterministic["rationale"]
        risks_flagged = deterministic["risks_flagged"]
        suggested_modifications = None

    # Defense-in-depth: schema model_validator enforces this, but we'd
    # rather emit a confirm than blow up the engagement on a borderline
    # LLM response that requested conditional without modifications.
    if verdict == "conditional" and suggested_modifications is None:
        log.warning(
            "trade_confirmation: LLM returned conditional without "
            "suggested_modifications — downgrading to confirm"
        )
        verdict = "confirm"

    tc = TradeConfirmation(
        engagement_id=spec.engagement_id,
        requesting_division=spec.requesting_division,
        subject_action=proposed_action,
        verdict=verdict,
        rationale=rationale,
        risks_flagged=risks_flagged,
        suggested_modifications=suggested_modifications,
        expert_audit_row_ids=list(expert_audit_row_ids),
        debate_audit_row_id=None,
    )
    return tc, llm_cost


# ── Deterministic helpers ────────────────────────────────────────────────


def _deterministic_verdict(
    valid: list[ExpertReport],
    refused: list[ExpertReport],
    proposed_action: dict,
) -> dict:
    """Build a verdict + rationale + risks_flagged from structured inputs only.
    Conditional verdicts are NEVER produced by the deterministic path
    because they require a SuggestedModifications.rationale we won't
    fabricate."""
    side = proposed_action.get("side")
    symbol = proposed_action.get("symbol", "")

    risks_flagged: list[str] = []
    for r in refused:
        risks_flagged.append(
            f"{r.role}: data unavailable -- {r.refusal_reason or 'no reason given'}"
        )
    for r in valid:
        if r.directional_lean == "bearish":
            risks_flagged.append(
                f"{r.role}: bearish (conf={r.confidence_score:.2f})"
            )

    if not valid:
        return {
            "verdict": "confirm",
            "rationale": (
                f"All experts refused on {symbol}; no signal to evaluate "
                f"{side} order. Defaulting to confirm — risk gate + HITL "
                f"remain the safety net."
            ),
            "risks_flagged": risks_flagged,
        }

    bearish = [r for r in valid if r.directional_lean == "bearish"]
    bullish = [r for r in valid if r.directional_lean == "bullish"]

    # Push back when ALL valid experts lean against the proposed direction.
    # For "buy", that's all-bearish. For "sell" (closing a long, which is
    # what Otter/Cypher do today on long_only spot), bearish is the
    # supportive lean for the close, so all-bullish would push back.
    # NOTE: futures is the next broker wire-up (coinbase_futures, Phase C).
    # When `direction_policy: both` lands, "sell" stops being unambiguously
    # a long-close — short-opens are also "sell". This branch needs to
    # consult the proposed_action's intent (open vs close, long vs short)
    # rather than infer from side alone. Revisit alongside that wire-up.
    push_back_quorum = (
        len(valid) >= 2
        and (
            (side == "buy" and len(bearish) == len(valid))
            or (side == "sell" and len(bullish) == len(valid))
        )
    )
    if push_back_quorum:
        leans = ", ".join(f"{r.role}={r.directional_lean}" for r in valid)
        return {
            "verdict": "push_back",
            "rationale": (
                f"All {len(valid)} experts lean against the proposed "
                f"{side} on {symbol} ({leans}). Recommend skip."
            ),
            "risks_flagged": risks_flagged,
        }

    leans_summary = ", ".join(
        f"{r.role}={r.directional_lean or 'n/a'}" for r in valid
    )
    return {
        "verdict": "confirm",
        "rationale": (
            f"Experts on {symbol}: {leans_summary}. No quorum against the "
            f"proposed {side}; confirm with risk gate to follow."
        ),
        "risks_flagged": risks_flagged,
    }


# ── LLM narration ────────────────────────────────────────────────────────


async def _narrate_trade_confirmation_if_available(
    spec: EngagementSpec,
    proposed_action: dict,
    valid: list[ExpertReport],
    refused: list[ExpertReport],
) -> tuple[dict, float] | None:
    """Best-effort LLM call. Returns (parsed_result_dict, cost) or None on
    any failure path. The result dict has keys:
      verdict: 'confirm' | 'push_back' | 'conditional'
      rationale: str
      risks_flagged: list[str]
      suggested_modifications: SuggestedModifications | None
    """
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
    except Exception:
        return None
    if not isinstance(spec.scope, TradeConfirmationScope):
        return None

    rep_lines: list[str] = []
    for r in valid:
        rep_lines.append(
            f"  - [{r.role} | lean={r.directional_lean} | "
            f"conf={r.confidence_score:.2f}] {r.summary}"
        )
    for r in refused:
        rep_lines.append(f"  - [{r.role} REFUSED] {r.refusal_reason}")

    action_str = json.dumps(proposed_action, sort_keys=True, default=str)
    context_str = json.dumps(
        dict(spec.scope.context or {}), sort_keys=True, default=str,
    )
    division = spec.requesting_division

    prompt = (
        f"You are a research analyst on a trading desk. The {division} "
        f"division is about to submit the following order and wants a "
        f"sanity check before placing it.\n\n"
        f"Proposed action (JSON):\n  {action_str}\n\n"
        f"Surrounding context (JSON):\n  {context_str}\n\n"
        f"Expert reports:\n" + "\n".join(rep_lines) + "\n\n"
        f"Decide a verdict:\n"
        f"  - 'confirm': the trade is reasonable as proposed.\n"
        f"  - 'push_back': the trade should be skipped given current evidence.\n"
        f"  - 'conditional': the trade is okay if specific modifications are made.\n\n"
        f"Return a single JSON object with exactly these keys:\n"
        f'  {{"verdict": "...", "rationale": "...", '
        f'"risks_flagged": ["...", "..."], '
        f'"suggested_modifications": null OR '
        f'{{"size_pct_equity": null|0.0-0.10, "entry_price": null|>0, '
        f'"side": null|"buy"|"sell", "rationale": "required when present"}}'
        f"}}\n\n"
        f"Rules:\n"
        f"- 'rationale' is a single short paragraph explaining the verdict.\n"
        f"- 'risks_flagged' lists 0-5 short bullet items: bearish leans, "
        f"refused expert dimensions, near-term hazards.\n"
        f"- Use 'conditional' ONLY when you can articulate a specific "
        f"modification (size, price, or side). If you cannot, use confirm "
        f"or push_back instead.\n"
        f"- 'suggested_modifications' MUST be null unless verdict is conditional.\n"
        f"- Treat refused expert dimensions as unobserved -- surface the gap "
        f"as a risk_flag; do not invent data.\n"
        f"- No prose outside the JSON.\n"
    )

    try:
        chat = build_chat_model("research_synthesis", max_tokens=1200)
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
            log.debug("trade_confirmation narration JSON parse failed: %s", e)
            return None
        if not isinstance(parsed, dict):
            return None
        verdict = str(parsed.get("verdict") or "").strip()
        if verdict not in ("confirm", "push_back", "conditional"):
            log.debug("trade_confirmation: invalid verdict %r", verdict)
            return None
        rationale = str(parsed.get("rationale") or "").strip()
        risks_raw = parsed.get("risks_flagged") or []
        risks_flagged = [str(x).strip() for x in risks_raw if str(x).strip()]

        sm: SuggestedModifications | None = None
        sm_raw = parsed.get("suggested_modifications")
        if isinstance(sm_raw, dict):
            try:
                sm = SuggestedModifications.model_validate(sm_raw)
            except Exception as e:
                log.debug(
                    "trade_confirmation: suggested_modifications validation "
                    "failed: %s", e,
                )
                sm = None

        if not rationale:
            return None

        return (
            {
                "verdict": verdict,
                "rationale": rationale,
                "risks_flagged": risks_flagged,
                "suggested_modifications": sm,
            },
            cost,
        )
    except Exception as e:
        log.debug("trade_confirmation narration LLM call failed: %s", e)
        return None
