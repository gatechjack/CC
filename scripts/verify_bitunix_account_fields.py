"""One-shot: dump raw /api/v1/futures/account JSON for each stablecoin
and print the per-field breakdown plus the current sum-of-seven equity
calculation, so the Board can compare against the BitUnix UI's "Total
Equity" and confirm whether `transfer` duplicates `available`.

Run on prod:
    cd /home/azureuser/trading_corp
    sudo -u azureuser ./venv/bin/python scripts/verify_bitunix_account_fields.py

Read-only; no orders touched.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

from trading_corp.brokers.bitunix import (
    _BASE_URL,
    _DEFAULT_TIMEOUT_S,
    _STABLE_MARGIN_COINS,
    _sign,
    _to_float,
)
from trading_corp.utils.secrets import load_secrets


_FIELDS = (
    "available",
    "frozen",
    "margin",
    "transfer",
    "crossUnrealizedPNL",
    "isolationUnrealizedPNL",
    "bonus",
)


async def main() -> int:
    secrets = load_secrets()
    api_key = secrets.bitunix_futures_api_key
    api_secret = secrets.bitunix_futures_api_secret
    if not api_key or not api_secret:
        print("ERROR: BitUnix credentials missing from env", file=sys.stderr)
        return 1

    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=_DEFAULT_TIMEOUT_S) as client:
        grand_total = 0.0
        for coin in _STABLE_MARGIN_COINS:
            query = {"marginCoin": coin}
            headers = _sign(api_key, api_secret, query=query)
            r = await client.get("/api/v1/futures/account", params=query, headers=headers)
            r.raise_for_status()
            payload = r.json()

            print(f"\n=== marginCoin={coin} ===")
            print("raw response:")
            print(json.dumps(payload, indent=2, sort_keys=True))

            data = payload.get("data") or {}
            if not data:
                print(f"(empty data for {coin})")
                continue

            print("\nfield breakdown:")
            running = 0.0
            for f in _FIELDS:
                v = _to_float(data.get(f))
                running += v
                print(f"  {f:28s} = {v:>12.4f}")
            print(f"  {'(sum of 7)':28s} = {running:>12.4f}   <-- current coin_equity")

            available = _to_float(data.get("available"))
            transfer = _to_float(data.get("transfer"))
            print(f"\n  transfer == available ? {'YES' if abs(transfer - available) < 1e-9 else 'no'}"
                  f"  (transfer={transfer}, available={available})")

            without_transfer = running - transfer
            print(f"  coin_equity WITHOUT transfer = {without_transfer:.4f}")

            grand_total += running

        print(f"\n=== grand total (sum across {','.join(_STABLE_MARGIN_COINS)}) = {grand_total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
