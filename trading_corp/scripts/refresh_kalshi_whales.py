"""K3 Kalshi whale selection refresh — quarterly orchestrator.

One-off script. Pulls Kalshi leaderboards via Apify, enriches top candidates
with profile + closed_positions, scores them via Wilson-LCB × ROI × category
bonus, and writes the selected list to `agent_state(kalshi_copy_trader.
selected_whales)` for the strategy loop to consume.

Why a script (not a scheduled loop): selection cadence is quarterly; an
autonomous loop is overkill. Running this manually also lets the human
eyeball candidates before committing — important on first runs.

Usage::

    python -m trading_corp.scripts.refresh_kalshi_whales [options]

Options:
    --top-n N              Cap selected whales globally (default 12)
    --per-category N       Top N per category before global dedup (default 2)
    --candidates N         Top N to enrich from each leaderboard (default 10)
    --time TF              Leaderboard window
                           (daily|weekly|monthly|yearly|all_time; default all_time)
    --metric M             Leaderboard sort
                           (volume|projected_pnl|num_markets_traded; default volume)
    --categories C,C       Override category list (default: 6-cat preset)
    --min-sample N         Min closed_positions to include (default 20)
    --dry-run              Print picks; don't write to agent_state
    --json                 Emit JSON to stdout instead of human table

Cost: ~$1.55 per run on Bronze (6 leaderboards × 100 rows × $0.001
+ ~60 profile + closed_positions enrichments). Quarterly cadence
amortizes to ~$0.50/mo.

Output goes to:
  agent_state(kalshi_copy_trader, "selected_whales")     -> list[str]
  agent_state(kalshi_copy_trader, "selection_metadata")  -> dict (audit)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_corp.data.kalshi_apify_client import (
    KalshiApifyClient, LeaderboardEntry, WhalePosition, WhaleProfile,
)
from trading_corp.data.kalshi_whale_stats import (
    KALSHI_CATEGORIES, ScoredWhale, WhaleStats,
    compute_stats, filter_leaderboard_for_discovery, score_whale,
)
from trading_corp.persistence.db import set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)


# Categories we ACTUALLY pull leaderboards for. Subset of KALSHI_CATEGORIES —
# these are the high-volume Kalshi categories where copy-trading is most
# likely to find consistent edge. URL-encoded form (Apify input).
_DEFAULT_CATEGORIES_INPUT = (
    "Politics", "Sports", "Crypto", "Economics",
    "Climate+and+Weather", "Financials",
)


async def refresh_whale_selection(
    *,
    apify_token: str,
    db_url: str,
    top_n_global: int = 12,
    top_per_category: int = 2,
    candidates_per_category: int = 10,
    time_window: str = "all_time",
    metric: str = "volume",
    categories: tuple[str, ...] = _DEFAULT_CATEGORIES_INPUT,
    min_sample: int = 20,
    min_composite: float = 0.30,
    watch_only_n: int = 20,
    watch_only_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full discovery → score → select pipeline. Returns a dict
    summarizing what would be written (and writes it unless dry_run)."""
    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "top_n_global": top_n_global,
            "top_per_category": top_per_category,
            "candidates_per_category": candidates_per_category,
            "time_window": time_window,
            "metric": metric,
            "categories": list(categories),
            "min_sample": min_sample,
            "min_composite": min_composite,
        },
        "categories_pulled": [],
        "candidate_pool": [],
        "scored": [],
        "selected_whales": [],
        "selection_details": [],
    }

    async with KalshiApifyClient(apify_token) as client:
        # 1. Pull leaderboards (one per category) and build the candidate set.
        candidate_handles: set[str] = set()
        leaderboards_by_cat: dict[str, list[LeaderboardEntry]] = {}
        for cat in categories:
            lb = await client.fetch_leaderboard(
                name=metric, time=time_window, category=cat,
            )
            leaderboards_by_cat[cat] = lb
            handles = filter_leaderboard_for_discovery(
                lb, skip_anonymous=True, max_rank=None,
            )[:candidates_per_category]
            candidate_handles.update(handles)
            summary["categories_pulled"].append({
                "category": cat,
                "leaderboard_rows": len(lb),
                "top_handles": handles,
            })
        candidates = sorted(candidate_handles)
        summary["candidate_pool"] = candidates
        log.info("refresh_kalshi_whales: %d unique candidates across %d categories",
                 len(candidates), len(categories))

        if not candidates:
            summary["error"] = "no_candidates_after_filter"
            return summary

        # 2. Enrich each candidate: profile + closed_positions (batched calls).
        profiles = await client.fetch_profiles(candidates)
        closed = await client.fetch_closed_positions(candidates)
        profile_by_name: dict[str, WhaleProfile] = {p.nickname: p for p in profiles}

        # 3. Build WhaleStats per candidate.
        stats_by_name: dict[str, WhaleStats] = {}
        for handle in candidates:
            stats = compute_stats(
                handle, closed, profile=profile_by_name.get(handle), venue="kalshi",
            )
            stats_by_name[handle] = stats

        # 4. Score each whale once per Apify-category-key (so the category-bonus
        # weights correctly). Apify's leaderboard input uses URL-encoded names;
        # WhaleProfile.top_categories uses the human form (e.g. "Climate" vs
        # "Climate+and+Weather"). The category-bonus function already handles
        # the substring match in both directions.
        scored_by_category: dict[str, list[ScoredWhale]] = {}
        for cat in categories:
            scored_for_cat: list[ScoredWhale] = []
            for handle in candidates:
                scored = score_whale(
                    stats_by_name[handle], target_category=cat,
                    min_closed_positions=min_sample,
                )
                scored_for_cat.append(scored)
            scored_by_category[cat] = scored_for_cat

        # 5. Diverse selection: first take top-N per category (gives cross-cat
        # coverage), dedup with highest score winning, then top up from the
        # global viable pool to fill `top_n_global`. Without the top-up step,
        # if the same 3 whales dominate all 6 categories' top-2, we'd select
        # only 3 even when many more viable whales exist.
        best_by_handle: dict[str, ScoredWhale] = {}
        for cat, scored_list in scored_by_category.items():
            valid = [s for s in scored_list if not s.excluded
                     and s.composite_score >= min_composite]
            valid.sort(key=lambda s: s.composite_score, reverse=True)
            for sw in valid[:top_per_category]:
                handle = sw.stats.nickname
                existing = best_by_handle.get(handle)
                if existing is None or sw.composite_score > existing.composite_score:
                    best_by_handle[handle] = sw

        # Full viable pool — used for both top-up fill AND the runner-up
        # watch-only list. Hoisted out of the conditional so the watch-only
        # path doesn't depend on whether top-up fired.
        all_scored: dict[str, ScoredWhale] = {}
        for scored_list in scored_by_category.values():
            for sw in scored_list:
                if sw.excluded or sw.composite_score < min_composite:
                    continue
                handle = sw.stats.nickname
                existing = all_scored.get(handle)
                if existing is None or sw.composite_score > existing.composite_score:
                    all_scored[handle] = sw

        if len(best_by_handle) < top_n_global:
            # Fill remaining slots from the global viable pool — but ONLY
            # whales clearing the `min_composite` quality floor. Bad whales
            # (Wilson LCB ≈ 0, negative edge) shouldn't fill empty slots.
            leftover = [
                s for h, s in all_scored.items() if h not in best_by_handle
            ]
            leftover.sort(key=lambda s: s.composite_score, reverse=True)
            needed = top_n_global - len(best_by_handle)
            for sw in leftover[:needed]:
                best_by_handle[sw.stats.nickname] = sw

        finalists = sorted(
            best_by_handle.values(), key=lambda s: s.composite_score, reverse=True,
        )[:top_n_global]
        selected = [s.stats.nickname for s in finalists]
        selected_set = set(selected)

        # Runner-ups for the watch-only list — viable scored pool minus
        # the finalists, top `watch_only_n` by composite score. Shape
        # mirrors what scripts/seed_kalshi_watchlist.py writes so the
        # daily stats refresher and dashboard consume both transparently.
        runner_ups = sorted(
            (s for h, s in all_scored.items() if h not in selected_set),
            key=lambda s: s.composite_score, reverse=True,
        )[:watch_only_n]

        summary["selected_whales"] = selected
        summary["selection_details"] = [
            {
                "rank": i + 1,
                "handle": s.stats.nickname,
                "composite_score": round(s.composite_score, 4),
                "wilson_lcb": round(s.wilson_lcb, 4),
                "edge_factor": round(s.edge_factor, 3),
                "category_bonus": round(s.category_bonus, 2),
                "best_target_category": s.target_category,
                "closed_positions": s.stats.closed_positions_count,
                "wins": s.stats.wins,
                "win_rate": round(s.stats.win_rate, 3),
                "avg_pnl_per_contract": round(s.stats.avg_pnl_per_contract, 4),
                "top_categories": list(s.stats.top_categories),
                "lifetime_num_markets_traded": s.stats.lifetime_num_markets_traded,
            }
            for i, s in enumerate(finalists)
        ]
        summary["runner_ups_count"] = len(runner_ups)
        summary["runner_ups_details"] = [
            {
                "rank_after_top_n": i + 1 + top_n_global,
                "handle": s.stats.nickname,
                "composite_score": round(s.composite_score, 4),
                "best_target_category": s.target_category,
                "top_categories": list(s.stats.top_categories),
                "closed_positions": s.stats.closed_positions_count,
            }
            for i, s in enumerate(runner_ups)
        ]

        # Diagnostics: how many candidates dropped at each filter stage.
        n_no_visibility = sum(
            1 for h in candidates if stats_by_name[h].closed_positions_count == 0
        )
        n_sample_short = sum(
            1 for h in candidates
            if 0 < stats_by_name[h].closed_positions_count < min_sample
        )
        n_passed = len(candidates) - n_no_visibility - n_sample_short
        summary["filters"] = {
            "candidates": len(candidates),
            "no_visibility": n_no_visibility,
            "sample_short": n_sample_short,
            "passed_filters": n_passed,
        }

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    now_iso = summary["finished_at"]
    watch_only_payload = [
        {
            "handle": s.stats.nickname,
            "tier": None,
            "source": "leaderboard_runner_up",
            "source_x_handle": None,
            "notes": (
                f"Leaderboard runner-up #{i + 1 + top_n_global} "
                f"(composite={s.composite_score:.3f}, "
                f"best fit: {s.target_category or 'generalist'})"
            ),
            "included_iso": now_iso,
            "composite_score": round(s.composite_score, 4),
            "probe": {
                "profile_resolved": True,
                "closed_positions": s.stats.closed_positions_count,
                "wilson_lcb": round(s.wilson_lcb, 4),
            },
        }
        for i, s in enumerate(runner_ups)
    ]

    if not dry_run:
        if not watch_only_only:
            set_agent_state(
                "kalshi_copy_trader", "selected_whales", selected, db_url=db_url,
            )
        set_agent_state(
            "kalshi_copy_trader", "selection_metadata", summary, db_url=db_url,
        )
        # Always overwrite watch_only_whales — the runner-up list IS the
        # current snapshot of what we observe-but-don't-copy.
        set_agent_state(
            "kalshi_copy_trader", "watch_only_whales", watch_only_payload,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== K3 Whale Selection — {summary['started_at']} ===")
    print()
    p = summary["params"]
    print(
        f"Leaderboard pulls: {len(p['categories'])} categories × "
        f"{p['metric']} × {p['time_window']}"
    )
    f = summary.get("filters", {})
    print(
        f"Candidates surveyed: {f.get('candidates', 0)}  |  "
        f"opaque (no visibility): {f.get('no_visibility', 0)}  |  "
        f"sample-short (<{p['min_sample']}): {f.get('sample_short', 0)}  |  "
        f"passed: {f.get('passed_filters', 0)}"
    )
    print()
    print(f"Selected top {len(summary['selected_whales'])} whales:")
    print()
    print(
        f"{'#':>3} | {'Handle':<22} | {'Score':>7} | {'Wilson':>7} | "
        f"{'PnL/c':>8} | {'N':>5} | {'WR':>5} | {'Best Cat':<22} | Top Categories"
    )
    print("-" * 130)
    for d in summary["selection_details"]:
        print(
            f"{d['rank']:>3} | {d['handle']:<22} | "
            f"{d['composite_score']:>7.4f} | {d['wilson_lcb']:>7.4f} | "
            f"{d['avg_pnl_per_contract']:>+8.4f} | {d['closed_positions']:>5} | "
            f"{d['win_rate']:>5.2f} | {str(d['best_target_category']):<22} | "
            f"{', '.join(d['top_categories'])}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--per-category", type=int, default=2)
    parser.add_argument("--candidates", type=int, default=10)
    parser.add_argument(
        "--time", choices=("daily", "weekly", "monthly", "yearly", "all_time"),
        default="all_time",
    )
    parser.add_argument(
        "--metric", choices=("volume", "projected_pnl", "num_markets_traded"),
        default="volume",
    )
    parser.add_argument(
        "--categories",
        default=",".join(_DEFAULT_CATEGORIES_INPUT),
        help="Comma-separated. Use Apify input form (e.g. 'Climate+and+Weather').",
    )
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument(
        "--min-composite", type=float, default=0.30,
        help="Quality floor on composite score (default 0.30). Filters out "
             "whales below this even if they pass sample/visibility filters.",
    )
    parser.add_argument(
        "--watch-only-n", type=int, default=20,
        help="How many runner-up scored whales to persist as watch_only_whales "
             "(default 20). They appear on the Watch List dashboard panel "
             "but are NOT copy-traded.",
    )
    parser.add_argument(
        "--watch-only-only", action="store_true",
        help="Skip writing selected_whales (so the live K3 copy roster is "
             "untouched). Still writes selection_metadata and watch_only_whales.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    secrets = load_secrets()
    if not secrets.apify_api_token:
        print("ERROR: APIFY_API_TOKEN not set (env or KV)", file=sys.stderr)
        return 1

    categories = tuple(c.strip() for c in args.categories.split(",") if c.strip())
    summary = asyncio.run(refresh_whale_selection(
        apify_token=secrets.apify_api_token,
        db_url=secrets.db_url,
        top_n_global=args.top_n,
        top_per_category=args.per_category,
        candidates_per_category=args.candidates,
        time_window=args.time,
        metric=args.metric,
        categories=categories,
        min_sample=args.min_sample,
        min_composite=args.min_composite,
        watch_only_n=args.watch_only_n,
        watch_only_only=args.watch_only_only,
        dry_run=args.dry_run,
    ))

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary)
        if args.dry_run:
            print("(dry-run — NOT written to agent_state)")
        else:
            if args.watch_only_only:
                print(
                    f"Written to agent_state(kalshi_copy_trader.watch_only_whales) "
                    f"— {summary.get('runner_ups_count', 0)} runner-ups. "
                    "selected_whales LEFT UNTOUCHED (--watch-only-only)."
                )
            else:
                print(
                    f"Written to agent_state(kalshi_copy_trader.selected_whales) "
                    f"({len(summary['selected_whales'])}) and "
                    f".watch_only_whales ({summary.get('runner_ups_count', 0)})."
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
