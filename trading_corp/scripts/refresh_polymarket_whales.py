"""Polymarket whale selection refresh.

One-off orchestrator. Mirrors `refresh_kalshi_whales.py` but for Polymarket:
free public APIs end-to-end (no Apify subscription).

Pipeline:
  1. Pull `/v1/leaderboard?category=<C>` for each of the 5 working
     categories (Politics, Sports, Crypto, Tech, Mentions) + global.
  2. Dedupe candidate wallets across categories.
  3. For each candidate: fetch `/activity?user=<wallet>&limit=N` (~last
     90d of trades).
  4. Batch-fetch market resolutions for every unique condition_id.
  5. Compute time-weighted Wilson LCB × ROI × category bonus per whale.
  6. Selection Rule B: top-N per category (default 2) + top-N global
     (default 2), deduped → 12 total selected.
  7. Write list of dicts (wallet + user_name + best_category + score
     breakdown) to `agent_state(polymarket_copy_trader.selected_whales)`.

Cost: $0 — all endpoints are free public.

Usage::

    python -m trading_corp.scripts.refresh_polymarket_whales [opts]

Options:
    --top-per-category N    Picks per category for Rule B (default 2)
    --top-global N          Top-N from global to fill (default 2)
    --candidates N          Top-N to enrich per category (default 20)
    --min-resolved N        Min resolved trades for inclusion (default 10)
    --half-life-days D      Recency decay half-life (default 30)
    --activity-limit N      Trades to fetch per whale (default 200)
    --dry-run               Print picks; don't write to agent_state
    --json                  JSON output instead of human table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from trading_corp.data.polymarket_data_api_client import (
    POLYMARKET_LEADERBOARD_CATEGORIES, PolymarketDataAPIClient,
)
from trading_corp.data.polymarket_whale_stats import (
    DEFAULT_HALF_LIFE_DAYS, DEFAULT_MIN_RESOLVED,
    compute_polymarket_stats, score_polymarket_whale,
)
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)


async def refresh_polymarket_selection(
    *,
    db_url: str,
    top_per_category: int = 2,
    top_global: int = 2,
    candidates_per_category: int = 20,
    min_resolved: int = DEFAULT_MIN_RESOLVED,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    activity_limit: int = 200,
    categories: tuple[str, ...] = POLYMARKET_LEADERBOARD_CATEGORIES,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full pipeline. Returns a summary dict; writes to agent_state
    unless `dry_run=True`."""
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "params": {
            "top_per_category": top_per_category,
            "top_global": top_global,
            "candidates_per_category": candidates_per_category,
            "min_resolved": min_resolved,
            "half_life_days": half_life_days,
            "activity_limit": activity_limit,
            "categories": list(categories),
        },
        "leaderboards_pulled": [],
        "selected_whales": [],
        "selection_details": [],
    }

    async with PolymarketDataAPIClient() as client:
        # 1. Pull leaderboard per category (and global).
        candidates: dict[str, dict[str, Any]] = {}  # wallet -> {entry, categories: set}
        for cat in list(categories) + [None]:
            lb = await client.fetch_leaderboard(
                category=cat, limit=candidates_per_category,
            )
            label = cat or "GLOBAL"
            summary["leaderboards_pulled"].append({
                "category": label, "rows": len(lb),
            })
            for entry in lb:
                if not entry.proxy_wallet:
                    continue
                rec = candidates.setdefault(entry.proxy_wallet, {
                    "entry": entry,
                    "categories_seen": set(),
                    "ranks_by_category": {},
                })
                rec["categories_seen"].add(label)
                rec["ranks_by_category"][label] = entry.rank

        log.info(
            "refresh_polymarket_whales: %d unique candidates across %d category buckets",
            len(candidates), len(list(categories)) + 1,
        )

        # 2. Enrich each candidate: activity (one call per whale).
        all_condition_ids: set[str] = set()
        activity_by_wallet: dict[str, list] = {}
        for wallet in candidates:
            try:
                acts = await client.fetch_activity(wallet, limit=activity_limit)
            except Exception as e:
                log.warning("activity fetch failed for %s: %s", wallet[:10], e)
                acts = []
            activity_by_wallet[wallet] = acts
            for a in acts:
                if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                    all_condition_ids.add(a.condition_id)

        log.info(
            "refresh_polymarket_whales: %d unique condition_ids across all whales' BUYs",
            len(all_condition_ids),
        )

        # 3. Batch-fetch resolutions for every unique condition_id.
        resolutions = await client.fetch_market_resolutions(list(all_condition_ids))

        # 4. Compute WhaleStats + score per (whale, target_category).
        # We score each whale once GLOBALLY (no category bonus) and once
        # per category they appeared on. Rule B then picks top-N per
        # category + top-N global, deduped by wallet (best score wins).
        scored_per_category: dict[str, list[Any]] = {}  # cat → [ScoredWhale]
        scored_global: list[Any] = []
        per_whale_best: dict[str, Any] = {}  # wallet → ScoredWhale with category context

        for wallet, rec in candidates.items():
            entry = rec["entry"]
            activity = activity_by_wallet.get(wallet, [])
            stats, _outcomes = compute_polymarket_stats(
                leaderboard_entry=entry, activity_rows=activity,
                market_resolutions=resolutions, half_life_days=half_life_days,
            )

            # Global score (no category bonus)
            scored_no_cat = score_polymarket_whale(
                stats, target_category=None, min_resolved=min_resolved,
            )
            scored_global.append((wallet, entry, scored_no_cat))

            # Per-category scores for the categories this whale appeared on
            for cat in rec["categories_seen"]:
                if cat == "GLOBAL":
                    continue
                # We can't easily set stats.top_categories to fake a match — but
                # the category bonus uses stats.top_categories. Set it.
                stats_cat = stats
                object.__setattr__(stats_cat, "top_categories", (cat,))
                scored = score_polymarket_whale(
                    stats_cat, target_category=cat, min_resolved=min_resolved,
                )
                scored_per_category.setdefault(cat, []).append((wallet, entry, scored))
                if not scored.excluded and (
                    wallet not in per_whale_best
                    or scored.composite_score > per_whale_best[wallet][2].composite_score
                ):
                    per_whale_best[wallet] = (wallet, entry, scored)

        # 5. Rule B: top-N per category + top-N global, deduped by wallet.
        selected: dict[str, tuple[Any, ...]] = {}  # wallet → (entry, scored, source_cat)
        for cat, scored_list in scored_per_category.items():
            valid = [t for t in scored_list if not t[2].excluded]
            valid.sort(key=lambda t: t[2].composite_score, reverse=True)
            for wallet, entry, sw in valid[:top_per_category]:
                if wallet in selected:
                    # Keep the higher-scoring entry
                    if sw.composite_score > selected[wallet][1].composite_score:
                        selected[wallet] = (entry, sw, cat)
                else:
                    selected[wallet] = (entry, sw, cat)

        # Add top-N global. Skip whales already selected (no double-counting).
        valid_global = [t for t in scored_global if not t[2].excluded]
        valid_global.sort(key=lambda t: t[2].composite_score, reverse=True)
        added_global = 0
        for wallet, entry, sw in valid_global:
            if wallet in selected:
                continue
            selected[wallet] = (entry, sw, "GLOBAL")
            added_global += 1
            if added_global >= top_global:
                break

        # 6. Materialize selection records for agent_state.
        finalists = sorted(
            selected.items(), key=lambda kv: kv[1][1].composite_score, reverse=True,
        )
        selected_records: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        for rank_i, (wallet, (entry, sw, source_cat)) in enumerate(finalists):
            selected_records.append({
                "wallet": wallet,
                "user_name": entry.user_name,
                "category": source_cat,
                "rank": rank_i + 1,
                "composite_score": round(sw.composite_score, 4),
            })
            details.append({
                "rank": rank_i + 1,
                "wallet": wallet,
                "user_name": entry.user_name,
                "source_category": source_cat,
                "composite_score": round(sw.composite_score, 4),
                "wilson_lcb": round(sw.wilson_lcb, 4),
                "edge_factor": round(sw.edge_factor, 3),
                "category_bonus": round(sw.category_bonus, 2),
                "lifetime_vol_usdc": round(entry.vol, 0),
                "lifetime_pnl_usdc": round(entry.pnl, 0),
                "closed_positions_count": sw.stats.closed_positions_count,
                "wins": sw.stats.wins,
                "win_rate": round(sw.stats.win_rate, 3),
                "avg_pnl_per_contract_usdc": round(sw.stats.avg_pnl_per_contract, 4),
            })

        summary["selected_whales"] = selected_records
        summary["selection_details"] = details
        summary["filters"] = {
            "candidates": len(candidates),
            "with_resolved_trades": sum(
                1 for w in candidates if any(
                    a.type == "TRADE" and a.side == "BUY"
                    for a in activity_by_wallet.get(w, [])
                )
            ),
            "resolutions_fetched": len(resolutions),
            "selected": len(finalists),
        }

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    # Merge manually-pinned whales (promoted via dashboard) into the
    # algorithm's selection so they survive this refresh. Dedupe by lower-
    # cased wallet. Without this step, dashboard promotions would be
    # silently evicted on every refresh run.
    try:
        pin_rec = load_agent_state(
            "polymarket_copy_trader", "pinned_whales", db_url=db_url,
        )
    except Exception:
        pin_rec = None
    pinned_entries = pin_rec[0] if (pin_rec and isinstance(pin_rec[0], list)) else []
    selected_wallets = {
        str(s.get("wallet") or s.get("proxy_wallet") or "").lower()
        for s in selected_records if isinstance(s, dict)
    }
    n_pinned_merged = 0
    for p in pinned_entries:
        if not isinstance(p, dict):
            continue
        w_lower = str(p.get("wallet") or p.get("proxy_wallet") or "").lower()
        if not w_lower or w_lower in selected_wallets:
            continue
        selected_records.append({
            "wallet": w_lower,
            "user_name": str(p.get("user_name") or ""),
            "category": str(p.get("category") or "pinned"),
            "rank": None,
            "composite_score": None,
            "source": "pinned_promotion",
        })
        selected_wallets.add(w_lower)
        n_pinned_merged += 1
    summary["pinned_merged"] = n_pinned_merged

    if not dry_run:
        set_agent_state(
            "polymarket_copy_trader", "selected_whales", selected_records,
            db_url=db_url,
        )
        set_agent_state(
            "polymarket_copy_trader", "selection_metadata", summary,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== Polymarket Whale Selection — {summary['started_at']} ===")
    print()
    print("Leaderboards pulled:")
    for lb in summary["leaderboards_pulled"]:
        print(f"  {lb['category']:<10}  {lb['rows']} rows")
    f = summary.get("filters", {})
    print()
    print(
        f"Candidates: {f.get('candidates', 0)}  |  "
        f"with resolved trades: {f.get('with_resolved_trades', 0)}  |  "
        f"resolutions fetched: {f.get('resolutions_fetched', 0)}  |  "
        f"selected: {f.get('selected', 0)}"
    )
    print()
    print(f"Selected top {len(summary['selected_whales'])} whales:")
    print()
    print(
        f"{'#':>3} | {'Wallet':<14} | {'User':<22} | {'Source':<10} | "
        f"{'Score':>7} | {'Wilson':>7} | {'PnL$/c':>8} | {'WR':>5} | {'N':>4} | "
        f"{'LifeVol$':>10} | {'LifePnL$':>10}"
    )
    print("-" * 130)
    for d in summary["selection_details"]:
        print(
            f"{d['rank']:>3} | {d['wallet'][:14]} | {d['user_name'][:22]:<22} | "
            f"{d['source_category']:<10} | {d['composite_score']:>7.4f} | "
            f"{d['wilson_lcb']:>7.4f} | "
            f"{d['avg_pnl_per_contract_usdc']:>+8.4f} | "
            f"{d['win_rate']:>5.2f} | {d['closed_positions_count']:>4} | "
            f"${d['lifetime_vol_usdc']:>9,.0f} | ${d['lifetime_pnl_usdc']:>9,.0f}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-per-category", type=int, default=2)
    parser.add_argument("--top-global", type=int, default=2)
    parser.add_argument("--candidates", type=int, default=20)
    parser.add_argument("--min-resolved", type=int, default=DEFAULT_MIN_RESOLVED)
    parser.add_argument("--half-life-days", type=float, default=DEFAULT_HALF_LIFE_DAYS)
    parser.add_argument("--activity-limit", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    secrets = load_secrets()

    summary = asyncio.run(refresh_polymarket_selection(
        db_url=secrets.db_url,
        top_per_category=args.top_per_category,
        top_global=args.top_global,
        candidates_per_category=args.candidates,
        min_resolved=args.min_resolved,
        half_life_days=args.half_life_days,
        activity_limit=args.activity_limit,
        dry_run=args.dry_run,
    ))

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary)
        if args.dry_run:
            print("(dry-run — NOT written to agent_state)")
        else:
            print("Written to agent_state(polymarket_copy_trader.selected_whales).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
