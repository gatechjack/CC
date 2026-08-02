"""S3 environment + reachability probe (READ-ONLY, no writes to any DB).

Determines whether the loaders can run from THIS machine or must be handed to
the operator: (a) required libs present, (b) creds/KV reachable, (c) each data
source reachable with a tiny keyless GET. Secrets are never printed — only bool
presence + a short source tag. Usage: python research/kalshi_crypto_v2/probe_s3_env.py
"""
from __future__ import annotations

import os
import ssl
import sys
import urllib.error
import urllib.request

CTX = ssl.create_default_context()


def _imp(name: str) -> str:
    try:
        mod = __import__(name)
        v = getattr(mod, "__version__", "?")
        return f"OK {v}"
    except Exception as e:  # noqa: BLE001
        return f"MISSING ({type(e).__name__})"


def _get(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "kc2-s3-probe"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read(120).decode("utf-8", "replace").replace("\n", " ")
            return f"{r.status} {r.reason} | {body[:90]}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} {e.reason} (reachable)"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {str(e)[:90]}"


def main() -> int:
    print(f"python: {sys.version.split()[0]}  ({sys.executable})")
    print("\n== libs ==")
    for lib in ("ccxt", "requests", "httpx", "pandas", "numpy",
                "catboost", "xgboost", "sklearn", "azure.identity",
                "azure.keyvault.secrets", "cryptography"):
        print(f"  {lib:26} {_imp(lib)}")

    print("\n== env / creds ==")
    for k in ("KEY_VAULT_URI", "KALSHI_KAREN_API_KEY_ID", "KALSHI_KAREN_PRIVATE_KEY_PEM"):
        print(f"  {k:30} {'set' if os.getenv(k) else 'unset'}")

    print("\n== source reachability (tiny keyless GET) ==")
    print(f"  binance.vision  {_get('https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1')}")
    print(f"  coinbase-exch   {_get('https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60')}")
    print(f"  coinbase-adv    {_get('https://api.coinbase.com/api/v3/brokerage/time')}")
    print(f"  coinalyze       {_get('https://api.coinalyze.net/v1/exchanges')}")  # 401 w/o key = reachable
    print(f"  kalshi-elect    {_get('https://api.elections.kalshi.com/trade-api/v2/exchange/status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
