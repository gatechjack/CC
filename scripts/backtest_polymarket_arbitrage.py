#!/usr/bin/env python3
"""Backtest polymarket_arbitrage by replaying paper would_have_placed rows.

Pulls audit_event rows for actor=polymarket_arbitrage over a horizon,
looks up each market's actual resolution via Polymarket's gamma-api,
computes binary-outcome P&L, aggregates metrics + per-category +
max consecutive-loss DD, prints a recommendation for Board sign-off.

Usage:
    python scripts/backtest_polymarket_arbitrage.py [--db URL] [--days 30] [--json]

Exit code 0 always — script outputs metrics for human review. Set
--json for machine-readable output (suitable for Board memo
attachment).

Phase 2.5 minimal-viable scope (Q4 of the Polymarket scope memo):
replay-only, binary settlement P&L, hit rate / category / DD
aggregations. NOT included (deferred to follow-ups):
  - Monte Carlo simulation (real fills are deterministic on Polymarket;
    add only if we ever model partial fills)
  - Slippage modeling (irrelevant at $1-USDC notional shakedown sizing)
  - Time-decay modeling (theta-equivalent for prediction markets is
    real but small for short-tail; add if/when long-tail markets are
    enabled)

Recommendation thresholds (heuristic — Board reads + decides):
  - n < 30 trades            → INSUFFICIENT_DATA
  - hit >= 55% AND avg > 0
    AND roi > 5%             → RECOMMEND_APPROVAL
  - hit < 45% OR avg < -$0.05 → RECOMMEND_REJECTION
  - else                     → MIXED_SIGNAL: continue paper-mode
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Default public RPC for the broker's snapshot connect-time call.
# Resolution lookups don't need authenticated RPC; this is read-only
# gamma-api against a public-info wallet address. Override with
# POLYGON_RPC_URL env var (e.g. set to your Alchemy URL) for higher
# rate limits if you're running the backtest frequently.
_DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"


def _fetch_paper_rows(db_url: str, *, days: int) -> list[dict]:
    """Pull would_have_placed audit rows for polymarket_arbitrage in horizon."""
    db_path = db_url.replace("sqlite:///", "")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows: list[dict] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT ts, payload_json FROM audit_event "
            "WHERE actor='polymarket_arbitrage' "
            "AND kind='would_have_placed' "
            "AND ts >= ? "
            "ORDER BY ts ASC",
            (cutoff,),
        )
        for r in cur.fetchall():
            try:
                p = json.loads(r["payload_json"])
                p["_ts"] = r["ts"]
                rows.append(p)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
    return rows


async def _lookup_resolutions(rows: list[dict]) -> list[tuple[dict, dict]]:
    """For each row, fetch the market's resolution status via gamma-api."""
    # Lazy import so the script is runnable from any directory that has
    # PYTHONPATH including the repo root.
    from trading_corp.brokers.polymarket import PolymarketBroker

    broker = PolymarketBroker(
        funder_address="0x0000000000000000000000000000000000000001",  # public-info; needed only for connect()
        polygon_rpc_url=os.getenv("POLYGON_RPC_URL") or _DEFAULT_RPC,
    )
    await broker.connect()
    pairs: list[tuple[dict, dict]] = []
    try:
        for row in rows:
            cid = row.get("condition_id")
            symbol = row.get("symbol", "")
            slug = row.get("market_slug") or symbol.split(":")[0] if symbol else None
            res = await broker.get_market_resolution(condition_id=cid, slug=slug)
            pairs.append((row, res))
    finally:
        await broker.disconnect()
    return pairs


def _compute_pnl(row: dict, res: dict) -> dict | None:
    """Binary-outcome P&L per trade.

    Bought outcome shares at price X (qty Y, paid X*Y in USDC):
      - share resolves to $1 → P&L = Y - X*Y = Y*(1-X)
      - share resolves to $0 → P&L = -X*Y

    Returns None for non-resolved or malformed rows.
    """
    if res.get("status") != "resolved":
        return None
    yes_won = bool(res.get("yes_won"))
    outcome = (row.get("outcome") or "yes").lower()
    qty = float(row.get("qty") or 0.0)
    price = float(row.get("limit_price") or 0.0)
    if qty <= 0 or price <= 0 or price >= 1.0:
        return None
    notional = qty * price

    if outcome == "yes":
        won = yes_won
    elif outcome == "no":
        won = not yes_won
    else:
        return None

    pnl = qty * (1.0 - price) if won else -qty * price
    return {
        "ts": row["_ts"],
        "slug": row.get("market_slug") or row.get("symbol", "?"),
        "outcome_bet": outcome,
        "category": row.get("category") or "other",
        "series": row.get("series") or "",
        "qty": qty,
        "entry_price": price,
        "notional": notional,
        "won": won,
        "pnl": pnl,
        "implied_at_entry": row.get("implied_prob_at_entry"),
        "llm_prob": row.get("llm_prob_estimate"),
        "divergence_pct": row.get("divergence_pct"),
        "yes_won_actual": yes_won,
    }


