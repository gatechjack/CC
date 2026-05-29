"""Item 6 + item 7: per-division Polymarket wallets.

Proves the per-division wallet refactor isolates divisions (distinct broker
instances with distinct keys, shared RPC), that arb still resolves to its
LEGACY env names (the no-regression guard for the deploy verification gate),
that unmapped divisions stub safely, that wallet values are redacted, and
that assert_live_ready's polymarket branch catches half/zero-configured
wallets without scope-creeping into balance/allowance checks.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.main import _build_broker_for_division
from trading_corp.utils import secrets as secrets_mod
from trading_corp.utils.secrets import assert_live_ready, load_secrets

# Realistic-length dummies (>16 chars so register_redact_literal accepts them).
ARB_PK = "0x" + "a" * 64
ARB_FUNDER = "0x" + "1" * 40
PCT_PK = "0x" + "b" * 64
PCT_FUNDER = "0x" + "2" * 40
RPC = "https://polygon-mainnet.example.invalid/v2/" + "k" * 24

_NONEXISTENT_ENV = Path(__file__).parent / "does_not_exist_.env"
_POLY_ENV_VARS = (
    "POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER_ADDRESS",
    "POLYMARKET_COPY_PRIVATE_KEY", "POLYMARKET_COPY_FUNDER_ADDRESS",
    "POLYGON_RPC_URL", "KEY_VAULT_URI",
)


def _clean_env(monkeypatch):
    """Ensure no ambient Polymarket/KV env leaks into a test."""
    for v in _POLY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def _load_with(monkeypatch, **env):
    _clean_env(monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # env_file points nowhere → no .env load; KEY_VAULT_URI deleted → no KV.
    return load_secrets(env_file=_NONEXISTENT_ENV)


def _poly_division(slug):
    return SimpleNamespace(broker="polymarket", slug=slug, account_filter="main")


def _both_wallets(monkeypatch):
    return _load_with(
        monkeypatch,
        POLYMARKET_PRIVATE_KEY=ARB_PK, POLYMARKET_FUNDER_ADDRESS=ARB_FUNDER,
        POLYMARKET_COPY_PRIVATE_KEY=PCT_PK, POLYMARKET_COPY_FUNDER_ADDRESS=PCT_FUNDER,
        POLYGON_RPC_URL=RPC, ANTHROPIC_API_KEY="sk-ant-test-0000000000",
    )


def test_load_secrets_builds_per_division_wallets(monkeypatch):
    s = _both_wallets(monkeypatch)
    assert set(s.polymarket_wallets) == {"polymarket_arbitrage", "polymarket_copy_trading"}
    assert s.polymarket_wallets["polymarket_arbitrage"].private_key == ARB_PK
    assert s.polymarket_wallets["polymarket_arbitrage"].funder_address == ARB_FUNDER
    assert s.polymarket_wallets["polymarket_copy_trading"].private_key == PCT_PK
    assert s.polymarket_wallets["polymarket_copy_trading"].funder_address == PCT_FUNDER
    assert s.polygon_rpc_url == RPC  # shared, not per-division


def test_factory_distinct_instances_and_keys(monkeypatch):
    s = _both_wallets(monkeypatch)
    arb = _build_broker_for_division(_poly_division("polymarket_arbitrage"), s, "PAPER", [])
    pct = _build_broker_for_division(_poly_division("polymarket_copy_trading"), s, "PAPER", [])
    assert arb is not pct
    assert arb._private_key == ARB_PK and arb._funder == ARB_FUNDER
    assert pct._private_key == PCT_PK and pct._funder == PCT_FUNDER
    assert arb._private_key != pct._private_key
    assert arb._funder != pct._funder
    assert arb._rpc_url == pct._rpc_url == RPC  # shared RPC
    assert arb._stub is False and pct._stub is False


def test_arb_resolves_to_legacy_keys(monkeypatch):
    """No-regression guard: arb must keep reading POLYMARKET_PRIVATE_KEY /
    POLYMARKET_FUNDER_ADDRESS (legacy names, migration option (i))."""
    s = _load_with(
        monkeypatch,
        POLYMARKET_PRIVATE_KEY=ARB_PK, POLYMARKET_FUNDER_ADDRESS=ARB_FUNDER,
        POLYGON_RPC_URL=RPC,
    )
    arb = _build_broker_for_division(_poly_division("polymarket_arbitrage"), s, "PAPER", [])
    assert arb._private_key == ARB_PK
    assert arb._funder == ARB_FUNDER
    assert arb._stub is False


def test_unmapped_division_stubs(monkeypatch):
    s = _both_wallets(monkeypatch)
    broker = _build_broker_for_division(_poly_division("polymarket_unknown"), s, "PAPER", [])
    assert broker._stub is True


def test_wallet_values_registered_for_redaction(monkeypatch):
    _both_wallets(monkeypatch)
    for literal in (ARB_PK, ARB_FUNDER, PCT_PK, PCT_FUNDER, RPC):
        assert literal in secrets_mod._REDACT_LITERALS


def test_assert_live_ready_passes_with_complete_wallet(monkeypatch):
    s = _both_wallets(monkeypatch)
    assert_live_ready(s, ("polymarket",))  # no raise


def test_assert_live_ready_rejects_half_configured_wallet(monkeypatch):
    # arb has a key but no funder → XOR → must fail loudly.
    s = _load_with(
        monkeypatch,
        POLYMARKET_PRIVATE_KEY=ARB_PK,  # funder intentionally absent
        POLYGON_RPC_URL=RPC, ANTHROPIC_API_KEY="sk-ant-test-0000000000",
    )
    with pytest.raises(RuntimeError, match="polymarket_arbitrage"):
        assert_live_ready(s, ("polymarket",))


def test_assert_live_ready_rejects_no_complete_wallet(monkeypatch):
    # No polymarket creds at all → no complete wallet → must fail.
    s = _load_with(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test-0000000000")
    with pytest.raises(RuntimeError, match="POLYMARKET"):
        assert_live_ready(s, ("polymarket",))


def test_assert_live_ready_ignores_polymarket_when_not_required(monkeypatch):
    # polymarket NOT in brokers_required → its (empty) wallet state is
    # irrelevant; only the always-on ANTHROPIC_API_KEY check runs.
    s = _load_with(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test-0000000000")
    assert_live_ready(s, ())  # no raise
