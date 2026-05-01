#!/usr/bin/env python3
"""Manually refresh `data/research_starter_universes/large_mid_cap.json`.

Per planning/research_firm_design.md §9.Q1: starter universe is the
S&P 500 + Nasdaq 100 union, deduped, refreshed manually quarterly.

Usage:
    python scripts/refresh_research_starter_universe.py
    python scripts/refresh_research_starter_universe.py --dry-run    # preview only

This is intentionally NOT a cron — Q1 explicitly chose calendar-driven
manual refresh (the file's `_meta.next_refresh_due` is the trigger). If a
refresh is missed, the universe is stale but not broken — analysts
simply won't screen names that joined an index after the as-of date.

Phase 1b+: this script becomes the input to a more elaborate
config_writer-style update. For now it just pulls Wikipedia and writes
the JSON.

Sources:
    https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
    https://en.wikipedia.org/wiki/Nasdaq-100
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT_PATH = Path("data/research_starter_universes/large_mid_cap.json")

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def _extract_first_table_tickers(html: str) -> list[str]:
    """Find the first wikitable with id='constituents' and pull symbols
    from each row's leading <a> link or first cell."""
    idx = html.find('id="constituents"')
    if idx < 0:
        return []
    end = html.find("</table>", idx)
    if end < 0:
        return []
    table = html[idx:end]
    # Each ticker is the first cell of a row, usually wrapped in <a>.
    syms = re.findall(
        r"<tr>\s*<t[hd][^>]*>\s*<a[^>]*>([A-Z][A-Z\.\-]{0,5})</a>",
        table,
    )
    if not syms:
        # Fallback: plain text first cell.
        syms = re.findall(
            r"<tr>\s*<t[hd][^>]*>\s*([A-Z][A-Z\.\-]{0,5})\s*</t[hd]>",
            table,
        )
    return syms


def fetch_universes() -> tuple[list[str], list[str]]:
    """Return (sp500_symbols, ndx_symbols). Raises on fetch failure."""
    sp = _extract_first_table_tickers(_fetch(SP500_URL))
    if len(sp) < 400:
        raise RuntimeError(
            f"S&P 500 fetch returned only {len(sp)} symbols — Wikipedia "
            f"layout may have changed; review the parser."
        )
    ndx = _extract_first_table_tickers(_fetch(NDX_URL))
    if len(ndx) < 80:
        raise RuntimeError(
            f"Nasdaq-100 fetch returned only {len(ndx)} symbols — Wikipedia "
            f"layout may have changed; review the parser."
        )
    return sp, ndx


def build_payload(sp: list[str], ndx: list[str]) -> dict:
    today = datetime.now(timezone.utc).date()
    next_refresh = today + timedelta(days=90)
    combined = sorted(set(sp) | set(ndx))
    return {
        "_meta": {
            "source": "S&P 500 + Nasdaq 100 union, deduped (Wikipedia)",
            "as_of_date": today.isoformat(),
            "sp500_count": len(sp),
            "nasdaq100_count": len(ndx),
            "combined_count": len(combined),
            "refresh_cadence": "manual quarterly",
            "next_refresh_due": next_refresh.isoformat(),
            "refresh_procedure": "see scripts/refresh_research_starter_universe.py",
        },
        "symbols": combined,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print summary; do not write the file.",
    )
    parser.add_argument(
        "--out", default=str(OUT_PATH),
        help=f"Output path (default: {OUT_PATH})",
    )
    args = parser.parse_args()

    out = Path(args.out)

    print(f"Fetching S&P 500 from {SP500_URL} ...")
    sp, ndx = fetch_universes()
    print(f"  S&P 500: {len(sp)} symbols")
    print(f"  Nasdaq-100: {len(ndx)} symbols")
    payload = build_payload(sp, ndx)
    n = payload["_meta"]["combined_count"]
    print(f"  Combined deduped: {n} symbols")

    if args.dry_run:
        print(f"(dry-run) would write {n} symbols to {out}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    # Compare against current to surface drift in diffs cleanly.
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            old_syms = set(old.get("symbols", []))
            new_syms = set(payload["symbols"])
            added = sorted(new_syms - old_syms)
            removed = sorted(old_syms - new_syms)
            print(f"  Drift since last refresh: +{len(added)} / -{len(removed)}")
            if added:
                print(f"    Added (first 10): {added[:10]}")
            if removed:
                print(f"    Removed (first 10): {removed[:10]}")
        except Exception as e:
            print(f"  (could not diff against existing: {e})")

    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