def _aggregate(realized: list[dict]) -> dict:
    if not realized:
        return {"n_trades": 0, "n_wins": 0, "n_losses": 0,
                "hit_rate": 0.0, "total_notional": 0.0, "total_pnl": 0.0,
                "roi_pct": 0.0, "avg_pnl_per_trade": 0.0,
                "median_pnl_per_trade": 0.0, "max_drawdown": 0.0,
                "by_category": {}}

    n = len(realized)
    total_pnl = sum(r["pnl"] for r in realized)
    total_notional = sum(r["notional"] for r in realized)
    wins = sum(1 for r in realized if r["won"])
    pnls_sorted = sorted(r["pnl"] for r in realized)
    median_pnl = pnls_sorted[n // 2]

    # Per-category
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in realized:
        by_cat[r["category"]].append(r)
    cat_metrics: dict[str, dict] = {}
    for cat, rs in by_cat.items():
        cat_metrics[cat] = {
            "n": len(rs),
            "hit_rate": sum(1 for r in rs if r["won"]) / len(rs),
            "total_pnl": sum(r["pnl"] for r in rs),
            "avg_pnl": sum(r["pnl"] for r in rs) / len(rs),
        }

    # Max drawdown: walk trades chronologically, peak-to-trough on
    # cumulative P&L. Reflects worst loss-streak depth from a high.
    sorted_realized = sorted(realized, key=lambda r: r["ts"])
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in sorted_realized:
        cum += r["pnl"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return {
        "n_trades": n,
        "n_wins": wins,
        "n_losses": n - wins,
        "hit_rate": wins / n,
        "total_notional": total_notional,
        "total_pnl": total_pnl,
        "roi_pct": 100 * total_pnl / total_notional if total_notional > 0 else 0.0,
        "avg_pnl_per_trade": total_pnl / n,
        "median_pnl_per_trade": median_pnl,
        "max_drawdown": max_dd,
        "by_category": cat_metrics,
    }


def _make_recommendation(metrics: dict) -> str:
    n = metrics.get("n_trades", 0)
    if n < 30:
        return f"INSUFFICIENT_DATA: only {n} resolved paper trades; need 30+ for meaningful inference."
    hit = metrics["hit_rate"]
    avg = metrics["avg_pnl_per_trade"]
    roi = metrics["roi_pct"]
    if hit >= 0.55 and avg > 0 and roi > 5:
        return (f"RECOMMEND_APPROVAL: hit={hit:.1%}, avg_pnl=${avg:+.3f}, "
                f"roi={roi:+.1f}%, n={n}.")
    if hit < 0.45 or avg < -0.05:
        return (f"RECOMMEND_REJECTION: hit={hit:.1%}, avg_pnl=${avg:+.3f}, "
                f"roi={roi:+.1f}%, n={n}. Strategy is losing money.")
    return (f"MIXED_SIGNAL: hit={hit:.1%}, avg_pnl=${avg:+.3f}, "
            f"roi={roi:+.1f}%, n={n}. Continue paper-mode; re-check later.")


async def _run(args) -> int:
    rows = _fetch_paper_rows(args.db, days=args.days)
    if not rows:
        out = {"n_paper_rows": 0, "verdict": "NO_DATA",
               "reason": "no would_have_placed rows for polymarket_arbitrage "
                         f"in last {args.days} days; strategy needs paper-mode runtime first"}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(out["reason"])
        return 0

    print(f"Found {len(rows)} paper rows; looking up resolutions via gamma-api…",
          file=sys.stderr)
    pairs = await _lookup_resolutions(rows)

    realized: list[dict] = []
    counts = {"resolved": 0, "pending": 0, "void": 0, "not_found": 0}
    for row, res in pairs:
        st = res.get("status", "not_found")
        counts[st] = counts.get(st, 0) + 1
        if st == "resolved":
            pnl = _compute_pnl(row, res)
            if pnl is not None:
                realized.append(pnl)

    metrics = _aggregate(realized)
    metrics["paper_rows_total"] = len(rows)
    metrics.update({f"n_{k}": v for k, v in counts.items()})
    metrics["verdict"] = _make_recommendation(metrics)

    if args.json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        print(f"\n=== Polymarket Arbitrage Backtest — {args.days}-day horizon ===")
        print(f"Paper rows total:     {len(rows)}")
        print(f"  Resolved:           {counts['resolved']}")
        print(f"  Pending:            {counts['pending']}")
        print(f"  Not found:          {counts.get('not_found', 0)}")
        print(f"  Void:               {counts['void']}")
        if metrics["n_trades"]:
            print()
            print(f"Resolved-trade metrics ({metrics['n_trades']} trades):")
            print(f"  Hit rate:           {metrics['hit_rate']:.1%}  "
                  f"({metrics['n_wins']}W / {metrics['n_losses']}L)")
            print(f"  Total notional:     ${metrics['total_notional']:.2f}")
            print(f"  Total P&L:          ${metrics['total_pnl']:+.2f}")
            print(f"  ROI:                {metrics['roi_pct']:+.1f}%")
            print(f"  Avg P&L per trade:  ${metrics['avg_pnl_per_trade']:+.3f}")
            print(f"  Median P&L:         ${metrics['median_pnl_per_trade']:+.3f}")
            print(f"  Max drawdown:       ${metrics['max_drawdown']:.2f}")
            print(f"\n  By category:")
            for cat, c in sorted(metrics["by_category"].items(),
                                 key=lambda x: -x[1]["n"]):
                print(f"    {cat:14s} n={c['n']:3d}  "
                      f"hit={c['hit_rate']:.1%}  "
                      f"total_pnl=${c['total_pnl']:+.2f}  "
                      f"avg=${c['avg_pnl']:+.3f}")
        print(f"\n=== Verdict ===")
        print(metrics["verdict"])
    return 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--db", default=os.getenv("TRADING_CORP_DB_URL") or "sqlite:///data/trading_corp.db",
                   help="SQLite URL or path; default reads env TRADING_CORP_DB_URL or local data/trading_corp.db")
    p.add_argument("--days", type=int, default=30,
                   help="Replay horizon in days (default: 30)")
    p.add_argument("--json", action="store_true",
                   help="Machine-readable JSON output instead of human")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
