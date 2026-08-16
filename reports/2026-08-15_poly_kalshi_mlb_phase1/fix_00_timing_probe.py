#!/usr/bin/env python3
"""Boot index-refresh fix — TIMING probe (READ-ONLY, no orders).

Measures, on a COLD client (fresh process/connection), how long the OPEN
KXMLBGAME fetch takes vs the heavy SETTLED fetch_all. This justifies the
boot-path design: if OPEN is fast and SETTLED is the slow/failing call, then
setting the index from OPEN first closes the match blind-spot in seconds while
SETTLED merges (with retry) behind it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
logging.basicConfig(level=logging.WARNING)

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.data.mlb_poly_kalshi_match import build_kalshi_game_index  # noqa: E402


async def _timed_open(client):
    from pykalshi import MarketStatus
    t0 = time.time()
    ms = await client.get_markets(series_ticker="KXMLBGAME",
                                  status=MarketStatus.OPEN, limit=1000, fetch_all=False)
    dt = time.time() - t0
    return [getattr(m, "ticker", "") or "" for m in ms], dt


async def _timed_settled(client):
    from pykalshi import MarketStatus
    min_ts = int(time.time()) - 160 * 86400
    t0 = time.time()
    ms = await client.get_markets(series_ticker="KXMLBGAME",
                                  status=MarketStatus.SETTLED, limit=1000,
                                  fetch_all=True, min_close_ts=min_ts)
    dt = time.time() - t0
    return [getattr(m, "ticker", "") or "" for m in ms], dt


async def main() -> int:
    s = load_secrets(ENV_FILE)
    t_boot = time.time()
    broker = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await broker.connect()
    print(f"connect: {time.time() - t_boot:.2f}s (cold)")

    open_t, dt_open = await _timed_open(broker._client)
    print(f"OPEN fetch:    {dt_open:6.2f}s  tickers={len(open_t)}")
    idx_open = build_kalshi_game_index(open_t)
    print(f"  -> OPEN index games={sum(len(v) for v in idx_open.values())} "
          f"dates={len(set(g.date_iso for v in idx_open.values() for g in v))}  "
          f"[index MATCHABLE at t+{time.time() - t_boot:.1f}s from boot]")

    try:
        settled_t, dt_settled = await _timed_settled(broker._client)
        print(f"SETTLED fetch: {dt_settled:6.2f}s  tickers={len(settled_t)}")
    except Exception as e:  # noqa: BLE001
        print(f"SETTLED fetch FAILED: {type(e).__name__}: {e}")
        settled_t = []
    idx_full = build_kalshi_game_index(open_t + settled_t)
    print(f"  -> FULL index games={sum(len(v) for v in idx_full.values())}  "
          f"[complete at t+{time.time() - t_boot:.1f}s from boot]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
