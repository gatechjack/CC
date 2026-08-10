#!/usr/bin/env python3
"""MACE economic-event calendar CLI — manage the `economic_event` table.

Plan: planning/mace_v1_plan.md § Phase 1. Manual events (OPEC+/Copom/
BR-election/politburo) are operator-supplied here as source='manual'; the
FOMC/CPI/NFP seeds and the LPR-fix rule are (re)generated idempotently.

Subcommands:
    list          show events (optionally filtered by date bounds/type/source/scope)
    add           add ONE manual event (source='manual')
    remove        delete matching events
    seed          re-import FOMC/CPI/NFP from config/macro_calendar.yaml (idempotent)
    generate-lpr  (re)generate LPR_FIX rows on the 20th-monthly rule (idempotent)
    refresh       seed + generate-lpr in one shot (the weekly-refresh action)

Usage:
    python scripts/mace_calendar_cli.py list
    python scripts/mace_calendar_cli.py list --from 2026-08-01 --to 2026-12-31 --type FOMC
    python scripts/mace_calendar_cli.py add --type OPEC --date 2026-09-07 --scope USO
    python scripts/mace_calendar_cli.py remove --type OPEC --date 2026-09-07 --scope USO
    python scripts/mace_calendar_cli.py refresh
    python scripts/mace_calendar_cli.py --db sqlite:///data/trading_corp.db list

All subcommands accept --db (default sqlite:///data/trading_corp.db).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trading_corp.persistence import db as _db
from trading_corp.mace import calendar as cal


def _print_events(rows: list[dict]) -> None:
    if not rows:
        print("(no matching events)")
        return
    print(f"{'DATE':<12} {'TYPE':<14} {'SCOPE':<8} {'SOURCE':<8} FETCHED_AT")
    for r in rows:
        print(f"{r['event_date']:<12} {r['event_type']:<14} {r['symbol_scope']:<8} "
              f"{r['source']:<8} {r['fetched_at']}")
    print(f"-- {len(rows)} event(s)")


def cmd_list(conn, args) -> int:
    rows = cal.list_events(
        conn, start=args.frm, end=args.to, event_type=args.type,
        source=args.source, symbol_scope=args.scope,
    )
    _print_events(rows)
    return 0


def cmd_add(conn, args) -> int:
    ins = cal.add_event(
        conn, event_type=args.type, event_date=args.date,
        source=cal.SOURCE_MANUAL, symbol_scope=args.scope,
    )
    print(f"{'ADDED' if ins else 'EXISTS (no-op)'}: {args.type} {args.date} "
          f"scope={args.scope} source=manual")
    return 0


def cmd_remove(conn, args) -> int:
    n = cal.remove_event(
        conn, event_type=args.type, event_date=args.date, symbol_scope=args.scope,
    )
    print(f"REMOVED {n} row(s): {args.type} {args.date} scope={args.scope}")
    return 0


def cmd_seed(conn, args) -> int:
    rep = cal.seed_from_macro_calendar(conn, args.macro)
    print(f"SEED FOMC/CPI/NFP: inserted={rep['inserted']} skipped={rep['skipped']} "
          f"unclassified={rep['unclassified']} by_type={rep['by_type']}")
    if rep.get("error"):
        print(f"  ERROR: {rep['error']}")
        return 1
    return 0


def cmd_generate_lpr(conn, args) -> int:
    rep = cal.generate_lpr_fix_rule(conn, months=args.months)
    print(f"LPR_FIX rule: inserted={rep['inserted']} skipped={rep['skipped']} "
          f"months={args.months}")
    print(f"  dates={rep['dates']}")
    return 0


def cmd_refresh(conn, args) -> int:
    rep = cal.weekly_refresh(conn, args.macro, lpr_months=args.months)
    s, l = rep["seed"], rep["lpr"]
    print(f"REFRESH seed: inserted={s['inserted']} skipped={s['skipped']} by_type={s['by_type']}")
    print(f"REFRESH lpr:  inserted={l['inserted']} skipped={l['skipped']} months={args.months}")
    if s.get("error"):
        print(f"  SEED ERROR: {s['error']}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="sqlite:///data/trading_corp.db",
                    help="target DB URL or path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show events")
    p.add_argument("--from", dest="frm", help="start date YYYY-MM-DD")
    p.add_argument("--to", dest="to", help="end date YYYY-MM-DD")
    p.add_argument("--type", help="filter event_type")
    p.add_argument("--source", help="filter source (seed|rule|manual|feed)")
    p.add_argument("--scope", help="filter symbol_scope")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("add", help="add a manual event")
    p.add_argument("--type", required=True, help="event_type e.g. OPEC")
    p.add_argument("--date", required=True, help="event date YYYY-MM-DD")
    p.add_argument("--scope", default=cal.SCOPE_ALL, help="symbol_scope (default ALL)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="delete matching events")
    p.add_argument("--type", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--scope", default=cal.SCOPE_ALL)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("seed", help="re-import FOMC/CPI/NFP from macro_calendar.yaml")
    p.add_argument("--macro", default="config/macro_calendar.yaml")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("generate-lpr", help="(re)generate LPR_FIX rule rows")
    p.add_argument("--months", type=int, default=13)
    p.set_defaults(func=cmd_generate_lpr)

    p = sub.add_parser("refresh", help="seed + generate-lpr (weekly refresh)")
    p.add_argument("--macro", default="config/macro_calendar.yaml")
    p.add_argument("--months", type=int, default=13)
    p.set_defaults(func=cmd_refresh)

    args = ap.parse_args()
    with _db.connect(args.db) as conn:
        return args.func(conn, args)


if __name__ == "__main__":
    raise SystemExit(main())
