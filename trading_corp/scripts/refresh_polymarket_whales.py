"""Polymarket whale selection refresh.

One-off orchestrator. Mirrors `refresh_kalshi_whales.py` but for Polymarket:
free public APIs end-to-end (no Apify subscription).

Pipeline:
  1. Pull `/v1/leaderboard?category=<C>` for each of the 5 working
     categories (Politics, Sports, Crypto, Tech, Mentions) + global.
  2. Dedupe candidate wallets across categories.
  3. For each candidate: walk the FULL `/activity?user=<wallet>` window via
     paginated fetch (shared with seed_*_deep) — the realized compute needs
     every fill of a decision (BUY+SELL+REDEEM) together, not a single page.
  4. Batch-fetch market resolutions for every unique condition_id.
  5. Build the REDEEM-grounded `WhaleAuditReport` per candidate (read through
     the audit cache) and score it with `score_whale_from_audit`: decision-unit
     Wilson LCB × realized-ROI edge × category bonus, gated by an inflation
     ratio. This is option (c) Phase 1 — replaces the legacy held-to-resolution
     `compute_polymarket_stats` + `score_polymarket_whale` path (kept importable
     for `seed_*_deep` + the dry-run naive-reference column).
  6. Selection Rule B: top-N per category (default 2) + top-N global
     (default 2), deduped → ~12 total selected.
  7. WRITE the roster to `agent_state(polymarket_copy_trader.selected_whales)`.
     DEFAULT is **pins-only**: only operator-pinned (dashboard-promoted) whales
     are written — the algorithm INFORMS the report (rankings, gated-out,
     unrankable, cause attribution) but never auto-selects, preserving the
     manual-promotion workflow. `--algo-select` (explicit opt-in) writes the
     algorithm's top-N + pins (legacy behavior). Pinned whales always survive
     (UNCHANGED merge). window_truncated whales are excluded from algo selection
     and surfaced in an "unrankable" section.

Cost: $0 — all endpoints are free public.

Usage::

    # Default-safe: full report + PINS-ONLY roster write (algorithm advisory)
    python -m trading_corp.scripts.refresh_polymarket_whales

    # Explicit opt-in: write algorithm top-N + pins (auto-expands the roster)
    python -m trading_corp.scripts.refresh_polymarket_whales --algo-select

Options:
    --top-per-category N    Picks per category for Rule B (default 2)
    --top-global N          Top-N from global to fill (default 2)
    --candidates N          Top-N to enrich per category (default 20)
    --min-resolved N        Min resolved DECISIONS for inclusion (default 10)
    --inflation-threshold R Exclude whales with pnl_inflation_ratio > R
                            (default 0.5; strictly-greater excludes)
    --half-life-days D      LEGACY — only affects the dry-run naive reference
                            column; the realized-basis scorer does NOT
                            time-weight (warns if passed)
    --activity-limit N      Rows per /activity page (default 500)
    --max-pages N           Max /activity pages walked per whale (default 10
                            → ~5000-row ceiling at limit=500)
    --target-buy-rows N     Legacy early-stop after N BUY rows. DEFAULT: unset →
                            walk to exhaustion (bounded by --max-pages). Whales
                            that hit the ceiling are flagged window_truncated.
    --dry-run               Print full report (rankings + cause attribution +
                            gated-out + unrankable); write NOTHING (read-only)
    --algo-select           Opt-in: WRITE algorithm top-N + pins (auto-expands
                            the copy roster). DEFAULT writes pins only.
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

from trading_corp.agents.research.polymarket_whale_audit_cache import (
    read_audit, write_audit,
)
from trading_corp.data.kalshi_whale_stats import _edge_factor, wilson_lcb_95
from trading_corp.data.polymarket_data_api_client import (
    POLYMARKET_LEADERBOARD_CATEGORIES, PolymarketDataAPIClient,
)
from trading_corp.data.polymarket_whale_audit import build_audit_report
from trading_corp.data.polymarket_whale_stats import (
    DEFAULT_HALF_LIFE_DAYS, DEFAULT_INFLATION_RATIO_THRESHOLD,
    DEFAULT_MIN_RESOLVED, compute_polymarket_stats, score_polymarket_whale,
    score_whale_from_audit,
)
# Reuse seed_*_deep's paginated activity walk — the realized compute needs the
# FULL window (BUY+SELL+REDEEM of each decision together), not a single page
# (scoping doc §6). Shared extraction into a whale_screening module is Phase 3;
# importing the existing, tested helper avoids a second pagination impl.
from trading_corp.scripts.seed_polymarket_watchlist_deep import (
    _fetch_wallet_activity_windowed,
)
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.utils.secrets import load_secrets

log = logging.getLogger(__name__)


def _select_rule_b(
    scored_per_category: dict[str, list[Any]],
    scored_global: list[Any],
    *,
    top_per_category: int,
    top_global: int,
) -> dict[str, tuple[Any, ...]]:
    """Rule B: top-N per category + top-N global, deduped by wallet (higher
    composite wins). Returns {wallet: (entry, scored, source_cat)}.

    Extracted verbatim from the inline selection so the dry-run can run it on
    both the realized scores (the written roster) and the legacy naive scores
    (the comparison reference) with identical logic.
    """
    selected: dict[str, tuple[Any, ...]] = {}
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
    return selected


async def refresh_polymarket_selection(
    *,
    db_url: str,
    top_per_category: int = 2,
    top_global: int = 2,
    candidates_per_category: int = 20,
    min_resolved: int = DEFAULT_MIN_RESOLVED,
    inflation_threshold: float = DEFAULT_INFLATION_RATIO_THRESHOLD,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    activity_limit: int = 500,
    max_pages: int = 10,
    target_buy_rows: int | None = None,  # None = walk to exhaustion (bounded by max_pages)
    categories: tuple[str, ...] = POLYMARKET_LEADERBOARD_CATEGORIES,
    dry_run: bool = False,
    algo_select: bool = False,  # default-safe: pins-only write; True = algo top-N + pins
) -> dict[str, Any]:
    """Run the full pipeline. Returns a summary dict; writes to agent_state
    unless `dry_run=True`.

    Selection uses the REDEEM-grounded realized basis (`build_audit_report` +
    `score_whale_from_audit`). In `dry_run` the legacy held-to-resolution
    pipeline is ALSO run, purely to attribute each roster mover to its cause
    (time-weighting removal vs realized-basis inputs) — see
    `summary["dry_run_comparison"]`. `dry_run` writes nothing to the DB (not
    even the audit cache), so it is strictly read-only.
    """
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "params": {
            "top_per_category": top_per_category,
            "top_global": top_global,
            "candidates_per_category": candidates_per_category,
            "min_resolved": min_resolved,
            "inflation_threshold": inflation_threshold,
            "half_life_days": half_life_days,
            "activity_limit": activity_limit,
            "max_pages": max_pages,
            "target_buy_rows": target_buy_rows,
            "categories": list(categories),
            "scorer": "realized_audit",  # option (c) Phase 1
            "algo_select": algo_select,
        },
        "leaderboards_pulled": [],
        "selected_whales": [],
        "selection_details": [],
    }
    # Review modes (dry-run + pins-only default) produce the naive-vs-realized
    # cause-attribution diff; the commit mode (algo_select) skips that extra
    # naive compute.
    compute_comparison = dry_run or not algo_select

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
        truncated_by_wallet: dict[str, bool] = {}
        # FULL paginated window (scoping doc §6): REDEEM-grounded realized PnL
        # needs every fill of a decision (BUY+SELL+REDEEM) together. Walk to
        # EXHAUSTION bounded by max_pages — a fixed target_buy_rows just moves
        # the truncation cliff (Phase E reconciliation finding). When
        # target_buy_rows is None (default) disable the early-stop; whales that
        # STILL hit the page ceiling are flagged `window_truncated` so their
        # realized is read as a floor-bounded estimate, not silently trusted.
        eff_target = (
            target_buy_rows if target_buy_rows is not None
            else max_pages * activity_limit + 1
        )
        for wallet in candidates:
            try:
                acts, _pages, reason = await _fetch_wallet_activity_windowed(
                    client, wallet, activity_limit=activity_limit,
                    max_pages=max_pages, target_buy_rows=eff_target,
                )
            except Exception as e:
                log.warning("activity fetch failed for %s: %s", wallet[:10], e)
                acts, reason = [], "fetch_error"
            truncated_by_wallet[wallet] = reason in ("max_pages_hit", "fetch_error")
            activity_by_wallet[wallet] = acts
            for a in acts:
                if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                    all_condition_ids.add(a.condition_id)
        n_truncated = sum(1 for t in truncated_by_wallet.values() if t)
        if n_truncated:
            log.warning(
                "%d/%d whales hit the activity page ceiling (window_truncated; "
                "their realized PnL is a floor-bounded estimate)",
                n_truncated, len(candidates),
            )

        log.info(
            "refresh_polymarket_whales: %d unique condition_ids across all whales' BUYs",
            len(all_condition_ids),
        )

        # 3. Batch-fetch resolutions for every unique condition_id.
        resolutions = await client.fetch_market_resolutions(list(all_condition_ids))

        # 4. Build the REDEEM-grounded audit report + score per whale on the
        # realized basis. Score each whale once GLOBALLY (no category bonus)
        # and once per category they appeared on; Rule B then picks top-N per
        # category + top-N global. The audit cache (keyed on wallet +
        # activity_max_ts) is read through; write-back happens only on a real
        # (non-dry-run) refresh so dry-run stays read-only against the DB.
        scored_per_category: dict[str, list[Any]] = {}  # cat -> [(wallet, entry, ScoredWhale)]
        scored_global: list[Any] = []
        report_by_wallet: dict[str, Any] = {}
        # Legacy (naive held-to-resolution) pipelines + per-whale score triple,
        # populated only in dry-run for the cause-attribution diff.
        scored_per_category_naive: dict[str, list[Any]] = {}
        scored_global_naive: list[Any] = []
        comparison_by_wallet: dict[str, dict[str, Any]] = {}

        for wallet, rec in candidates.items():
            entry = rec["entry"]
            activity = activity_by_wallet.get(wallet, [])
            activity_max_ts = max(
                (a.timestamp for a in activity if a.timestamp > 0), default=0,
            )

            report = read_audit(wallet, activity_max_ts, db_url=db_url) if db_url else None
            if report is None:
                report = build_audit_report(
                    leaderboard_entry=entry, activity_rows=activity,
                    resolutions=resolutions, proxy_wallet=wallet,
                )
                if not dry_run and db_url:
                    write_audit(report, db_url=db_url)
            report_by_wallet[wallet] = report

            wt = truncated_by_wallet.get(wallet, False)
            # Global realized score (no category bonus). window_truncated whales
            # are hard-gated out of algorithmic selection (cost basis incomplete).
            scored_no_cat = score_whale_from_audit(
                report, target_category=None, min_resolved=min_resolved,
                inflation_threshold=inflation_threshold, window_truncated=wt,
            )
            scored_global.append((wallet, entry, scored_no_cat))

            # Per-category realized scores for the categories this whale appeared on
            for cat in rec["categories_seen"]:
                if cat == "GLOBAL":
                    continue
                scored = score_whale_from_audit(
                    report, target_category=cat, whale_categories=(cat,),
                    min_resolved=min_resolved, inflation_threshold=inflation_threshold,
                    window_truncated=wt,
                )
                scored_per_category.setdefault(cat, []).append((wallet, entry, scored))

            # Review modes only: compute the legacy naive references so each
            # mover can be attributed to time-weighting removal vs realized inputs.
            if compute_comparison:
                stats, _outcomes = compute_polymarket_stats(
                    leaderboard_entry=entry, activity_rows=activity,
                    market_resolutions=resolutions, half_life_days=half_life_days,
                )
                # naive, time-weighted (today's production), no category bonus
                naive_tw = score_polymarket_whale(
                    stats, target_category=None, min_resolved=min_resolved,
                )
                # plain Wilson on the SAME naive inputs — the bridge column that
                # isolates the time-weighting effect (naive_tw -> naive_plain).
                s_naive_plain = (
                    wilson_lcb_95(stats.wins, stats.closed_positions_count)
                    * _edge_factor(stats.avg_pnl_per_contract)
                )
                comparison_by_wallet[wallet] = {
                    "wallet": wallet,
                    "user_name": entry.user_name,
                    "s_naive_tw": round(naive_tw.composite_score, 4),
                    "s_naive_plain": round(s_naive_plain, 4),
                    "s_realized": round(scored_no_cat.composite_score, 4),
                    "pnl_inflation_ratio": report.realized_pnl.pnl_inflation_ratio,
                    "realized_pnl_usdc": report.realized_pnl.realized_pnl_usdc,
                    "excluded_realized": scored_no_cat.excluded,
                    "exclusion_reason": scored_no_cat.exclusion_reason,
                    "window_truncated": truncated_by_wallet.get(wallet, False),
                }
                scored_global_naive.append((wallet, entry, naive_tw))
                for cat in rec["categories_seen"]:
                    if cat == "GLOBAL":
                        continue
                    stats_cat = stats
                    object.__setattr__(stats_cat, "top_categories", (cat,))
                    scored_per_category_naive.setdefault(cat, []).append(
                        (wallet, entry, score_polymarket_whale(
                            stats_cat, target_category=cat, min_resolved=min_resolved,
                        )),
                    )

        # 5. Rule B selection (realized scores → the roster we write).
        selected = _select_rule_b(
            scored_per_category, scored_global,
            top_per_category=top_per_category, top_global=top_global,
        )

        # 6. Materialize selection records for agent_state (realized metrics
        # are ADDITIVE — the consumed keys wallet/user_name/category/rank/
        # composite_score are preserved).
        finalists = sorted(
            selected.items(), key=lambda kv: kv[1][1].composite_score, reverse=True,
        )
        selected_records: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        for rank_i, (wallet, (entry, sw, source_cat)) in enumerate(finalists):
            report = report_by_wallet.get(wallet)
            rp = report.realized_pnl if report else None
            buy_usdc = report.total_buy_usdc_resolved if report else 0.0
            realized = rp.realized_pnl_usdc if rp else 0.0
            realized_roi = (realized / buy_usdc) if buy_usdc > 0 else 0.0
            n_res = report.n_resolved_decisions if report else 0
            n_win = report.n_winning_decisions if report else 0
            dec_wr = (n_win / n_res) if n_res else 0.0
            inflation = rp.pnl_inflation_ratio if rp else 0.0
            selected_records.append({
                "wallet": wallet,
                "user_name": entry.user_name,
                "category": source_cat,
                "rank": rank_i + 1,
                "composite_score": round(sw.composite_score, 4),
                # additive realized metrics (option (c) Phase 1)
                "realized_pnl_usdc": round(realized, 2),
                "realized_roi": round(realized_roi, 4),
                "pnl_inflation_ratio": round(inflation, 4),
                "n_resolved_decisions": n_res,
                "n_winning_decisions": n_win,
                "decision_win_rate": round(dec_wr, 4),
                "window_truncated": truncated_by_wallet.get(wallet, False),
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
                "realized_roi": round(realized_roi, 4),
                "pnl_inflation_ratio": round(inflation, 3),
                "decision_win_rate": round(dec_wr, 3),
                "n_resolved_decisions": n_res,
                "realized_pnl_usdc": round(realized, 0),
                "lifetime_vol_usdc": round(entry.vol, 0),
                "lifetime_pnl_usdc": round(entry.pnl, 0),
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
            "window_truncated": n_truncated,
        }

        # Inflation-gated-out list (D4) — whales excluded specifically by the
        # inflation gate, surfaced so the operator can calibrate the threshold.
        # Computed always (cheap); written into selection_metadata.
        gated_out: list[dict[str, Any]] = []
        for wallet, rec in candidates.items():
            report = report_by_wallet.get(wallet)
            if not report:
                continue
            ratio = report.realized_pnl.pnl_inflation_ratio
            if ratio > inflation_threshold:
                gated_out.append({
                    "wallet": wallet,
                    "user_name": rec["entry"].user_name,
                    "pnl_inflation_ratio": round(ratio, 4),
                    "realized_pnl_usdc": round(report.realized_pnl.realized_pnl_usdc, 2),
                    "n_resolved_decisions": report.n_resolved_decisions,
                    "window_truncated": truncated_by_wallet.get(wallet, False),
                })
        gated_out.sort(key=lambda g: g["pnl_inflation_ratio"], reverse=True)
        summary["gated_out_inflation"] = gated_out

        # Unrankable: window_truncated whales, hard-gated from algorithmic
        # selection (realized PnL is a floor-bounded estimate — activity window
        # exceeds the fetch ceiling). Surfaced with their PARTIAL numbers, not
        # silently dropped; an operator can still manually pin one after review.
        unrankable: list[dict[str, Any]] = []
        for wallet, rec in candidates.items():
            if not truncated_by_wallet.get(wallet, False):
                continue
            report = report_by_wallet.get(wallet)
            rp = report.realized_pnl if report else None
            buy = report.total_buy_usdc_resolved if report else 0.0
            realized = rp.realized_pnl_usdc if rp else 0.0
            unrankable.append({
                "wallet": wallet,
                "user_name": rec["entry"].user_name,
                "window_truncated": True,
                "n_resolved_decisions_partial": report.n_resolved_decisions if report else 0,
                "realized_pnl_usdc_partial": round(realized, 2),
                "realized_roi_partial": round((realized / buy) if buy > 0 else 0.0, 4),
                "pnl_inflation_ratio_partial": round(rp.pnl_inflation_ratio, 4) if rp else 0.0,
            })
        unrankable.sort(key=lambda u: -u["n_resolved_decisions_partial"])
        summary["unrankable_truncated"] = unrankable

        # Cause attribution: diff the realized roster vs the legacy naive
        # roster and split each mover's score change into the time-weighting
        # component and the realized-basis component (review modes only).
        if compute_comparison:
            selected_naive = _select_rule_b(
                scored_per_category_naive, scored_global_naive,
                top_per_category=top_per_category, top_global=top_global,
            )
            new_rank = {w: i + 1 for i, (w, _v) in enumerate(finalists)}
            naive_finalists = sorted(
                selected_naive.items(),
                key=lambda kv: kv[1][1].composite_score, reverse=True,
            )
            naive_rank = {w: i + 1 for i, (w, _v) in enumerate(naive_finalists)}
            new_w, naive_w = set(new_rank), set(naive_rank)
            movers: list[dict[str, Any]] = []
            for wallet in sorted(new_w | naive_w):
                nr, oldr = new_rank.get(wallet), naive_rank.get(wallet)
                if wallet in new_w and wallet in naive_w and nr == oldr:
                    continue  # unchanged — not a mover
                comp = comparison_by_wallet.get(wallet, {})
                s_tw = comp.get("s_naive_tw") or 0.0
                s_plain = comp.get("s_naive_plain") or 0.0
                s_real = comp.get("s_realized") or 0.0
                movers.append({
                    "wallet": wallet,
                    "user_name": comp.get("user_name", ""),
                    "naive_rank": oldr,
                    "new_rank": nr,
                    "s_naive_tw": comp.get("s_naive_tw"),
                    "s_naive_plain": comp.get("s_naive_plain"),
                    "s_realized": comp.get("s_realized"),
                    "delta_timeweight": round(s_plain - s_tw, 4),
                    "delta_realized": round(s_real - s_plain, 4),
                    "excluded_realized": comp.get("excluded_realized"),
                    "exclusion_reason": comp.get("exclusion_reason"),
                })
            summary["dry_run_comparison"] = {
                "naive_roster_size": len(naive_w),
                "new_roster_size": len(new_w),
                "added": sorted(new_w - naive_w),
                "dropped": sorted(naive_w - new_w),
                "movers": movers,
            }

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    # Build the roster to WRITE to selected_whales. DEFAULT (pins-only) writes
    # ONLY the operator's pinned whales: the algorithm INFORMS (full report
    # above) but never auto-selects, preserving the manual-promotion workflow
    # (verification report 2026-06-09). algo_select (explicit opt-in) writes the
    # algorithm's picks + pins — the legacy behavior. The report's
    # `selected_whales` keeps the algorithm RANKING either way for operator
    # review; only the WRITTEN roster differs by mode.
    write_records: list[dict[str, Any]] = (
        [dict(r) for r in selected_records] if algo_select else []
    )

    # Merge manually-pinned whales (promoted via dashboard) so they survive
    # this refresh. Dedupe by lower-cased wallet. UNCHANGED merge logic — only
    # the base list it merges into differs by mode (algo picks, or empty).
    try:
        pin_rec = load_agent_state(
            "polymarket_copy_trader", "pinned_whales", db_url=db_url,
        )
    except Exception:
        pin_rec = None
    pinned_entries = pin_rec[0] if (pin_rec and isinstance(pin_rec[0], list)) else []
    write_wallets = {
        str(s.get("wallet") or s.get("proxy_wallet") or "").lower()
        for s in write_records if isinstance(s, dict)
    }
    n_pinned_merged = 0
    for p in pinned_entries:
        if not isinstance(p, dict):
            continue
        w_lower = str(p.get("wallet") or p.get("proxy_wallet") or "").lower()
        if not w_lower or w_lower in write_wallets:
            continue
        write_records.append({
            "wallet": w_lower,
            "user_name": str(p.get("user_name") or ""),
            "category": str(p.get("category") or "pinned"),
            "rank": None,
            "composite_score": None,
            "source": "pinned_promotion",
        })
        write_wallets.add(w_lower)
        n_pinned_merged += 1
    summary["pinned_merged"] = n_pinned_merged
    summary["write_mode"] = "algo_select" if algo_select else "pins_only"
    summary["written_selected_whales"] = write_records

    if not dry_run:
        set_agent_state(
            "polymarket_copy_trader", "selected_whales", write_records,
            db_url=db_url,
        )
        set_agent_state(
            "polymarket_copy_trader", "selection_metadata", summary,
            db_url=db_url,
        )

    return summary


def _print_human(summary: dict[str, Any]) -> None:
    print(f"=== Polymarket Whale Selection (realized basis) — {summary['started_at']} ===")
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
    print(
        f"Algorithm ranking — top {len(summary['selected_whales'])} "
        f"(realized basis; written to the roster only under --algo-select):"
    )
    print()
    print(
        f"{'#':>3} | {'Wallet':<14} | {'User':<22} | {'Source':<10} | "
        f"{'Score':>7} | {'Wilson':>7} | {'ROI':>7} | {'InflR':>6} | "
        f"{'DecWR':>5} | {'Ndec':>4} | {'RealPnL$':>11} | {'LifePnL$':>10}"
    )
    print("-" * 140)
    for d in summary["selection_details"]:
        print(
            f"{d['rank']:>3} | {d['wallet'][:14]} | {d['user_name'][:22]:<22} | "
            f"{d['source_category']:<10} | {d['composite_score']:>7.4f} | "
            f"{d['wilson_lcb']:>7.4f} | {d['realized_roi']:>+7.3f} | "
            f"{d['pnl_inflation_ratio']:>6.3f} | {d['decision_win_rate']:>5.2f} | "
            f"{d['n_resolved_decisions']:>4} | "
            f"${d['realized_pnl_usdc']:>10,.0f} | ${d['lifetime_pnl_usdc']:>9,.0f}"
        )
    print()


def _print_gated_out(summary: dict[str, Any]) -> None:
    gated = summary.get("gated_out_inflation", [])
    thr = summary.get("params", {}).get("inflation_threshold")
    print(f"=== Gated out by inflation ratio > {thr} ({len(gated)} whales) ===")
    if not gated:
        print("  (none)")
        print()
        return
    print(f"{'Wallet':<14} | {'User':<22} | {'InflR':>6} | {'RealPnL$':>12} | {'Ndec':>4}")
    print("-" * 70)
    for g in gated:
        print(
            f"{g['wallet'][:14]} | {g['user_name'][:22]:<22} | "
            f"{g['pnl_inflation_ratio']:>6.3f} | ${g['realized_pnl_usdc']:>11,.0f} | "
            f"{g['n_resolved_decisions']:>4}"
        )
    print()


def _print_unrankable(summary: dict[str, Any]) -> None:
    unr = summary.get("unrankable_truncated", [])
    print(
        f"=== Unrankable: activity window exceeds fetch ceiling "
        f"({len(unr)} whales — excluded from algo selection; pin-overridable) ==="
    )
    if not unr:
        print("  (none)")
        print()
        return
    print(
        f"{'Wallet':<14} | {'User':<22} | {'Ndec*':>5} | {'ROI*':>7} | "
        f"{'RealPnL$*':>12} | {'InflR*':>7}"
    )
    print("-" * 82)
    for u in unr:
        print(
            f"{u['wallet'][:14]} | {u['user_name'][:22]:<22} | "
            f"{u['n_resolved_decisions_partial']:>5} | {u['realized_roi_partial']:>+7.3f} | "
            f"${u['realized_pnl_usdc_partial']:>11,.0f} | {u['pnl_inflation_ratio_partial']:>7.3f}"
        )
    print("  (* PARTIAL — floor-bounded estimate over the truncated window)")
    print()


def _print_dry_run_diff(summary: dict[str, Any]) -> None:
    cmp = summary.get("dry_run_comparison")
    if not cmp:
        return
    print(
        f"=== Dry-run diff vs naive roster — naive {cmp['naive_roster_size']} "
        f"vs realized {cmp['new_roster_size']} ==="
    )
    print(f"  added (in realized, not naive):   {cmp['added'] or '(none)'}")
    print(f"  dropped (in naive, not realized): {cmp['dropped'] or '(none)'}")
    print()
    print("Movers — score change attributed to cause:")
    print("  delta_timeweight = naive_plain - naive_tw  (effect of dropping time-weighting)")
    print("  delta_realized   = realized   - naive_plain (effect of realized-basis inputs)")
    print()
    print(
        f"{'Wallet':<14} | {'User':<18} | {'oldR':>4} | {'newR':>4} | "
        f"{'TW':>7} | {'Plain':>7} | {'Real':>7} | {'dTW':>7} | {'dReal':>7} | gate"
    )
    print("-" * 110)
    for m in cmp["movers"]:
        gate = m.get("exclusion_reason") or ("" if not m.get("excluded_realized") else "excluded")
        print(
            f"{m['wallet'][:14]} | {(m['user_name'] or '')[:18]:<18} | "
            f"{str(m['naive_rank'] or '-'):>4} | {str(m['new_rank'] or '-'):>4} | "
            f"{(m['s_naive_tw'] if m['s_naive_tw'] is not None else 0.0):>7.4f} | "
            f"{(m['s_naive_plain'] if m['s_naive_plain'] is not None else 0.0):>7.4f} | "
            f"{(m['s_realized'] if m['s_realized'] is not None else 0.0):>7.4f} | "
            f"{m['delta_timeweight']:>+7.4f} | {m['delta_realized']:>+7.4f} | {gate}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-per-category", type=int, default=2)
    parser.add_argument("--top-global", type=int, default=2)
    parser.add_argument("--candidates", type=int, default=20)
    parser.add_argument("--min-resolved", type=int, default=DEFAULT_MIN_RESOLVED)
    parser.add_argument(
        "--inflation-threshold", type=float, default=DEFAULT_INFLATION_RATIO_THRESHOLD,
    )
    # Sentinel default None so we can WARN when the legacy flag is explicitly
    # passed rather than silently no-op'ing it.
    parser.add_argument("--half-life-days", type=float, default=None)
    parser.add_argument("--activity-limit", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=10)
    # Default None = walk to exhaustion (bounded by --max-pages). Set a value
    # only to restore the legacy early-stop behavior.
    parser.add_argument("--target-buy-rows", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--algo-select", action="store_true",
        help="DANGEROUS opt-in: write algorithm top-N + pins (auto-expands the "
             "copy roster). DEFAULT is pins-only — the algorithm informs the "
             "report but only pinned (operator-promoted) whales are written.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    secrets = load_secrets()

    half_life = args.half_life_days
    if half_life is not None:
        log.warning(
            "--half-life-days=%s does NOT affect the written roster: the "
            "realized-basis selection scorer is not time-weighted (it only "
            "changes the dry-run naive reference column). Time-weighting is "
            "deferred to Phase 3.", half_life,
        )
    else:
        half_life = DEFAULT_HALF_LIFE_DAYS

    summary = asyncio.run(refresh_polymarket_selection(
        db_url=secrets.db_url,
        top_per_category=args.top_per_category,
        top_global=args.top_global,
        candidates_per_category=args.candidates,
        min_resolved=args.min_resolved,
        inflation_threshold=args.inflation_threshold,
        half_life_days=half_life,
        activity_limit=args.activity_limit,
        max_pages=args.max_pages,
        target_buy_rows=args.target_buy_rows,
        dry_run=args.dry_run,
        algo_select=args.algo_select,
    ))

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary)
        _print_gated_out(summary)
        _print_unrankable(summary)
        _print_dry_run_diff(summary)  # no-ops if no comparison (algo-select mode)
        n_pins = summary.get("pinned_merged", 0)
        n_written = len(summary.get("written_selected_whales", []))
        if args.dry_run:
            print("(dry-run — NOT written to agent_state; DB read-only)")
        elif summary.get("write_mode") == "pins_only":
            print(
                f"Written PINS-ONLY: {n_written} whale(s) (= {n_pins} pinned) to "
                f"selected_whales. The algorithm ranking above is ADVISORY — NOT "
                f"written. Re-run with --algo-select to write algo picks + pins."
            )
        else:
            print(
                f"Written ALGO-SELECT: {n_written} whale(s) (algo picks + {n_pins} "
                f"pinned) to selected_whales."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
