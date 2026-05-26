"""On-demand per-whale audit for the Polymarket watch list.

When an operator considers a candidate from the watchlist, this CLI
generates the same deep-dive analysis the 2026-05-26 hand-proof on
Magamyman produced — but in one command:

  - Decision-level breakdown (clustering ratio vs raw fills)
  - REDEEM-grounded realized PnL (vs the watchlist's held-to-resolution
    estimate — surfaces the gap when there are partial sells)
  - Sell footprint (round-trip flag + partial-sell flag, gap-free
    composition; round-trip is a STRICT SUBSET of partial-sell)
  - Edge profile (sharp/contrarian vs favorite-farmer at a glance)
  - Category concentration (event-slug bucketing — flags single-event
    domination of the track record)
  - Plain-language verdict from a cheap LLM (Haiku) — narrates the
    deterministic numbers; never does arithmetic

Read-only against everything that matters:
  - No promotion / demotion. No writes to watch_only_whales,
    selected_whales, pinned_whales, or metrics_epoch.
  - Cache writes use an isolated `polymarket_whale_analyst` agent
    namespace in `agent_state` — see
    `agents/research/polymarket_whale_audit_cache.py` for the isolation
    invariant.

Usage::

    python -m trading_corp.scripts.analyze_polymarket_whale <wallet|user_name>
        [--json]                           # JSON output for piping
        [--force]                          # evict cache, force fresh run
        [--no-llm]                         # skip the LLM verdict
        [--partial-sell-threshold 0.20]    # sell_share cutoff for flag
        [--max-pages 10]                   # activity pages to walk (×500 rows)
        [--db-url sqlite:///data/trading_corp.db]
        [--no-cache]                       # disable cache layer entirely

Resolution:
  - If argv matches `0x[0-9a-f]{40}` → treat as wallet directly
  - Else → look up in `agent_state(polymarket_copy_trader, watch_only_whales)`
    by `user_name`. Stop-and-report if not found.

Cost:
  - Per LLM call (Haiku 4.5): ~$0.0013 uncached, ~$0.0011 cached. Daily
    cap of $1.00 enforced via `agent_state` accumulator — when hit, the
    verdict line just says "(daily cap reached)" instead of going silent.
  - --no-llm path: $0.00 LLM cost; deterministic-only report.

References:
  - reports/2026-05-26_polymarket_pnl_aggregation_fix_plan.md
    (background: why the watchlist's PnL needs an audit-time correction)
  - trading_corp/data/polymarket_whale_audit.py (the compute core)
  - trading_corp/agents/polymarket_whale_analyst.py (the LLM narrator)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone

from trading_corp.agents.polymarket_whale_analyst import (
    DEFAULT_DAILY_COST_CAP_USD, WhaleAnalyst,
)
from trading_corp.agents.research.polymarket_whale_audit_cache import (
    evict_audit, read_audit, write_audit,
)
from trading_corp.data.polymarket_data_api_client import (
    LeaderboardEntry, PolymarketDataAPIClient, PolymarketDataAPIError,
)
from trading_corp.data.polymarket_whale_audit import (
    DEFAULT_PARTIAL_SELL_THRESHOLD, WhaleAuditReport, build_audit_report,
)
from trading_corp.persistence.db import load_agent_state

log = logging.getLogger(__name__)

_WALLET_RE = re.compile(r"^0x[0-9a-f]{40}$", re.IGNORECASE)
_DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"


def _resolve_wallet(target: str, db_url: str) -> tuple[str, str]:
    """Map argv to (wallet, user_name).

    If `target` matches the wallet regex, return it directly with empty
    user_name (the audit will fill in from activity rows).

    Otherwise look up by user_name in the watchlist. Raises ValueError
    if not found — caller surfaces a stop-and-report.
    """
    target = target.strip()
    if _WALLET_RE.match(target):
        return target.lower(), ""
    # Look up in the watchlist
    loaded = load_agent_state(
        "polymarket_copy_trader", "watch_only_whales", db_url=db_url,
    )
    if loaded is None:
        raise ValueError(
            f"user_name '{target}' supplied, but the watchlist slot is empty "
            "— provide a 0x-wallet directly or run the seed first."
        )
    value, _ = loaded
    if not isinstance(value, list):
        raise ValueError(
            f"user_name '{target}' supplied, but watchlist is malformed: "
            f"expected list, got {type(value).__name__}"
        )
    # Try exact match first, then case-insensitive
    for row in value:
        if isinstance(row, dict) and row.get("user_name") == target:
            return str(row.get("proxy_wallet", "")).lower(), target
    target_lower = target.lower()
    for row in value:
        if (
            isinstance(row, dict)
            and str(row.get("user_name", "")).lower() == target_lower
        ):
            return str(row.get("proxy_wallet", "")).lower(), row.get("user_name", "")
    raise ValueError(
        f"user_name '{target}' not found in watchlist "
        f"(searched {len(value)} entries). Provide a 0x-wallet directly or "
        "confirm the user_name (case-sensitive first, then case-insensitive)."
    )


async def _fetch_activity_all(
    client: PolymarketDataAPIClient,
    wallet: str,
    *,
    max_pages: int,
    page_size: int = 500,
) -> list:
    """Walk /activity until exhaustion or max_pages. Returns flat list
    of ActivityRow most-recent-first."""
    out = []
    for page_idx in range(max_pages):
        offset = page_idx * page_size
        try:
            page = await client.fetch_activity(
                wallet, limit=page_size, offset=offset,
            )
        except PolymarketDataAPIError as e:
            log.warning(
                "activity fetch failed at offset=%d for %s: %s — stopping",
                offset, wallet[:10], e,
            )
            break
        if not page:
            break
        out.extend(page)
        if len(page) < page_size:
            break
    return out


async def _resolve_leaderboard_entry(
    client: PolymarketDataAPIClient, wallet: str,
) -> LeaderboardEntry | None:
    """Best-effort leaderboard lookup for the wallet. Returns None if
    not on the leaderboard — the audit still runs without it (the
    user_name fallback is the activity feed's `name` field).

    We don't paginate the whole leaderboard; we just check the GLOBAL
    bucket's top-500 because that's where the watchlist seed sources
    candidates from anyway. If a wallet isn't there, we skip — the
    deeper lookup is out of scope for the CLI.
    """
    try:
        rows = await client.fetch_leaderboard(category=None, limit=50, offset=0)
        for r in rows:
            if r.proxy_wallet.lower() == wallet.lower():
                return r
    except PolymarketDataAPIError:
        pass
    return None


async def _build_report(
    *,
    wallet: str,
    user_name_hint: str,
    max_pages: int,
    partial_sell_threshold: float,
    db_url: str | None,
    use_cache: bool,
    force: bool,
    no_llm: bool,
    daily_cost_cap_usd: float,
) -> tuple[WhaleAuditReport, bool, float]:
    """Returns (report, cache_hit, duration_seconds)."""
    started = datetime.now(timezone.utc)
    async with PolymarketDataAPIClient() as client:
        # Peek at most-recent activity timestamp for cache key
        try:
            head = await client.fetch_activity(wallet, limit=1, offset=0)
        except PolymarketDataAPIError as e:
            raise RuntimeError(f"activity peek failed: {e}") from e
        if not head:
            raise RuntimeError(
                f"no activity rows found for wallet {wallet[:10]}… — "
                "either the wallet has no Polymarket trades, or the data-api "
                "is returning empty (try again)."
            )
        activity_max_ts = head[0].timestamp

        # Cache lookup
        if use_cache and not force and db_url is not None:
            cached = read_audit(wallet, activity_max_ts, db_url=db_url)
            if cached is not None:
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                return cached, True, elapsed

        if force and db_url is not None:
            evict_audit(wallet, activity_max_ts, db_url=db_url)

        # Full fetch + resolutions
        activity = await _fetch_activity_all(
            client, wallet, max_pages=max_pages,
        )
        if not activity:
            raise RuntimeError(
                f"no activity rows after full fetch for {wallet[:10]}…"
            )
        # Recompute max_ts in case the deeper fetch yielded a later row
        # (shouldn't happen — feed is most-recent-first — but defensive)
        activity_max_ts = max(activity_max_ts, max(a.timestamp for a in activity))

        unique_cids = {a.condition_id for a in activity if a.condition_id}
        resolutions = await client.fetch_market_resolutions(list(unique_cids))

        leaderboard_entry = await _resolve_leaderboard_entry(client, wallet)

    # Compose audit (no I/O)
    report = build_audit_report(
        leaderboard_entry=leaderboard_entry,
        activity_rows=activity,
        resolutions=resolutions,
        proxy_wallet=wallet,
        partial_sell_threshold=partial_sell_threshold,
    )

    # If we have a user_name hint from the watchlist lookup and the
    # audit didn't pick one up from leaderboard or activity, use it.
    if user_name_hint and not report.user_name:
        import dataclasses as _dc
        report = _dc.replace(report, user_name=user_name_hint)

    # LLM narration (or null with reason)
    analyst = WhaleAnalyst(
        narrator_enabled=not no_llm,
        daily_cost_cap_usd=daily_cost_cap_usd,
        db_url=db_url,
    )
    narration_result = await analyst.narrate(report)
    import dataclasses as _dc
    report = _dc.replace(
        report,
        verdict_narration=narration_result.narration,
        verdict_null_reason=narration_result.null_reason,
        llm_cost_usd=narration_result.cost_usd,
        llm_tokens_in=narration_result.tokens_in,
        llm_tokens_out=narration_result.tokens_out,
    )

    # Persist to cache
    if use_cache and db_url is not None:
        write_audit(report, db_url=db_url)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return report, False, elapsed


def _print_human(report: WhaleAuditReport, *, cache_hit: bool, duration_s: float) -> None:
    """Six-section human-readable report. Mirrors `seed_polymarket_watchlist_deep.py`."""
    span_sec = (
        (report.activity_max_ts - report.activity_min_ts)
        if report.activity_max_ts > 0 else 0
    )
    span_days = span_sec / 86400.0
    c = report.clustering
    s = report.sell_footprint
    e = report.edge
    cat = report.category
    p = report.realized_pnl

    print(f"=== Polymarket Whale Audit — {report.user_name or '<no name>'} ({report.proxy_wallet[:10]}…) ===")
    print(
        f"Window: {report.n_resolved_decisions} resolved decisions over "
        f"{span_days:.1f} days  |  "
        f"raw rows examined: {report.n_raw_rows_examined}  |  "
        f"cache: {'HIT' if cache_hit else 'MISS'}  |  "
        f"elapsed: {duration_s:.1f}s"
    )
    print()
    print("Clustering")
    print(
        f"  raw_fills={c.n_raw_fills}  decisions={c.n_decisions}  "
        f"ratio={c.clustering_ratio}×  "
        f"(decisions_with_≥5_fills={c.decisions_with_ge_5_fills})"
    )
    if c.top_clusters_by_fill_count:
        print("  Top clusters (cid, oi, n_fills):")
        for cid, oi, n in c.top_clusters_by_fill_count:
            print(f"    {cid}.. oi={oi}  n={n}")
    print()

    print("Sell footprint")
    partial_share = (
        s.n_partial_sells / s.n_decisions_total if s.n_decisions_total else 0.0
    )
    print(
        f"  decisions_with_sells={s.n_decisions_with_sells}/{s.n_decisions_total} "
        f"({s.n_decisions_with_sells / s.n_decisions_total * 100:.0f}%)"
        if s.n_decisions_total else "  decisions_total=0"
    )
    print(
        f"  round_trips={s.n_round_trips}  "
        f"(sell_share ≥ 0.95 — STRICT subset of partial-sell)"
    )
    print(
        f"  partial_sells={s.n_partial_sells}  "
        f"(sell_share ≥ {s.partial_sell_threshold:.2f}, includes round-trips; "
        f"share_of_decisions={partial_share:.1%})"
    )
    print(f"  held_cleanly={s.n_held_cleanly}")
    if s.top_flagged_by_inflation_usdc:
        print("  Top flagged by inflation (held-to-res minus realized USDC):")
        for fd in s.top_flagged_by_inflation_usdc:
            print(
                f"    '{fd.title}'  oi={fd.outcome_index}  "
                f"buy=${fd.sum_buy_usdc:,.0f}  sell=${fd.sum_sell_usdc:,.0f}  "
                f"redeem=${fd.redeem_payout_usdc:,.0f}  "
                f"sell_share={fd.sell_share:.0%}  "
                f"{'[ROUND-TRIP]' if fd.is_round_trip else '[PARTIAL]'}  "
                f"realized=${fd.realized_pnl:,.0f}  held=${fd.held_to_resolution_pnl:,.0f}"
            )
    print()

    print("Edge profile")
    print(
        f"  avg_entry={e.avg_entry_price_decision_weighted}  "
        f"<.70={e.share_below_70:.0%}  >.85={e.share_above_85:.0%}"
    )
    print(f"  p25/p50/p75 = {e.p25_entry} / {e.p50_entry} / {e.p75_entry}")
    print()

    print("Category concentration")
    print(
        f"  distinct_event_slugs={cat.n_distinct_event_slugs}  "
        f"largest_event_share={cat.largest_event_share:.0%}"
    )
    if cat.top_3_event_slugs:
        for slug, n in cat.top_3_event_slugs:
            print(f"    {slug} ({n})")
    print()

    print("Realized PnL (REDEEM-grounded)")
    print(f"  realized            = ${p.realized_pnl_usdc:>+14,.2f}")
    print(f"  held_to_resolution  = ${p.held_to_resolution_pnl_usdc:>+14,.2f}  (watchlist view)")
    print(
        f"  inflation_gap       = ${p.pnl_inflation_usdc:>+14,.2f}  "
        f"(inflation_ratio={p.pnl_inflation_ratio:.2f})"
    )
    print(f"  from_clean_holds    = ${p.pnl_from_clean_holds_usdc:>+14,.2f}")
    print(f"  from_partial_sells  = ${p.pnl_from_partial_sells_usdc:>+14,.2f}")
    print()

    if report.verdict_narration:
        print(
            f"Verdict (Haiku 4.5, "
            f"{report.llm_tokens_in}+{report.llm_tokens_out} tok, "
            f"${report.llm_cost_usd:.4f}):"
        )
        for line in report.verdict_narration.splitlines():
            print(f"  {line}")
    else:
        # Surface the reason — never silent None per the build spec
        reason = report.verdict_null_reason or "unknown"
        reason_text = {
            "disabled_by_flag": "LLM disabled (--no-llm)",
            "llm_unavailable": "LLM unavailable (no ANTHROPIC_API_KEY or langchain-anthropic)",
            "daily_cap_hit": f"daily LLM cost cap reached ($DEFAULT_DAILY_COST_CAP_USD/day)",
            "llm_error": "LLM call errored (check logs); deterministic report still valid",
        }.get(reason, f"unknown reason: {reason}")
        print(f"Verdict: (none — {reason_text})")
    print()
    print(
        f"[ elapsed {duration_s:.1f}s · LLM cost ${report.llm_cost_usd:.4f} ]"
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze_polymarket_whale",
        description=(
            "On-demand per-whale audit for the Polymarket watch list. "
            "Computes decision clustering, sell footprint, edge profile, "
            "category concentration, and REDEEM-grounded realized PnL; "
            "optionally narrates a 2-4 sentence verdict via Haiku."
        ),
    )
    p.add_argument(
        "target",
        help="0x-wallet address OR user_name (resolved via watchlist agent_state)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of the human-readable block",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Evict the cache entry for this (wallet, activity_max_ts) and re-run",
    )
    p.add_argument(
        "--no-llm", action="store_true",
        help="Skip the LLM verdict (deterministic report only)",
    )
    p.add_argument(
        "--no-cache", action="store_true",
        help="Disable cache layer entirely (no read, no write)",
    )
    p.add_argument(
        "--partial-sell-threshold", type=float,
        default=DEFAULT_PARTIAL_SELL_THRESHOLD,
        help=f"sell_share cutoff for partial-sell flag (default {DEFAULT_PARTIAL_SELL_THRESHOLD})",
    )
    p.add_argument(
        "--max-pages", type=int, default=10,
        help="Activity pages to walk (×500 rows per page; default 10 → 5000 rows ceiling)",
    )
    p.add_argument(
        "--daily-cost-cap-usd", type=float,
        default=DEFAULT_DAILY_COST_CAP_USD,
        help=f"24h LLM spend cap (default ${DEFAULT_DAILY_COST_CAP_USD})",
    )
    p.add_argument(
        "--db-url", default=_DEFAULT_DB_URL,
        help=f"sqlite db url (default {_DEFAULT_DB_URL})",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable INFO-level logging",
    )
    return p


async def _main_async(args: argparse.Namespace) -> int:
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    db_url = None if args.no_cache else args.db_url

    try:
        wallet, user_name_hint = _resolve_wallet(args.target, args.db_url)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        report, cache_hit, elapsed = await _build_report(
            wallet=wallet,
            user_name_hint=user_name_hint,
            max_pages=args.max_pages,
            partial_sell_threshold=args.partial_sell_threshold,
            db_url=db_url,
            use_cache=not args.no_cache,
            force=args.force,
            no_llm=args.no_llm,
            daily_cost_cap_usd=args.daily_cost_cap_usd,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR (unexpected): {e}", file=sys.stderr)
        log.exception("unexpected failure in analyze_polymarket_whale")
        return 4

    if args.json:
        from dataclasses import asdict
        print(json.dumps(asdict(report), indent=2, default=str))
    else:
        _print_human(report, cache_hit=cache_hit, duration_s=elapsed)
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
