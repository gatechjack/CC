"""Tests for main.py helpers and config wiring added in 2026-06-25 SFP edits.

Covers:
  - _resolve_bitunix_creds
  - _live_bitunix_divisions
  - BitunixSfpConfig.from_dict
  - divisions.yaml has bitunix_sfp with secret_ref == "bitunix_futures"
"""
import types
import yaml
import pytest

from trading_corp.main import _resolve_bitunix_creds, _live_bitunix_divisions
from trading_corp.agents.divisions.bitunix_sfp_observer import BitunixSfpConfig


# ── _resolve_bitunix_creds ──────────────────────────────────────────────────

def _secrets_stub():
    return types.SimpleNamespace(
        bitunix_futures_api_key="K",
        bitunix_futures_api_secret="S",
    )


def test_resolve_creds_explicit_ref():
    div = types.SimpleNamespace(secret_ref="bitunix_futures")
    key, secret = _resolve_bitunix_creds(div, _secrets_stub())
    assert key == "K"
    assert secret == "S"


def test_resolve_creds_none_ref_falls_back():
    div = types.SimpleNamespace(secret_ref=None)
    key, secret = _resolve_bitunix_creds(div, _secrets_stub())
    assert key == "K"
    assert secret == "S"


def test_resolve_creds_missing_attr_falls_back():
    # Division without a secret_ref attribute at all
    div = types.SimpleNamespace()
    key, secret = _resolve_bitunix_creds(div, _secrets_stub())
    assert key == "K"
    assert secret == "S"


# ── _live_bitunix_divisions ─────────────────────────────────────────────────

def _make_divs():
    return [
        types.SimpleNamespace(slug="bitunix_a", broker="bitunix", enabled=True),
        types.SimpleNamespace(slug="bitunix_b", broker="bitunix", enabled=True),
        types.SimpleNamespace(slug="x", broker="coinbase", enabled=True),
    ]


def test_live_bitunix_divisions_one_live(tmp_path):
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "bitunix_a:\n  execution_mode: live\n"
        "bitunix_b:\n  execution_mode: paper\n",
        encoding="utf-8",
    )
    result = _live_bitunix_divisions(_make_divs(), strategies_yaml_path=str(yaml_path))
    assert result == ["bitunix_a"]


def test_live_bitunix_divisions_two_live(tmp_path):
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "bitunix_a:\n  execution_mode: live\n"
        "bitunix_b:\n  execution_mode: live\n",
        encoding="utf-8",
    )
    result = _live_bitunix_divisions(_make_divs(), strategies_yaml_path=str(yaml_path))
    assert set(result) == {"bitunix_a", "bitunix_b"}
    assert len(result) == 2


def test_live_bitunix_divisions_none_live(tmp_path):
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "bitunix_a:\n  execution_mode: paper\n"
        "bitunix_b:\n  execution_mode: paper\n",
        encoding="utf-8",
    )
    result = _live_bitunix_divisions(_make_divs(), strategies_yaml_path=str(yaml_path))
    assert result == []


def test_live_bitunix_divisions_skips_non_bitunix(tmp_path):
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "x:\n  execution_mode: live\n"
        "bitunix_a:\n  execution_mode: paper\n",
        encoding="utf-8",
    )
    result = _live_bitunix_divisions(_make_divs(), strategies_yaml_path=str(yaml_path))
    assert result == []


def test_live_bitunix_divisions_read_failure_returns_empty(tmp_path):
    # Non-existent file → fail-safe []
    result = _live_bitunix_divisions(
        _make_divs(), strategies_yaml_path=str(tmp_path / "nonexistent.yaml")
    )
    assert result == []


# ── BitunixSfpConfig.from_dict ─────────────────────────────────────────────

def test_sfp_config_from_dict_explicit():
    cfg = BitunixSfpConfig.from_dict({
        "symbols": ["BTC/USDT.P"],
        "risk_pct_real": 0.007,
        "risk_pct_considerable": 0.003,
        "execution_mode": "live",
    })
    assert cfg.symbols == ("BTC/USDT.P",)
    assert cfg.risk_pct_real == pytest.approx(0.007)
    assert cfg.risk_pct_considerable == pytest.approx(0.003)
    assert cfg.execution_mode == "live"


def test_sfp_config_from_dict_none_defaults():
    cfg = BitunixSfpConfig.from_dict(None)
    assert cfg.symbols == ("BTC/USDT.P",)
    assert cfg.execution_mode == "paper"
    assert cfg.risk_pct_real == pytest.approx(0.005)
    assert cfg.risk_pct_considerable == pytest.approx(0.005)


# ── divisions.yaml has bitunix_sfp ─────────────────────────────────────────

def test_divisions_yaml_has_bitunix_sfp():
    from trading_corp.utils.divisions import load_divisions
    divs = load_divisions()
    sfp = next((d for d in divs if d.slug == "bitunix_sfp"), None)
    assert sfp is not None, "bitunix_sfp not found in divisions.yaml"
    assert sfp.secret_ref == "bitunix_futures"
    assert sfp.broker == "bitunix"
    assert sfp.enabled is True
