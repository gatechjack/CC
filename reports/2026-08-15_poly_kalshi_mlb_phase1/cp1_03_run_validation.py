#!/usr/bin/env python3
"""CP1 validation — one pass over REAL history. READ-ONLY. NO ORDERS.

Poly (public API): full activity for the 4 discovered MLB whales, deduped to
distinct (whale, market-slug). Kalshi (authed, read-only): open + ~7wk settled
KXMLBGAME. Runs the deterministic matcher over every distinct MLB market and
reports the CP1 table + confidence distribution + doubleheader study + spot
checks. Emits a JSON for the writeup. Places nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
ENV_FILE = Path(r"C:\Users\AA Incorporado\cc\.env")
OUT_JSON = WT / "reports/2026-08-15_poly_kalshi_mlb_phase1/cp1_validation_out.json"
logging.basicConfig(level=logging.WARNING)

from trading_corp.utils.secrets import load_secrets  # noqa: E402
from trading_corp.brokers.kalshi import KalshiBroker  # noqa: E402
from trading_corp.data.polymarket_data_api_client import (  # noqa: E402
    PolymarketDataAPIClient, PolymarketDataAPIError, PolymarketRateLimitError,
)
from trading_corp.data.mlb_poly_kalshi_match import (  # noqa: E402
    build_kalshi_game_index, match_poly_to_kalshi, parse_poly_mlb_bet,
)

WHALES = {
    "SDTrading":             "0x16bb9951a36fce71e2ef57890b786145e0ba8492",
    "xifutloong3":           "0x2dc13c6bda81b202281e796953a7323de675b33c",
    "monkeymashingkeyboard": "0x684baa57c338c2549aec0aa3f034f695d72a8409",
    "0x0x23kjookhai":        "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9",
}

_PAGE = 500
_MAX_PAGES = 60
_BACKOFF = (10, 20, 40, 60)


def _log(m):
    print(m, file=sys.stderr, flush=True)


async def _fetch_activity(client, wallet):
    rows, capped = [], False
    for i in range(_MAX_PAGES):
        page = None
        for attempt in range(len(_BACKOFF) + 1):
            try:
                page = await client.fetch_activity(wallet, limit=_PAGE, offset=i * _PAGE)
                break
            except PolymarketRateLimitError:
                if attempt == len(_BACKOFF):
                    _log(f"    429 gave up offset={i*_PAGE}")
                    return rows, capped
                await asyncio.sleep(_BACKOFF[attempt])
            except PolymarketDataAPIError as e:
                _log(f"    stop offset={i*_PAGE}: {e}")
                return rows, capped
        if not page:
            return rows, capped
        rows.extend(page)
        if len(page) < _PAGE:
            return rows, capped
        await asyncio.sleep(0.8)
    return rows, True


async def _fetch_kalshi(broker):
    from pykalshi import MarketStatus
    min_ts = int(time.time()) - 160 * 86400
    tickers = []
    for status, extra in ((MarketStatus.OPEN, {}),
                          (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await broker._client.get_markets(
            series_ticker="KXMLBGAME", status=status, limit=1000,
            fetch_all=(status == MarketStatus.SETTLED), **extra)
        tickers += [getattr(m, "ticker", "") or "" for m in ms]
    return tickers


def _distinct_markets(rows):
    """Collapse activity rows to distinct (slug) markets; keep entry outcome."""
    by_slug = {}
    for r in rows:
        if not r.slug:
            continue
        cur = by_slug.get(r.slug)
        if cur is None:
            by_slug[r.slug] = {"slug": r.slug, "outcome": r.outcome, "title": r.title,
                               "event_slug": r.event_slug, "condition_id": r.condition_id,
                               "ts": r.timestamp, "n_fills": 1,
                               "sides": {r.side}, "outcomes": {r.outcome}}
        else:
            cur["n_fills"] += 1
            cur["sides"].add(r.side)
            cur["outcomes"].add(r.outcome)
            if r.side == "BUY" and cur["outcome"] in (None, ""):
                cur["outcome"] = r.outcome
    return list(by_slug.values())


async def main() -> int:
    s = load_secrets(ENV_FILE)
    broker = KalshiBroker(api_key_id=s.kalshi_api_key_id, private_key_pem=s.kalshi_private_key_pem)
    await broker.connect()
    _log("fetching kalshi KXMLBGAME (open + settled)...")
    kalshi_tickers = await _fetch_kalshi(broker)
    kindex = build_kalshi_game_index(kalshi_tickers)
    kdates = frozenset(k[0] for k in kindex)
    dh_keys = {k: v for k, v in kindex.items() if len(v) > 1}
    _log(f"kalshi: {len(kalshi_tickers)} tickers -> {sum(len(v) for v in kindex.values())} games "
         f"({len(kdates)} dates {min(kdates)}..{max(kdates)}); kalshi doubleheader keys={len(dh_keys)}")

    per_whale = {}
    markets_all = []
    async with PolymarketDataAPIClient() as pc:
        for name, wallet in WHALES.items():
            _log(f"[{name}] fetching activity...")
            rows, capped = await _fetch_activity(pc, wallet)
            markets = _distinct_markets(rows)
            _log(f"[{name}] {len(rows)} rows -> {len(markets)} distinct markets (capped={capped})")
            per_whale[name] = {"rows": len(rows), "markets": len(markets), "capped": capped}
            for mk in markets:
                mk["whale"] = name
            markets_all += markets

    # ── classify + match every distinct market ──
    buckets = Counter()
    conf_hist = Counter()
    matched_rows = []
    fails = []
    ml_within_window = 0
    # poly-side doubleheader detection: (whale,date,teams) -> distinct ML slugs
    poly_game_groups = defaultdict(set)
    results = []
    for mk in markets_all:
        p = parse_poly_mlb_bet(mk["slug"], mk["outcome"] or "", mk["title"] or "", mk["event_slug"] or "")
        r = match_poly_to_kalshi(p, kindex, kdates)
        rec = {"whale": mk["whale"], "slug": mk["slug"], "outcome": mk["outcome"],
               "market_type": p.market_type, "status": r.status, "confidence": r.confidence,
               "kalshi_ticker": r.kalshi_ticker, "reason": r.reason,
               "n_candidates": len(r.kalshi_candidates), "date": p.date_iso,
               "away": p.away_name, "home": p.home_name, "side": p.side}
        results.append(rec)

        # bucketing for the report
        if p.market_type == "moneyline":
            if r.status == "matched":
                buckets["matched"] += 1
                conf_hist[round(r.confidence, 2)] += 1
                matched_rows.append(rec)
            elif r.status == "doubleheader_ambiguous":
                buckets["doubleheader"] += 1
                conf_hist[round(r.confidence, 2)] += 1
            elif r.status == "no_kalshi_contract":
                buckets["no_kalshi_contract_in_window"] += 1
            elif r.status == "out_of_window":
                buckets["out_of_kalshi_window"] += 1
            else:
                buckets["fail"] += 1
                fails.append(rec)
            if p.date_iso in kdates:
                ml_within_window += 1
                if p.away_name and p.home_name:
                    poly_game_groups[(mk["whale"], p.date_iso,
                                      frozenset({p.away_name, p.home_name}))].add(mk["slug"])
        elif p.market_type in ("total", "spread", "prop"):
            buckets[f"skip_non_ml:{p.market_type}"] += 1
        elif p.market_type == "mlb_non_game":
            buckets["skip_mlb_futures_series"] += 1
        else:  # non_mlb
            buckets["non_mlb_other_sport"] += 1

    poly_dh = {f"{w}|{d}|{'/'.join(sorted(t))}": sorted(slugs)
               for (w, d, t), slugs in poly_game_groups.items() if len(slugs) > 1}

    out = {
        "kalshi": {"tickers": len(kalshi_tickers),
                   "games": sum(len(v) for v in kindex.values()),
                   "dates": [min(kdates), max(kdates)], "n_dates": len(kdates),
                   "doubleheader_keys": [
                       {"key": f"{k[0]}|{'/'.join(sorted(k[1]))}",
                        "tickers": sorted(t for g in v for t in g.ticker_by_side_code.values()),
                        "times": sorted(g.time_str for g in v)}
                       for k, v in dh_keys.items()]},
        "per_whale": per_whale,
        "totals": {"distinct_markets": len(markets_all),
                   "ml_within_kalshi_window": ml_within_window},
        "buckets": dict(buckets.most_common()),
        "confidence_hist": {str(k): v for k, v in sorted(conf_hist.items())},
        "poly_doubleheader_groups": poly_dh,
        "spot_checks": matched_rows[:12],
        "fails": fails[:40],
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    # ── console summary ──
    print("\n" + "=" * 78)
    print("CP1 VALIDATION SUMMARY (read-only, no orders)")
    print("=" * 78)
    print(f"Kalshi window: {out['kalshi']['games']} games, {out['kalshi']['n_dates']} dates "
          f"{out['kalshi']['dates'][0]}..{out['kalshi']['dates'][1]}; "
          f"kalshi doubleheaders={len(out['kalshi']['doubleheader_keys'])}")
    print(f"Distinct Poly markets processed: {len(markets_all)}")
    for w, d in per_whale.items():
        print(f"   {w:24} rows={d['rows']:>6} markets={d['markets']:>5} capped={d['capped']}")
    print("\nBUCKETS:")
    for b, n in buckets.most_common():
        print(f"   {n:>6}  {b}")
    ml_total = sum(v for k, v in buckets.items()
                   if k in ("matched", "doubleheader", "no_kalshi_contract_in_window",
                            "out_of_kalshi_window", "fail"))
    ml_in_window = ml_total - buckets.get("out_of_kalshi_window", 0)
    print(f"\nMoneyline markets total: {ml_total}  | within Kalshi window: {ml_in_window}")
    if ml_in_window:
        print(f"   matched:      {buckets.get('matched',0):>5}  "
              f"({buckets.get('matched',0)/ml_in_window:.1%} of in-window ML)")
        print(f"   doubleheader: {buckets.get('doubleheader',0):>5}")
        print(f"   no_contract:  {buckets.get('no_kalshi_contract_in_window',0):>5}")
        print(f"   fail:         {buckets.get('fail',0):>5}")
    print("\nCONFIDENCE HISTOGRAM (ML match attempts):")
    for k in sorted(conf_hist):
        print(f"   {k:>4}: {conf_hist[k]}")
    print(f"\nKalshi doubleheaders found: {len(out['kalshi']['doubleheader_keys'])}")
    for dh in out['kalshi']['doubleheader_keys'][:6]:
        print(f"   {dh['key']}  times={dh['times']}")
        for t in dh['tickers']:
            print(f"       {t}")
    print(f"\nPoly same-matchup-date groups with >1 ML slug: {len(poly_dh)}")
    for k, slugs in list(poly_dh.items())[:6]:
        print(f"   {k}: {slugs}")
    print(f"\nFails: {len(fails)} (first few)")
    for f in fails[:8]:
        print(f"   {f['whale']} {f['slug']} outcome={f['outcome']!r} reason={f['reason']}")
    print(f"\nwrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
