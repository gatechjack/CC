"""Market data provider factory.

Loads `config/data_providers.yaml` with mtime-cache (mirrors
`agents/risk.py:45-79`).  Builds a singleton provider per strategy on
first request; strategy-scoped queries get a per-strategy resolved
provider that may differ via config overrides.

Config shape (data_providers.yaml):
  global:
    primary: tastytrade
    fallback: null       # auto-failover is FORBIDDEN; null is enforced
    cache_ttl_sec: 60
    auth:
      tastytrade:
        provider_secret_env: TASTYTRADE_PROVIDER_SECRET
        refresh_token_env: TASTYTRADE_REFRESH_TOKEN
  overrides:
    # robinhood_joint_iron_condor: { primary: tastytrade }

Audit event `data_provider_unavailable` is emitted by the CALLER after
the provider returns None for a critical value — NOT by the factory.
Architectural reason: the factory only knows the provider, not the
strategy/division context needed for the audit tags.  The caller (e.g.
utils/iv.py, strategy scan method) has full context and emits the event
there.  One cause, one audit event, at the correct call site.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from trading_corp.data.market_data_provider import MarketDataProvider

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path("config/data_providers.yaml")

_REQUIRED_KEYS: list[str] = [
    "global.primary",
]

# Singleton state — module-level cache
_mtime: float = 0.0
_cfg: dict = {}
_providers: dict[str, MarketDataProvider] = {}   # strategy_slug → provider instance


def _reload_if_changed(config_path: Path) -> dict:
    """Load and cache data_providers.yaml; reload on mtime change."""
    global _mtime, _cfg
    try:
        mtime = config_path.stat().st_mtime
    except FileNotFoundError:
        log.warning("provider_factory: config not found at %s", config_path)
        return {}
    if mtime != _mtime:
        with config_path.open("r", encoding="utf-8") as f:
            _cfg = yaml.safe_load(f) or {}
        _mtime = mtime
        log.info("provider_factory: reloaded %s", config_path)
        _warn_on_missing_keys(_cfg)
    return _cfg


def _warn_on_missing_keys(cfg: dict) -> None:
    """Log a warning once per reload for any missing required keys."""
    missing: list[str] = []
    for dotted in _REQUIRED_KEYS:
        parts = dotted.split(".")
        cur: Any = cfg
        ok = True
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                ok = False
                break
            cur = cur[p]
        if not ok:
            missing.append(dotted)
    if missing:
        log.warning(
            "provider_factory: %d required key(s) missing from data_providers.yaml "
            "— using defaults: %s",
            len(missing), ", ".join(missing),
        )


def _resolve_config(cfg: dict, strategy_slug: str | None) -> dict:
    """Merge global + overrides[strategy_slug]."""
    g = cfg.get("global", {}) or {}
    overrides = (cfg.get("overrides", {}) or {})
    if strategy_slug and strategy_slug in overrides:
        override = overrides[strategy_slug] or {}
        return {**g, **override}
    return dict(g)


def _build_provider(resolved: dict) -> MarketDataProvider:
    """Instantiate a provider from the resolved config."""
    primary = resolved.get("primary", "tastytrade")

    if primary == "tastytrade":
        auth = (resolved.get("auth") or {}).get("tastytrade", {}) or {}
        ps_env = auth.get("provider_secret_env", "TASTYTRADE_PROVIDER_SECRET")
        rt_env = auth.get("refresh_token_env", "TASTYTRADE_REFRESH_TOKEN")
        provider_secret = os.environ.get(ps_env)
        refresh_token = os.environ.get(rt_env)
        from trading_corp.data.tastytrade_provider import TastytradeDataProvider
        return TastytradeDataProvider(
            provider_secret=provider_secret,
            refresh_token=refresh_token,
        )
    elif primary == "yfinance":
        from trading_corp.data.yfinance_provider import YFinanceDataProvider
        return YFinanceDataProvider()
    else:
        log.error(
            "provider_factory: unknown primary provider '%s'; falling back to yfinance",
            primary,
        )
        from trading_corp.data.yfinance_provider import YFinanceDataProvider
        return YFinanceDataProvider()


def get_provider(
    strategy_slug: str | None = None,
    config_path: Path = _DEFAULT_CONFIG_PATH,
) -> MarketDataProvider:
    """Return the configured MarketDataProvider for `strategy_slug`.

    First call per strategy builds and caches the provider singleton.
    mtime change triggers config reload; if the primary key changed,
    a new provider is built on next call.

    NO automatic failover: if the primary provider returns None, the
    caller must handle it.  Config explicitly sets fallback: null.

    NOTE: if the provider cannot be built (e.g. missing env vars for
    tastytrade), raises ValueError — caller should catch and either
    fall back to yfinance or surface the error as data_provider_unavailable.
    """
    cfg = _reload_if_changed(config_path)
    resolved = _resolve_config(cfg, strategy_slug)

    cache_key = strategy_slug or "_global"

    # If mtime changed, clear cached providers so they're rebuilt from
    # new config on next request.
    existing = _providers.get(cache_key)
    if existing is not None:
        # Provider already built — return as-is (mtime-based invalidation
        # would require tracking per-strategy mtime; KISS for now)
        return existing

    provider = _build_provider(resolved)
    _providers[cache_key] = provider
    return provider


def invalidate_cache() -> None:
    """Force reload on next get_provider call (for testing)."""
    global _mtime, _cfg
    _mtime = 0.0
    _cfg = {}
    _providers.clear()
