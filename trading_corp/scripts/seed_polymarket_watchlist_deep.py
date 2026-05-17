"""Polymarket watch-only watchlist deep seed — true-PnL whale discovery.

Finds the top-N most-profitable Polymarket wallets that pass a quality gate
and writes them to `agent_state(polymarket_copy_trader, watch_only_whales)`.
These wallets are observation-only: no ProposedOrders are ever emitted from
this list. The copy-trade roster lives separately in `selected_whales`.

Pipeline:
  1. Pull `/v1/leaderboard?category=<C>` for each working category + global,
     paginated to `candidates_per_category` rows per key. Dedupe wallets.
  2. For each candidate wallet, fetch `/activity?user=<wallet>&limit=N`
     (single call by default; paginate if --activity-pages > 1).
  3. Batch-fetch market resolutions for every unique condition_id seen
     across all wallets' BUY activity (gamma-api /markets).
  4. Per wallet, call `compute_polymarket_stats` (the same helper used by
     the live copy-roster refresh) to determine wins, losses, and total
     realized PnL on resolved BUYs.
  5. Quality gate: closed_positions_count >= min_positions
     AND wins/closed_positions_count >= min_win_rate.
  6. Rank survivors by descending total realized PnL (USDC). Take top-N.
  7. Write to `agent_state(polymarket_copy_trader, watch_only_whales)`.

Why this path (vs `/closed-positions`):
  `/closed-positions` only surfaces positions with positive realizedPnl —
  true losses (held to zero, negative PnL) don't appear. That makes any
  win-rate computed from it always near 100% and any profit-sum a
  one-sided upper bound. Going through `/activity` + gamma-api joins is
  slower but yields true wins/losses (winning resolution → BUY's
  outcome_index matched the market's winning_outcome_index).

Cost: $0 — all endpoints are free public.

Usage::

    python -m trading_corp.scripts.seed_polymarket_watchlist_deep [opts]

Options:
    --categories C1,C2,...  Leaderboard categories (default: all 5 working)
    --candidates N          Top-N to consider per category (default 500)
    --top N                 Final watchlist size (default 50)
    --min-positions N       Min resolved positions gate (default 100)
    --min-win-rate F        Min win rate gate [0.0-1.0] (default 0.70)
    --activity-limit N      /activity rows per call (default 500, max 1000)
    --activity-pages N      Pages of activity per wallet (default 2)
    --dry-run               Print results; don't write to agent_state
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
    POLYMARKET_LEADERBOARD_CATEGORIES,
    ActivityRow,
    PolymarketDataAPIClient,
    PolymarketDataAPIError,
)
from trading_corp.data.polymarket_whale_stats import compute_polymarket_stats
from trading_corp.persistence.db import set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)

_LEADERBOARD_PAGE = 50  # data-api caps /v1/leaderboard at 50 rows per call


async def _fetch_wallet_activity(
    client: PolymarketDataAPIClient,
    wallet: str,
    *,
    activity_limit: int,
    activity_pages: int,
) -> list[ActivityRow]:
    """Fetch up to `activity_pages` pages of `/activity` for one wallet."""
    out: list[ActivityRow] = []
    for page_idx in range(activity_pages):
        offset = page_idx * activity_limit
        try:
            page = await client.fetch_activity(
                wallet, limit=activity_limit, offset=offset,
            )
        except PolymarketDataAPIError as e:
            log.warning(
                "activity fetch failed at offset=%d for %s: %s",
                offset, wallet[:10], e,
            )
            break
        if not page:
            break
        out.extend(page)
        if len(page) < activity_limit:
            break
    return out


async def seed_polymarket_watchlist_deep(
    *,
    db_url: str,
    candidates_per_category: int = 500,
    top_n: int = 50,
    min_positions: int = 100,
    min_win_rate: float = 0.70,
    activity_limit: int = 500,
    activity_pages: int = 2,
    categories: tuple[str, ...] = POLYMARKET_LEADERBOARD_CATEGORIES,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full pipeline. Returns a summary dict; writes to agent_state
    unless `dry_run=True`."""
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "params": {
            "candidates_per_category": candidates_per_category,
            "top_n": top_n,
            "min_positions": min_positions,
            "min_win_rate": min_win_rate,
            "activity_limit": activity_limit,
            "activity_pages": activity_pages,
            "categories": list(categories),
        },
        "leaderboards_pulled": [],
        "unique_candidates": 0,
        "with_activity": 0,
        "quality_gate_pass": 0,
        "watch_only_whales": [],
    }

    async with PolymarketDataAPIClient() as client:
        # 1. Pull leaderboard per category + global; paginate via offset.
        candidates: dict[str, dict[str, Any]] = {}
        for cat in list(categories) + [None]:
            label = cat or "GLOBAL"
            lb: list = []
            fetch_error: str | None = None
            offset = 0
            while len(lb) < candidates_per_category:
                try:
                    page = await client.fetch_leaderboard(
                        category=cat, limit=_LEADERBOARD_PAGE, offset=offset,
                    )
                except PolymarketDataAPIError as e:
                    fetch_error = str(e)
                    log.warning(
                        "leaderboard pull failed for %s at offset=%d: %s",
                        label, offset, e,
                    )
                    break
                if not page:
                    break
                lb.extend(page)
                if len(page) < _LEADERBOARD_PAGE:
                    break
                offset += _LEADERBOARD_PAGE
            lb = lb[:candidates_per_category]
            summary["leaderboards_pulled"].append(
                {"category": label, "rows": len(lb),
                 **({"error": fetch_error} if fetch_error else {})}
            )
            if fetch_error and not lb:
                continue
            for entry in lb:
                if not entry.proxy_wallet:
                    continue
                if entry.proxy_wallet not in candidates:
                    candidates[entry.proxy_wallet] = {
                        "entry": entry,
                        "best_category": label,
                        "best_rank": entry.rank,
                        "lifetime_pnl_from_leaderboard": entry.pnl,
                        "lifetime_vol_from_leaderboard": entry.vol,
                    }
                else:
                    existing = candidates[entry.proxy_wallet]
                    if entry.rank < existing["best_rank"]:
                        existing["best_rank"] = entry.rank
                        existing["best_category"] = label

        summary["unique_candidates"] = len(candidates)
        log.info(
            "seed_polymarket_watchlist_deep: %d unique candidates from %d category buckets",
            len(candidates), len(list(categories)) + 1,
        )

        # 2. Fetch /activity for each candidate, collecting condition_ids.
        all_condition_ids: set[str] = set()
        activity_by_wallet: dict[str, list[ActivityRow]] = {}
        for wallet in candidates:
            acts = await _fetch_wallet_activity(
                client, wallet,
                activity_limit=activity_limit, activity_pages=activity_pages,
            )
            activity_by_wallet[wallet] = acts
            if acts:
                summary["with_activity"] += 1
            for a in acts:
                if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                    all_condition_ids.add(a.condition_id)
        log.info(
            "seed_polymarket_watchlist_deep: %d wallets with activity, "
            "%d unique condition_ids across all BUYs",
            summary["with_activity"], len(all_condition_ids),
        )

        # 3. Batch-fetch market resolutions.
        resolutions = await client.fetch_market_resolutions(list(all_condition_ids))

        # 4. Compute stats per wallet, apply quality gate.
        survivors: list[dict[str, Any]] = []
        for wallet, rec in candidates.items():
            entry = rec["entry"]
            activity = activity_by_wallet.get(wallet, [])
            if not activity:
                continue
            stats, _outcomes = compute_polymarket_stats(
                leaderboard_entry=entry,
                activity_rows=activity,
                market_resolutions=resolutions,
            )
            closed = stats.closed_positions_count
            win_rate = stats.wins / closed if closed > 0 else 0.0
            if closed < min_positions or win_rate < min_win_rate:
                continue
            summary["quality_gate_pass"] += 1
            survivors.append({
                "proxy_wallet": wallet,
                "user_name": entry.user_name,
                "x_username": entry.x_username,
                "verified_badge": entry.verified_badge,
                "best_category": rec["best_category"],
                "lifetime_pnl_from_leaderboard": rec["lifetime_pnl_from_leaderboard"],
                "lifetime_vol_from_leaderboard": rec["lifetime_vol_from_leaderboard"],
                "total_resolved_positions": closed,
                "wins": stats.wins,
                "losses": stats.losses,
                "win_rate": win_rate,
                "realized_pnl_usdc": stats.total_pnl,
                "total_usdc_size": stats.total_contracts,
            })
            log.info(
                "quality gate PASS: %s (%s) closed=%d wr=%.2f pnl=%.0f",
                wallet[:10], entry.user_name, closed, win_rate, stats.total_pnl,
            )

    # 5. Rank by descending realized PnL, take top-N.
    survivors.sort(key=lambda r: r["realized_pnl_usdc"], reverse=True)
    top_survivors = survivors[:top_n]

    now_iso = started.isoformat()
    watch_only_payload: list[dict[str, Any]] = []
    for rank_i, s in enumerate(top_survivors, start=1):
        watch_only_payload.append({
            "rank": rank_i,
            "proxy_wallet": s["proxy_wallet"],
            "user_name": s["user_name"],
            "x_username": s["x_username"],
            "verified_badge": s["verified_badge"],
            "total_resolved_positions": s["total_resolved_positions"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": round(s["win_rate"], 4),
            "realized_pnl_usdc": round(s["realized_pnl_usdc"], 2),
            "total_usdc_size_resolved": round(s["total_usdc_size"], 2),
            "lifetime_pnl_from_leaderboard": round(
                s["lifetime_pnl_from_leaderboard"], 2,
            ),
            "lifetime_vol_from_leaderboard": round(
                s["lifetime_vol_from_leaderboard"], 2,
            ),
            "best_category": s["best_category"],
            "included_iso": now_iso,
        })

    summary["watch_only_whales"] = watch_only_payload
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["stats"] = {
        "candidates": summary["unique_candidates"],
        "with_activity": summary["with_activity"],
        "quality_gate_pass": summary["quality_gate_pass"],
        "written": len(watch_only_payload),
    }

    if not dry_run:
        set_agent_state(
            "polymarket_copy_trader", "watch_only_whales", watch_only_payload,
            db_url=db_url,
        )
        set_agent_state(
            "polymarket_copy_trader", "watch_only_whales_metadata", summary,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== Polymarket Watchlist Deep Seed — {summary['started_at']} ===")
    print()
    print("Leaderboards pulled:")
    for lb in summary["leaderboards_pulled"]:
        err = f"  ERROR: {lb['error']}" if "error" in lb else ""
        print(f"  {lb['category']:<12}  {lb['rows']} rows{err}")
    s = summary.get("stats", {})
    print()
    print(
        f"Candidates: {s.get('candidates', 0)}  |  "
        f"With activity: {s.get('with_activity', 0)}  |  "
        f"Quality gate pass: {s.get('quality_gate_pass', 0)}  |  "
        f"Written: {s.get('written', 0)}"
    )
    print()
    whales = summary.get("watch_only_whales", [])
    if not whales:
        print("No wallets passed the quality gate.")
        return
    print(f"Top {len(whales)} watchlist whales (ranked by realized PnL on resolved BUYs):")
    print()
    print(
        f"{'#':>3} | {'Wallet':<14} | {'User':<22} | {'Category':<10} | "
        f"{'N':>5} | {'WR':>6} | {'PnL (USDC)':>12} | {'Vol':>14}"
    )
    print("-" * 120)
    for w in whales:
        print(
            f"{w['rank']:>3} | {w['proxy_wallet'][:14]} | "
            f"{w['user_name'][:22]:<22} | {w['best_category']:<10} | "
            f"{w['total_resolved_positions']:>5} | {w['win_rate']:>6.2%} | "
            f"${w['realized_pnl_usdc']:>11,.2f} | "
            f"${w['lifetime_vol_from_leaderboard']:>13,.2f}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--categories",
        default=",".join(POLYMARKET_LEADERBOARD_CATEGORIES),
        help="Comma-separated leaderboard categories (default: all 5 working).",
    )
    parser.add_argument(
        "--candidates", type=int, default=500,
        help="Top-N candidates to pull per category (default 500).",
    )
    parser.add_argument(
        "--top", type=int, default=50,
        help="Final watchlist size (default 50).",
    )
    parser.add_argument(
        "--min-positions", type=int, default=100,
        help="Minimum resolved positions for inclusion (default 100).",
    )
    parser.add_argument(
        "--min-win-rate", type=float, default=0.70,
        help="Minimum win rate [0.0-1.0] for inclusion (default 0.70).",
    )
    parser.add_argument(
        "--activity-limit", type=int, default=500,
        help="/activity rows per call (default 500; max ~1000).",
    )
    parser.add_argument(
        "--activity-pages", type=int, default=2,
        help="Pages of /activity to fetch per wallet (default 2).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print results without writing to agent_state.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON output instead of human table.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cats = tuple(c.strip() for c in args.categories.split(",") if c.strip())

    secrets = load_secrets()
    summary = asyncio.run(seed_polymarket_watchlist_deep(
        db_url=secrets.db_url,
        candidates_per_category=args.candidates,
        top_n=args.top,
        min_positions=args.min_positions,
        min_win_rate=args.min_win_rate,
        activity_limit=args.activity_limit,
        activity_pages=args.activity_pages,
        categories=cats,
        dry_run=args.dry_run,
    ))

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary)
        if args.dry_run:
            print("(dry-run — NOT written to agent_state)")
        else:
            n = len(summary.get("watch_only_whales", []))
            print(
                f"Written to agent_state(polymarket_copy_trader.watch_only_whales) "
                f"({n} whales)."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
