"""Redaction tests for `trading_corp.utils.secrets`.

Covers:
  - KEY=value pattern catches new Polymarket / Polygon env names.
  - register_redact_literal substitutes loaded secret VALUES anywhere
    they appear (the defense against third-party libs that log raw
    values without our key-name conventions).
  - RedactingFilter applies both passes end-to-end through the
    standard logging pipeline (record.msg + record.args).
  - No-op guards: empty/short literals are NOT registered (would
    risk false-positive redactions of common substrings).
"""
from __future__ import annotations

import logging

import pytest

from trading_corp.utils import secrets as secrets_mod
from trading_corp.utils.secrets import (
    RedactingFilter,
    _REDACT_LITERALS,
    redact,
    register_redact_literal,
)


@pytest.fixture(autouse=True)
def _reset_literals():
    """Each test starts + ends with no leaked literals from prior runs."""
    saved = set(_REDACT_LITERALS)
    _REDACT_LITERALS.clear()
    yield
    _REDACT_LITERALS.clear()
    _REDACT_LITERALS.update(saved)


# ── Key=value pattern ─────────────────────────────────────────────────


def test_polymarket_private_key_in_key_value_form_is_redacted():
    msg = "loaded POLYMARKET_PRIVATE_KEY=0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef from .env"
    out = redact(msg)
    assert "0x1234567890abcdef" not in out
    assert "POLYMARKET_PRIVATE_KEY=***REDACTED***" in out


def test_polymarket_funder_address_in_key_value_form_is_redacted():
    msg = "configured POLYMARKET_FUNDER_ADDRESS=0xabcdef0123456789abcdef0123456789abcdef01"
    out = redact(msg)
    assert "0xabcdef0123456789" not in out
    assert "POLYMARKET_FUNDER_ADDRESS=***REDACTED***" in out


def test_polygon_rpc_url_in_key_value_form_is_redacted():
    msg = "POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/abcdef-fake-api-key"
    out = redact(msg)
    assert "abcdef-fake-api-key" not in out
    assert "POLYGON_RPC_URL=***REDACTED***" in out


def test_existing_keys_still_redacted():
    """Sanity check — additive change must not break existing key redaction."""
    msg = "ANTHROPIC_API_KEY=sk-ant-xxxxx loaded"
    out = redact(msg)
    assert "sk-ant-xxxxx" not in out
    assert "ANTHROPIC_API_KEY=***REDACTED***" in out


# ── Literal-value substitution ────────────────────────────────────────


def test_register_redact_literal_substitutes_anywhere():
    """When a third-party lib logs the raw key value without a
    KEY_NAME= prefix, the literal-value redaction must catch it."""
    pk = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    register_redact_literal(pk)

    # Simulating py-clob-client DEBUG output: no env-var prefix at all.
    msg = f"signing transaction with private key {pk} via web3"
    out = redact(msg)
    assert pk not in out
    assert "***REDACTED***" in out


def test_register_redact_literal_inside_url_path_is_redacted():
    """Embedded API key inside an Alchemy RPC URL — the entire URL
    string is registered, so any logged URL gets scrubbed wholesale."""
    rpc = "https://polygon-mainnet.g.alchemy.com/v2/abcdef-fake-api-key"
    register_redact_literal(rpc)
    msg = f"connecting web3 provider at {rpc} for chain id 137"
    out = redact(msg)
    assert "abcdef-fake-api-key" not in out
    assert "***REDACTED***" in out


def test_register_redact_literal_short_value_is_ignored():
    """Strings under 16 chars are NOT registered — would risk
    false-positive redaction of common substrings like 'localhost'."""
    register_redact_literal("short")
    register_redact_literal("")
    register_redact_literal(None)
    msg = "the word short appears here normally"
    out = redact(msg)
    assert "short" in out
    assert "***REDACTED***" not in out


def test_register_redact_literal_idempotent():
    """Same value registered twice should remain a single set entry."""
    pk = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    register_redact_literal(pk)
    register_redact_literal(pk)
    assert len([x for x in _REDACT_LITERALS if x == pk]) == 1


# ── RedactingFilter integration ───────────────────────────────────────


def test_filter_redacts_record_msg():
    pk = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    register_redact_literal(pk)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0, msg=f"signing with {pk}", args=None, exc_info=None,
    )
    f = RedactingFilter()
    assert f.filter(record) is True
    assert pk not in record.msg
    assert "***REDACTED***" in record.msg


def test_filter_redacts_record_args():
    pk = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    register_redact_literal(pk)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0, msg="key was %s", args=(pk,), exc_info=None,
    )
    f = RedactingFilter()
    assert f.filter(record) is True
    # After filter, args should have the literal redacted.
    assert pk not in record.args[0]
    assert "***REDACTED***" in record.args[0]


def test_filter_handles_non_str_args_without_error():
    """Filter must not break on int/float/dict args — keep the existing
    permissive behavior."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0, msg="qty=%s price=%s", args=(0.5, 80000.0), exc_info=None,
    )
    f = RedactingFilter()
    assert f.filter(record) is True
    assert record.args == (0.5, 80000.0)


def test_filter_redacts_both_passes_in_one_record():
    """A record can have both a KEY=value form and a raw value — both
    must be scrubbed in a single pass."""
    pk = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    register_redact_literal(pk)
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0,
        msg=f"loaded POLYMARKET_PRIVATE_KEY=foo and signed with {pk}",
        args=None, exc_info=None,
    )
    f = RedactingFilter()
    f.filter(record)
    assert "foo" not in record.msg
    assert pk not in record.msg
    assert record.msg.count("***REDACTED***") == 2


# ── load_secrets registers bitunix live-trading creds as literals ──────
# C-1 rotation: the bitunix api-key VALUE rides in the `api-key` request
# header on every signed call, so it must be redactable even without a
# `KEY=` prefix (which the api-key/sign header form lacks). Defense-in-depth
# matching the other live-money keys (polymarket/kalshi/tastytrade).
#
# SECURITY NOTE: this test must NOT assert membership in any collection that
# also holds *real* loaded secrets — a failed `x in _REDACT_LITERALS` makes
# pytest repr the whole set, dumping real secret values into the transcript.
# So we monkeypatch `register_redact_literal` to capture calls into a LOCAL
# list and assert with pre-computed booleans (pytest reprs the bool, not the
# list). No real secret value can reach an assert expression here.


def test_load_secrets_registers_bitunix_literals(monkeypatch, tmp_path):
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)  # no Azure calls in test
    fake_key = "FAKEbitunixkey_0123456789abcdef"      # >=16 chars; NOT a real secret
    fake_secret = "FAKEbitunixsecret_0123456789abcdef"
    monkeypatch.setenv("BITUNIX_FUTURES_API_KEY", fake_key)
    monkeypatch.setenv("BITUNIX_FUTURES_API_SECRET", fake_secret)

    registered: list[str | None] = []
    monkeypatch.setattr(
        secrets_mod, "register_redact_literal", registered.append,
    )
    # Nonexistent env_file → the real .env is never loaded.
    secrets_mod.load_secrets(env_file=tmp_path / "nonexistent.env")

    # Pre-compute booleans so the assert never reprs `registered` (which may
    # hold real secret values pulled from the process env).
    key_registered = fake_key in registered
    secret_registered = fake_secret in registered
    assert key_registered, "load_secrets did not register the bitunix api_key as a redact literal"
    assert secret_registered, "load_secrets did not register the bitunix api_secret as a redact literal"
