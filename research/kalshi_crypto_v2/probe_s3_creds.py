"""S3 credential probe (REDACTED). Confirms KV secrets are fetchable from this
machine via DefaultAzureCredential (az-cli chain). Prints ONLY presence + length
+ a fingerprint (first 4 sha256 hex) so a value is never revealed. No DB writes.

Usage: python research/kalshi_crypto_v2/probe_s3_creds.py
"""
from __future__ import annotations

import hashlib
import os

VAULT = os.getenv("KEY_VAULT_URI") or "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
SECRETS = ["coinalyze-api-key", "KALSHI-KAREN-API-KEY-ID", "KALSHI-KAREN-PRIVATE-KEY-PEM"]


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:4]


def main() -> int:
    print(f"vault: {VAULT}")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:
        print(f"STOP azure SDK: {e}")
        return 2
    try:
        client = SecretClient(vault_url=VAULT, credential=DefaultAzureCredential())
    except Exception as e:  # noqa: BLE001
        print(f"STOP client: {type(e).__name__}: {str(e)[:120]}")
        return 2
    for name in SECRETS:
        try:
            v = client.get_secret(name).value or ""
            print(f"  {name:30} present len={len(v):>4} fp={fp(v)}")
        except Exception as e:  # noqa: BLE001
            print(f"  {name:30} FAIL {type(e).__name__}: {str(e)[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
