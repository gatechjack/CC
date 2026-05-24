#!/usr/bin/env python3
"""Retro-compute B-leadlag EV-at-fill on the existing kalshi_sports_scout
corpus, reversing the 100× units bug at scout.py:232-240.

Per [[project-kalshi-sports-scout-phase0-blocked]]: the 461-row scout
corpus has stored `kalshi_implied_yes` 100× smaller than reality
(scout divides by 100 assuming cents, but quotes are already in
dollars). Bug is deterministically reversible: true_implied = stored × 100.

This script:
  1. Walks audit_event rows kind='kalshi_sports_observed'.
  2. Reverses the 100× bug; guards reversed value in (0,1) or tags
     unrecoverable_post_correction.
  3. Computes Hypothesis B EV-at-fill (kalshi ask vs bookmaker_yes_implied
     soft-book-proxy model_prob) at $10 and $25 sizings using
     trading_corp.agents.strategies._sports_math.
  4. Aggregates by league + overall. Outputs human-readable (default) or
     JSON (--json) for the retro_assessment report.

Hypothesis A is NOT computable from this corpus — scout stores median
bookmaker_yes_implied only, no per-book breakdown for an opposing-leg
arb partner. The fresh observer (Step 4 of the Phase 0 plan) addresses
that gap.

Read-only. Does NOT modify any row. Does NOT modify the scout.

Usage:
  python scripts/retro_compute_ev_at_fill_scout.py --db data/trading_corp.db
  python scripts/retro_compute_ev_at_fill_scout.py --db data/trading_corp.db --json
  python scripts/retro_compute_ev_at_fill_scout.py --db data/trading_corp.db --league NBA
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# Repo-root on sys.path so trading_corp.* imports work from scripts/.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from trading_corp.agents.strategies._sports_math import (  # noqa: E402
    LegFill,
    compute_ev_at_fill_b_directional,
    kalshi_fee,
)


def _reverse_bug(stored_kalshi_implied_yes: float) -> tuple[float | None, str | None]:
    """Returns (true_implied_yes, error_tag). Both None means recoverable."""
    if stored_kalshi_implied_yes is None:
        return None, "missing_kalshi_implied_yes"
    true_val = stored_kalshi_implied_yes * 100.0
    if not (0.0 < true_val < 1.0):
        return None, "unrecoverable_post_correction"
    return true_val, None


def _ev_at_sizing(true_yes_ask: float, model_prob_yes: float, qty: int) -> float:
    """Compute B EV-at-fill for one (qty, ask, model_prob) combo, in dollars."""
    fee = kalshi_fee(qty, true_yes_ask)
    leg = LegFill("kalshi", "yes", qty=qty, price_per_unit=true_yes_ask, fee=fee)
    return compute_ev_at_fill_b_directional(leg, model_prob_outcome=model_prob_yes).ev_dollars


def retro_analyze(db_path: str, league_filter: str | None = None) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind='kalshi_sports_observed' "
        "ORDER BY ts ASC"
    )
    rows = conn.execute(sql).fetchall()
    conn.close()

    # Per-league counters and EV aggregates.
    per_league: dict[str, dict] = defaultdict(
        lambda: {
            "n_total": 0,
            "n_recoverable": 0,
            "errors": defaultdict(int),
            "ev_dollars_10": [],
            "ev_dollars_25": [],
            "would_fire_b_at_10": 0,    # positive-EV count @ $10
            "would_fire_b_at_25": 0,
            "buggy_would_fire_buy_yes": 0,   # scout's own (corrupted) flag
            "kalshi_ask_distribution": [],
        }
    )

    for row in rows:
        payload = json.loads(row["payload_json"])
        league = payload.get("league", "UNKNOWN")
        if league_filter and league != league_filter:
            continue
        per_league[league]["n_total"] += 1

        if payload.get("would_fire_buy") == "yes":
            per_league[league]["buggy_would_fire_buy_yes"] += 1

        stored_k_implied = payload.get("kalshi_implied_yes")
        true_k_implied, err = _reverse_bug(stored_k_implied)
        if err:
            per_league[league]["errors"][err] += 1
            continue

        bookmaker_implied = payload.get("bookmaker_yes_implied")
        if bookmaker_implied is None or not (0.0 < bookmaker_implied < 1.0):
            per_league[league]["errors"]["bookmaker_yes_implied_invalid"] += 1
            continue

        # `true_k_implied` is the ASK side per scout source (yes_ask/100→×100).
        # For B-leadlag we'd pay the ask to take YES exposure.
        ev_10 = _ev_at_sizing(true_k_implied, bookmaker_implied, 10)
        ev_25 = _ev_at_sizing(true_k_implied, bookmaker_implied, 25)
        per_league[league]["n_recoverable"] += 1
        per_league[league]["ev_dollars_10"].append(ev_10)
        per_league[league]["ev_dollars_25"].append(ev_25)
        per_league[league]["kalshi_ask_distribution"].append(true_k_implied)
        if ev_10 > 0:
            per_league[league]["would_fire_b_at_10"] += 1
        if ev_25 > 0:
            per_league[league]["would_fire_b_at_25"] += 1

    # Summarize.
    summary = {"per_league": {}, "overall": {}}
    overall_ev_10: list[float] = []
    overall_ev_25: list[float] = []
    overall_n_total = 0
    overall_n_recoverable = 0
    overall_fire_10 = 0
    overall_fire_25 = 0
    overall_buggy_fire = 0
    overall_errors: dict[str, int] = defaultdict(int)
    for league, agg in sorted(per_league.items()):
        ev10 = agg["ev_dollars_10"]
        ev25 = agg["ev_dollars_25"]
        summary["per_league"][league] = {
            "n_total": agg["n_total"],
            "n_recoverable": agg["n_recoverable"],
            "errors": dict(agg["errors"]),
            "buggy_would_fire_buy_yes": agg["buggy_would_fire_buy_yes"],
            "mean_ev_dollars_at_10": round(sum(ev10) / len(ev10), 4) if ev10 else None,
            "mean_ev_dollars_at_25": round(sum(ev25) / len(ev25), 4) if ev25 else None,
            "median_ev_dollars_at_10": round(sorted(ev10)[len(ev10) // 2], 4) if ev10 else None,
            "median_ev_dollars_at_25": round(sorted(ev25)[len(ev25) // 2], 4) if ev25 else None,
            "max_ev_dollars_at_10": round(max(ev10), 4) if ev10 else None,
            "max_ev_dollars_at_25": round(max(ev25), 4) if ev25 else None,
            "n_positive_ev_at_10": agg["would_fire_b_at_10"],
            "n_positive_ev_at_25": agg["would_fire_b_at_25"],
            "frac_positive_ev_at_10": (
                round(agg["would_fire_b_at_10"] / agg["n_recoverable"], 4)
                if agg["n_recoverable"] else None
            ),
            "frac_positive_ev_at_25": (
                round(agg["would_fire_b_at_25"] / agg["n_recoverable"], 4)
                if agg["n_recoverable"] else None
            ),
        }
        overall_ev_10.extend(ev10)
        overall_ev_25.extend(ev25)
        overall_n_total += agg["n_total"]
        overall_n_recoverable += agg["n_recoverable"]
        overall_fire_10 += agg["would_fire_b_at_10"]
        overall_fire_25 += agg["would_fire_b_at_25"]
        overall_buggy_fire += agg["buggy_would_fire_buy_yes"]
        for k, v in agg["errors"].items():
            overall_errors[k] += v

    summary["overall"] = {
        "n_total": overall_n_total,
        "n_recoverable": overall_n_recoverable,
        "errors": dict(overall_errors),
        "buggy_would_fire_buy_yes": overall_buggy_fire,
        "mean_ev_dollars_at_10": round(sum(overall_ev_10) / len(overall_ev_10), 4) if overall_ev_10 else None,
        "mean_ev_dollars_at_25": round(sum(overall_ev_25) / len(overall_ev_25), 4) if overall_ev_25 else None,
        "median_ev_dollars_at_10": round(sorted(overall_ev_10)[len(overall_ev_10) // 2], 4) if overall_ev_10 else None,
        "median_ev_dollars_at_25": round(sorted(overall_ev_25)[len(overall_ev_25) // 2], 4) if overall_ev_25 else None,
        "max_ev_dollars_at_10": round(max(overall_ev_10), 4) if overall_ev_10 else None,
        "max_ev_dollars_at_25": round(max(overall_ev_25), 4) if overall_ev_25 else None,
        "n_positive_ev_at_10": overall_fire_10,
        "n_positive_ev_at_25": overall_fire_25,
        "frac_positive_ev_at_10": (
            round(overall_fire_10 / overall_n_recoverable, 4)
            if overall_n_recoverable else None
        ),
        "frac_positive_ev_at_25": (
            round(overall_fire_25 / overall_n_recoverable, 4)
            if overall_n_recoverable else None
        ),
    }
    return summary


def _print_human(summary: dict) -> None:
    print("=" * 78)
    print("KALSHI SPORTS SCOUT — RETRO B-LEADLAG EV-AT-FILL ANALYSIS")
    print("=" * 78)
    print("Soft-book proxy: bookmaker_yes_implied (median vig-removed across books).")
    print("Bug reversal: true_kalshi_implied_yes = stored × 100 (scout.py:232-240).")
    print("Sizings reported: $10 and $25 contracts (Kalshi fee applied per leg).")
    print()
    print(f"{'LEAGUE':8} {'N':>5} {'REC':>5} {'BUGFIRE':>8} {'POS10':>6} {'POS25':>6} "
          f"{'MEAN10':>9} {'MEAN25':>9} {'MAX10':>8} {'MAX25':>8}")
    print("-" * 78)
    for league, agg in summary["per_league"].items():
        print(
            f"{league:8} {agg['n_total']:>5} {agg['n_recoverable']:>5} "
            f"{agg['buggy_would_fire_buy_yes']:>8} "
            f"{agg['n_positive_ev_at_10']:>6} {agg['n_positive_ev_at_25']:>6} "
            f"{agg['mean_ev_dollars_at_10'] or 0:>9.4f} "
            f"{agg['mean_ev_dollars_at_25'] or 0:>9.4f} "
            f"{agg['max_ev_dollars_at_10'] or 0:>8.4f} "
            f"{agg['max_ev_dollars_at_25'] or 0:>8.4f}"
        )
    print("-" * 78)
    o = summary["overall"]
    print(
        f"{'OVERALL':8} {o['n_total']:>5} {o['n_recoverable']:>5} "
        f"{o['buggy_would_fire_buy_yes']:>8} "
        f"{o['n_positive_ev_at_10']:>6} {o['n_positive_ev_at_25']:>6} "
        f"{o['mean_ev_dollars_at_10'] or 0:>9.4f} "
        f"{o['mean_ev_dollars_at_25'] or 0:>9.4f} "
        f"{o['max_ev_dollars_at_10'] or 0:>8.4f} "
        f"{o['max_ev_dollars_at_25'] or 0:>8.4f}"
    )
    print()
    print("Errors / dropped rows by reason:")
    for k, v in o["errors"].items():
        print(f"  {k}: {v}")
    print()
    print("Caveats (per Phase-0 plan Verdict design):")
    print("  - SOFT-BOOK PROXY: bookmaker_yes_implied is median across DK/FD/BetMGM-class")
    print("    books, NOT Pinnacle/sharp. A null B result here is not a true sharp-book test.")
    print("  - HOUR-SCALE: scout polls every 1h. Sub-hour edges invisible.")
    print("  - H2H ONLY: scout has no spreads/totals coverage.")
    print("  - A-ARB UNCOMPUTABLE: scout stores median only; per-book opposing-leg")
    print("    pricing required for cross-venue arb is unavailable.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, help="Path to SQLite DB containing audit_event")
    p.add_argument("--league", default=None, help="Filter to a single league (e.g. NBA)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable table")
    args = p.parse_args()

    summary = retro_analyze(args.db, league_filter=args.league)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
