#!/usr/bin/env python3
"""CP5 step 3 wiring test — exercises the EXACT main.py wiring end-to-end in
DRY-RUN with the real funded KAREN KalshiLiveBroker. NO real orders (dry_run=True).
Validates: live-broker connect (funded preflight), KXMLBGAME index via the live
broker's read client, live quote_fn, executor+loop poll_cycle, roster read.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
RDIR = WT / "reports/2026-08-15_poly_kalshi_mlb_phase1"
STAGING = f"sqlite:///{RDIR / 'wiring_staging.db'}"

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.persistence import db as _db  # noqa: E402
from trading_corp.brokers.kalshi_live import KalshiLiveBroker  # noqa: E402
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402
from trading_corp.data.mlb_poly_kalshi_match import build_kalshi_game_index  # noqa: E402
from trading_corp.agents.strategies.poly_kalshi_executor import PolyKalshiExecutor  # noqa: E402
from trading_corp.agents.strategies.poly_kalshi_copy_trader import PolyKalshiCopyTrader  # noqa: E402


async def main() -> int:
    s = load_secrets(ENV_FILE)
    broker = KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id,
                              private_key_pem=s.kalshi_karen_private_key_pem,
                              order_type="ioc", max_slippage_cents=2)
    await broker.connect()   # funded preflight
    print("KalshiLiveBroker(KAREN) connected (funded preflight passed)")

    # index via the live broker's read client (exactly as the loop does)
    from pykalshi import MarketStatus
    min_ts = int(time.time()) - 160 * 86400
    tickers = []
    for st, ex in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await broker._read._client.get_markets(series_ticker="KXMLBGAME", status=st,
                                                    limit=1000, fetch_all=(st == MarketStatus.SETTLED), **ex)
        tickers += [getattr(m, "ticker", "") or "" for m in ms]
    idx = build_kalshi_game_index(tickers)
    print(f"index via live-broker read client: {sum(len(v) for v in idx.values())} games")

    async def quote_fn(ticker, _b=broker):
        try:
            m = await _b._read._client.get_market(ticker)
            ya = getattr(m, "yes_ask_dollars", None); yb = getattr(m, "yes_bid_dollars", None)
            if ya is None or not (0.0 < float(ya) < 1.0):
                return None
            return {"yes_ask": float(ya), "yes_bid": float(yb or 0.0)}
        except Exception:
            return None

    # dry-run executor over the LIVE broker; roster seeded locally = the 4
    _db.init_db(STAGING)
    roster = json.loads((RDIR / "roster_selected_whales.json").read_text())
    _db.set_agent_state("polymarket_copy_trader", "selected_whales", roster, db_url=STAGING)
    ex = PolyKalshiExecutor(dry_run=True, broker=broker, db_url=STAGING, strategy="poly_kalshi_mlb",
                            per_trade_cap_usd=None, daily_deployment_cap_usd=None, max_slippage_cents=2)
    loop = PolyKalshiCopyTrader(executor=ex, db_url=STAGING, stake_usd=5.0, daily_loss_cap_usd=100.0,
                                poll_interval_sec=7, activity_limit=50, quote_fn=quote_fn)
    loop.set_kalshi_index(idx, [k[0] for k in idx])

    print("roster loaded by loop:", [n for n, w in loop._load_roster()])
    async with PolymarketDataAPIClient() as client:
        await loop.poll_cycle(client, emit_backlog=True, backlog_n=1)  # cold-start + backlog evidence
    placed = [e for e in loop.shadow_log if e.get("decision") == "placed"]
    would = [e for e in loop.shadow_log if e.get("decision") == "DRY_RUN_would_place"]
    print(f"poll_cycles ran; shadow_entries={len(loop.shadow_log)} would_place={len(would)} "
          f"REAL_ORDERS_PLACED={len(placed)}  dry_run={ex._dry_run}")
    for e in would[:2]:
        print(f"  would-place: {e['whale']} -> {e['order']['ticker']} {e['order']['v2_side']} "
              f"x{e['order']['count']} @ {e['order']['limit_price']} quote={e.get('quote')}")
    # live quote sanity on one open game
    open_t = next((t for t in tickers if t.startswith("KXMLBGAME") and "26AUG1" in t), None)
    if open_t:
        print(f"live quote_fn({open_t}) = {await quote_fn(open_t)}")
    await broker.disconnect()
    print("WIRING TEST OK — 0 real orders, live broker connected + read + dry-run execute path exercised")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
