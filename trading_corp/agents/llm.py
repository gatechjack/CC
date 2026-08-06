"""Shared Anthropic Claude client builder with prompt caching.

All LLM agents go through this so model IDs, temperature, and caching are
configured in one place.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DEFAULT_AGENTS_YAML = Path("config/agents.yaml")


@lru_cache(maxsize=1)
def _load_config(path: str = str(DEFAULT_AGENTS_YAML)) -> dict:
    p = Path(path)
    if not p.exists():
        return {"models": {"default": "claude-sonnet-4-6"}, "agents": {}}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_model_for(agent_name: str, agents_yaml: str = str(DEFAULT_AGENTS_YAML)) -> tuple[str, float]:
    cfg = _load_config(agents_yaml)
    a = (cfg.get("agents", {}) or {}).get(agent_name, {})
    model = a.get("model") or cfg.get("models", {}).get("default", "claude-sonnet-4-6")
    temperature = float(a.get("temperature", 0.1))
    return model, temperature


_TEMPERATURE_REJECTING_MODELS = {
    # Opus 4.7 deprecated the temperature parameter; passing it returns
    # 400 invalid_request_error. The Sonnet line still accepts it.
    # Add new model IDs here as Anthropic deprecates temperature on more
    # models. Caught 2026-05-02 by Phase 1f UAT against the judge role.
    "claude-opus-4-7",
}


def build_chat_model(agent_name: str, *, max_tokens: int = 1024):
    """Return a langchain_anthropic.ChatAnthropic instance for `agent_name`.

    Imported lazily so test envs without langchain-anthropic still import the
    package (callers should fall back to deterministic-only behavior).

    Temperature handling: Opus 4.7 (and any other model in
    _TEMPERATURE_REJECTING_MODELS) deprecated the temperature parameter
    — we omit it on the constructor, falling back to the model's
    server-side default. Other models still receive the configured
    temperature from agents.yaml.
    """
    try:
        from langchain_anthropic import ChatAnthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "langchain-anthropic is required for LLM agents. "
            "pip install langchain-anthropic"
        ) from e

    model, temperature = get_model_for(agent_name)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        # Prompt caching is configured per-message via cache_control headers
        # in callers; this constructor honors them when set.
    }
    if model not in _TEMPERATURE_REJECTING_MODELS:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)


def is_llm_available() -> bool:
    """True iff langchain-anthropic is importable AND ANTHROPIC_API_KEY is set."""
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import langchain_anthropic  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def extract_usage_metadata(resp) -> dict:
    """Best-effort Anthropic token-usage counters from a langchain response.

    Returns a dict with input_tokens, cache_creation_input_tokens,
    cache_read_input_tokens, output_tokens (missing values -> 0). Purely
    observational (cost / prompt-cache hit-rate measurement); never raises,
    so it cannot affect the LLM call path or any trading behavior.

    Prefers the raw Anthropic ``usage`` block (``response_metadata['usage']``),
    where ``input_tokens`` is the uncached-input count and the two cache
    counters are separate/additive — matching Anthropic's native billing
    semantics. Falls back to langchain's normalized ``usage_metadata``.
    """
    out = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    }
    try:
        raw = (getattr(resp, "response_metadata", None) or {}).get("usage") or {}
        if raw:
            out["input_tokens"] = int(raw.get("input_tokens", 0) or 0)
            out["output_tokens"] = int(raw.get("output_tokens", 0) or 0)
            out["cache_creation_input_tokens"] = int(raw.get("cache_creation_input_tokens", 0) or 0)
            out["cache_read_input_tokens"] = int(raw.get("cache_read_input_tokens", 0) or 0)
            return out
        um = getattr(resp, "usage_metadata", None) or {}
        if um:
            out["input_tokens"] = int(um.get("input_tokens", 0) or 0)
            out["output_tokens"] = int(um.get("output_tokens", 0) or 0)
            details = um.get("input_token_details") or {}
            out["cache_creation_input_tokens"] = int(details.get("cache_creation", 0) or 0)
            out["cache_read_input_tokens"] = int(details.get("cache_read", 0) or 0)
    except Exception:
        pass
    return out
