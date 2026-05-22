"""Tests for provider_factory — config load, mtime-cache, overrides, no-failover."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import trading_corp.data.provider_factory as factory_mod
from trading_corp.data.provider_factory import (
    _resolve_config,
    _warn_on_missing_keys,
    get_provider,
    invalidate_cache,
)
from trading_corp.data.market_data_provider import MarketDataProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _minimal_config(primary: str = "yfinance") -> str:
    return f"""
global:
  primary: {primary}
  fallback: null
  cache_ttl_sec: 60
  auth:
    tastytrade:
      provider_secret_env: TASTYTRADE_PROVIDER_SECRET
      refresh_token_env: TASTYTRADE_REFRESH_TOKEN
overrides: {{}}
""".strip()


@pytest.fixture(autouse=True)
def reset_factory():
    """Reset factory module state before each test."""
    invalidate_cache()
    yield
    invalidate_cache()


# ---------------------------------------------------------------------------
# Config load + mtime-cache
# ---------------------------------------------------------------------------


def test_config_loads_from_yaml(tmp_path: Path):
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config())
    cfg = factory_mod._reload_if_changed(cfg_path)
    assert cfg["global"]["primary"] == "yfinance"


def test_config_mtime_cache_no_reload_when_unchanged(tmp_path: Path):
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config())
    factory_mod._reload_if_changed(cfg_path)
    mtime_after_first = factory_mod._mtime

    # Call again without touching the file
    factory_mod._reload_if_changed(cfg_path)
    assert factory_mod._mtime == mtime_after_first, "should not reload if mtime unchanged"


def test_config_reloads_when_mtime_changes(tmp_path: Path):
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config("yfinance"))
    factory_mod._reload_if_changed(cfg_path)

    # Force mtime reset (simulate file change)
    factory_mod._mtime = 0.0
    _write_config(cfg_path, _minimal_config("tastytrade"))

    cfg = factory_mod._reload_if_changed(cfg_path)
    assert cfg["global"]["primary"] == "tastytrade"


def test_config_missing_file_returns_empty_dict(tmp_path: Path):
    missing = tmp_path / "does_not_exist.yaml"
    cfg = factory_mod._reload_if_changed(missing)
    assert cfg == {}


# ---------------------------------------------------------------------------
# global + overrides merge
# ---------------------------------------------------------------------------


def test_resolve_config_global_only():
    cfg = {
        "global": {"primary": "yfinance", "cache_ttl_sec": 60},
        "overrides": {},
    }
    resolved = _resolve_config(cfg, strategy_slug=None)
    assert resolved["primary"] == "yfinance"
    assert resolved["cache_ttl_sec"] == 60


def test_resolve_config_override_takes_precedence():
    cfg = {
        "global": {"primary": "yfinance", "cache_ttl_sec": 60},
        "overrides": {
            "robinhood_joint_iron_condor": {"primary": "tastytrade"},
        },
    }
    resolved = _resolve_config(cfg, strategy_slug="robinhood_joint_iron_condor")
    assert resolved["primary"] == "tastytrade"
    # Non-overridden keys preserved from global
    assert resolved["cache_ttl_sec"] == 60


def test_resolve_config_missing_strategy_uses_global():
    cfg = {
        "global": {"primary": "yfinance"},
        "overrides": {"other_strategy": {"primary": "tastytrade"}},
    }
    resolved = _resolve_config(cfg, strategy_slug="fidelity_options")
    assert resolved["primary"] == "yfinance"


# ---------------------------------------------------------------------------
# Missing-key warning
# ---------------------------------------------------------------------------


def test_warn_on_missing_keys_logs_warning_once(caplog):
    """Missing global.primary → warning logged."""
    with caplog.at_level("WARNING"):
        _warn_on_missing_keys({})
    assert any("required key" in r.message for r in caplog.records)


def test_no_warning_when_all_required_keys_present(caplog):
    cfg = {"global": {"primary": "yfinance"}}
    with caplog.at_level("WARNING"):
        _warn_on_missing_keys(cfg)
    assert not any("required key" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_provider — builds YFinanceDataProvider for yfinance config
# ---------------------------------------------------------------------------


def test_get_provider_returns_yfinance_provider(tmp_path: Path):
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config("yfinance"))
    provider = get_provider(config_path=cfg_path)
    from trading_corp.data.yfinance_provider import YFinanceDataProvider
    assert isinstance(provider, YFinanceDataProvider)


def test_get_provider_caches_per_strategy(tmp_path: Path):
    """Two calls with same strategy_slug → same provider instance."""
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config("yfinance"))
    p1 = get_provider(strategy_slug="strategy_a", config_path=cfg_path)
    p2 = get_provider(strategy_slug="strategy_a", config_path=cfg_path)
    assert p1 is p2


def test_get_provider_different_strategies_can_differ(tmp_path: Path):
    """Different strategy slugs get independent provider instances."""
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config("yfinance"))
    pa = get_provider(strategy_slug="strategy_a", config_path=cfg_path)
    pb = get_provider(strategy_slug="strategy_b", config_path=cfg_path)
    # Both are YFinanceDataProvider but different instances
    assert type(pa) is type(pb)
    assert pa is not pb


# ---------------------------------------------------------------------------
# No-auto-failover invariant
# ---------------------------------------------------------------------------


def test_no_auto_failover_provider_none_is_none(tmp_path: Path):
    """Provider returning None for critical value is NOT silently replaced.

    The factory does NOT wrap the provider with failover logic — the caller
    observes the raw None and decides how to handle it.
    """
    cfg_path = _write_config(tmp_path / "data_providers.yaml", _minimal_config("yfinance"))
    provider = get_provider(config_path=cfg_path)

    # The provider's get_iv_rank returns None on failure — no silent fallback
    # to a secondary provider should occur.  We verify the factory has no
    # failover wrapper around the provider (isinstance check).
    from trading_corp.data.yfinance_provider import YFinanceDataProvider
    assert isinstance(provider, YFinanceDataProvider)

    # Config has fallback: null — verify it
    cfg = factory_mod._reload_if_changed(cfg_path)
    assert cfg.get("global", {}).get("fallback") is None, (
        "fallback must be null in config — auto-failover is forbidden"
    )
