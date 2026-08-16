#!/usr/bin/env python3
"""CP5 PRE-ARM proof — armed-ready, NOT live. Zero real orders.

Builds the loop exactly as the deploy will: reads config gates from
strategies.yaml (arm switch auto_execute=false -> executor dry_run=True), reads
the roster from selected_whales (seeded locally = the 4, simulating the prod
row), runs a short dry-run against live Poly, and confirms the $100 halt + the
arm switch OFF + the V2 POST unreachable.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import yaml

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
RDIR = WT / "reports/2026-08-15_poly_kalshi_mlb_phase1"
STAGING = f"sqlite:///{RDIR / 'prearm_staging.db'}"

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.persistence import db as _db  # noqa: E402
from trading_corp.persistence.models import StrategyState  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402
from trading_corp.data.mlb_poly_kalshi_match import build_kalshi_game_index  # noqa: E402
from trading_corp.agents.strategies.poly_kalshi_executor import (  # noqa: E402
    PolyKalshiExecutor, translate_whale_action,
)
from trading_corp.agents.strategies.poly_kalshi_copy_trader import PolyKalshiCopyTrader  # noqa: E402


async def _index(broker):
    from pykalshi import MarketStatus
    min_ts = int(time.time()) - 160 * 86400
    tickers = []
    for st, ex in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await broker._client.get_markets(series_ticker="KXMLBGAME", status=st, limit=1000,
                                              fetch_all=(st == MarketStatus.SETTLED), **ex)
        tickers += [getattr(m, "ticker", "") or "" for m in ms]
    idx = build_kalshi_game_index(tickers)
    return idx, frozenset(k[0] for k in idx)


async def main() -> int:
    cfg = yaml.safe_load((WT / "config/strategies.yaml").read_text())["poly_kalshi_mlb"]
    arm = bool(cfg["auto_execute"])          # ARM SWITCH
    dry_run = not arm                        # false -> dry_run True (shadow)

    s = load_secrets(ENV_FILE)
    broker = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await broker.connect()
    idx, dates = await _index(broker)

    # seed the roster (simulating the prod selected_whales row = the 4)
    roster = json.loads((RDIR / "roster_selected_whales.json").read_text())
    _db.init_db(STAGING)
    _db.set_agent_state(cfg["roster_actor"], cfg["roster_key"], roster, db_url=STAGING)

    ex = PolyKalshiExecutor(dry_run=dry_run, db_url=STAGING, strategy="poly_kalshi_mlb",
                            per_trade_cap_usd=cfg["per_trade_cap_usd"],
                            daily_deployment_cap_usd=cfg["daily_deployment_cap_usd"],
                            max_slippage_cents=cfg["max_slippage_cents"])
    loop = PolyKalshiCopyTrader(executor=ex, db_url=STAGING, stake_usd=cfg["stake_usd"],
                                daily_loss_cap_usd=cfg["daily_loss_cap_usd"],
                                poll_interval_sec=cfg["poll_interval_sec"],
                                roster_actor=cfg["roster_actor"], roster_key=cfg["roster_key"])
    loop.set_kalshi_index(idx, dates)

    print("=" * 74)
    print("CP5 PRE-ARM — armed-ready, NOT live")
    print("=" * 74)
    print("CONFIG GATES (strategies.yaml poly_kalshi_mlb):")
    print(f"  stake_usd={cfg['stake_usd']}  daily_loss_cap_usd={cfg['daily_loss_cap_usd']}  "
          f"per_trade_cap={cfg['per_trade_cap_usd']}  daily_deployment_cap={cfg['daily_deployment_cap_usd']}")
    print(f"ARM SWITCH auto_execute = {arm}  ->  executor dry_run = {ex._dry_run}  (True = shadow, POST unreachable)")
    print(f"loop.daily_loss_cap reads $100: {loop._daily_loss_cap_usd == 100.0}")
    print("\nROSTER loaded from selected_whales (NOT a hardcoded dict):")
    for name, wallet in loop._load_roster():
        print(f"  {name:26} {wallet}")

    # short dry-run against live Poly (backlog for end-to-end evidence)
    async with PolymarketDataAPIClient() as pc:
        await loop.run_for(20, client=pc, emit_backlog=True, backlog_n=1)
    placed = [e for e in loop.shadow_log if e.get("decision") == "placed"]
    would = [e for e in loop.shadow_log if e.get("decision") == "DRY_RUN_would_place"]
    print(f"\nDRY-RUN: polls={loop.poll_count} shadow_entries={len(loop.shadow_log)} "
          f"would_place={len(would)} REAL_ORDERS_PLACED={len(placed)}")
    for e in would[:3]:
        print(f"  would-place: {e['whale']} -> {e['order']['ticker']} {e['order']['v2_side']} "
              f"x{e['order']['count']} key={e['order']['idempotency_key'][:8]}..")

    # $100 daily-loss halt: crossing the threshold halts + blocks subsequent submits
    print("\n$100 DAILY-LOSS HALT:")
    print(f"  record_realized(-60) halted? {loop.record_realized(-60.0)}")
    fired = loop.record_realized(-45.0)      # cumulative -105 <= -100
    print(f"  record_realized(-45) -> cumulative -105 -> halted? {fired}")
    print(f"  StrategyState halted persisted: {StrategyState.from_persistence('poly_kalshi_mlb', db_url=STAGING).halted}")
    o = translate_whale_action(whale="SDTrading", whale_wallet=roster[0]["wallet"],
                               kalshi_ticker="KXMLBGAME-26AUG161605COLSF-SF", confidence=1.0,
                               whale_side="BUY", base_price=0.55, stake_usd=cfg["stake_usd"])
    print(f"  subsequent submit -> {(await ex.submit(o))['status']}  (blocked_halt = halt enforced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
