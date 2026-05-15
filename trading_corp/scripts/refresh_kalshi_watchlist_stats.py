"""K3 Watch-only stats refresh — daily cron.

For each handle in `agent_state(kalshi_copy_trader, watch_only_whales)`:
  1. fetch_profiles  → top_categories, lifetime markets traded
  2. fetch_closed_positions → win/loss/pnl aggregates via compute_stats
  3. fetch_open_positions → current open exposure count

Persists per-handle stats to
`agent_state(kalshi_copy_trader, watch_only_stats)`.

NEVER emits a ProposedOrder. This is observation-only; copy-trading
requires explicit promotion to `selected_whales` via the (future) promote
flow.

Audit kind logged: `kalshi_watch_only_refresh` (per run summary).

Cost (Bronze plan, ~14 handles): ~3 actor calls × ~$0.001-0.0015/row ≈
$0.50 per run, ≈ $15/mo at daily cadence.

Usage::

    python -m trading_corp.scripts.refresh_kalshi_watchlist_stats [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from trading_corp.agents.logger import LoggerAgent
from trading_corp.data.kalshi_apify_client import KalshiApifyClient
from trading_corp.data.kalshi_whale_stats import compute_stats
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)


async def refresh_watchlist_stats(
    *,
    apify_token: str,
    db_url: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "handles_total": 0,
        "handles_refreshed": 0,
        "handles_with_visibility": 0,
        "handles_opaque": 0,
        "errors": [],
    }

    loaded = load_agent_state(
        "kalshi_copy_trader", "watch_only_whales", db_url=db_url,
    )
    if loaded is None or not loaded[0]:
        summary["error"] = "no_watchlist"
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        return summary

    watchlist: list[dict[str, Any]] = loaded[0]
    summary["handles_total"] = len(watchlist)
    handles = [w["handle"] for w in watchlist]

    async with KalshiApifyClient(apify_token) as client:
        profiles = await client.fetch_profiles(handles)
        closed = await client.fetch_closed_positions(handles)
        open_ = await client.fetch_open_positions(handles)

    profile_by_handle = {p.nickname: p for p in profiles}
    # Count open positions per handle. WhalePosition.is_open is bool; the
    # open_positions feature returns is_open=True rows, but defensive filter
    # in case the actor leaks resolved ones.
    open_count_by_handle: dict[str, int] = {}
    for p in open_:
        if p.is_open:
            open_count_by_handle[p.name] = open_count_by_handle.get(p.name, 0) + 1

    stats_out: dict[str, dict[str, Any]] = {}
    for entry in watchlist:
        h = entry["handle"]
        try:
            stats = compute_stats(
                h, closed,
                profile=profile_by_handle.get(h),
                venue="kalshi",
            )
        except Exception as e:
            log.warning("compute_stats failed for %s: %s", h, e)
            summary["errors"].append({"handle": h, "error": str(e)})
            continue

        if stats.closed_positions_count == 0:
            summary["handles_opaque"] += 1
        else:
            summary["handles_with_visibility"] += 1

        stats_out[h] = {
            "handle": h,
            "tier": entry.get("tier"),
            "source_x_handle": entry.get("source_x_handle"),
            "notes": entry.get("notes"),
            "venue": "kalshi",
            "resolved_count": stats.closed_positions_count,
            "wins": stats.wins,
            "losses": stats.losses,
            "win_rate": round(stats.win_rate, 4),
            "total_pnl": round(stats.total_pnl, 2),
            "total_contracts": stats.total_contracts,
            "avg_pnl_per_contract": round(stats.avg_pnl_per_contract, 4),
            "top_categories": list(stats.top_categories),
            "lifetime_markets_traded": stats.lifetime_num_markets_traded,
            "n_open": open_count_by_handle.get(h, 0),
            "last_refresh_iso": started.isoformat(),
        }
        summary["handles_refreshed"] += 1

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        set_agent_state(
            "kalshi_copy_trader", "watch_only_stats", stats_out, db_url=db_url,
        )
        LoggerAgent(db_url=db_url).log_event(
            "kalshi_copy_trader", "kalshi_watch_only_refresh", summary,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== K3 Watchlist Stats Refresh — {summary['started_at']} ===")
    if summary.get("error"):
        print(f"ERROR: {summary['error']}")
        return
    print(
        f"Handles: {summary['handles_refreshed']}/{summary['handles_total']} "
        f"refreshed  |  visibility: {summary['handles_with_visibility']}  |  "
        f"opaque: {summary['handles_opaque']}"
    )
    if summary["errors"]:
        print(f"Errors: {len(summary['errors'])}")
        for e in summary["errors"]:
            print(f"  {e['handle']}: {e['error']}")
    print(f"Finished: {summary['finished_at']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    secrets = load_secrets()
    if not secrets.apify_api_token:
        print("ERROR: APIFY_API_TOKEN not set (env or KV)", file=sys.stderr)
        return 1

    summary = asyncio.run(refresh_watchlist_stats(
        apify_token=secrets.apify_api_token,
        db_url=secrets.db_url,
        dry_run=args.dry_run,
    ))

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary)
        if args.dry_run:
            print("(dry-run — NOT written to agent_state)")
        else:
            print("Written to agent_state(kalshi_copy_trader.watch_only_stats).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
