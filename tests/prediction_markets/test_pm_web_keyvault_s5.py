"""Stage 5 e3: pm_web's SCOPED Anthropic-only Key Vault fetch (`scripts/pm_web.py`). Proves the least-privilege
ruling holds -- it fetches EXACTLY ONE secret (ANTHROPIC-API-KEY), NEVER load_secrets() (which would pull the whole
trading-key set into a network-facing web process) -- and that it FAILS SOFT on every failure mode (no vault, no
azure libs, a raised fetch) so pm_web always boots and Analyze just degrades to llm_unavailable. No real Azure:
fake azure modules are injected for the success/failure paths.
"""
import os
import sys
import types

from trading_corp.scripts import pm_web


def _inject_azure(monkeypatch, cred_cls, client_cls):
    azure = types.ModuleType("azure")
    ident = types.ModuleType("azure.identity"); ident.DefaultAzureCredential = cred_cls
    keyvault = types.ModuleType("azure.keyvault")
    secrets = types.ModuleType("azure.keyvault.secrets"); secrets.SecretClient = client_cls
    azure.identity = ident; azure.keyvault = keyvault; keyvault.secrets = secrets
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.identity", ident)
    monkeypatch.setitem(sys.modules, "azure.keyvault", keyvault)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", secrets)


class _Cred:
    def __init__(self, *a, **k):
        pass


def test_env_precedence_skips_fetch(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-existing")
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example/")

    class _Boom:                                                  # would raise if constructed -> proves NOT called
        def __init__(self, *a, **k):
            raise AssertionError("must not construct a KV client when the key is already in env")

    _inject_azure(monkeypatch, _Boom, _Boom)
    pm_web._load_anthropic_key_from_keyvault()                    # must not raise
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-existing"      # untouched (env precedence)


def test_no_vault_uri_is_fail_soft(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    pm_web._load_anthropic_key_from_keyvault()                    # no vault -> no-op, no raise
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_scoped_fetch_sets_only_the_anthropic_secret(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example/")
    requested = []

    class _Secret:
        def __init__(self, v):
            self.value = v

    class _Client:
        def __init__(self, *, vault_url, credential):
            pass

        def get_secret(self, name):
            requested.append(name)
            return _Secret("sk-from-kv")

    _inject_azure(monkeypatch, _Cred, _Client)
    pm_web._load_anthropic_key_from_keyvault()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-kv"       # loaded
    assert requested == ["ANTHROPIC-API-KEY"]                    # EXACTLY ONE secret -- never the full load_secrets set


def test_fetch_error_is_fail_soft(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example/")

    class _FailClient:
        def __init__(self, **k):
            pass

        def get_secret(self, name):
            raise RuntimeError("key vault unreachable")

    _inject_azure(monkeypatch, _Cred, _FailClient)
    pm_web._load_anthropic_key_from_keyvault()                    # a raised fetch must NOT propagate
    assert "ANTHROPIC_API_KEY" not in os.environ                 # degrades to unavailable, pm_web still boots


def test_does_not_import_load_secrets():
    """Structural guard against a future 'simplify to load_secrets()': the launcher must never bind load_secrets in
    its namespace (the docstring MENTIONS it to warn against it -- that is intentional; a real import would be the
    regression). It does pull the one scoped secret name."""
    assert not hasattr(pm_web, "load_secrets")                   # never imported -> can't call the full secret loader
    import inspect
    assert "ANTHROPIC-API-KEY" in inspect.getsource(pm_web)      # the one secret name it does pull
