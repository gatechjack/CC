"""Variance/disagreement gate for the bull/bear debate (Phase 1f, Q10).

Pure function: given a list of `ExpertReport`s for one symbol, decides
whether the debate round should fire. Reads thresholds from
`config/research.yaml` `debate_gate` block; falls back to design defaults
on any read error.

Gate rule (per design Q10):
    fire if (variance_of_confidence_scores >= variance_threshold)
         OR (count_of_distinct_directional_leans >= min_disagreeing_experts)

Only `data_sufficiency=True` reports are considered. Refused experts
don't contribute to variance OR to disagreement count — design §3.3
"treat refused dimensions as unobserved." If fewer than 2 valid
reports exist, the gate cannot fire (single voice = no disagreement
to debate).

The gate emits a structured "reason" string when it fires, used by
the audit row + the bull/bear prompt context so they know WHY they
were summoned.
"""
from __future__ import annotations

import logging
import statistics
from pathlib import Path
from typing import Iterable

import yaml

from trading_corp.agents.research.schemas import ExpertReport

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESEARCH_YAML = _REPO_ROOT / "config" / "research.yaml"

_DEFAULT_VARIANCE_THRESHOLD = 0.25
_DEFAULT_MIN_DISAGREEING_EXPERTS = 2


def _load_debate_gate_cfg() -> tuple[float, int]:
    """Read (variance_threshold, min_disagreeing_experts) from
    research.yaml. Returns design defaults on any error."""
    try:
        with _RESEARCH_YAML.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return _DEFAULT_VARIANCE_THRESHOLD, _DEFAULT_MIN_DISAGREEING_EXPERTS
    except Exception as e:
        log.warning("debate_gate: yaml read failed: %s", e)
        return _DEFAULT_VARIANCE_THRESHOLD, _DEFAULT_MIN_DISAGREEING_EXPERTS
    block = cfg.get("debate_gate") or {}
    var_t = block.get("variance_threshold")
    min_d = block.get("min_disagreeing_experts")
    var_threshold = (
        float(var_t) if isinstance(var_t, (int, float)) and var_t > 0
        else _DEFAULT_VARIANCE_THRESHOLD
    )
    min_disagreeing = (
        int(min_d) if isinstance(min_d, int) and min_d > 0
        else _DEFAULT_MIN_DISAGREEING_EXPERTS
    )
    return var_threshold, min_disagreeing


def evaluate_debate_gate(
    reports: Iterable[ExpertReport],
) -> tuple[bool, str | None]:
    """Decide whether to fire the debate round for this set of reports.

    Returns (should_fire, reason). `reason` is None when the gate skips,
    a short structured string when it fires (e.g. "variance=0.31 >=
    0.25" or "leans split: bullish=2, bearish=1"). Reason is consumed
    by the audit row and the bull/bear prompt context.
    """
    valid = [r for r in reports if r.data_sufficiency]
    if len(valid) < 2:
        return False, None

    var_threshold, min_disagreeing = _load_debate_gate_cfg()

    confidences = [r.confidence_score for r in valid]
    # Population variance — fits the "spread of opinions" framing better
    # than sample variance, which is normally used for inference about
    # an unknown distribution. Here we have the entire panel.
    variance = (
        statistics.pvariance(confidences) if len(confidences) >= 2 else 0.0
    )

    distinct_leans = {
        r.directional_lean for r in valid if r.directional_lean is not None
    }
    distinct_leans_count = len(distinct_leans)

    # First arm: variance trigger
    if variance >= var_threshold:
        return True, (
            f"variance={variance:.3f} >= {var_threshold:.3f} "
            f"({len(valid)} valid experts)"
        )

    # Second arm: disagreement quorum
    if distinct_leans_count >= min_disagreeing:
        leans_summary = ", ".join(
            f"{lean}={sum(1 for r in valid if r.directional_lean == lean)}"
            for lean in sorted(distinct_leans)
        )
        return True, (
            f"leans split across {distinct_leans_count} categories "
            f"({leans_summary})"
        )

    return False, None
