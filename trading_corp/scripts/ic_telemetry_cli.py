"""CLI tool for IC telemetry queries.

Usage examples (run from repo root):

  # Combo-grouped P&L for all combos:
  python -m trading_corp.scripts.ic_telemetry_cli pnl

  # Same, scoped to May 2026:
  python -m trading_corp.scripts.ic_telemetry_cli pnl \\
      --start 2026-05-01 --end 2026-06-01

  # Win rate bucketed by IVR-at-entry:
  python -m trading_corp.scripts.ic_telemetry_cli ivr

  # Adjusted vs unadjusted P&L distribution:
  python -m trading_corp.scripts.ic_telemetry_cli adjust

  # Scan-filter counters (today only):
  python -m trading_corp.scripts.ic_telemetry_cli scan --date 2026-05-17

  # Combo slippage distribution:
  python -m trading_corp.scripts.ic_telemetry_cli slippage

Output is plain-text tables on stdout — pipe to less / a file for
review. JSON mode (`--json`) is available per command for downstream
tooling.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from trading_corp.agents.ic_telemetry import (
    adjustment_outcome_stats,
    combo_pnl_report,
    combo_slippage_stats,
    scan_filter_counters,
    win_rate_by_ivr,
)


DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"


def _fmt_money(x: float | None) -> str:
    if x is None:
        return "    —    "
    sign = "-" if x < 0 else " "
    return f"{sign}${abs(x):>9,.2f}"


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "  —  "
    return f"{x*100:>5.1f}%"


def _fmt_int(x: int | None) -> str:
    return "  —  " if x is None else f"{x:>5d}"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_pnl(args) -> None:
    report = combo_pnl_report(
        strategy=args.strategy, division=args.division,
        start_ts=args.start, end_ts=args.end, db_url=args.db,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return
    s = report["summary"]
    print("\nIRON CONDOR — Combo P&L Report")
    print("=" * 78)
    print(f"  Realized combos:    {s['realized_count']}  "
          f"(open: {s['open_count']})")
    print(f"  Wins / losses:      {s['win_count']} / {s['loss_count']}    "
          f"Win rate: {_fmt_pct(s['win_rate'])}")
    print(f"  Mean win:           {_fmt_money(s['mean_win'])}")
    print(f"  Mean loss:          {_fmt_money(s['mean_loss'])}")
    print(f"  Expectancy / combo: {_fmt_money(s['expectancy'])}")
    print(f"  Total realized:     {_fmt_money(s['total_realized'])}")
    if not args.summary_only and report["combos"]:
        print("\n  Combo                          Symbol  Status     P&L         Legs (O/C)")
        print("  " + "-" * 76)
        for c in report["combos"][:args.limit]:
            cid = (c["combo_id"] or "")[:24]
            sym = c.get("symbol") or "?"
            print(
                f"  {cid:<24}    {sym:<6}  {c['status']:<9} "
                f"{_fmt_money(c['net_pnl'])}     "
                f"{c['open_legs']}/{c['close_legs']}"
            )
    print()


def cmd_ivr(args) -> None:
    report = win_rate_by_ivr(
        strategy=args.strategy,
        start_ts=args.start, end_ts=args.end, db_url=args.db,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return
    print("\nIRON CONDOR — Win Rate by IVR-at-Entry Bucket")
    print("=" * 78)
    print(f"  Total closed:   {report['total_closed']}    "
          f"Unbucketed (missing IVR): {report['unbucketed_count']}")
    print()
    print("  Bucket    Count   Wins   Losses  Win Rate    Mean P&L     Mean Credit")
    print("  " + "-" * 76)
    for b in report["buckets"]:
        print(
            f"  {b['label']:<8}  {_fmt_int(b['count'])}  "
            f"{_fmt_int(b['win_count'])}  {_fmt_int(b['loss_count'])}   "
            f"{_fmt_pct(b['win_rate'])}    "
            f"{_fmt_money(b['mean_pnl_dollars'])}    "
            f"{_fmt_money(b['mean_credit_at_entry'])}"
        )
    print()


def cmd_adjust(args) -> None:
    report = adjustment_outcome_stats(
        strategy=args.strategy,
        start_ts=args.start, end_ts=args.end, db_url=args.db,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return
    print("\nIRON CONDOR — Adjustment Outcome Stats")
    print("=" * 78)
    print(f"  Metric            Adjusted     Unadjusted")
    print("  " + "-" * 76)
    a = report["adjusted"]; u = report["unadjusted"]
    print(f"  Count             {_fmt_int(a['count'])}        {_fmt_int(u['count'])}")
    print(f"  Wins / Losses     {a['win_count']}/{a['loss_count']}          "
          f"{u['win_count']}/{u['loss_count']}")
    print(f"  Win rate          {_fmt_pct(a['win_rate'])}       "
          f"{_fmt_pct(u['win_rate'])}")
    print(f"  Mean P&L          {_fmt_money(a['mean_pnl'])}    "
          f"{_fmt_money(u['mean_pnl'])}")
    print(f"  Median P&L        {_fmt_money(a['median_pnl'])}    "
          f"{_fmt_money(u['median_pnl'])}")
    print(f"  Stdev P&L         {_fmt_money(a['stdev_pnl'])}    "
          f"{_fmt_money(u['stdev_pnl'])}")
    print(f"  Total P&L         {_fmt_money(a['total_pnl'])}    "
          f"{_fmt_money(u['total_pnl'])}")
    print()


def cmd_scan(args) -> None:
    report = scan_filter_counters(
        strategy=args.strategy, date_iso=args.date, db_url=args.db,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return
    print("\nIRON CONDOR — Scan-Filter Counters")
    print("=" * 78)
    print(f"  Total filtered passes:  {report['total_filtered']}")
    print()
    print("  Totals by reason:")
    print("  " + "-" * 76)
    for reason, n in sorted(
        report["totals_by_reason"].items(),
        key=lambda x: x[1], reverse=True,
    ):
        print(f"    {reason:<40} {_fmt_int(n)}")
    if not args.summary_only:
        print()
        print("  Per-day breakdown:")
        print("  " + "-" * 76)
        for day in sorted(report["by_day"].keys()):
            symbols = report["by_day"][day] or {}
            day_total = sum(int(s.get("total", 0)) for s in symbols.values())
            print(f"    {day}   total={day_total}")
            for sym, payload in sorted(symbols.items()):
                bk = payload.get("by_reason") or {}
                reasons = ", ".join(f"{k}={v}" for k, v in sorted(bk.items()))
                print(f"      {sym:<6}  total={payload.get('total')}  {reasons}")
    print()


def cmd_slippage(args) -> None:
    report = combo_slippage_stats(
        strategy=args.strategy, division=args.division,
        start_ts=args.start, end_ts=args.end, db_url=args.db,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return
    s = report["summary"]
    print("\nIRON CONDOR — Combo Slippage Distribution")
    print("=" * 78)
    print(f"  Combo fills:      {s['n']}")
    print(f"  Mean slippage:    {_fmt_money(s['mean_slippage'])}")
    print(f"  Median slippage:  {_fmt_money(s['median_slippage'])}")
    print(f"  p90 slippage:     {_fmt_money(s['p90_slippage'])}")
    print(f"  Max slippage:     {_fmt_money(s['max_slippage'])}")
    print(f"  Total realized:   {_fmt_money(s['total_slippage_realized'])}")
    if not args.summary_only and report["events"]:
        print()
        print("  Recent events:")
        print("  " + "-" * 76)
        for e in report["events"][-args.limit:]:
            cid = (e["combo_id"] or "")[:16]
            print(
                f"    {e['ts']}  {cid:<16}  {e['direction']:<6}  "
                f"limit={_fmt_money(e['net_limit'])}  "
                f"actual={_fmt_money(e['net_actual'])}  "
                f"slip={_fmt_money(e['slippage_dollars'])}"
            )
    print()


# ---------------------------------------------------------------------------
# argparse glue
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ic-telemetry",
        description="Telemetry queries for the Robinhood Joint IC strategy.",
    )
    p.add_argument("--db", default=DEFAULT_DB_URL,
                   help="SQLite db_url (default %(default)s)")
    p.add_argument("--strategy", default="robinhood_joint_iron_condor",
                   help="strategy slug filter; pass empty to disable")
    p.add_argument("--division", default="robinhood_joint",
                   help="division filter; pass empty to disable")
    p.add_argument("--json", action="store_true",
                   help="emit raw JSON instead of formatted table")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_pnl = sub.add_parser("pnl", help="combo-grouped P&L report")
    p_pnl.add_argument("--start", help="start ts (ISO-8601, inclusive)")
    p_pnl.add_argument("--end", help="end ts (ISO-8601, exclusive)")
    p_pnl.add_argument("--limit", type=int, default=25,
                       help="max combos to list (default %(default)d)")
    p_pnl.add_argument("--summary-only", action="store_true")
    p_pnl.set_defaults(func=cmd_pnl)

    p_ivr = sub.add_parser("ivr", help="win rate bucketed by IVR-at-entry")
    p_ivr.add_argument("--start", help="start ts (ISO-8601, inclusive)")
    p_ivr.add_argument("--end", help="end ts (ISO-8601, exclusive)")
    p_ivr.set_defaults(func=cmd_ivr)

    p_adj = sub.add_parser("adjust", help="adjusted-vs-unadjusted outcome stats")
    p_adj.add_argument("--start", help="start ts (ISO-8601, inclusive)")
    p_adj.add_argument("--end", help="end ts (ISO-8601, exclusive)")
    p_adj.set_defaults(func=cmd_adjust)

    p_scan = sub.add_parser("scan", help="scan-filter counters")
    p_scan.add_argument("--date", help="single date (YYYY-MM-DD)")
    p_scan.add_argument("--summary-only", action="store_true")
    p_scan.set_defaults(func=cmd_scan)

    p_slip = sub.add_parser("slippage", help="combo slippage distribution")
    p_slip.add_argument("--start", help="start ts (ISO-8601, inclusive)")
    p_slip.add_argument("--end", help="end ts (ISO-8601, exclusive)")
    p_slip.add_argument("--limit", type=int, default=25)
    p_slip.add_argument("--summary-only", action="store_true")
    p_slip.set_defaults(func=cmd_slippage)

    args = p.parse_args(argv)
    # Empty string → None semantics on the strategy/division filters.
    if args.strategy == "":
        args.strategy = None
    if args.division == "":
        args.division = None
    args.func(args)
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
