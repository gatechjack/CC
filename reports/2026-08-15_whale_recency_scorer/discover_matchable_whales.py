#!/usr/bin/env python3
"""ONE-TIME go/no-go discovery probe: does a quality pool of Kalshi-matchable
Polymarket whales exist in the discoverable (volume-leaderboard) population?

Read-only. No roster changes, no writes to selected/pinned/watch, no LLM. Reuses
the existing discovery source (fetch_leaderboard per category) + the existing
quality bar (build_audit_report) + the recency scorer (30d). NEVER modifies the
pipeline.

Funnel (keeps the expensive gamma-resolution calls off non-matchable whales):
  Stage 1 CLASSIFY: leaderboard candidate -> /closed-positions (deep, 429-backoff)
    -> classify every resolved market by title into the target taxonomy -> the
    whale's dominant bucket. closed-positions carries title+realized, so this
    needs NO gamma resolutions.
  Stage 2 AUDIT (only matchable-dominant candidates): /activity + gamma
    resolutions -> build_audit_report -> full quality bar + recency flag.

Realized basis throughout; NEVER held. Incremental JSON output so a mid-run
Cloudflare block doesn't lose progress.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from trading_corp.scripts.polymarket_whale_recency import ResolvedTrade, score_recency
except ModuleNotFoundError:  # pragma: no cover - prod temp-run path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from polymarket_whale_recency import ResolvedTrade, score_recency  # type: ignore

from trading_corp.data.polymarket_data_api_client import (
    PolymarketDataAPIClient, PolymarketDataAPIError, PolymarketRateLimitError,
)
from trading_corp.data.polymarket_whale_audit import (
    DEFAULT_PARTIAL_SELL_THRESHOLD, build_audit_report, group_fills_by_decision,
)

# --- quality bar (the impostor-catching thresholds) -------------------------
MIN_RESOLVED = 20
MAX_INFLATION_RATIO = 1.0        # near/below 1: reject unrealized-mark riders
MAX_FAVORITE_SHARE = 0.50        # >50% entries >85c = favorite-farmed -> reject
MAX_EVENT_CONCENTRATION = 0.50   # one event > 50% of decisions -> reject
HALF_LIFE = 30.0

# PRIMARY matchable target categories (clean 1:1 to Kalshi) -- these count toward
# the go/no-go pool. UFC/tennis/golf are NOT in the user's PRIMARY set -> other.
PRIMARY = {"nfl", "nba", "mlb", "nhl", "awards_culture", "cpi_fed", "politics", "soccer"}

_ACT_PAGE = 500
_CP_PAGE = 50
_RATE_BACKOFF = (15, 30, 60, 90)   # seconds, per 429 retry


def _log(m):
    print(m, file=sys.stderr, flush=True)


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "-"


# --- taxonomy classifier ----------------------------------------------------
_EXCLUDE = [
    ("weather", ("weather", "temperature", "highest temp", "degrees f", "rainfall",
                 "snowfall", "hurricane")),
    ("crypto_price", ("bitcoin", "btc ", "ethereum", " eth ", "solana", " sol ",
                      "price of", "hit $", "all-time high", "dogecoin", "xrp")),
    ("geopolitics_war", ("war", "ukraine", "russia", "israel", "gaza", "hamas",
                         "iran", "north korea", "nuclear", "ceasefire", "invade",
                         "military", "troops", "nato", "missile", "annex")),
    ("viral_gossip", ("tweet", "how many times", " elon", "musk", "kanye", "tiktok",
                      " viral", "post on x", "instagram")),
    ("parlay", ("parlay", "multi-leg", "same game parlay")),
]
_ESPORTS = ("league of legends", "lol:", "lol ", " lck", " lpl", "dota",
            "counter-strike", "cs2", "csgo", "valorant", "esports", " worlds ",
            "the international", "rocket league")
_SERIES_MARK = ("(bo3", "(bo5", "(bo7", "best of", " series", "(bo")
_MATCH_MARK = (" map ", "- map", "map 1", "map 2", "map 3", "game 1", "game 2", "game 3")
_PRIMARY_RULES = [
    ("nfl", ("nfl", "super bowl")),
    ("nba", ("nba", "nba finals")),
    ("mlb", ("mlb", "world series")),
    ("nhl", ("nhl", "stanley cup")),
    ("soccer", ("premier league", "epl", "la liga", "laliga", "champions league",
                "uefa", "serie a", "bundesliga", " mls", "world cup", "ligue 1",
                "copa", "europa league", "matchday")),
    ("awards_culture", ("oscar", "grammy", "emmy", "billboard", "spotify",
                        "golden globe", "album of the year", "box office",
                        "rotten tomatoes")),
    ("cpi_fed", ("cpi", "inflation", "consumer price", "core pce", "fed ", "fomc",
                 "federal reserve", "rate cut", "rate hike", "interest rate",
                 "powell", "basis points")),
    ("politics", ("election", "senate", "governor", "president", "presidential",
                  "primary", "caucus", "control of", "majority", "house seat",
                  "parliament", "prime minister", "referendum", "nomination",
                  "mayor", "reelect")),
]
_OTHER_SPORTS = ("ufc", "heavyweight", "tennis", "wimbledon", "us open", "golf",
                 "pga", "formula 1", " f1 ", "nascar", "ncaa", "wnba", "boxing")


def classify(title, event_slug=""):
    hay = f" {(title or '').lower()} | {(event_slug or '').lower()} "
    for b, kws in _EXCLUDE:
        if any(k in hay for k in kws):
            return b
    if any(k in hay for k in _ESPORTS):
        if any(k in hay for k in _MATCH_MARK):
            return "esports_match"
        return "esports_series"   # (BOn)/series or bare-ambiguous -> parked (conservative)
    for b, kws in _PRIMARY_RULES:
        if any(k in hay for k in kws):
            return b
    if any(k in hay for k in _OTHER_SPORTS):
        return "other_sports"
    return "other_unknown"


# --- fetch with 429 backoff -------------------------------------------------
async def _paged(fetch, page_size, *, max_pages, pace):
    out, capped = [], False
    for i in range(max_pages):
        offset = i * page_size
        page = None
        for attempt in range(len(_RATE_BACKOFF) + 1):
            try:
                page = await fetch(limit=page_size, offset=offset)
                break
            except PolymarketRateLimitError:
                if attempt == len(_RATE_BACKOFF):
                    _log(f"    429 gave up at offset={offset} after retries")
                    return out, capped
                await asyncio.sleep(_RATE_BACKOFF[attempt])
            except PolymarketDataAPIError as e:
                _log(f"    stop at offset={offset}: {e}")
                return out, capped
        if not page:
            return out, capped
        out.extend(page)
        if len(page) < page_size:
            return out, capped
        await asyncio.sleep(pace)
    return out, True   # ran all pages without a short page -> possibly truncated


async def _closed(client, wallet, *, pace):
    return await _paged(lambda **kw: client.fetch_closed_positions(wallet, **kw),
                        _CP_PAGE, max_pages=80, pace=pace)


async def _activity(client, wallet, *, pace):
    return await _paged(lambda **kw: client.fetch_activity(wallet, **kw),
                        _ACT_PAGE, max_pages=120, pace=pace)


def _quality_bar(realized_full, clean_hold, infl, fav, n_res, clust):
    reasons = []
    if realized_full <= 0:
        reasons.append(f"realized<=0 ({realized_full:.0f})")
    if clean_hold <= 0:
        reasons.append(f"clean_hold<=0 ({clean_hold:.0f})")
    if infl > MAX_INFLATION_RATIO:
        reasons.append(f"held_inflation>{MAX_INFLATION_RATIO} ({infl:.2f})")
    if fav > MAX_FAVORITE_SHARE:
        reasons.append(f"favorite_farm ({fav:.0%}>85c)")
    if n_res < MIN_RESOLVED:
        reasons.append(f"n<{MIN_RESOLVED} ({n_res})")
    if clust > MAX_EVENT_CONCENTRATION:
        reasons.append(f"1-event {clust:.0%}")
    return ("PASS" if not reasons else "FAIL"), reasons


def _resolutions_from_closed(closed):
    """group_fills_by_decision-compatible resolutions built from closed-positions
    -- avoids the Cloudflare-prone gamma endpoint entirely. A market in
    closed-positions IS resolved; a position with cur_price>=0.9 identifies its
    outcome_index as the market winner. Losing-side decisions need no winner --
    their realized is (sells - buys) with zero redeem, which is correct."""
    winners, cids = {}, set()
    for cp in closed:
        cids.add(cp.condition_id)
        if cp.cur_price >= 0.9:
            winners[cp.condition_id] = cp.outcome_index
    return {cid: {"status": "resolved", "winning_outcome_index": winners.get(cid),
                  "yes_won": None, "closed": True, "outcomes": [], "outcome_prices": []}
            for cid in cids}


async def _audit_candidate(client, entry, *, as_of, pace):
    wallet, name = entry.proxy_wallet, entry.user_name
    closed, cp_capped = await _closed(client, wallet, pace=pace)
    if not closed:
        return {"wallet": wallet, "name": name, "stage": "no_closed_positions"}

    buckets, closed_by_key = {}, {}
    for cp in closed:
        b = classify(cp.title, cp.event_slug)
        buckets[b] = buckets.get(b, 0) + 1
        closed_by_key[(cp.condition_id, cp.outcome_index)] = cp
    total_n = len(closed)
    dominant = max(buckets.items(), key=lambda kv: kv[1])[0]
    primary_n = sum(n for b, n in buckets.items() if b in PRIMARY)
    primary_share = primary_n / total_n
    top_primary = max(((b, n) for b, n in buckets.items() if b in PRIMARY),
                      key=lambda x: x[1], default=(None, 0))[0]
    base = {
        "wallet": wallet, "name": name, "vol": round(entry.vol, 0),
        "lb_pnl": round(entry.pnl, 0), "n_closed": total_n, "dominant": dominant,
        "primary_share": round(primary_share, 3),
        "bucket_n": dict(sorted(buckets.items(), key=lambda kv: -kv[1])),
        "cp_capped": cp_capped,
    }
    # audit specialists (dominant matchable) + matchable-heavy (>=50% matchable)
    if not (dominant in PRIMARY or dominant == "esports_match" or primary_share >= 0.5):
        base["stage"] = "classified_only"
        return base
    category = dominant if (dominant in PRIMARY or dominant == "esports_match") else top_primary

    # Stage 2 audit. Resolutions derived from closed-positions (no gamma).
    # Realized stays REDEEM-GROUNDED from fills; the glitchy closed-positions
    # realizedPnl field is never summed into any score (Sassy-Bucket lesson:
    # its closed-sum was -$3.47M for a +$355k leaderboard whale).
    activity, act_capped = await _activity(client, wallet, pace=pace)
    resolutions = _resolutions_from_closed(closed)
    report = build_audit_report(leaderboard_entry=entry, activity_rows=activity,
                                resolutions=resolutions, proxy_wallet=wallet)
    rp = report.realized_pnl
    decisions = group_fills_by_decision(activity, resolutions)
    trades = []
    for (cid, oi), d in decisions.items():
        if not d.is_resolved:
            continue
        cp = closed_by_key.get((cid, oi))
        ts = cp.timestamp if cp else max(
            (r.timestamp for r in (*d.buy_rows, *d.sell_rows)), default=0)
        avg = cp.avg_price if cp else d.weighted_avg_buy_price
        trades.append(ResolvedTrade(
            cid, oi, d.realized_pnl, ts, avg,
            d.sell_share < DEFAULT_PARTIAL_SELL_THRESHOLD, d.held_to_resolution_pnl))
    rec = score_recency(wallet, name, trades, half_life_days=HALF_LIFE, as_of_ts=as_of,
                        held_inflation_ratio=rp.pnl_inflation_ratio)
    verdict, reasons = _quality_bar(
        rp.realized_pnl_usdc, rp.pnl_from_clean_holds_usdc, rp.pnl_inflation_ratio,
        report.edge.share_above_85, report.n_resolved_decisions,
        report.category.largest_event_share)
    base.update({
        "stage": "audited", "category": category,
        "realized": round(rp.realized_pnl_usdc, 0),
        "clean_hold": round(rp.pnl_from_clean_holds_usdc, 0),
        "held_inflation": round(rp.pnl_inflation_ratio, 3),
        "favorite_share": round(report.edge.share_above_85, 3),
        "n_resolved": report.n_resolved_decisions,
        "resolution_coverage": round(report.n_resolved_decisions / total_n, 2),
        "event_concentration": round(report.category.largest_event_share, 3),
        "act_capped": act_capped, "n_recency_trades": len(trades),
        "recency_trend": rec.trend,
        "recency_ratio": (None if rec.recent_vs_lifetime is None
                          else round(rec.recent_vs_lifetime, 2)),
        "recency_last_active": _iso(rec.last_active_ts),
        "verdict": verdict, "fail_reasons": reasons,
    })
    return base


async def _main_async(args):
    as_of = int(datetime.strptime(args.as_of, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc).timestamp()) if args.as_of else int(time.time())
    plan = [("Sports", args.sports_top), ("Politics", args.politics_top)]
    results, seen = [], set()
    async with PolymarketDataAPIClient() as client:
        candidates = []
        for cat, top in plan:
            lb = await client.fetch_leaderboard(category=cat, limit=top)
            _log(f"leaderboard {cat}: {len(lb)} entries")
            for e in lb:
                if e.proxy_wallet and e.proxy_wallet not in seen:
                    seen.add(e.proxy_wallet)
                    candidates.append((cat, e))
            await asyncio.sleep(args.pace)
        _log(f"[discover] {len(candidates)} unique candidates; as_of={_iso(as_of)}")

        for i, (src_cat, e) in enumerate(candidates):
            _log(f"[{i+1}/{len(candidates)}] {src_cat}:{e.user_name or e.proxy_wallet[:10]} "
                 f"vol=${e.vol:,.0f} ...")
            try:
                r = await _audit_candidate(client, e, as_of=as_of, pace=args.pace)
            except Exception as ex:  # noqa: BLE001
                r = {"wallet": e.proxy_wallet, "name": e.user_name,
                     "stage": "error", "error": f"{type(ex).__name__}: {ex}"}
                _log(f"    ERROR {type(ex).__name__}: {ex}")
            r["src_leaderboard"] = src_cat
            results.append(r)
            with open(args.out, "w", encoding="utf-8") as f:   # incremental
                json.dump({"as_of": as_of, "results": results}, f, default=str, indent=1)
            v = r.get("verdict", r.get("stage"))
            _log(f"    -> {r.get('dominant', '?')} | {v}")
            await asyncio.sleep(args.pace)

    _print_summary(results)
    _log(f"wrote {args.out} ({len(results)} candidates)")
    return 0


def _print_summary(results):
    audited = [r for r in results if r.get("stage") == "audited"]
    passed = [r for r in audited if r.get("verdict") == "PASS"]
    print("=" * 100)
    print(f"DISCOVERY SUMMARY: {len(results)} candidates classified, {len(audited)} audited, "
          f"{len(passed)} PASS the quality bar")
    by_cat = {}
    for r in passed:
        by_cat.setdefault(r["category"], []).append(r)
    print("-" * 100)
    print("PRIMARY POOL (PASS), by category, ranked by clean-hold realized:")
    print(f"{'category':<16}{'#pass':>6}  whales (realized / clean-hold / recency / n)")
    for cat in ["nfl", "nba", "mlb", "nhl", "soccer", "awards_culture", "cpi_fed", "politics"]:
        rows = sorted(by_cat.get(cat, []), key=lambda r: -r["clean_hold"])
        names = "; ".join(f"{r['name'][:14]}(${r['realized']:,.0f}/${r['clean_hold']:,.0f}"
                          f"/{r['recency_trend'][:4]}/{r['n_resolved']})" for r in rows[:6])
        print(f"{cat:<16}{len(rows):>6}  {names or '-'}")
    em = [r for r in audited if r.get("category") == "esports_match"]
    em_pass = [r for r in em if r.get("verdict") == "PASS"]
    es_series = [r for r in results if r.get("dominant") == "esports_series"]
    excl = [r for r in results if r.get("stage") == "classified_only"
            and r.get("dominant") not in ("esports_series",)]
    print("-" * 100)
    print(f"ESPORTS MATCH-scoped (transferable): {len(em_pass)} PASS / {len(em)} audited")
    print(f"ESPORTS SERIES (parked, needs decomposition): {len(es_series)} whales (count only)")
    print(f"OTHER/EXCLUDED (classified-only, non-matchable dominant): {len(excl)}")
    print("=" * 100)
    total_primary_pass = len(passed)
    print(f"GO/NO-GO: {total_primary_pass} PRIMARY-matchable whales pass the full quality bar.")


def _build_parser():
    p = argparse.ArgumentParser(prog="discover_matchable_whales")
    p.add_argument("--sports-top", type=int, default=40)
    p.add_argument("--politics-top", type=int, default=40)
    p.add_argument("--as-of", default="2026-08-15")
    p.add_argument("--pace", type=float, default=1.5, help="seconds between API calls")
    p.add_argument("--out", default="/tmp/matchable_discovery_out.json")
    return p


def main():
    return asyncio.run(_main_async(_build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
