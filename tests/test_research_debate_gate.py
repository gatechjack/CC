"""Unit tests for the debate-gate decision function (Phase 1f, Q10).

Pure function. Two arms:
  - variance arm: pvariance(confidence_scores) >= variance_threshold
  - disagreement arm: distinct directional_lean count >= min_disagreeing_experts

Refused experts (data_sufficiency=False) never contribute to either arm.
A single-voice panel can never fire. Boundary cases (== threshold) fire.
"""
from __future__ import annotations

from trading_corp.agents.research.debate_gate import evaluate_debate_gate
from trading_corp.agents.research.schemas import ExpertReport


def _r(role: str, conf: float, lean: str | None, sufficient: bool = True) -> ExpertReport:
    return ExpertReport(
        role=role,
        engagement_id="e1",
        symbol="AAPL",
        summary=f"{role} report",
        confidence_score=conf,
        directional_lean=lean,
        data_sufficiency=sufficient,
        refusal_reason=None if sufficient else "no data",
    )


def test_aligned_experts_skip_debate():
    reports = [
        _r("technical", 0.7, "bullish"),
        _r("macro", 0.65, "bullish"),
        _r("sentiment", 0.6, "bullish"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is False
    assert reason is None


def test_disagreement_quorum_fires():
    """Two distinct leans (>= min_disagreeing_experts=2) -> fire."""
    reports = [
        _r("technical", 0.7, "bullish"),
        _r("macro", 0.65, "bearish"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is True
    assert "leans split" in reason


def test_disagreement_quorum_with_neutral_still_fires():
    """bullish + neutral = 2 distinct leans -> fire."""
    reports = [
        _r("technical", 0.7, "bullish"),
        _r("macro", 0.6, "neutral"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is True
    assert "leans split" in reason


def test_variance_at_threshold_fires():
    """pvariance([0.0, 1.0]) = 0.25 — exactly at default threshold."""
    reports = [
        _r("technical", 0.0, "bullish"),
        _r("macro", 1.0, "bullish"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is True
    assert "variance" in reason


def test_low_variance_aligned_leans_skips():
    """Tight conviction band, same lean -> no debate."""
    reports = [
        _r("technical", 0.6, "bullish"),
        _r("macro", 0.65, "bullish"),
        _r("sentiment", 0.7, "bullish"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is False
    assert reason is None


def test_single_valid_expert_never_fires():
    """One voice can't disagree with itself."""
    reports = [
        _r("technical", 0.99, "bullish"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is False
    assert reason is None


def test_zero_valid_experts_never_fires():
    fire, reason = evaluate_debate_gate([])
    assert fire is False
    assert reason is None


def test_refused_expert_does_not_count_toward_disagreement():
    """If a refused expert had `directional_lean='bearish'` set, it must
    NOT count toward the disagreement arm. Treat refused dimensions as
    unobserved (design §3.3)."""
    reports = [
        _r("technical", 0.7, "bullish"),
        # Refused expert with a (would-be-disagreeing) lean — must be ignored
        _r("macro", 0.0, "bearish", sufficient=False),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is False
    assert reason is None


def test_refused_expert_does_not_count_toward_variance():
    """A refused expert with extreme confidence_score should not blow
    up variance — they're unobserved per §3.3."""
    reports = [
        _r("technical", 0.5, "bullish"),
        _r("macro", 0.55, "bullish"),
        # Refused with extreme conf — would inflate variance if counted
        _r("sentiment", 0.0, None, sufficient=False),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is False, (
        f"refused expert with conf=0.0 should not push variance >= 0.25; "
        f"got reason={reason}"
    )


def test_none_directional_lean_does_not_count():
    """An expert with directional_lean=None contributes to variance but
    not to the disagreement arm."""
    reports = [
        _r("technical", 0.5, "bullish"),
        _r("macro", 0.55, None),  # unknown lean
    ]
    # Same lean count = 1 (only bullish), variance is tiny -> skip
    fire, reason = evaluate_debate_gate(reports)
    assert fire is False


def test_three_experts_all_different_leans_fires():
    """bullish + bearish + neutral = 3 distinct leans -> fire."""
    reports = [
        _r("technical", 0.55, "bullish"),
        _r("macro", 0.5, "bearish"),
        _r("sentiment", 0.45, "neutral"),
    ]
    fire, reason = evaluate_debate_gate(reports)
    assert fire is True
    assert "leans split" in reason
    # Check the structured reason includes per-lean counts
    assert "bullish=1" in reason
    assert "bearish=1" in reason
    assert "neutral=1" in reason
