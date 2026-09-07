#!/usr/bin/env python3
"""Launch pm_web (uvicorn). Standalone: NO WebDeps, NO engine imports; reads only prediction_markets.db.

On the box it runs via the systemd unit (prediction-markets-web.service). Locally / manually:
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_web.py
Host/port from PM_WEB_HOST / PM_WEB_PORT (default 127.0.0.1:8081 -- loopback-only, so pm_web is reachable ONLY
via Caddy+Authelia, never directly, unlike the engine dashboard on 0.0.0.0:8000).

Stage 5 (e3): before serving, pm_web does a SCOPED Anthropic-only Key Vault fetch so the Analyze narrator has its
key. This is DELIBERATELY not load_secrets() -- see `_load_anthropic_key_from_keyvault` for why (least privilege);
it FAILS SOFT so a missing/unreachable key never blocks the web process from booting.
Spec: reports/prediction_markets/P2_PLAN.md §3.1, §12.
"""
from __future__ import annotations

import logging
import os

# KV secret NAME for the Anthropic key. Azure Key Vault forbids underscores in secret names, so the env var
# ANTHROPIC_API_KEY is stored as ANTHROPIC-API-KEY (the same translation utils/secrets._populate_from_keyvault does).
_ANTHROPIC_SECRET_NAME = "ANTHROPIC-API-KEY"


def _load_anthropic_key_from_keyvault() -> None:
    """SCOPED, Anthropic-only Key Vault fetch for pm_web's Analyze narrator (Stage 5, e3).

    ★ DELIBERATELY NOT `utils.secrets.load_secrets()`. DO NOT "simplify" this into load_secrets() (Jack ruled
    2026-08-31). load_secrets() pulls the ENTIRE secret set -- Polymarket / Kalshi / Bitunix PRIVATE KEYS,
    Robinhood credentials, everything in `expected_env_vars` -- into os.environ. The engine calls it because the
    engine TRADES and needs those. pm_web imports NO broker and places NO orders; it needs EXACTLY ONE secret, the
    Anthropic key, to narrate. Loading live trading keys into a network-facing (Authelia-fronted) web process would
    widen its blast radius on an RCE from "read the PM database" to "trade the accounts" -- a real least-privilege
    regression, and not worth it for one API key. So we fetch that ONE secret and nothing else.

    ★ FAIL SOFT -- the OPPOSITE of the engine's assert_live_ready posture, and correct here. If KEY_VAULT_URI is
    unset, the azure libs are missing, or the fetch raises, we LOG ONCE and RETURN -- pm_web still boots and Analyze
    degrades to the existing `llm_unavailable` reasoned-null (the deterministic report + loss-completeness block
    still render). A web process that refuses to start because a narration key is unreachable is worse than one
    that narrates nothing. The engine refuses to trade blind; pm_web only refuses to narrate.

    Env precedence: an ANTHROPIC_API_KEY already in the process env wins (a local override), matching load_secrets().
    The outcome is logged once so "no narration" is diagnosable rather than silent.
    """
    log = logging.getLogger("pm_web.keyvault")
    if os.getenv("ANTHROPIC_API_KEY"):
        log.info("pm_web: ANTHROPIC_API_KEY already present in env -- Key Vault fetch skipped (narration enabled).")
        return
    vault_uri = os.getenv("KEY_VAULT_URI")
    if not vault_uri:
        log.info("pm_web: KEY_VAULT_URI unset -- no Key Vault fetch; Analyze narration stays unavailable "
                 "(deterministic report + loss-completeness still render).")
        return
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        log.warning("pm_web: azure-identity / azure-keyvault-secrets not installed -- narration unavailable.")
        return
    try:
        credential = DefaultAzureCredential()                      # VM system-assigned Managed Identity on the box
        client = SecretClient(vault_url=vault_uri, credential=credential)
        secret = client.get_secret(_ANTHROPIC_SECRET_NAME)
        if secret and secret.value:
            os.environ["ANTHROPIC_API_KEY"] = secret.value
            log.info("pm_web: ANTHROPIC_API_KEY loaded from Key Vault (%s) -- Analyze narration ENABLED.", vault_uri)
        else:
            log.warning("pm_web: Key Vault returned an empty %s -- narration unavailable.", _ANTHROPIC_SECRET_NAME)
    except Exception as exc:   # noqa: BLE001 -- fail soft: a narration key must never block the web process booting
        log.warning("pm_web: Key Vault fetch of %s failed (%s) -- narration unavailable; pm_web boots normally.",
                    _ANTHROPIC_SECRET_NAME, type(exc).__name__)


def main() -> int:
    import uvicorn  # lazy: offline unit tests import the app directly, never this launcher
    logging.basicConfig(level=logging.INFO)                        # so the one-line KV outcome reaches the journal
    _load_anthropic_key_from_keyvault()                            # SCOPED + fail-soft; before serving (narrate key)
    host = os.environ.get("PM_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("PM_WEB_PORT", "8081"))
    uvicorn.run("trading_corp.prediction_markets.web.app:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
