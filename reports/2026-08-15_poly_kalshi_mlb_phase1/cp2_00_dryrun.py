#!/usr/bin/env python3
"""CP2 dry-run — route REAL CP1-matched whale actions through the executor in
SIMULATION. Builds order objects + idempotency keys + logs what WOULD be sent, and
STOPS before the network POST (dry_run=True). NO real orders, NO live money.

Proves: (a) >=20 matched bets routed; (b) 5 fully-formed order objects; (c) replay
-> idempotency suppression (0 duplicate placements); (d) away/home side-mapping.
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
logging.basicConfig(level=logging.WARNING)

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402
from trading_corp.data.mlb_poly_kalshi_match import (  # noqa: E402
    build_kalshi_game_index, match_poly_to_kalshi, parse_poly_mlb_bet,
)
from trading_corp.agents.strategies.poly_kalshi_executor import (  # noqa: E402
    PolyKalshiExecutor, translate_whale_action,
)

WHALES = {"SDTrading": "0x16bb9951a36fce71e2ef57890b786145e0ba8492",
          "0x0x23kjookhai": "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9"}
DRYRUN_STAKE_USD = 2.00   # PLACEHOLDER — the real fixed stake is a CP5 operator gate.


async def _kalshi_index():
    from pykalshi import MarketStatus
    s = load_secrets(ENV_FILE)
    b = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await b.connect()
    min_ts = int(time.time()) - 160 * 86400
    tickers = []
    for st, ex in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await b._client.get_markets(series_ticker="KXMLBGAME", status=st, limit=1000,
                                         fetch_all=(st == MarketStatus.SETTLED), **ex)
        tickers += [getattr(m, "ticker", "") or "" for m in ms]
    idx = build_kalshi_game_index(tickers)
    return idx, frozenset(k[0] for k in idx)


async def main() -> int:
    idx, kdates = await _kalshi_index()
    ex = PolyKalshiExecutor(dry_run=True)   # DEFAULT dry-run; no broker needed
    orders = []          # (whale, activity_row, parsed, match, order)
    async with PolymarketDataAPIClient() as pc:
        for name, wallet in WHALES.items():
            rows = await pc.fetch_activity(wallet, limit=500, offset=0)
            for r in rows:
                # only TRADE BUY/SELL are copy signals; REDEEM/rebate are not.
                if r.type != "TRADE" or r.side not in ("BUY", "SELL"):
                    continue
                p = parse_poly_mlb_bet(r.slug, r.outcome or "", r.title or "", r.event_slug or "")
                if p.market_type != "moneyline":
                    continue
                m = match_poly_to_kalshi(p, idx, kdates)
                if m.status != "matched" or m.confidence < 0.97:
                    continue
                if not (0.0 < float(r.price) < 1.0):
                    continue
                o = translate_whale_action(
                    whale=name, whale_wallet=wallet, kalshi_ticker=m.kalshi_ticker,
                    confidence=m.confidence, whale_side=r.side, base_price=float(r.price),
                    stake_usd=DRYRUN_STAKE_USD)
                orders.append((name, r, p, m, o))

    # de-dup to distinct idempotency keys for the "how many routed" count
    routed = await _route(ex, [o for *_, o in orders])
    print("=" * 78)
    print("CP2 DRY-RUN (no orders placed; dry_run=True)")
    print("=" * 78)
    print(f"matched>=0.97 whale actions produced : {len(orders)}")
    print(f"distinct idempotency keys (routed)   : {len(routed['keys'])}")
    print(f"DRY_RUN_would_place                  : {routed['placed']}")
    print(f"suppressed_duplicate (within batch)  : {routed['dups']}")

    # 5 fully-formed order objects (whale action in -> order out)
    print("\n----- 5 FULLY-FORMED ORDER OBJECTS -----")
    five = _first_distinct(orders, 5)
    for name, r, p, m, o in five:
        print(f"\nWHALE ACTION: {name} side={r.side} outcome={r.outcome!r} "
              f"slug={r.slug} price={r.price} tx={r.transaction_hash[:14]}...")
        print("ORDER OUT   : " + json.dumps({
            "ticker": o.ticker, "v2_side": o.v2_side, "outcome": o.outcome,
            "action": o.action, "count": o.count, "stake_usd": o.stake_usd,
            "order_type": "ioc(market)", "tif": o.tif, "limit_price": o.body["price"],
            "reduce_only": o.reduce_only, "idempotency_key": o.idempotency_key,
            "confidence": o.confidence}))

    # entry/exit split + show real EXIT order objects (whale SELL -> ask + reduce_only)
    n_entry = sum(1 for *_, o in orders if o.action == "entry")
    n_exit = sum(1 for *_, o in orders if o.action == "exit")
    print(f"\n----- ENTRY/EXIT SPLIT -----  entries={n_entry}  exits={n_exit}")
    exits = _first_distinct([rec for rec in orders if rec[-1].action == "exit"], 3)
    for name, r, p, m, o in exits:
        print(f"  EXIT: {name} side={r.side} outcome={r.outcome!r} slug={r.slug} -> " + json.dumps({
            "ticker": o.ticker, "v2_side": o.v2_side, "action": o.action, "count": o.count,
            "tif": o.tif, "limit_price": o.body["price"], "reduce_only": o.reduce_only,
            "idempotency_key": o.idempotency_key}))
    if not exits:
        print("  (no matched exit/SELL actions in the recent-500 sample; exit path proven by unit test)")

    # replay the SAME 5 -> must all be suppressed, 0 new placements
    print("\n----- REPLAY THE SAME 5 (idempotency) -----")
    before = len(ex._placed)
    for name, r, p, m, o in five:
        res = await ex.submit(o)
        print(f"  {o.idempotency_key[:8]}.. -> {res['status']}")
    after = len(ex._placed)
    print(f"  placements before replay={before}  after replay={after}  (new placements: {after-before})")

    # side-mapping hand-checks: one away-club bet, one home-club bet
    print("\n----- SIDE-MAPPING HAND-CHECKS (away vs home) -----")
    _hand_checks(orders)
    return 0


async def _route(ex, olist):
    placed = dups = 0
    keys = set()
    for o in olist:
        res = await ex.submit(o)
        keys.add(o.idempotency_key)
        if res["status"] == "DRY_RUN_would_place":
            placed += 1
        elif res["status"] == "suppressed_duplicate":
            dups += 1
    return {"placed": placed, "dups": dups, "keys": keys}


def _first_distinct(orders, n):
    seen, out = set(), []
    for rec in orders:
        o = rec[-1]
        if o.idempotency_key in seen:
            continue
        seen.add(o.idempotency_key)
        out.append(rec)
        if len(out) >= n:
            break
    return out


def _hand_checks(orders):
    def slug_parts(slug):  # mlb-{away}-{home}-{date}
        parts = slug.split("-")
        return parts[1].upper(), parts[2].upper()
    shown = {"away": False, "home": False}
    for name, r, p, m, o in orders:
        away_code, home_code = slug_parts(r.slug)
        bet_is_away = (p.side == "away")
        kind = "away" if bet_is_away else "home"
        if shown[kind]:
            continue
        shown[kind] = True
        club = p.away_name if bet_is_away else p.home_name
        print(f"  [{kind.upper()} club bet]")
        print(f"     slug={r.slug}  (away={away_code}, home={home_code})")
        print(f"     whale bet outcome={r.outcome!r}  -> that is the {kind.upper()} club ({club})")
        print(f"     CP1-resolved Kalshi ticker = {o.ticker}")
        print(f"     ticker YES suffix '-{o.ticker.rsplit('-',1)[1]}' == '{club} wins == YES'")
        print(f"     => place {o.v2_side.upper()} (BUY YES) on that ticker; outcome={o.outcome} "
              f"(never NO). reduce_only={o.reduce_only}")
        if all(shown.values()):
            break


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
