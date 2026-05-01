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


def build_chat_model(agent_name: str, *, max_tokens: int = 1024):
    """Return a langchain_anthropic.ChatAnthropic instance for `agent_name`.

    Imported lazily so test envs without langchain-anthropic still import the
    package (callers should fall back to deterministic-only behavior).
    """
    try:
        from langchain_anthropic import ChatAnthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "langchain-anthropic is required for LLM agents. "
            "pip install langchain-anthropic"
        ) from e

    model, temperature = get_model_for(agent_name)
    return ChatAnthropic(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        # Prompt caching is configured per-message via cache_control headers
        # in callers; this constructor honors them when set.
    )


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
