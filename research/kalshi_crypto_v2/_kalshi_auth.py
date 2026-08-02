"""Shared in-memory Kalshi auth + signed REST GET for kalshi_crypto_v2 research.

READ-ONLY. Creds are fetched at runtime from Azure Key Vault (house pattern,
DefaultAzureCredential + SecretClient) or an env override; the PEM stays in
memory and is NEVER written to disk. RSA-PSS request signing replicates
pykalshi _base (message = f"{ts}{method}{path}", PSS/SHA256, salt=digest).
The signed path is the request path WITHOUT query (per pykalshi _get_headers).

No order/placement code — pure read helpers.
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

REST_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_API_PATH = "/trade-api/v2"
_CTX = ssl.create_default_context()


class KalshiAuthError(Exception):
    """Cred/auth/REST failure — messages carry no secret values."""


def load_creds(prefix: str = "KALSHI_KAREN") -> tuple[str, str, str]:
    """Return (api_key_id, private_key_pem, source). In-memory only, no disk.
    Order: env override, then Azure Key Vault via DefaultAzureCredential. Fails
    loudly (KalshiAuthError); never falls back to files."""
    kid = os.getenv(f"{prefix}_API_KEY_ID")
    pem = os.getenv(f"{prefix}_PRIVATE_KEY_PEM")
    if kid and pem:
        return kid, pem.replace("\\n", "\n"), "env-override"
    vault = os.getenv("KEY_VAULT_URI")
    if not vault:
        raise KalshiAuthError("KEY_VAULT_URI not set and env creds absent")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:
        raise KalshiAuthError(f"azure SDK missing: {e}")
    try:
        client = SecretClient(vault_url=vault, credential=DefaultAzureCredential())
        hy = prefix.replace("_", "-")
        kid = client.get_secret(f"{hy}-API-KEY-ID").value
        pem = client.get_secret(f"{hy}-PRIVATE-KEY-PEM").value
    except Exception as e:
        raise KalshiAuthError(f"Key Vault fetch failed ({type(e).__name__}: {str(e)[:160]})")
    if not kid or not pem:
        raise KalshiAuthError(f"Key Vault returned empty {prefix} secret(s)")
    return kid, pem.replace("\\n", "\n"), "keyvault"


def make_signer(pem: str):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(pem.encode(), password=None)

    def sign(method: str, path: str) -> tuple[str, str]:
        ts = str(int(time.time() * 1000))
        sig = key.sign(f"{ts}{method}{path}".encode(),
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                   salt_length=padding.PSS.DIGEST_LENGTH),
                       hashes.SHA256())
        return ts, base64.b64encode(sig).decode()

    return sign


class KalshiRest:
    """Minimal signed GET client. Read-only; holds creds in memory."""

    def __init__(self, prefix: str = "KALSHI_KAREN") -> None:
        self.kid, pem, self.source = load_creds(prefix)
        self._sign = make_signer(pem)

    def get(self, endpoint: str, params: dict | None = None, retries: int = 4) -> dict:
        path = _API_PATH + endpoint                      # signed path (no query)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        qs = ("?" + urllib.parse.urlencode(clean)) if clean else ""
        last = ""
        for attempt in range(retries):
            ts, sig = self._sign("GET", path)            # fresh signature each attempt
            req = urllib.request.Request(
                REST_BASE + endpoint + qs, method="GET",
                headers={"KALSHI-ACCESS-KEY": self.kid, "KALSHI-ACCESS-SIGNATURE": sig,
                         "KALSHI-ACCESS-TIMESTAMP": ts, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429 or 500 <= e.code < 600:     # transient: retry
                    last = f"HTTP {e.code} {e.reason}"
                    time.sleep(1.6 ** attempt + 0.5)
                    continue
                body = e.read(300).decode("utf-8", "replace")
                raise KalshiAuthError(f"GET {endpoint} -> {e.code} {e.reason}: {body[:200]}")
            except OSError as e:  # URLError/TimeoutError/ConnectionError (all OSError) -> retry
                last = f"{type(e).__name__}: {str(e)[:80]}"
                time.sleep(1.6 ** attempt + 0.5)
                continue
        raise KalshiAuthError(f"GET {endpoint} exhausted {retries} retries: {last}")

    def paginated(self, endpoint: str, key: str, params: dict | None = None,
                  max_pages: int = 50) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            page = self.get(endpoint, {**(params or {}), "cursor": cursor})
            out.extend(page.get(key, []) or [])
            cursor = page.get("cursor")
            if not cursor:
                break
        return out
