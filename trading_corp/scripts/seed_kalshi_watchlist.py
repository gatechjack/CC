"""K3 Watch-only seed — one-shot.

Loads `config/kalshi_watchlist_seed.yaml`, probes each handle via Apify, and
writes survivors to `agent_state(kalshi_copy_trader, watch_only_whales)`.

The watch-only path is the read-only sibling of K3's `selected_whales`:
performance is tracked daily by `refresh_kalshi_watchlist_stats.py` but
NO ProposedOrders are ever emitted from these handles. A future `[Promote]`
flow moves a watch-only handle onto the active copy-trade roster.

Survivor rules (set by user 2026-05-15):
  - Tier 1: included if `fetch_profiles` returns a row whose nickname
    matches. Big public names worth tracking even with sparse data.
  - Tier 2: included only if `fetch_trades` returns ≥ 1 row. Curators /
    aggregators that don't actually trade are filtered out.

Output shape persisted to agent_state(kalshi_copy_trader, watch_only_whales):

    [
      {
        "handle": "Domahhhh",
        "tier": 1,
        "source_x_handle": "@Domahhhh",
        "notes": "...",
        "included_iso": "2026-05-15T...",
        "probe": {"profile_resolved": true, "trades_count": 0|N},
      },
      ...
    ]

Usage::

    python -m trading_corp.scripts.seed_kalshi_watchlist [--seed PATH] [--dry-run] [--json]

Idempotent — overwrites the full list each run. Edit the YAML and re-run
to add or remove handles.
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

import yaml

from trading_corp.data.kalshi_apify_client import KalshiApifyClient
from trading_corp.persistence.db import set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)

_DEFAULT_SEED = Path("config/kalshi_watchlist_seed.yaml")


def _load_seed(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (tier_1_entries, tier_2_entries). Each entry is the raw YAML dict
    plus a normalized `kalshi_handle` field."""
    with path.open("r") as f:
        data = yaml.safe_load(f) or {}
    tier_1 = []
    tier_2 = []
    for raw in (data.get("tier_1") or []):
        if not isinstance(raw, dict):
            continue
        x_handle = str(raw.get("x_handle") or "").strip()
        if not x_handle:
            continue
        tier_1.append({
            "x_handle": x_handle,
            "kalshi_handle": x_handle.lstrip("@"),
            "notes": str(raw.get("notes") or ""),
        })
    for raw in (data.get("tier_2") or []):
        if not isinstance(raw, dict):
            continue
        x_handle = str(raw.get("x_handle") or "").strip()
        if not x_handle:
            continue
        tier_2.append({
            "x_handle": x_handle,
            "kalshi_handle": x_handle.lstrip("@"),
            "notes": str(raw.get("notes") or ""),
        })
    return tier_1, tier_2


