"""Diagnostic: plain HTTPS GET (no WS upgrade, no creds) against candidate paths
on external-api-ws.kalshi.com to distinguish 'path exists' (400/426 upgrade
required, 401/403 auth) from 'not found' (404). Guides the cfbenchmarks WS path.
READ-ONLY. Usage: run_capped python research/kalshi_crypto_v2/probe_paths.py"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request

HOST = "https://external-api-ws.kalshi.com"
PATHS = [
    "/", "/cfbenchmarks_value", "/v1/cfbenchmarks_value", "/ws/cfbenchmarks_value",
    "/ws/v1/cfbenchmarks_value", "/trade-api/ws/v2", "/trade-api/ws/v2/cfbenchmarks_value",
    "/cfbenchmarks/value", "/external/cfbenchmarks_value", "/health", "/status",
]
CTX = ssl.create_default_context()


def probe(url: str) -> str:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "kc2-probe"})
    try:
        with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
            body = r.read(160).decode("utf-8", "replace").replace("\n", " ")
            return f"{r.status} {r.reason} | server={r.headers.get('server','?')} | {body[:120]}"
    except urllib.error.HTTPError as e:
        body = e.read(160).decode("utf-8", "replace").replace("\n", " ")
        return f"{e.code} {e.reason} | server={e.headers.get('server','?')} | {body[:120]}"
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:120]}"


def main() -> int:
    print(f"host: {HOST}\n")
    for p in PATHS:
        print(f"GET {p:40} -> {probe(HOST + p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
