#!/usr/bin/env python3
"""pm_cli -- Prediction Markets P1 CLI. Delegates to trading_corp.prediction_markets; NO engine
imports. Subcommands: g0-validate, backfill, refresh, rollup, repair-categories, report.

Run on the box (existing venv), from the repo root:
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py <cmd> [opts]

Spec: reports/prediction_markets/P1_PLAN.md §5, §8, §10.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from trading_corp.prediction_markets import analyze, category, db, ingest, names, paper, rosters, stats


def _now() -> int:
    return int(time.time())


def _client():
    # imported lazily so offline unit tests (which never call network subcommands) don't need httpx
    from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
    return PolymarketDataAPIClient()


async def _cmd_g0_validate(args) -> int:
    async with _client() as c:
        res = await ingest.g0_validate(c, rosters.G0_KNOWN_LOSERS)
    print(json.dumps(res, indent=2, default=str))
    if not res["passed"]:
        print("G0 FAIL -- STOP AND REPORT (no pre-authorized pivot).", file=sys.stderr)
        return 1
    print("G0 PASS")
    return 0


def _seed_wallets(args) -> list[str]:
    if getattr(args, "only_wallets", None):
        return [w.lower() for w in args.only_wallets]   # subset backfill (bypass roster) - deploy checkpoint
    roster = rosters.load_seed_roster(
        legacy_db_path=args.legacy_db, seed_yaml_path=args.seed_yaml, extra_wallets=args.wallets or [])
    return [r["wallet"] for r in roster]


async def _cmd_backfill(args, *, backfill: bool = True) -> int:
    wallets = _seed_wallets(args)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "count": len(wallets), "wallets": wallets}, indent=2))
        return 0
    db.init_db(args.db)
    async with _client() as c:
        with db.connect(args.db) as conn:
            summary = await ingest.backfill_wallets(conn, wallets, client=c, now_ts=_now(),
                                                    backfill=backfill, cap=args.cap)
            stats.rollup(conn, now_ts=_now())
            stats.compute_scores(conn, now_ts=_now())
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary["failed"] else 1


def _cmd_rollup(args) -> int:
    db.init_db(args.db)
    with db.connect(args.db) as conn:
        n = stats.rollup(conn, now_ts=_now())
        m = stats.compute_scores(conn, now_ts=_now())
    print(json.dumps({"rolled_categories": n, "scored_snapshots": m}))
    return 0


async def _cmd_repair_categories(args) -> int:
    with db.connect(args.db) as conn:
        slugs = [r["event_slug"] for r in conn.execute(
            "SELECT DISTINCT event_slug FROM pm_closed_position WHERE category=? AND event_slug<>''",
            (category.CATEGORY_UNKNOWN,)).fetchall()]
    if not slugs:
        print(json.dumps({"unknown_events": 0, "repaired_rows": 0}))
        return 0
    mapping = await category.derive_categories_batch(slugs)
    repaired = 0
    with db.connect(args.db) as conn:
        for es, (cat, src) in mapping.items():
            if cat != category.CATEGORY_UNKNOWN:
                cur = conn.execute(
                    "UPDATE pm_closed_position SET category=?, category_source=? WHERE event_slug=? AND category=?",
                    (cat, src, es, category.CATEGORY_UNKNOWN))
                repaired += cur.rowcount or 0
        if hasattr(conn, "commit"):
            conn.commit()
        stats.rollup(conn, now_ts=_now())
        stats.compute_scores(conn, now_ts=_now())
    print(json.dumps({"unknown_events": len(slugs), "repaired_rows": repaired}))
    return 0


def _cmd_report(args) -> int:
    with db.connect(args.db) as conn:
        board = stats.query_scoreboard(conn, category=args.category, routine=args.routine,
                                       min_resolved=args.min_resolved)
    print(stats.format_report(board, fmt=args.format))
    return 0


def _cmd_sync_names(args) -> int:
    """Populate pm_whale.user_name from the roster labels (CP2 Phase 3, Option A). Re-runnable +
    idempotent -- names go STALE if a whale renames on Polymarket, so re-run to refresh. --status prints
    the last run (ts + counts) without writing. Does NOT touch ingest.py; never writes the legacy DB."""
    if getattr(args, "status", False):
        with db.connect(args.db) as conn:
            rec = names.last_sync(conn)
        print(json.dumps(rec or {"last_run_ts": None, "note": "sync-names never run"}, indent=2))
        return 0
    roster = rosters.load_seed_roster(
        legacy_db_path=args.legacy_db, seed_yaml_path=args.seed_yaml, extra_wallets=args.wallets or [])
    with db.connect(args.db) as conn:
        counts = names.sync_user_names(conn, roster, now_ts=_now())
    print(json.dumps(counts, indent=2))
    return 0


async def _cmd_paper_poll(args) -> int:
    """Poll /positions for PINNED whales -> capture genuinely-open paper entries (CP3a). Reads the live
    client read-only; writes ONLY pm_paper_trade in the PM DB (never the legacy DB, never the engine).
    NO cron -- run as a one-shot for Jack to review before any schedule is installed."""
    db.init_db(args.db)
    async with _client() as c:
        with db.connect(args.db) as conn:
            res = await paper.poll_pinned(conn, client=c, now_ts=_now())
    print(json.dumps(res, indent=2, default=str))
    return 0


def _cmd_paper_adjudicate(args) -> int:
    """Resolve pending_adjudication paper trades off pm_closed_position (weekly). Sync; no network.
    FAILS LOUD (exit 2) if a pinned-paper whale is not in the refresh set (C2.3) -- never warn-continue."""
    db.init_db(args.db)
    try:
        with db.connect(args.db) as conn:
            res = paper.adjudicate(conn, now_ts=_now())
    except paper.PaperSubsetError as e:
        print(json.dumps({"error": "subset_assertion_failed", "detail": str(e)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, default=str))
    return 0


def _cmd_migrate_roster(args) -> int:
    """Seed pm_roster(active=1) + pm_watchlist(pinned) with EVERY (wallet, category) in pm_category_stats
    for the migrated legacy whale set (Jack's ruling 2026-08-24; C2.4 REVERSED; P2_PLAN Ruling B). NO floor,
    'unknown' included, ALL categories paper-trade. Idempotent. Nothing can be unresolved -- pairs are
    generated from rows that exist. Reports the full eyeball table (wallet, user_name, category,
    rows_in_category, status) -- every pair -- for Jack to review before the poller's first run."""
    db.init_db(args.db)
    roster = rosters.load_seed_roster(legacy_db_path=args.legacy_db, seed_yaml_path=args.seed_yaml)
    wallets = [r["wallet"] for r in roster]
    with db.connect(args.db) as conn:
        res = paper.seed_farm_roster(conn, wallets=wallets, now_ts=_now())
        res["pairs"] = paper.seeded_pairs_table(conn, wallets=wallets)
        try:
            res["subset_after"] = paper.assert_pinned_subset_of_refresh(conn)
        except paper.PaperSubsetError as e:
            res["subset_after"] = {"error": str(e)}
    res["n_migrated_wallets"] = len(wallets)
    print(json.dumps(res, indent=2, default=str))
    return 0


def _cmd_analyze(args) -> int:
    """On-demand ANALYZE of ONE (wallet, category) over RESOLVED positions -- the SAME code path as the farm
    [Analyze] button. Writes pm_analysis_cache + pm_analysis_cost in the PM DB (NEVER agent_state, NEVER the
    legacy DB). Prints the deterministic report + verdict/null_reason + the day's cost as JSON. --force
    re-analyzes (evicts the cached verdict); --no-llm forces the disabled_by_flag reasoned-null (the
    deterministic report still renders). With no ANTHROPIC key in the env the verdict is llm_unavailable --
    the SAME reasoned-null pm_web renders today (the key is not wired, e3)."""
    from dataclasses import asdict
    db.init_db(args.db)
    with db.connect(args.db) as conn:
        rep = analyze.analyze_whale(conn, args.wallet, args.category, now_ts=_now(),
                                    force=args.force, narrator_enabled=not args.no_llm)
        day = analyze._utc_day(_now())
        spent, n_calls = analyze.daily_cost(conn, day)
    out = asdict(rep)
    out["flags"] = analyze.analysis_flags(rep)
    out["_cost_today_usd"] = spent
    out["_cost_cap_usd"] = analyze.PM_ANALYZE_DAILY_CAP_USD
    out["_cost_day_utc"] = day
    out["_llm_available"] = analyze.is_llm_available()
    print(json.dumps(out, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pm_cli", description="Prediction Markets P1 CLI")
    p.add_argument("--db", default=db.pm_db_path(), help="PM DB path (default: PM_DB_PATH or data/prediction_markets.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    g0 = sub.add_parser("g0-validate", help="prove negative realizedPnl rows exist for known losers")
    g0.set_defaults(func=_cmd_g0_validate, is_async=True)

    for name, help_ in (("backfill", "full pull per wallet"), ("refresh", "idempotent re-pull")):
        b = sub.add_parser(name, help=help_)
        b.add_argument("--legacy-db", default=rosters.LEGACY_DB_DEFAULT)
        b.add_argument("--seed-yaml", default=None)
        b.add_argument("--wallets", nargs="*", default=None, help="extra wallets ADDED to the roster union")
        b.add_argument("--only-wallets", nargs="*", default=None, dest="only_wallets",
                       help="backfill ONLY these wallets, bypassing the roster (deploy single-wallet checkpoint)")
        b.add_argument("--dry-run", action="store_true")
        b.add_argument("--cap", type=int, default=8000,
                       help="max rows pulled per wallet (completeness backstop; raise for mega-whales, "
                            "e.g. --cap 50000; a wallet that hits the cap is marked PARTIAL and NOT ranked)")
        b.set_defaults(func=(lambda a: _cmd_backfill(a, backfill=(name == "backfill"))), is_async=True)

    r = sub.add_parser("rollup", help="recompute pm_category_stats + scores")
    r.set_defaults(func=_cmd_rollup, is_async=False)

    rc = sub.add_parser("repair-categories", help="gamma events-tag-join for unknown-category rows")
    rc.set_defaults(func=_cmd_repair_categories, is_async=True)

    rep = sub.add_parser("report", help="ranked scoreboard")
    rep.add_argument("--category", default=None)
    rep.add_argument("--routine", default="net_roi", choices=["net_roi", "recency_weighted"])
    rep.add_argument("--min-resolved", type=int, default=stats.DEFAULT_MIN_RESOLVED, dest="min_resolved")
    rep.add_argument("--format", default="table", choices=["table", "json"])
    rep.set_defaults(func=_cmd_report, is_async=False)

    sn = sub.add_parser("sync-names", help="populate pm_whale.user_name from roster labels "
                        "(re-runnable + idempotent; names go STALE on a Polymarket rename -> re-run)")
    sn.add_argument("--legacy-db", default=rosters.LEGACY_DB_DEFAULT)
    sn.add_argument("--seed-yaml", default=None)
    sn.add_argument("--wallets", nargs="*", default=None, help="extra wallets ADDED to the roster union")
    sn.add_argument("--status", action="store_true",
                    help="print the last sync-names run (ts + counts) and exit; no write")
    sn.set_defaults(func=_cmd_sync_names, is_async=False)

    pp = sub.add_parser("paper-poll", help="poll /positions for PINNED whales -> capture paper entries (CP3a)")
    pp.set_defaults(func=_cmd_paper_poll, is_async=True)

    pa = sub.add_parser("paper-adjudicate",
                        help="resolve pending_adjudication paper trades off pm_closed_position (CP3a)")
    pa.set_defaults(func=_cmd_paper_adjudicate, is_async=False)

    mr = sub.add_parser("migrate-roster",
                        help="seed pm_roster + pm_watchlist(pinned) = every (wallet,category) in pm_category_stats for the migrated whales (CP3a; Ruling B)")
    mr.add_argument("--legacy-db", default=rosters.LEGACY_DB_DEFAULT)
    mr.add_argument("--seed-yaml", default=None)
    mr.set_defaults(func=_cmd_migrate_roster, is_async=False)

    an = sub.add_parser("analyze",
                        help="on-demand narrated audit of ONE (wallet,category) over resolved positions (CP3b-2)")
    an.add_argument("--wallet", required=True)
    an.add_argument("--category", required=True)
    an.add_argument("--force", action="store_true", help="re-analyze: evict the cached verdict first")
    an.add_argument("--no-llm", action="store_true", dest="no_llm",
                    help="skip narration (disabled_by_flag reasoned-null); deterministic report only")
    an.set_defaults(func=_cmd_analyze, is_async=False)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "is_async", False):
        return asyncio.run(args.func(args))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
