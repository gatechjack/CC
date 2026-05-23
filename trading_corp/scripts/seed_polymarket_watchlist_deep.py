"""Polymarket watch-only watchlist deep seed — windowed (last-N resolved BUYs).

Finds Polymarket wallets whose **last 100 resolved BUYs** clear a quality
gate and writes them to
`agent_state(polymarket_copy_trader, watch_only_whales)`.
These wallets are observation-only: no ProposedOrders are ever emitted
from this list. The copy-trade roster lives separately in
`selected_whales` and is untouched by this script.

Pipeline:
  1. Pull `/v1/leaderboard?category=<C>` for each working category +
     global, paginated to `candidates_per_category` rows per key. Dedupe
     wallets.
  2. Per candidate wallet, walk `/activity` with three termination
     conditions, whichever fires first:
       - Accumulated BUY-trade row count >= `target_buy_rows` (default
         150, a buffer above the 100-window so unresolveds don't starve
         it).
       - `/activity` returns an empty page (feed exhausted).
       - Page index reaches `max_pages_per_wallet` (default 10, → 5000
         activity rows ceiling at activity_limit=500).
     Record the termination reason for telemetry.
  3. Batch-fetch market resolutions for every unique condition_id seen
     across all wallets' BUY activity (gamma-api /markets).
  4. Per wallet, build the windowed slice = most-recent 100 BUYs whose
     market is `resolved`. If <window_size, true N is recorded — never
     silently report 100 on a short window.
  5. Apply floors (whichever fails → drop):
       - `n >= min_resolved_buys` (default 10, hard noise floor)
       - `last_trade_iso` of ANY side <= recency_days old (default 60)
       - windowed WR >= min_windowed_wr (default 0.62)
       - windowed realized PnL > min_windowed_pnl (default 0.01, i.e. > $0)
  6. Rank survivors by **windowed** `realized_pnl_usdc` desc. No top-N
     cap by default (`--top 0`).
  7. Mark `provisional=true` iff `window_size_n < provisional_threshold`
     (default 50). Dashboard greys these rows independent of sort column.
  8. Write to `agent_state(polymarket_copy_trader, watch_only_whales)`.

Why windowed: lifetime stats (the prior design) rank dormant whales and
high-volume-low-edge whales as if they were still good bets. A 100-trade
sliding window filters inactive wallets for free (they never accumulate
100 recent resolved trades) and makes WR/PnL directly comparable across
whales (sample size held constant).

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
    --categories C1,C2,...   Leaderboard categories (default: all 5 working)
    --candidates N           Top-N to consider per category (default 500)
    --top N                  Cap final watchlist (default 0 = no cap)
    --window-size N          Resolved-BUY window size (default 100)
    --min-resolved-buys N    Hard noise floor on n (default 10)
    --min-windowed-wr F      Windowed WR floor [0.0-1.0] (default 0.62)
    --min-windowed-pnl F     Windowed realized PnL floor USDC (default 0.01)
    --recency-days N         Drop if last activity older than N days (default 60)
    --provisional-threshold N  Mark provisional iff n < this (default 50)
    --activity-limit N       /activity rows per call (default 500, max 1000)
    --max-pages-per-wallet N Ceiling on /activity pages per wallet (default 10)
    --target-buy-rows N      Stop paging when this many BUYs seen (default 150)
    --merge                  Union with existing watchlist (weekly-refresh mode)
    --max-total N            Cap merged list size (only with --merge)
    --dry-run                Print results; don't write to agent_state
    --json                   JSON output instead of human table
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
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)

_LEADERBOARD_PAGE = 50  # data-api caps /v1/leaderboard at 50 rows per call


async def _fetch_wallet_activity_windowed(
    client: PolymarketDataAPIClient,
    wallet: str,
    *,
    activity_limit: int,
    max_pages: int,
    target_buy_rows: int,
) -> tuple[list[ActivityRow], int, str]:
    """Walk `/activity` until enough BUY trades or feed exhausted or ceiling.

    Three explicit stop conditions, whichever fires first:
      - Cumulative TRADE+BUY row count >= target_buy_rows
        (a buffer above the eventual window_size to account for some BUYs
        being unresolved when we batch-fetch resolutions)
      - A page returns 0 rows (feed exhausted)
      - page index reaches max_pages (the hard ceiling on examined rows)

    Returns (rows, pages_fetched, termination_reason). The
    termination_reason is one of {"target_buys_reached", "exhausted",
    "max_pages_hit", "fetch_error"}.
    """
    out: list[ActivityRow] = []
    buy_count = 0
    pages_fetched = 0
    for page_idx in range(max_pages):
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
            return out, pages_fetched, "fetch_error"
        pages_fetched += 1
        if not page:
            return out, pages_fetched, "exhausted"
        out.extend(page)
        for a in page:
            if a.type == "TRADE" and a.side == "BUY":
                buy_count += 1
        if buy_count >= target_buy_rows:
            return out, pages_fetched, "target_buys_reached"
        if len(page) < activity_limit:
            return out, pages_fetched, "exhausted"
    return out, pages_fetched, "max_pages_hit"


def _select_resolved_buys_window(
    activity: list[ActivityRow],
    resolutions: dict[str, dict],
    *,
    window_size: int,
) -> list[ActivityRow]:
    """Pick the most-recent `window_size` BUYs whose market is resolved.

    `activity` is assumed most-recent-first (the `/activity` API
    contract). We iterate in order and collect resolved BUYs until the
    window is full or activity is exhausted. The returned list preserves
    the most-recent-first ordering so callers can read window_days_span
    as `activity[0].ts - activity[-1].ts`.
    """
    window: list[ActivityRow] = []
    for a in activity:
        if a.type != "TRADE" or a.side != "BUY":
            continue
        if not a.condition_id:
            continue
        res = resolutions.get(a.condition_id)
        if not res:
            continue
        if (res.get("status") or "").lower() != "resolved":
            continue
        window.append(a)
        if len(window) >= window_size:
            break
    return window


def _merge_watchlists(
    existing: list[dict[str, Any]] | None,
    fresh: list[dict[str, Any]],
    *,
    max_total: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Union existing + fresh entries by proxy_wallet for the weekly-refresh
    accumulation mode.

    Existing entries' `included_iso` is preserved (so we can track how long
    each wallet has been observed); fresh entries get the new `included_iso`
    from the current run. Per-wallet stats (wins, losses, win_rate, etc.)
    always take the FRESH value when both sides see the wallet — we want the
    most-recent observation, not the oldest.

    Re-ranks the merged list by `realized_pnl_usdc` desc, then trims to
    `max_total` if set. Returns (merged, stats) where stats reports
    `preserved`, `added`, `replaced`, `dropped` counts for the summary.
    """
    by_wallet: dict[str, dict[str, Any]] = {}
    for e in existing or []:
        wallet = e.get("proxy_wallet")
        if wallet:
            by_wallet[wallet] = dict(e)
    stats = {"preserved": 0, "added": 0, "replaced": 0, "dropped": 0}
    for f in fresh:
        wallet = f.get("proxy_wallet")
        if not wallet:
            continue
        if wallet in by_wallet:
            prior_iso = by_wallet[wallet].get("included_iso")
            merged_entry = dict(f)
            if prior_iso:
                merged_entry["included_iso"] = prior_iso
            by_wallet[wallet] = merged_entry
            stats["replaced"] += 1
        else:
            by_wallet[wallet] = dict(f)
            stats["added"] += 1
    stats["preserved"] = max(
        0, len(by_wallet) - stats["added"] - stats["replaced"],
    )
    combined = sorted(
        by_wallet.values(),
        key=lambda r: r.get("realized_pnl_usdc", 0.0),
        reverse=True,
    )
    if max_total is not None and len(combined) > max_total:
        stats["dropped"] = len(combined) - max_total
        combined = combined[:max_total]
    # Re-rank in-place so the rank field reflects the merged ordering.
    for new_rank, entry in enumerate(combined, start=1):
        entry["rank"] = new_rank
    return combined, stats


