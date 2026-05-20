#!/usr/bin/env python3
"""Backtest kalshi_structure_arb — feasibility check + simulation.

Strategy under test (deterministic, no LLM):
  For each Kalshi event_ticker with K sub-markets, compute
  sum_yes_implied = sum(implied_yes_i for all i).
  Fire when:
    - sum_yes_implied > threshold (default 1.5)
    - K >= 3
    - Not a skip category (Crypto, Climate and Weather)
    - Not a price-bucket market (ticker contains B/T suffix patterns)
    - No ASK quote missing on the NO side

  When firing: emit NO orders against top-M=3 most-overpriced sub-markets
  (highest implied_yes).

  PnL per NO bet at no_ask=q, implied_yes=p (q ≈ 1-p):
    win: (1/q - 1) = p/(1-p) [YES resolved NO]
    loss: -1.0              [YES resolved YES]

Data sources:
  - audit_event (kind='kalshi_llm_probability_called') — primary source of
    per-sub-market implied_yes at LLM scan time.
  - kalshi_round_trips — resolution lookup (strategy='kalshi_llm_arbitrage').

Usage:
    python scripts/backtest_kalshi_structure_arb.py [--db PATH] [--days 60]
    python scripts/backtest_kalshi_structure_arb.py [--json] [--out FILE]

Exit code 0 always — script outputs metrics for human review.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DEFAULT_DB = "data/trading_corp.db"
_DEFAULT_DAYS = 60
_DEFAULT_M = 3          # top-M sub-markets to bet NO on
_DEFAULT_THRESHOLD = 1.5


# ---------------------------------------------------------------------------
# Skip-rule helpers
# ---------------------------------------------------------------------------

_SKIP_CATEGORIES = {"Crypto", "Climate and Weather"}

# Ticker suffix patterns that indicate price-bucket markets handled by other
# strategies (kalshi_tail_price_arb, kalshi_temporal_bucket_arb).
# Matches Kalshi suffix conventions: -B<digits> or -T<digits> appended to
# base ticker before any date suffix.
import re
_PRICE_BUCKET_RE = re.compile(r'-(?:B|T)-?\d')

def _is_price_bucket(ticker: str) -> bool:
    """True if ticker looks like a price-bucket market (handled by other arbs)."""
    return bool(_PRICE_BUCKET_RE.search(ticker))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _feasibility_check(conn: sqlite3.Connection, days: int) -> dict:
    """Return a dict of feasibility metrics. Also returns the raw rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # 1. Check kalshi_llm_probability_called audit rows
    llm_rows = conn.execute(
        """
        SELECT ts, payload_json
          FROM audit_event
         WHERE kind = 'kalshi_llm_probability_called'
           AND ts >= ?
         ORDER BY ts ASC
        """,
        (cutoff,),
    ).fetchall()

    # 2. Check any kalshi audit events at all
    kalshi_audit_counts = {}
    for r in conn.execute(
        """
        SELECT kind, COUNT(*) AS cnt
          FROM audit_event
         WHERE kind LIKE 'kalshi%'
           AND ts >= ?
         GROUP BY kind ORDER BY cnt DESC
        """,
        (cutoff,),
    ).fetchall():
        kalshi_audit_counts[r["kind"]] = r["cnt"]

    # 3. Check kalshi_round_trips table existence + row count
    round_trips_available = False
    round_trips_count = 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM kalshi_round_trips WHERE strategy='kalshi_llm_arbitrage'"
        ).fetchone()
        round_trips_count = row[0]
        round_trips_available = True
    except sqlite3.OperationalError:
        pass  # table doesn't exist

    # 4. Parse llm_probability_called rows
    sub_market_records: list[dict] = []
    parse_errors = 0
    for r in llm_rows:
        try:
            p = json.loads(r["payload_json"])
            p["_ts"] = r["ts"]
            sub_market_records.append(p)
        except Exception:
            parse_errors += 1

    # 5. Distinct event_tickers with K >= 3 sub-markets
    events_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for rec in sub_market_records:
        et = rec.get("event_ticker") or ""
        if et:
            events_by_ticker[et].append(rec)

    events_k3 = {et: v for et, v in events_by_ticker.items() if len(v) >= 3}

    return {
        "llm_audit_rows_total": len(llm_rows),
        "parse_errors": parse_errors,
        "sub_market_records": sub_market_records,
        "distinct_event_tickers": len(events_by_ticker),
        "events_k3_plus": len(events_k3),
        "events_by_ticker": events_by_ticker,
        "events_k3": events_k3,
        "kalshi_audit_counts": kalshi_audit_counts,
        "round_trips_available": round_trips_available,
        "round_trips_count": round_trips_count,
        "days": days,
        "cutoff": cutoff,
    }