async def seed_watchlist(
    *,
    apify_token: str,
    db_url: str,
    seed_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    tier_1, tier_2 = _load_seed(seed_path)
    all_handles = [e["kalshi_handle"] for e in (tier_1 + tier_2)]

    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "seed_path": str(seed_path),
        "tier_1_requested": [e["kalshi_handle"] for e in tier_1],
        "tier_2_requested": [e["kalshi_handle"] for e in tier_2],
        "tier_1_included": [],
        "tier_1_dropped": [],
        "tier_2_included": [],
        "tier_2_dropped": [],
    }

    async with KalshiApifyClient(apify_token) as client:
        profiles = await client.fetch_profiles(all_handles)
        # Profile actor's result list isn't strictly 1-per-name — sometimes
        # absent names are silently omitted. Index by nickname for lookup.
        profile_by_handle = {p.nickname: p for p in profiles}

        # Trades only for Tier 2 (the survival gate). Skipping for Tier 1
        # saves ~$0.10 of Apify spend on the typical 9-handle list.
        tier_2_handles = [e["kalshi_handle"] for e in tier_2]
        trades = (
            await client.fetch_trades(tier_2_handles)
            if tier_2_handles else []
        )
        trades_count_by_handle: dict[str, int] = {}
        for t in trades:
            trades_count_by_handle[t.name] = trades_count_by_handle.get(t.name, 0) + 1

    now_iso = datetime.now(timezone.utc).isoformat()
    survivors: list[dict[str, Any]] = []

    for entry in tier_1:
        h = entry["kalshi_handle"]
        prof = profile_by_handle.get(h)
        if prof is None:
            log.warning(
                "tier_1 DROP: %s (Kalshi profile did not resolve via Apify)", h,
            )
            summary["tier_1_dropped"].append({
                "handle": h, "reason": "profile_unresolved",
            })
            continue
        survivors.append({
            "handle": h, "tier": 1,
            "source_x_handle": entry["x_handle"],
            "notes": entry["notes"],
            "included_iso": now_iso,
            "probe": {
                "profile_resolved": True,
                "trades_count": None,  # not probed for Tier 1
                "social_id": prof.social_id or None,
                "lifetime_markets": prof.num_markets_traded,
            },
        })
        summary["tier_1_included"].append(h)
        log.info("tier_1 KEEP: %s (markets=%d)", h, prof.num_markets_traded)

    for entry in tier_2:
        h = entry["kalshi_handle"]
        n_trades = trades_count_by_handle.get(h, 0)
        if n_trades < 1:
            log.warning(
                "tier_2 DROP: %s (Apify returned %d trades, threshold ≥1)",
                h, n_trades,
            )
            summary["tier_2_dropped"].append({
                "handle": h, "reason": "no_trades", "trades_count": n_trades,
            })
            continue
        prof = profile_by_handle.get(h)
        survivors.append({
            "handle": h, "tier": 2,
            "source_x_handle": entry["x_handle"],
            "notes": entry["notes"],
            "included_iso": now_iso,
            "probe": {
                "profile_resolved": prof is not None,
                "trades_count": n_trades,
                "social_id": (prof.social_id if prof else None) or None,
                "lifetime_markets": prof.num_markets_traded if prof else None,
            },
        })
        summary["tier_2_included"].append(h)
        log.info("tier_2 KEEP: %s (trades=%d)", h, n_trades)

    summary["survivors_count"] = len(survivors)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        set_agent_state(
            "kalshi_copy_trader", "watch_only_whales", survivors, db_url=db_url,
        )
        set_agent_state(
            "kalshi_copy_trader", "watch_only_seed_metadata", summary,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== K3 Watchlist Seed — {summary['started_at']} ===")
    print(f"Seed: {summary['seed_path']}")
    print()
    print(
        f"Tier 1: {len(summary['tier_1_included'])}/"
        f"{len(summary['tier_1_requested'])} included"
    )
    for h in summary["tier_1_included"]:
        print(f"  KEEP {h}")
    for d in summary["tier_1_dropped"]:
        print(f"  DROP {d['handle']}  ({d['reason']})")
    print()
    print(
        f"Tier 2: {len(summary['tier_2_included'])}/"
        f"{len(summary['tier_2_requested'])} included"
    )
    for h in summary["tier_2_included"]:
        print(f"  KEEP {h}")
    for d in summary["tier_2_dropped"]:
        extra = f", trades={d.get('trades_count', '?')}" if "trades_count" in d else ""
        print(f"  DROP {d['handle']}  ({d['reason']}{extra})")
    print()
    print(f"Total survivors: {summary['survivors_count']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=Path, default=_DEFAULT_SEED)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    secrets = load_secrets()
    if not secrets.apify_api_token:
        print("ERROR: APIFY_API_TOKEN not set (env or KV)", file=sys.stderr)
        return 1
    if not args.seed.exists():
        print(f"ERROR: seed file not found: {args.seed}", file=sys.stderr)
        return 1

    summary = asyncio.run(seed_watchlist(
        apify_token=secrets.apify_api_token,
        db_url=secrets.db_url,
        seed_path=args.seed,
        dry_run=args.dry_run,
    ))

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary)
        if args.dry_run:
            print("(dry-run — NOT written to agent_state)")
        else:
            print("Written to agent_state(kalshi_copy_trader.watch_only_whales).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