async def seed_polymarket_watchlist_deep(
    *,
    db_url: str,
    candidates_per_category: int = 500,
    top_n: int = 0,
    window_size: int = 100,
    min_resolved_buys: int = 10,
    min_windowed_wr: float = 0.62,
    min_windowed_pnl: float = 0.01,
    recency_days: int = 60,
    provisional_threshold: int = 50,
    activity_limit: int = 500,
    max_pages_per_wallet: int = 10,
    target_buy_rows: int = 150,
    categories: tuple[str, ...] = POLYMARKET_LEADERBOARD_CATEGORIES,
    dry_run: bool = False,
    merge: bool = False,
    max_total: int | None = None,
) -> dict[str, Any]:
    """Run the full pipeline. Returns a summary dict; writes to agent_state
    unless `dry_run=True`.

    Scoring is over each wallet's last `window_size` resolved BUYs (no
    lifetime). Survivors must clear four floors (see module docstring).
    No top-N cap by default (`top_n=0`); pass a positive value to cap.

    `merge=True` unions the freshly-computed list with the existing
    `agent_state(polymarket_copy_trader, watch_only_whales)` slot — used
    by the weekly cron so the watchlist accumulates over time. New
    entries get a fresh `included_iso`; previously-seen wallets keep
    their original `included_iso` so we can track observation duration.
    `max_total` (if set) caps the merged list by windowed
    `realized_pnl_usdc` desc.
    """
    started = datetime.now(timezone.utc)
    started_ts = started.timestamp()
    recency_cutoff_ts = started_ts - (recency_days * 86400)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "params": {
            "candidates_per_category": candidates_per_category,
            "top_n": top_n,
            "window_size": window_size,
            "min_resolved_buys": min_resolved_buys,
            "min_windowed_wr": min_windowed_wr,
            "min_windowed_pnl": min_windowed_pnl,
            "recency_days": recency_days,
            "provisional_threshold": provisional_threshold,
            "activity_limit": activity_limit,
            "max_pages_per_wallet": max_pages_per_wallet,
            "target_buy_rows": target_buy_rows,
            "categories": list(categories),
            "merge": merge,
            "max_total": max_total,
        },
        "leaderboards_pulled": [],
        "unique_candidates": 0,
        "with_activity": 0,
        "termination_reasons": {
            "target_buys_reached": 0, "exhausted": 0,
            "max_pages_hit": 0, "fetch_error": 0,
        },
        "drop_reasons": {
            "no_activity": 0, "n_floor": 0, "recency_floor": 0,
            "wr_floor": 0, "pnl_floor": 0,
        },
        "quality_gate_pass": 0,
        "provisional_count": 0,
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

        # 2. Walk /activity per candidate with the windowed termination spec.
        all_condition_ids: set[str] = set()
        activity_by_wallet: dict[str, list[ActivityRow]] = {}
        for wallet in candidates:
            acts, pages_fetched, term_reason = await _fetch_wallet_activity_windowed(
                client, wallet,
                activity_limit=activity_limit,
                max_pages=max_pages_per_wallet,
                target_buy_rows=target_buy_rows,
            )
            activity_by_wallet[wallet] = acts
            summary["termination_reasons"][term_reason] = (
                summary["termination_reasons"].get(term_reason, 0) + 1
            )
            if acts:
                summary["with_activity"] += 1
            for a in acts:
                if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                    all_condition_ids.add(a.condition_id)
        log.info(
            "seed_polymarket_watchlist_deep: %d wallets with activity, "
            "%d unique condition_ids across all BUYs; termination=%s",
            summary["with_activity"], len(all_condition_ids),
            summary["termination_reasons"],
        )

        # 3. Batch-fetch market resolutions.
        resolutions = await client.fetch_market_resolutions(list(all_condition_ids))

        # 4. Per wallet: build windowed slice, apply floors.
        survivors: list[dict[str, Any]] = []
        for wallet, rec in candidates.items():
            entry = rec["entry"]
            activity = activity_by_wallet.get(wallet, [])
            if not activity:
                summary["drop_reasons"]["no_activity"] += 1
                continue

            # Most-recent activity row of ANY side governs the recency floor —
            # a whale actively SELLing or holding open positions is not dormant
            # even if their most recent resolved BUY is older.
            last_trade_ts = max((a.timestamp for a in activity), default=0)
            if last_trade_ts < recency_cutoff_ts:
                summary["drop_reasons"]["recency_floor"] += 1
                continue

            window = _select_resolved_buys_window(
                activity, resolutions, window_size=window_size,
            )
            n_resolved = len(window)
            if n_resolved < min_resolved_buys:
                summary["drop_reasons"]["n_floor"] += 1
                continue

            # half_life_days=36500.0 → effectively no half-life weighting. The
            # fixed-size window IS the recency mechanism; applying a 30-day
            # half-life on top would double-count and let the most-recent ~30
            # trades dominate a slice we deliberately sized at 100.
            stats, _outcomes = compute_polymarket_stats(
                leaderboard_entry=entry,
                activity_rows=window,
                market_resolutions=resolutions,
                half_life_days=36500.0,
            )
            closed = stats.closed_positions_count
            if closed != n_resolved:
                # Defensive: the slice and stats should agree on n. If they
                # diverge (shouldn't), trust compute_polymarket_stats.
                n_resolved = closed
                if n_resolved < min_resolved_buys:
                    summary["drop_reasons"]["n_floor"] += 1
                    continue

            win_rate = stats.wins / closed if closed > 0 else 0.0
            if win_rate < min_windowed_wr:
                summary["drop_reasons"]["wr_floor"] += 1
                continue
            if stats.total_pnl <= min_windowed_pnl:
                summary["drop_reasons"]["pnl_floor"] += 1
                continue

            # window_days_span = span from oldest-in-window BUY to most-recent.
            window_ts = [a.timestamp for a in window]
            window_days_span = (
                (max(window_ts) - min(window_ts)) / 86400.0 if window_ts else 0.0
            )
            last_trade_iso = datetime.fromtimestamp(
                last_trade_ts, tz=timezone.utc,
            ).isoformat() if last_trade_ts else ""
            provisional = n_resolved < provisional_threshold

            summary["quality_gate_pass"] += 1
            if provisional:
                summary["provisional_count"] += 1
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
                "window_size_n": n_resolved,
                "window_days_span": window_days_span,
                "last_trade_iso": last_trade_iso,
                "provisional": provisional,
            })
            log.info(
                "quality gate PASS: %s (%s) n=%d wr=%.2f pnl=%.2f span=%.0fd %s",
                wallet[:10], entry.user_name, closed, win_rate, stats.total_pnl,
                window_days_span, "[PROVISIONAL]" if provisional else "",
            )

    # 5. Rank by descending windowed realized PnL. Optional top-N cap.
    survivors.sort(key=lambda r: r["realized_pnl_usdc"], reverse=True)
    top_survivors = survivors if top_n <= 0 else survivors[:top_n]

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
            "window_size_n": s["window_size_n"],
            "window_days_span": round(s["window_days_span"], 2),
            "last_trade_iso": s["last_trade_iso"],
            "provisional": s["provisional"],
        })

    final_payload = watch_only_payload
    merge_stats: dict[str, int] | None = None
    if merge:
        loaded = load_agent_state(
            "polymarket_copy_trader", "watch_only_whales", db_url=db_url,
        )
        existing_value = loaded[0] if loaded else None
        existing_list = (
            existing_value if isinstance(existing_value, list) else []
        )
        final_payload, merge_stats = _merge_watchlists(
            existing_list, watch_only_payload, max_total=max_total,
        )
        log.info(
            "merge: existing=%d fresh=%d added=%d replaced=%d preserved=%d "
            "dropped=%d final=%d",
            len(existing_list), len(watch_only_payload),
            merge_stats["added"], merge_stats["replaced"],
            merge_stats["preserved"], merge_stats["dropped"], len(final_payload),
        )

    summary["watch_only_whales"] = final_payload
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["stats"] = {
        "candidates": summary["unique_candidates"],
        "with_activity": summary["with_activity"],
        "quality_gate_pass": summary["quality_gate_pass"],
        "provisional_count": summary["provisional_count"],
        "fresh_top_n": len(watch_only_payload),
        "written": len(final_payload),
    }
    if merge_stats is not None:
        summary["merge_stats"] = merge_stats

    if not dry_run:
        set_agent_state(
            "polymarket_copy_trader", "watch_only_whales", final_payload,
            db_url=db_url,
        )
        set_agent_state(
            "polymarket_copy_trader", "watch_only_whales_metadata", summary,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== Polymarket Watchlist Deep Seed (windowed) — {summary['started_at']} ===")
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
        f"Provisional: {s.get('provisional_count', 0)}  |  "
        f"Fresh top-N: {s.get('fresh_top_n', s.get('written', 0))}  |  "
        f"Written: {s.get('written', 0)}"
    )
    tr = summary.get("termination_reasons", {})
    if tr:
        print(
            f"Termination: target_buys={tr.get('target_buys_reached', 0)}  "
            f"exhausted={tr.get('exhausted', 0)}  "
            f"max_pages={tr.get('max_pages_hit', 0)}  "
            f"fetch_err={tr.get('fetch_error', 0)}"
        )
    dr = summary.get("drop_reasons", {})
    if dr:
        print(
            f"Drops:  no_activity={dr.get('no_activity', 0)}  "
            f"n<floor={dr.get('n_floor', 0)}  "
            f"recency={dr.get('recency_floor', 0)}  "
            f"wr<floor={dr.get('wr_floor', 0)}  "
            f"pnl<floor={dr.get('pnl_floor', 0)}"
        )
    merge_stats = summary.get("merge_stats")
    if merge_stats:
        print(
            f"Merge: added={merge_stats.get('added', 0)}  "
            f"replaced={merge_stats.get('replaced', 0)}  "
            f"preserved={merge_stats.get('preserved', 0)}  "
            f"dropped={merge_stats.get('dropped', 0)}"
        )
    print()
    whales = summary.get("watch_only_whales", [])
    if not whales:
        print("No wallets passed the quality gate.")
        return
    print(f"{len(whales)} watchlist whales (ranked by windowed realized PnL):")
    print()
    print(
        f"{'#':>3} | {'Wallet':<14} | {'User':<22} | {'Cat':<8} | "
        f"{'N':>4} | {'Span':>6} | {'WR':>6} | {'PnL':>10} | "
        f"{'Last':<12} | {'P'}"
    )
    print("-" * 120)
    for w in whales:
        last_short = (w.get("last_trade_iso") or "")[:10]
        prov_mark = "*" if w.get("provisional") else " "
        print(
            f"{w['rank']:>3} | {w['proxy_wallet'][:14]} | "
            f"{w['user_name'][:22]:<22} | {w['best_category'][:8]:<8} | "
            f"{w['window_size_n']:>4} | "
            f"{w['window_days_span']:>5.0f}d | "
            f"{w['win_rate']:>6.2%} | "
            f"${w['realized_pnl_usdc']:>9,.2f} | "
            f"{last_short:<12} | {prov_mark}"
        )
    print()
    if any(w.get("provisional") for w in whales):
        print("  * = provisional (n < threshold) — dashboard will grey these rows.")
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
        "--top", type=int, default=0,
        help="Cap final watchlist size (default 0 = no cap).",
    )
    parser.add_argument(
        "--window-size", type=int, default=100,
        help="Number of most-recent resolved BUYs in the scoring window (default 100).",
    )
    parser.add_argument(
        "--min-resolved-buys", type=int, default=10,
        help="Hard noise floor on resolved BUY count (default 10).",
    )
    parser.add_argument(
        "--min-windowed-wr", type=float, default=0.62,
        help="Min windowed win rate [0.0-1.0] (default 0.62).",
    )
    parser.add_argument(
        "--min-windowed-pnl", type=float, default=0.01,
        help="Min windowed realized PnL in USDC (default 0.01, i.e. > $0).",
    )
    parser.add_argument(
        "--recency-days", type=int, default=60,
        help="Drop whales whose most recent activity is older than N days (default 60).",
    )
    parser.add_argument(
        "--provisional-threshold", type=int, default=50,
        help="Mark `provisional=true` iff window_size_n < this (default 50).",
    )
    parser.add_argument(
        "--activity-limit", type=int, default=500,
        help="/activity rows per call (default 500; max ~1000).",
    )
    parser.add_argument(
        "--max-pages-per-wallet", type=int, default=10,
        help="Ceiling on /activity pages per wallet (default 10).",
    )
    parser.add_argument(
        "--target-buy-rows", type=int, default=150,
        help=(
            "Stop paging once accumulated BUY-trade rows reach this count "
            "(default 150; buffer above window_size for unresolveds)."
        ),
    )
    parser.add_argument(
        "--merge", action="store_true",
        help=(
            "Union freshly-computed list with the existing "
            "agent_state(polymarket_copy_trader, watch_only_whales). "
            "New entries get fresh included_iso; previously-seen wallets "
            "keep their original included_iso. Used by the weekly cron."
        ),
    )
    parser.add_argument(
        "--max-total", type=int, default=None,
        help=(
            "When --merge is set, cap the merged list to top-N by windowed "
            "realized_pnl_usdc desc. Without this, the merged list grows "
            "unbounded as new wallets pass the gate each week."
        ),
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
        window_size=args.window_size,
        min_resolved_buys=args.min_resolved_buys,
        min_windowed_wr=args.min_windowed_wr,
        min_windowed_pnl=args.min_windowed_pnl,
        recency_days=args.recency_days,
        provisional_threshold=args.provisional_threshold,
        activity_limit=args.activity_limit,
        max_pages_per_wallet=args.max_pages_per_wallet,
        target_buy_rows=args.target_buy_rows,
        categories=cats,
        dry_run=args.dry_run,
        merge=args.merge,
        max_total=args.max_total,
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
