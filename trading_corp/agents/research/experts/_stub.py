"""Stub expert — returns a valid `ExpertReport` with `data_sufficiency=False`.

Now used as the **graceful fallback** when an engagement references a
role whose Expert instance isn't wired into deps (e.g. test fixtures
that intentionally inject only 1-2 fake experts). Post-Phase 1c, the
production default deps wire all four roles real (technical, macro,
fundamental, sentiment); the stub path normally only runs in tests.

Stub semantics are load-bearing regardless of who triggers them: the
synthesis prompt explicitly tells the model "N experts refused — treat
their dimension as unobserved" so weight isn't silently zero-credited
as something.

The debate gate (Phase 1f) considers only `data_sufficiency=True`
experts; refusals don't contribute to variance.
"""
from __future__ import annotations

from trading_corp.agents.research.schemas import ExpertReport


def stub_expert_report(
    role: str,
    engagement_id: str,
    symbol: str,
    *,
    refusal_reason: str | None = None,
) -> ExpertReport:
    """Construct a refusal report for an expert whose data source isn't wired.

    Default refusal_reason maps from role; callers can override for
    test scenarios that want a specific message.
    """
    default_reasons = {
        "sentiment": "sentiment expert not wired in this deps build",
        "fundamental": "fundamental expert not wired in this deps build",
        "technical": "technical data unavailable",
        "macro": "macro data unavailable",
    }
    reason = refusal_reason or default_reasons.get(role, f"no data for {role}")
    return ExpertReport(
        role=role,
        engagement_id=engagement_id,
        symbol=symbol,
        summary=f"[STUB] {role} expert refused: {reason}",
        key_evidence=[],
        confidence_score=0.0,
        directional_lean=None,
        data_sufficiency=False,
        refusal_reason=reason,
    )
