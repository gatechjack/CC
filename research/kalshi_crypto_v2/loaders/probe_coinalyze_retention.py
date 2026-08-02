"""Rider A: measure Coinalyze fine-interval retention floors precisely and derive
the re-pull cadence needed to never lose a day. READ-ONLY, no DB. Fetches each
fine interval full-window twice (stability check) and reports the oldest point
retained = the retention floor. Cadence must be < floor to guarantee overlap.

Usage: python research/kalshi_crypto_v2/loaders/probe_coinalyze_retention.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
BASE = "https://api.coinalyze.net/v1"
SYM = "BTCUSDT_PERP.A"


def get_key() -> str:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    return SecretClient(vault_url=os.environ["KEY_VAULT_URI"],
                        credential=DefaultAzureCredential()).get_secret("coinalyze-api-key").value


def measure(key: str, interval: str) -> tuple[int, int, int]:
    frm = common.PERIOD_START_MS // 1000
    to = common.now_ms() // 1000
    data = common.http_get(f"{BASE}/ohlcv-history", headers={"api_key": key}, throttle=1.2,
                           params={"symbols": SYM, "interval": interval, "from": frm, "to": to})
    hist = (data[0].get("history") if data else []) or []
    if not hist:
        return 0, 0, 0
    return len(hist), int(hist[0]["t"]), int(hist[-1]["t"])


def main() -> int:
    key = get_key()
    now = common.now_ms() // 1000
    print(f"now = {common.iso(now*1000)} UTC   symbol={SYM}\n")
    print(f"{'interval':8} {'pts':>6} {'oldest retained':17} {'floor(h)':>9} {'cadence rec.':>16}")
    print("-" * 62)
    for interval in ("1min", "5min", "15min"):
        # two reads for stability
        a = measure(key, interval)
        b = measure(key, interval)
        pts, t0, t1 = a
        floor_h = (now - t0) / 3600 if t0 else 0
        # never-lose-a-day: re-pull at <= floor with margin. Use floor/2 (round to sane cadence).
        half = floor_h / 2
        if half >= 20:
            cad = "daily (24h)"
        elif half >= 10:
            cad = "every 12h"
        elif half >= 5:
            cad = "every 6h"
        else:
            cad = f"every {max(1, int(half))}h"
        stab = "stable" if abs(a[0] - b[0]) <= 3 else f"VARIES {a[0]}vs{b[0]}"
        print(f"{interval:8} {pts:>6} {common.iso(t0*1000):17} {floor_h:>9.1f} {cad:>16}  [{stab}]")
    print("\nnote: cadence rec. = floor/2 (safety margin). Re-pull interval MUST be")
    print("< retention floor so each pull overlaps the last -> zero lost minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