def _lookup_resolution(conn: sqlite3.Connection, ticker: str) -> str | None:
    """Return 'yes'/'no'/'void' resolution from kalshi_round_trips, or None."""
    row = conn.execute(
        """
        SELECT market_result
          FROM kalshi_round_trips
         WHERE ticker = ?
           AND strategy = 'kalshi_llm_arbitrage'
         ORDER BY resolved_ts DESC LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    return row["market_result"] if row else None


def _compute_pnl(implied_yes: float, no_ask: float | None, resolution: str) -> float:
    """Compute P&L for a $1 NO bet.

    If no_ask is provided, use it; else fall back to (1 - implied_yes).
    win: market resolved NO → payout = 1/no_ask, net = 1/no_ask - 1
    loss: market resolved YES → net = -1.0
    void: net = 0.0
    """
    q = no_ask if (no_ask is not None and 0 < no_ask < 1) else (1.0 - implied_yes)
    q = max(0.01, min(0.99, q))  # safety clamp
    if resolution == "no":
        return (1.0 / q) - 1.0
    elif resolution == "yes":
        return -1.0
    else:  # void
        return 0.0


def _run_backtest(
    feas: dict,
    conn: sqlite3.Connection,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    m: int = _DEFAULT_M,
) -> dict:
    """Simulate structure_arb over the audit data collected by kalshi_llm_arbitrage.

    Fire-time policy: use the LATEST known set of sub-market observations per
    event_ticker as the fire-time snapshot. This is conservative (maximizes K
    and sum_yes_implied coverage) while avoiding cross-event look-ahead. Within
    a single event, all sub-market observations are from the LLM scan cycle —
    they are naturally clustered in time. Using the latest snapshot per event
    minimizes the risk of using a stale sum that would not have triggered.

    Caveat: because kalshi_llm_probability_called only fires for sub-markets
    in prob_lo..prob_hi (0.05-0.95), extreme-tail sub-markets are invisible to
    this backtest. Sum_yes_implied is under-counted for events where extreme-tail
    sub-markets exist.
    """
    events_by_ticker = feas["events_by_ticker"]
    round_trips_available = feas["round_trips_available"]

    bets: list[dict] = []
    events_evaluated = 0
    events_qualifying = 0
    events_skipped_category = 0
    events_skipped_k = 0
    events_skipped_threshold = 0

    for event_ticker, records in sorted(events_by_ticker.items()):
        events_evaluated += 1

        # Determine category (use most common across records)
        categories = [r.get("category") or "" for r in records]
        category = max(set(categories), key=categories.count) if categories else ""

        # Skip category
        if category in _SKIP_CATEGORIES:
            events_skipped_category += 1
            continue

        # Use latest observation per sub-market ticker (to get best implied at "fire time")
        latest_per_ticker: dict[str, dict] = {}
        for rec in records:
            t = rec.get("ticker") or ""
            if not t:
                continue
            if t not in latest_per_ticker or rec["_ts"] > latest_per_ticker[t]["_ts"]:
                latest_per_ticker[t] = rec

        sub_markets = list(latest_per_ticker.values())

        # Filter price-bucket tickers
        sub_markets = [m_ for m_ in sub_markets if not _is_price_bucket(m_.get("ticker") or "")]

        k = len(sub_markets)
        if k < 3:
            events_skipped_k += 1
            continue

        # Compute sum_yes_implied
        sum_yes = sum(float(r.get("implied_prob_yes") or 0) for r in sub_markets)

        if sum_yes <= threshold:
            events_skipped_threshold += 1
            continue

        events_qualifying += 1

        # Select top-M by implied_yes
        sub_markets_sorted = sorted(
            sub_markets,
            key=lambda r: float(r.get("implied_prob_yes") or 0),
            reverse=True,
        )
        top_m = sub_markets_sorted[:m]

        for sub in top_m:
            ticker = sub.get("ticker") or ""
            implied_yes = float(sub.get("implied_prob_yes") or 0.5)
            no_ask = None  # kalshi_llm_probability_called doesn't surface no_ask in payload

            # Resolution lookup
            resolution = None
            if round_trips_available:
                resolution = _lookup_resolution(conn, ticker)

            bets.append({
                "event_ticker": event_ticker,
                "ticker": ticker,
                "category": category,
                "implied_yes": implied_yes,
                "no_ask": no_ask,
                "resolution": resolution,
                "sum_yes_at_fire": sum_yes,
                "k_at_fire": k,
                "fire_ts": max(r["_ts"] for r in sub_markets),
            })

    # ── P&L computation ────────────────────────────────────────────────────
    n_bets = len(bets)
    n_wins = 0
    n_losses = 0
    n_unresolved = 0
    n_voids = 0
    gross_pnl = 0.0
    notional = 0.0

    for bet in bets:
        res = bet["resolution"]
        if res is None:
            n_unresolved += 1
            bet["pnl"] = None
            continue
        if res == "void":
            n_voids += 1
            bet["pnl"] = 0.0
            continue
        pnl = _compute_pnl(bet["implied_yes"], bet["no_ask"], res)
        bet["pnl"] = pnl
        gross_pnl += pnl
        notional += 1.0  # $1 per bet
        if res == "no":  # NO bet wins when market resolves NO
            n_wins += 1
        else:
            n_losses += 1

    n_resolved = n_wins + n_losses + n_voids
    win_rate = (n_wins / (n_wins + n_losses)) if (n_wins + n_losses) > 0 else None
    roi_pct = (gross_pnl / notional * 100) if notional > 0 else None

    # ── Normalized threshold variants (Q1) ─────────────────────────────────
    normalized_variants: dict[str, dict] = {}
    for norm_thresh in [0.4, 0.5, 0.6]:
        v_qualifying = 0
        v_bets: list[dict] = []
        for event_ticker, records in sorted(events_by_ticker.items()):
            categories = [r.get("category") or "" for r in records]
            category = max(set(categories), key=categories.count) if categories else ""
            if category in _SKIP_CATEGORIES:
                continue
            latest_per_ticker: dict[str, dict] = {}
            for rec in records:
                t = rec.get("ticker") or ""
                if not t:
                    continue
                if t not in latest_per_ticker or rec["_ts"] > latest_per_ticker[t]["_ts"]:
                    latest_per_ticker[t] = rec
            sub_mkts = [m_ for m_ in latest_per_ticker.values() if not _is_price_bucket(m_.get("ticker") or "")]
            k = len(sub_mkts)
            if k < 3:
                continue
            sum_yes = sum(float(r.get("implied_prob_yes") or 0) for r in sub_mkts)
            norm = sum_yes / k
            if norm <= norm_thresh:
                continue
            v_qualifying += 1
            top_m_sub = sorted(sub_mkts, key=lambda r: float(r.get("implied_prob_yes") or 0), reverse=True)[:m]
            for sub in top_m_sub:
                ticker = sub.get("ticker") or ""
                implied_yes = float(sub.get("implied_prob_yes") or 0.5)
                resolution = None
                if round_trips_available:
                    resolution = _lookup_resolution(conn, ticker)
                v_bets.append({
                    "event_ticker": event_ticker,
                    "ticker": ticker,
                    "implied_yes": implied_yes,
                    "resolution": resolution,
                    "sum_yes": sum_yes,
                    "norm": norm,
                    "k": k,
                })
        v_wins = sum(1 for b in v_bets if b["resolution"] == "no")
        v_losses = sum(1 for b in v_bets if b["resolution"] == "yes")
        v_unresolved = sum(1 for b in v_bets if b["resolution"] is None)
        v_pnl = sum(
            _compute_pnl(b["implied_yes"], None, b["resolution"])
            for b in v_bets if b["resolution"] is not None and b["resolution"] != "void"
        )
        v_notional = float(v_wins + v_losses)
        normalized_variants[f"norm>{norm_thresh}"] = {
            "threshold": norm_thresh,
            "n_events_qualifying": v_qualifying,
            "n_bets": len(v_bets),
            "n_wins": v_wins,
            "n_losses": v_losses,
            "n_unresolved": v_unresolved,
            "win_rate": (v_wins / (v_wins + v_losses)) if (v_wins + v_losses) > 0 else None,
            "gross_pnl": round(v_pnl, 4),
            "roi_pct": round(v_pnl / v_notional * 100, 2) if v_notional > 0 else None,
        }

    # ── Per-event breakdown ────────────────────────────────────────────────
    per_event: dict[str, dict] = defaultdict(lambda: {
        "pnl": 0.0, "n_bets": 0, "n_wins": 0, "n_losses": 0,
        "n_unresolved": 0, "sum_yes": 0.0, "k": 0, "category": "",
        "tickers": [],
    })
    for bet in bets:
        e = per_event[bet["event_ticker"]]
        e["category"] = bet["category"]
        e["sum_yes"] = bet["sum_yes_at_fire"]
        e["k"] = bet["k_at_fire"]
        e["tickers"].append(bet["ticker"])
        e["n_bets"] += 1
        res = bet["resolution"]
        if res == "no":
            e["n_wins"] += 1
            e["pnl"] += bet["pnl"]
        elif res == "yes":
            e["n_losses"] += 1
            e["pnl"] += bet["pnl"]
        elif res is None:
            e["n_unresolved"] += 1

    top_events = sorted(per_event.items(), key=lambda x: x[1]["pnl"], reverse=True)

    # ── Event-ticker pattern analysis (Q2) ────────────────────────────────
    ticker_patterns: dict[str, int] = defaultdict(int)
    for event_ticker in events_by_ticker:
        # Extract pattern: base prefix before the date suffix
        m_pat = re.match(r'^(KX[A-Z]+(?:-[A-Z]+)*)-\d{2}', event_ticker)
        if m_pat:
            ticker_patterns[m_pat.group(1)] += 1
        else:
            ticker_patterns[event_ticker] += 1

    return {
        "events_evaluated": events_evaluated,
        "events_skipped_category": events_skipped_category,
        "events_skipped_k": events_skipped_k,
        "events_skipped_threshold": events_skipped_threshold,
        "events_qualifying": events_qualifying,
        "n_bets": n_bets,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "n_voids": n_voids,
        "n_unresolved": n_unresolved,
        "n_resolved": n_resolved,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "gross_pnl_usd": round(gross_pnl, 4),
        "roi_pct": round(roi_pct, 2) if roi_pct is not None else None,
        "threshold_used": threshold,
        "m_used": m,
        "bets": bets,
        "per_event": dict(per_event),
        "top_events": top_events[:20],
        "normalized_variants": normalized_variants,
        "ticker_patterns": dict(ticker_patterns),
    }


def _print_report(feas: dict, bt: dict | None, *, json_output: bool = False) -> None:
    if json_output:
        out = {"feasibility": {k: v for k, v in feas.items() if k not in ("sub_market_records", "events_by_ticker", "events_k3")}}
        if bt is not None:
            out["backtest"] = {k: v for k, v in bt.items() if k not in ("bets", "per_event")}
            out["top_events"] = [(et, {k: v for k, v in stats.items() if k != "tickers"}) for et, stats in (bt.get("top_events") or [])]
        print(json.dumps(out, indent=2, default=str))
        return

    print("=" * 70)
    print("kalshi_structure_arb — Feasibility + Backtest")
    print(f"Window: last {feas['days']} days | cutoff: {feas['cutoff'][:10]}")
    print("=" * 70)

    print("\n[FEASIBILITY]")
    print(f"  kalshi_llm_probability_called audit rows : {feas['llm_audit_rows_total']}")
    print(f"  distinct event_tickers with implied data : {feas['distinct_event_tickers']}")
    print(f"  events with K>=3 sub-markets visible     : {feas['events_k3_plus']}")
    print(f"  kalshi_round_trips table exists          : {feas['round_trips_available']}")
    print(f"  kalshi_round_trips rows (llm_arb)        : {feas['round_trips_count']}")
    print()
    if feas["kalshi_audit_counts"]:
        print("  Kalshi audit event kinds in window:")
        for k, cnt in sorted(feas["kalshi_audit_counts"].items(), key=lambda x: -x[1]):
            print(f"    {cnt:6d}  {k}")
    else:
        print("  *** NO kalshi audit events in window — INFEASIBLE for backtest ***")

    if feas["llm_audit_rows_total"] == 0:
        print()
        print("[VERDICT] INFEASIBLE — zero kalshi_llm_probability_called audit rows.")
        print("  The local trading_corp.db has no Kalshi strategy data.")
        print("  All Kalshi activity (kalshi_llm_arbitrage, etc.) runs on the")
        print("  production VM (tc-prod-vm). The local DB covers only 2026-04-26")
        print("  to 2026-05-03 and contains only Lord Otter (BTC) and PMCC data.")
        print()
        print("  To run this backtest with live data, SSH to tc-prod-vm and run:")
        print("    python scripts/backtest_kalshi_structure_arb.py \\")
        print("      --db /home/azureuser/trading_corp/data/trading_corp.db")
        return

    print()
    if bt is None:
        return

    print("[BACKTEST RESULTS]")
    print(f"  threshold: sum_yes_implied > {bt['threshold_used']}, top-M={bt['m_used']}")
    print(f"  events evaluated   : {bt['events_evaluated']}")
    print(f"  skipped (category) : {bt['events_skipped_category']}")
    print(f"  skipped (K<3)      : {bt['events_skipped_k']}")
    print(f"  skipped (threshold): {bt['events_skipped_threshold']}")
    print(f"  qualifying events  : {bt['events_qualifying']}")
    print()
    print(f"  n_bets       : {bt['n_bets']}")
    print(f"  n_wins       : {bt['n_wins']}")
    print(f"  n_losses     : {bt['n_losses']}")
    print(f"  n_voids      : {bt['n_voids']}")
    print(f"  n_unresolved : {bt['n_unresolved']}")
    wr = bt["win_rate"]
    print(f"  win_rate     : {wr*100:.1f}%" if wr is not None else "  win_rate     : N/A (no resolved bets)")
    print(f"  gross_pnl    : ${bt['gross_pnl_usd']:.2f}")
    roi = bt["roi_pct"]
    print(f"  ROI          : {roi:.1f}%" if roi is not None else "  ROI          : N/A")

    print()
    print("[NORMALIZED THRESHOLD VARIANTS (Q1)]")
    print(f"  {'Variant':<16} {'N_events':>8} {'N_bets':>7} {'Wins':>5} {'Losses':>7} {'Unresolved':>11} {'WR%':>7} {'PnL':>8} {'ROI%':>7}")
    print("  " + "-" * 80)
    # Baseline (additive threshold)
    n_ev = bt["events_qualifying"]
    n_b = bt["n_bets"]
    n_w = bt["n_wins"]
    n_l = bt["n_losses"]
    n_u = bt["n_unresolved"]
    wr_pct = f"{bt['win_rate']*100:.1f}" if bt["win_rate"] is not None else "N/A"
    roi_pct_ = f"{bt['roi_pct']:.1f}" if bt["roi_pct"] is not None else "N/A"
    print(f"  {'Additive>1.5':<16} {n_ev:>8} {n_b:>7} {n_w:>5} {n_l:>7} {n_u:>11} {wr_pct:>7} ${bt['gross_pnl_usd']:>6.2f} {roi_pct_:>7}")
    for var_name, var in bt["normalized_variants"].items():
        wr_v = f"{var['win_rate']*100:.1f}" if var["win_rate"] is not None else "N/A"
        roi_v = f"{var['roi_pct']:.1f}" if var["roi_pct"] is not None else "N/A"
        pnl_v = f"${var['gross_pnl']:.2f}"
        print(f"  {var_name:<16} {var['n_events_qualifying']:>8} {var['n_bets']:>7} {var['n_wins']:>5} {var['n_losses']:>7} {var['n_unresolved']:>11} {wr_v:>7} {pnl_v:>8} {roi_v:>7}")

    print()
    print("[TOP EVENTS BY PnL]")
    print(f"  {'event_ticker':<35} {'K':>3} {'sum_yes':>8} {'n':>4} {'wins':>5} {'pnl':>8}")
    print("  " + "-" * 65)
    for et, stats in (bt["top_events"] or [])[:10]:
        wr_e = stats["n_wins"]
        pnl_e = stats["pnl"]
        print(f"  {et:<35} {stats['k']:>3} {stats['sum_yes']:>8.2f} {stats['n_bets']:>4} {wr_e:>5} ${pnl_e:>6.2f}")

    print()
    print("[EVENT TICKER PATTERNS (Q2)]")
    for pattern, cnt in sorted(bt["ticker_patterns"].items(), key=lambda x: -x[1])[:15]:
        print(f"  {cnt:4d}  {pattern}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest kalshi_structure_arb")
    parser.add_argument("--db", default=_DEFAULT_DB, help="Path to SQLite DB")
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    parser.add_argument("--threshold", type=float, default=_DEFAULT_THRESHOLD,
                        help="sum_yes_implied threshold (default 1.5)")
    parser.add_argument("--m", type=int, default=_DEFAULT_M,
                        help="Top-M sub-markets to bet NO on (default 3)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Machine-readable JSON output")
    parser.add_argument("--out", default=None,
                        help="Write JSON output to this file path")
    args = parser.parse_args()

    db_path = str(Path(args.db))
    try:
        conn = _connect(db_path)
    except Exception as e:
        print(f"ERROR: cannot open database at {db_path}: {e}", file=sys.stderr)
        sys.exit(0)

    feas = _feasibility_check(conn, args.days)

    bt = None
    if feas["llm_audit_rows_total"] > 0:
        bt = _run_backtest(feas, conn, threshold=args.threshold, m=args.m)

    if args.out or args.json_output:
        out_data = {
            "feasibility": {k: v for k, v in feas.items() if k not in ("sub_market_records", "events_by_ticker", "events_k3")},
        }
        if bt is not None:
            out_data["backtest"] = {k: v for k, v in bt.items() if k not in ("bets", "per_event")}
            out_data["backtest"]["bets_summary"] = [
                {
                    "event_ticker": b["event_ticker"],
                    "ticker": b["ticker"],
                    "implied_yes": round(b["implied_yes"], 4),
                    "resolution": b["resolution"],
                    "pnl": round(b["pnl"], 4) if b.get("pnl") is not None else None,
                }
                for b in (bt.get("bets") or [])
            ]
        if args.out:
            Path(args.out).write_text(json.dumps(out_data, indent=2, default=str), encoding="utf-8")
            print(f"Wrote JSON to {args.out}")
        if args.json_output:
            print(json.dumps(out_data, indent=2, default=str))
    else:
        _print_report(feas, bt)

    conn.close()


if __name__ == "__main__":
    main()
