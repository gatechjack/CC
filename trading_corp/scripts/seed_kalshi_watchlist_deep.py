"""K3 Watch-only DEEP seed — multi-leaderboard rank-walk with visibility cache.

Why this exists: the standard `refresh_kalshi_whales.py` taps only the top-10
of each leaderboard. On 2026-05-15 a fresh `volume/all_time` pull found
47/48 candidates were Apify-opaque (closed_positions_count == 0) — Kalshi
appears to have changed default visibility so most top traders don't expose
their position history. Walking only the top of each leaderboard yields
zero runner-ups under those conditions.

This script casts a wider net:
  - Iterates `(category, time_window)` combos (default: 6 × 3 = 18 leaderboards)
  - Walks DOWN each leaderboard in rank order
  - Batch-probes candidates (groups of 10) until target_n VISIBLE whales found
  - Uses a 30-day per-handle visibility cache so re-runs skip known-opaque

Acceptance bar lowered relative to selection: `min_sample` defaults to 5
(was 20 for `selected_whales`) — these are observation candidates, not
copy-trade targets. Wilson-LCB at n=5 is loose but acceptable for "should
we even watch this person."

Output: overwrites `agent_state(kalshi_copy_trader, watch_only_whales)`
with the deep-scan results. Daily `refresh_kalshi_watchlist_stats.py`
then fills stats. Idempotent: re-running with same params is safe.

Cache: `agent_state(kalshi_copy_trader, apify_visibility_cache)` is a
dict {handle: {visibility: 'visible'|'opaque', last_probed_iso, closed_count}}.
Skipped probes save ~$0.03 per opaque handle on Bronze.

Usage::

    python -m trading_corp.scripts.seed_kalshi_watchlist_deep \\
        [--target-n N] [--max-probe N] [--min-sample N] \\
        [--time-windows w1,w2,...] [--categories c1,c2,...] [--dry-run] [--json]

Cost (Bronze, first run): ~$3-5 (18 leaderboard calls + ~50 enrichments).
Subsequent runs with warm cache: ~$0.50-$1.50.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from trading_corp.data.kalshi_apify_client import (
    KalshiApifyClient, LeaderboardEntry,
)
from trading_corp.data.kalshi_whale_stats import (
    compute_stats, filter_leaderboard_for_discovery,
)
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)


_DEFAULT_CATEGORIES = (
    "Politics", "Sports", "Crypto", "Economics",
    "Climate+and+Weather", "Financials",
)
_DEFAULT_TIME_WINDOWS = ("monthly", "weekly", "all_time")
_VISIBILITY_CACHE_KEY = "apify_visibility_cache"
_VISIBILITY_CACHE_TTL_DAYS = 30


def _load_visibility_cache(db_url: str) -> dict[str, dict[str, Any]]:
    loaded = load_agent_state(
        "kalshi_copy_trader", _VISIBILITY_CACHE_KEY, db_url=db_url,
    )
    if loaded is None:
        return {}
    cache, _ = loaded
    return cache if isinstance(cache, dict) else {}


def _is_fresh(entry: dict[str, Any], now: datetime) -> bool:
    iso = entry.get("last_probed_iso")
    if not iso:
        return False
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts) < timedelta(days=_VISIBILITY_CACHE_TTL_DAYS)
    except Exception:
        return False


async def deep_seed_watchlist(
    *,
    apify_token: str,
    db_url: str,
    target_n: int = 10,
    max_probe: int = 60,
    min_sample: int = 5,
    batch_size: int = 10,
    categories: tuple[str, ...] = _DEFAULT_CATEGORIES,
    time_windows: tuple[str, ...] = _DEFAULT_TIME_WINDOWS,
    metric: str = "volume",
    dry_run: bool = False,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "params": {
            "target_n": target_n, "max_probe": max_probe,
            "min_sample": min_sample, "batch_size": batch_size,
            "categories": list(categories),
            "time_windows": list(time_windows),
            "metric": metric,
        },
        "leaderboards_pulled": [],
        "candidate_pool_size": 0,
        "cache_skips_opaque": 0,
        "cache_skips_visible": 0,
        "newly_probed": 0,
        "newly_visible": 0,
        "newly_opaque": 0,
        "found": [],
    }

    # Don't include current selected_whales — those are the live copy roster.
    # Same handle in both lists would muddy the dashboard semantics.
    selected_loaded = load_agent_state(
        "kalshi_copy_trader", "selected_whales", db_url=db_url,
    )
    selected_set: set[str] = set()
    if selected_loaded is not None and isinstance(selected_loaded[0], list):
        selected_set = {str(h) for h in selected_loaded[0]}

    visibility_cache = _load_visibility_cache(db_url)
    now = datetime.now(timezone.utc)

    async with KalshiApifyClient(apify_token) as client:
        # 1. Pull every (category, time_window) leaderboard and build a
        #    deduped, rank-ordered candidate pool. First-seen rank wins.
        pool: list[tuple[str, int, str, str]] = []
        # tuples: (handle, rank, category, time_window) — for provenance.
        seen_handles: set[str] = set()
        for tw in time_windows:
            for cat in categories:
                try:
                    lb: list[LeaderboardEntry] = await client.fetch_leaderboard(
                        name=metric, time=tw, category=cat,
                    )
                except Exception as e:
                    log.warning("leaderboard pull failed for %s/%s: %s", cat, tw, e)
                    summary["leaderboards_pulled"].append({
                        "category": cat, "time_window": tw, "rows": 0,
                        "error": str(e),
                    })
                    continue
                summary["leaderboards_pulled"].append({
                    "category": cat, "time_window": tw, "rows": len(lb),
                })
                handles = filter_leaderboard_for_discovery(
                    lb, skip_anonymous=True, max_rank=None,
                )
                for rank, h in enumerate(handles, start=1):
                    if h in selected_set:
                        continue
                    if h in seen_handles:
                        continue
                    seen_handles.add(h)
                    pool.append((h, rank, cat, tw))
        summary["candidate_pool_size"] = len(pool)

        # 2. Walk DOWN the pool in (time_window-first, then category, then rank)
        #    order — already produced by the iteration above. For each handle:
        #      a) Cache hit + fresh + opaque → skip
        #      b) Cache hit + fresh + visible → admit immediately (no probe)
        #      c) Cache miss/stale → queue for batch probe
        found: list[dict[str, Any]] = []
        probe_queue: list[tuple[str, int, str, str]] = []
        provenance_by_handle: dict[str, tuple[int, str, str]] = {}

        for handle, rank, cat, tw in pool:
            if len(found) >= target_n:
                break
            cached = visibility_cache.get(handle)
            if cached and _is_fresh(cached, now):
                if cached.get("visibility") == "opaque":
                    summary["cache_skips_opaque"] += 1
                    continue
                if cached.get("visibility") == "visible":
                    summary["cache_skips_visible"] += 1
                    found.append({
                        "handle": handle, "rank": rank, "category": cat,
                        "time_window": tw, "source_cache": True,
                        "cached_closed_count": int(cached.get("closed_count") or 0),
                    })
                    continue
            probe_queue.append((handle, rank, cat, tw))
            provenance_by_handle[handle] = (rank, cat, tw)

        # 3. Batch-probe the queue. Stop once we have target_n found OR run
        #    past max_probe.
        async def probe_batch(names: list[str]) -> None:
            nonlocal found
            if not names:
                return
            try:
                profiles = await client.fetch_profiles(names)
                closed = await client.fetch_closed_positions(names)
            except Exception as e:
                log.warning("batch probe failed (%d names): %s", len(names), e)
                return
            profile_by_handle = {p.nickname: p for p in profiles}
            closed_by_handle: dict[str, list] = {}
            for p in closed:
                closed_by_handle.setdefault(p.name, []).append(p)
            for name in names:
                summary["newly_probed"] += 1
                prof = profile_by_handle.get(name)
                stats = compute_stats(
                    name, closed_by_handle.get(name, []) or [],
                    profile=prof, venue="kalshi",
                )
                if stats.closed_positions_count >= min_sample:
                    summary["newly_visible"] += 1
                    visibility_cache[name] = {
                        "visibility": "visible",
                        "last_probed_iso": now.isoformat(),
                        "closed_count": stats.closed_positions_count,
                    }
                    rank, cat, tw = provenance_by_handle[name]
                    found.append({
                        "handle": name, "rank": rank, "category": cat,
                        "time_window": tw, "source_cache": False,
                        "cached_closed_count": stats.closed_positions_count,
                    })
                else:
                    summary["newly_opaque"] += 1
                    visibility_cache[name] = {
                        "visibility": "opaque",
                        "last_probed_iso": now.isoformat(),
                        "closed_count": stats.closed_positions_count,
                    }

        i = 0
        while i < len(probe_queue):
            if len(found) >= target_n:
                break
            if summary["newly_probed"] >= max_probe:
                break
            batch = probe_queue[i:i + batch_size]
            await probe_batch([b[0] for b in batch])
            i += batch_size

    # 4. Build the watch_only_whales payload.
    now_iso = now.isoformat()
    watch_only_payload = [
        {
            "handle": f["handle"],
            "tier": None,
            "source": "deep_leaderboard_scan",
            "source_x_handle": None,
            "notes": (
                f"Deep-scan: rank #{f['rank']} on {f['category']}/{f['time_window']} "
                f"leaderboard ({'cached' if f['source_cache'] else 'fresh'} "
                f"closed_positions={f['cached_closed_count']})"
            ),
            "included_iso": now_iso,
            "probe": {
                "profile_resolved": True,
                "closed_positions": f["cached_closed_count"],
                "leaderboard_category": f["category"],
                "leaderboard_time_window": f["time_window"],
                "leaderboard_rank": f["rank"],
            },
        }
        for f in found
    ]
    summary["found"] = [f["handle"] for f in found]
    summary["found_count"] = len(found)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        set_agent_state(
            "kalshi_copy_trader", "watch_only_whales", watch_only_payload,
            db_url=db_url,
        )
        set_agent_state(
            "kalshi_copy_trader", _VISIBILITY_CACHE_KEY, visibility_cache,
            db_url=db_url,
        )
        set_agent_state(
            "kalshi_copy_trader", "watch_only_deep_metadata", summary,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== K3 Watchlist Deep Scan — {summary['started_at']} ===")
    p = summary["params"]
    print(
        f"Params: target_n={p['target_n']} max_probe={p['max_probe']} "
        f"min_sample={p['min_sample']}  cats={len(p['categories'])} "
        f"× windows={len(p['time_windows'])}"
    )
    print(
        f"Leaderboards: {len(summary['leaderboards_pulled'])} pulled  |  "
        f"candidate pool: {summary['candidate_pool_size']}"
    )
    print(
        f"Cache: opaque skips={summary['cache_skips_opaque']}  visible "
        f"hits={summary['cache_skips_visible']}"
    )
    print(
        f"Probed: {summary['newly_probed']} new  →  "
        f"visible={summary['newly_visible']}  opaque={summary['newly_opaque']}"
    )
    print()
    print(f"Found {summary['found_count']} watch-list whales:")
    for h in summary["found"]:
        print(f"  KEEP {h}")
    print(f"Finished: {summary['finished_at']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target-n", type=int, default=10)
    parser.add_argument("--max-probe", type=int, default=60)
    parser.add_argument("--min-sample", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument(
        "--time-windows",
        default=",".join(_DEFAULT_TIME_WINDOWS),
        help="Comma-separated leaderboard windows. Each window costs ~$0.10 "
             "per category in leaderboard pulls.",
    )
    parser.add_argument(
        "--categories",
        default=",".join(_DEFAULT_CATEGORIES),
        help="Comma-separated. Use Apify input form (e.g. 'Climate+and+Weather').",
    )
    parser.add_argument(
        "--metric", choices=("volume", "projected_pnl", "num_markets_traded"),
        default="volume",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    secrets = load_secrets()
    if not secrets.apify_api_token:
        print("ERROR: APIFY_API_TOKEN not set (env or KV)", file=sys.stderr)
        return 1

    cats = tuple(c.strip() for c in args.categories.split(",") if c.strip())
    tws = tuple(c.strip() for c in args.time_windows.split(",") if c.strip())

    summary = asyncio.run(deep_seed_watchlist(
        apify_token=secrets.apify_api_token,
        db_url=secrets.db_url,
        target_n=args.target_n,
        max_probe=args.max_probe,
        min_sample=args.min_sample,
        batch_size=args.batch_size,
        categories=cats,
        time_windows=tws,
        metric=args.metric,
        dry_run=args.dry_run,
    ))

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary)
        if args.dry_run:
            print("(dry-run — NOT written to agent_state)")
        else:
            print(
                f"Written to agent_state(kalshi_copy_trader.watch_only_whales) "
                f"({summary['found_count']} whales) + visibility_cache."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
