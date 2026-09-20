"""load_secrets wires the third Kalshi account (kalshi_marc, 2026-09-20).

Exact mirror of the karen fields: KALSHI_MARC_API_KEY_ID /
KALSHI_MARC_PRIVATE_KEY_PEM env (KALSHI-MARC-* in KV) -> the kalshi_marc_*
Secrets fields, and the private-key PEM is registered as a redact literal
(defense-in-depth, same as karen / jack).

SECURITY NOTE (same discipline as test_secrets_redaction.py): never assert
membership in a collection that also holds REAL loaded secrets -- a failed
`x in set` reprs the whole set into the transcript, dumping real values. We
monkeypatch register_redact_literal into a LOCAL list and assert a
pre-computed boolean only. The env values used here are FAKE (not real keys).
"""
from __future__ import annotations

from trading_corp.utils import secrets as secrets_mod


def test_load_secrets_populates_marc_fields(monkeypatch, tmp_path):
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)  # no Azure/KV calls in test
    fake_kid = "FAKEmarc_api_key_id_0123456789abcdef"   # >=16 chars; NOT a real secret
    fake_pem = "FAKEmarc_private_key_pem_0123456789abcdef"
    monkeypatch.setenv("KALSHI_MARC_API_KEY_ID", fake_kid)
    monkeypatch.setenv("KALSHI_MARC_PRIVATE_KEY_PEM", fake_pem)

    # Nonexistent env_file -> the real .env is never loaded.
    s = secrets_mod.load_secrets(env_file=tmp_path / "nonexistent.env")
    assert s.kalshi_marc_api_key_id == fake_kid
    assert s.kalshi_marc_private_key_pem == fake_pem


def test_load_secrets_registers_marc_pem_literal(monkeypatch, tmp_path):
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    fake_pem = "FAKEmarc_private_key_pem_0123456789abcdef"
    monkeypatch.setenv("KALSHI_MARC_PRIVATE_KEY_PEM", fake_pem)

    registered: list = []
    monkeypatch.setattr(secrets_mod, "register_redact_literal", registered.append)
    secrets_mod.load_secrets(env_file=tmp_path / "nonexistent.env")

    # Pre-compute the boolean so the assert never reprs `registered` (which may
    # hold real secret values pulled from the process env on the box).
    pem_registered = fake_pem in registered
    assert pem_registered, "load_secrets did not register the kalshi_marc PEM as a redact literal"


def test_resolve_kalshi_keys_end_to_end_against_real_loaded_secrets(monkeypatch, tmp_path):
    """END-TO-END linkage: the attr-name STRINGS in _SECRET_REF_KEYPAIR must
    match REAL Secrets dataclass fields. A SimpleNamespace stub can't prove this
    (it would mask a typo like 'kalshi_marc_api_key_id_X'). Load marc's (fake)
    creds through the real dataclass, resolve through the whitelist, and assert
    we get marc's OWN values back -- and NOT jack's. This is the guard that a
    typo'd attr name in the whitelist (-> silent (None,None) -> marc skipped)
    would otherwise slip past. Asserts only touch fake values (never reprs real
    secrets)."""
    from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys

    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    fake_kid = "FAKEmarc_api_key_id_0123456789abcdef"
    fake_pem = "FAKEmarc_private_key_pem_0123456789abcdef"
    monkeypatch.setenv("KALSHI_MARC_API_KEY_ID", fake_kid)
    monkeypatch.setenv("KALSHI_MARC_PRIVATE_KEY_PEM", fake_pem)

    s = secrets_mod.load_secrets(env_file=tmp_path / "nonexistent.env")
    kid, pem = resolve_kalshi_keys("kalshi_marc", s)
    assert (kid, pem) == (fake_kid, fake_pem)
