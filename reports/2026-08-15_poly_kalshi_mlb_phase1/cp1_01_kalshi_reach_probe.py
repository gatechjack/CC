#!/usr/bin/env python3
"""CP1 step 1 — how far back does Kalshi serve SETTLED KXMLBGAME? READ-ONLY.

The whales' Poly MLB history spans months; Kalshi's OPEN slate is only
today/tomorrow. To round-trip matching against REAL Kalshi contracts on
history we need SETTLED markets. This probes the reach (count + date span)
and how many distinct games it covers. NO orders.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import Counter
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
logging.basicConfig(level=logging.WARNING)

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.data.sports_team_mapping import parse_sports_ticker  # noqa: E402


async def _fetch(client, status, limit, min_close_ts=None):
    from pykalshi import MarketStatus
    st = getattr(MarketStatus, status)
    extra = {}
    if min_close_ts is not None:
        extra["min_close_ts"] = int(min_close_ts)
    ms = await client.get_markets(
        series_ticker="KXMLBGAME", status=st, limit=limit,
        fetch_all=(status == "SETTLED"), **extra,
    )
    return [getattr(m, "ticker", "") or "" for m in ms]


def _summarize(label, tickers):
    dates = Counter()
    games = set()
    for t in tickers:
        p = parse_sports_ticker(t)
        if p is None:
            continue
        dates[p.date_str] += 1
        games.add((p.date_str, frozenset({p.team_a, p.team_b})))
    print(f"[{label}] tickers={len(tickers)} distinct_dates={len(dates)} distinct_games={len(games)}")
    if dates:
        ordered = sorted(dates)
        print(f"      date span: {ordered[0]} .. {ordered[-1]}")
        print(f"      per-date counts (first/last 6): "
              f"{[(d, dates[d]) for d in ordered[:6]]} ... {[(d, dates[d]) for d in ordered[-6:]]}")


async def main() -> int:
    s = load_secrets(ENV_FILE)
    import time
    broker = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await broker.connect()
    min_ts = int(time.time()) - 160 * 86400  # ~160 days back (this MLB season)
    for status, limit, mct in (("OPEN", 1000, None), ("SETTLED", 1000, min_ts)):
        try:
            tickers = await _fetch(broker._client, status, limit, min_close_ts=mct)
        except Exception as e:  # noqa: BLE001
            print(f"[{status}] get_markets failed: {type(e).__name__}: {e}")
            continue
        _summarize(status, tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
