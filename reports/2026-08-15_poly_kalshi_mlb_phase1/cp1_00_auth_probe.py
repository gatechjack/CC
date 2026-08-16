#!/usr/bin/env python3
"""CP1 step 0 — confirm LOCAL Kalshi API auth + a read. READ-ONLY. NO ORDERS.

Proves, before any validation run:
  1. Cred resolution — primary (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PEM,
     inline in cc/.env) and KAREN (from Key Vault via DefaultAzureCredential).
  2. Request signing — a signed, authenticated account read (portfolio balance;
     surfaced as a boolean only, never the value).
  3. Market read — list_markets(KXMLBGAME) returns real contracts; prints a
     sample of real tickers so we can eyeball the format + spot any live
     doubleheaders (same teams+date, different HHMM start-time).

Confirming BOTH creds now de-risks CP2/CP5 (the new strategy uses its own
KAREN-keyed KalshiLiveBroker instance). This script NEVER places an order.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Worktree root = two levels up from reports/2026-08-15_poly_kalshi_mlb_phase1/
WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")

ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")

logging.basicConfig(level=logging.WARNING)

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402


async def _probe(label: str, api_key_id: str | None, pem: str | None) -> list[str]:
    if not (api_key_id and pem):
        print(f"[{label}] MISSING cred: api_key_id={'set' if api_key_id else 'MISSING'} "
              f"pem={'set' if pem else 'MISSING'}")
        return []
    broker = KalshiBroker(api_key_id=api_key_id, private_key_pem=pem)
    try:
        await broker.connect()
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] connect() RAISED: {type(e).__name__}: {e}")
        return []
    print(f"[{label}] connect() ok — stub={broker._stub} (stub=True means creds not accepted)")

    # Explicit signed account read (auth + signing proof). Boolean only.
    bal_ok = False
    try:
        if broker._client is not None:
            await broker._client.portfolio.get_balance()
            bal_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] balance read failed: {type(e).__name__}: {e}")
    print(f"[{label}] signed account read (balance) ok = {bal_ok}")

    # Read-only market listing — the real KXMLBGAME contracts.
    tickers: list[str] = []
    try:
        res = await broker.list_markets(
            categories=("Sports",),
            series_filter=("KXMLBGAME",),
            max_series_per_category=10,
            max_markets_per_series=200,
        )
        for event in res.events:
            for m in event.markets:
                t = getattr(m, "ticker", None) or ""
                if t.startswith("KXMLBGAME"):
                    tickers.append(t)
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] list_markets(KXMLBGAME) failed: {type(e).__name__}: {e}")

    print(f"[{label}] KXMLBGAME markets read: {len(tickers)}")
    for t in sorted(set(tickers))[:14]:
        print(f"      {t}")

    close = getattr(broker, "close", None)
    if close is not None:
        try:
            await close()
        except Exception:  # noqa: BLE001
            pass
    return tickers


async def main() -> int:
    s = load_secrets(ENV_FILE)
    print("=== cred resolution ===")
    print("primary  api_key_id:", bool(s.kalshi_api_key_id), "| pem:", bool(s.kalshi_private_key_pem))
    print("karen    api_key_id:", bool(s.kalshi_karen_api_key_id), "| pem:", bool(s.kalshi_karen_private_key_pem))
    print("\n=== PRIMARY probe ===")
    await _probe("PRIMARY", s.kalshi_api_key_id, s.kalshi_private_key_pem)
    print("\n=== KAREN probe (new strategy's instance) ===")
    await _probe("KAREN", s.kalshi_karen_api_key_id, s.kalshi_karen_private_key_pem)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
