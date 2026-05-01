"""LLM cost tracking for the research firm.

Phases 1a–1c: cost = LLM API spend only (yfinance + MacroCalendar are free).
Phase 1d onward: add data-API spend; cap values may need re-tuning.

Pricing (USD per 1M tokens, current Anthropic public pricing — verify
before each model swap):
    claude-sonnet-4-6: $3 input / $15 output
    claude-opus-4-7:   $15 input / $75 output

If usage info is missing or the model name doesn't match, returns 0.0 —
the engagement keeps running but cost tracking is best-effort. The hard
cap still triggers via accumulated estimates from cached pricing tables.
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

# Per-million-tokens. Update when Anthropic pricing changes.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0},
    # Older fallbacks — used if config still references them.
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0},
}

# Soft default for unknown models — matches Sonnet so we under-estimate
# rather than over-estimate against the cap (cost cap is the safety
# rail; we'd rather fire it slightly later than refuse a legitimate
# engagement on bogus pricing).
_DEFAULT_PRICE = {"input": 3.0, "output": 15.0}


@lru_cache(maxsize=1)
def _agents_cfg() -> dict:
    """Lazy single-shot read of config/agents.yaml. Same shape as
    `agents/llm.py:_load_config()` — duplicating it lightly here keeps
    the cost module decoupled from the LLM client builder."""
    from pathlib import Path
    import yaml
    p = Path("config/agents.yaml")
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.warning("cost: failed to read config/agents.yaml: %s", e)
        return {}


def model_for_role(role: str) -> str:
    """Resolve the model id for a research role (`research_expert`,
    `research_synthesis`, `research_judge`).

    Mirrors the lookup in `agents/llm.py:get_model_for` but doesn't share
    the lru_cache so config tweaks are picked up immediately. Returns
    the default Sonnet if absent.
    """
    cfg = _agents_cfg()
    a = (cfg.get("agents", {}) or {}).get(role, {})
    return a.get("model") or cfg.get("models", {}).get("default", "claude-sonnet-4-6")


def cost_for_anthropic_usage(model: str, usage: dict) -> float:
    """Compute dollars from a usage dict.

    `usage` keys can be either Anthropic-native (`input_tokens`,
    `output_tokens`, `cache_creation_input_tokens`,
    `cache_read_input_tokens`) or LangChain's normalized shape
    (`prompt_tokens`, `completion_tokens`). Both supported.

    Cache-read tokens are billed at 10% of base input; cache-creation at
    125%. We approximate without round-tripping every distinction —
    research firm calls are short and the cache layer matters less than
    the cap-fire ordering.
    """
    if not usage:
        return 0.0

    price = _PRICING.get(model, _DEFAULT_PRICE)

    in_tok = (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("prompt_tokens") or 0)
    )
    out_tok = (
        int(usage.get("output_tokens") or 0)
        + int(usage.get("completion_tokens") or 0)
    )
    cache_create = int(usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)

    cost = 0.0
    cost += (in_tok / 1_000_000.0) * price["input"]
    cost += (out_tok / 1_000_000.0) * price["output"]
    cost += (cache_create / 1_000_000.0) * price["input"] * 1.25
    cost += (cache_read / 1_000_000.0) * price["input"] * 0.10
    return cost
