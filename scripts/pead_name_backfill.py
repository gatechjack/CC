#!/usr/bin/env python3
"""One-shot backfill: write `company_name` (EODHD General::Name) into the extra_json
of OPEN robinhood_pead positions that were entered BEFORE the company-name feature
shipped (2026-08-02), so the dashboard renders company names on them instead of bare
tickers.

DISPLAY-ONLY + SURGICAL. Touches `extra_json["company_name"]` and NOTHING else — every
other key (entry_reference_price, stop_price, the 6 locked primitives, notional, sue,
qty, etc.) is preserved value-for-value. Never touches qty/entry/stop/result or any
trading field, and never touches CLOSED rows. Idempotent (re-running is a no-op).

OFF the scan hot path and OFF the asyncio event loop: this is a standalone synchronous
one-shot. `get_company_facts` uses the shared 24h fundamentals cache — one read per
symbol here, once (8 names). Requires EODHD_API_KEY in env (same as the engine); on
prod, run it with the engine's EnvironmentFile sourced.

Usage:
  python scripts/pead_name_backfill.py            # DRY-RUN: print before/after, write nothing
  python scripts/pead_name_backfill.py --apply    # write company_name into the rows
"""
from __future__ import annotations

import argparse
import json

from trading_corp.data.earnings_provider import EarningsProvider
from trading_corp.persistence import db

DIVISION = "robinhood_pead"
_DEFAULT_DB = "sqlite:///data/trading_corp.db"


def add_company_name(extra: dict, name: str) -> tuple[dict, bool]:
    """Return (new_extra, changed). Pure: adds/updates ONLY `company_name`, preserving
    every other key and value exactly. `changed` is False when already equal."""
    if not name or extra.get("company_name") == name:
        return dict(extra), False
    new_extra = dict(extra)                 # preserves key order + all values
    new_extra["company_name"] = name
    return new_extra, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is dry-run)")
    ap.add_argument("--db", default=_DEFAULT_DB)
    args = ap.parse_args()

    provider = EarningsProvider(db_url=args.db)
    with db.connect(args.db) as conn:
        rows = conn.execute(
            "SELECT order_id, symbol, extra_json FROM paper_trade_record "
            "WHERE division = ? AND result IS NULL ORDER BY symbol",
            (DIVISION,),
        ).fetchall()

    written = 0
    for r in rows:
        sym = r["symbol"]
        try:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
        except (ValueError, TypeError):
            extra = {}
        facts = provider.get_company_facts(sym) or {}     # 24h-cached; one read/symbol
        name = facts.get("name")
        if not name:
            print(f"  {sym:6s}  NO NAME from facts -> row unchanged")
            continue
        new_extra, changed = add_company_name(extra, name)
        if not changed:
            print(f"  {sym:6s}  already {name!r} -> no change")
            continue
        preserved = sorted(k for k in extra)              # every pre-existing key survives
        print(f"  {sym:6s}  + company_name = {name!r}   (unchanged keys: {preserved})")
        if args.apply:
            with db.connect(args.db) as conn:
                conn.execute(
                    "UPDATE paper_trade_record SET extra_json = ? "
                    "WHERE order_id = ? AND division = ? AND result IS NULL",
                    (json.dumps(new_extra), r["order_id"], DIVISION),
                )
            written += 1

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"{mode}: {written} row(s) written; {len(rows)} open rows scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
