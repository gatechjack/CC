#!/usr/bin/env python3
"""CP4 live SHADOW run — full detection loop on live data, dry_run, ZERO orders.

Polls the 4 discovered whales at 5s, matches -> order -> guardrails -> shadow log.
Emits real recent actions at cold-start (backlog=True) for end-to-end evidence,
then detects genuinely-new in-window actions (backlog=False, true seconds latency).
Writes shadow_out.json. Places nothing (executor dry_run=True).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
OUT = WT / "reports/2026-08-15_poly_kalshi_mlb_phase1/shadow_out.json"
logging.basicConfig(level=logging.WARNING)

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402
from trading_corp.data.mlb_poly_kalshi_match import build_kalshi_game_index  # noqa: E402
from trading_corp.agents.strategies.poly_kalshi_executor import PolyKalshiExecutor  # noqa: E402
from trading_corp.agents.strategies.poly_kalshi_copy_trader import PolyKalshiCopyTrader  # noqa: E402

WHALES = {"SDTrading": "0x16bb9951a36fce71e2ef57890b786145e0ba8492",
          "xifutloong3": "0x2dc13c6bda81b202281e796953a7323de675b33c",
          "monkeymashingke": "0x684baa57c338c2549aec0aa3f034f695d72a8409",
          "0x0x23kjookhai": "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9"}
DURATION_S = 660      # ~11 min
POLL_S = 5.0          # aggressive end of 5-10s, to exercise 429 backoff


async def _build_index(broker):
    from pykalshi import MarketStatus
    min_ts = int(time.time()) - 160 * 86400
    tickers = []
    for st, ex in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await broker._client.get_markets(series_ticker="KXMLBGAME", status=st, limit=1000,
                                              fetch_all=(st == MarketStatus.SETTLED), **ex)
        tickers += [getattr(m, "ticker", "") or "" for m in ms]
    idx = build_kalshi_game_index(tickers)
    return idx, frozenset(k[0] for k in idx)


def _make_quote_fn(broker):
    async def quote_fn(ticker):
        m = await broker._client.get_market(ticker)   # live book (AsyncMarket)
        ya = getattr(m, "yes_ask_dollars", None)      # already in dollars
        yb = getattr(m, "yes_bid_dollars", None)
        if ya is None or not (0.0 < float(ya) < 1.0):
            return None                               # settled/closed -> no book -> fail-closed (live)
        return {"yes_ask": float(ya), "yes_bid": float(yb or 0.0)}
    return quote_fn


async def main() -> int:
    s = load_secrets(ENV_FILE)
    broker = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await broker.connect()
    idx, dates = await _build_index(broker)
    print(f"kalshi index: {sum(len(v) for v in idx.values())} games; starting {DURATION_S}s shadow @ {POLL_S}s", flush=True)

    # roster now comes from selected_whales (no hardcoded dict); seed a local
    # staging row from WHALES so this historical runner still executes.
    from trading_corp.persistence import db as _db
    STAGING = f"sqlite:///{WT / 'reports/2026-08-15_poly_kalshi_mlb_phase1/shadow_staging.db'}"
    _db.init_db(STAGING)
    _db.set_agent_state("polymarket_copy_trader", "selected_whales",
                        [{"wallet": w, "user_name": n, "category": "mlb"} for n, w in WHALES.items()],
                        db_url=STAGING)
    ex = PolyKalshiExecutor(dry_run=True, strategy="poly_kalshi_mlb", db_url=STAGING)
    loop = PolyKalshiCopyTrader(executor=ex, db_url=STAGING, poll_interval_sec=POLL_S,
                                stake_usd=2.00, quote_fn=_make_quote_fn(broker))
    loop.set_kalshi_index(idx, dates)

    t0 = time.time()
    async with PolymarketDataAPIClient() as pc:
        await loop.run_for(DURATION_S, client=pc, emit_backlog=True, backlog_n=2)
    wall = round(time.time() - t0, 1)

    placed = [e for e in loop.shadow_log if e.get("decision") == "placed"]
    live = [e for e in loop.shadow_log if not e.get("backlog") and e.get("stage") == "submitted"]
    backlog = [e for e in loop.shadow_log if e.get("backlog") and e.get("stage") == "submitted"]
    out = {
        "wall_s": wall, "poll_count": loop.poll_count, "poll_interval_s": POLL_S,
        "backoff_events": loop.backoff_events,
        "n_shadow_entries": len(loop.shadow_log),
        "n_would_place": len([e for e in loop.shadow_log if e.get("decision") == "DRY_RUN_would_place"]),
        "n_real_orders_placed": len(placed),
        "n_live_detected_actions": len(live),
        "n_backlog_actions": len(backlog),
        "live_latency_s": sorted(e["latency_s"] for e in live),
        "executor_deployed_usd": ex._deployed_usd,
        "shadow_log": loop.shadow_log,
    }
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"DONE wall={wall}s polls={loop.poll_count} backoff_events={len(loop.backoff_events)} "
          f"shadow_entries={len(loop.shadow_log)} would_place={out['n_would_place']} "
          f"REAL_ORDERS_PLACED={len(placed)} live_actions={len(live)} backlog_actions={len(backlog)}", flush=True)
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
