#!/usr/bin/env python3
"""Backtest Kalshi BTC limit-order TTL strategy against path_logger.db + trading_corp.db.

For each market in market_ladder (last --days days), simulates resting limit
bids at (forecast_fair - offset_bp/10000) and walks the order book to detect
fills within TTL minutes of market open. Reports adverse-selection sign, fill
rate, base-rate separation, and P&L.

Report sections (mandated order — see spec §Phase 3):
  1. ADVERSE-SELECTION SIGN BY (ASSET, TTL)
  2. FILL RATE BY (ASSET, TTL, OFFSET)
  3. BASE-RATE SEPARATION
  4. PnL (LAST — NOT THE PRIMARY METRIC)

Usage:
    python scripts/backtest_kalshi_limit_ttl.py \\
        --path-db data/path_logger.db \\
        --main-db data/trading_corp.db \\
        --ttl 5,10,15,20 \\
        --offsets 0,-50,-100 \\
        --days 7

Exit code 0 always — outputs human-readable text for runbook entry.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)


# ── Data fetching ─────────────────────────────────────────────────────────────

def _connect_ro(db_path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection (uri=True + ?mode=ro)."""
    # Fall back to normal connect if the file doesn't exist (better error message)
    import os
    path = db_path.replace("sqlite:///", "")
    if not os.path.exists(path):
        print(f"ERROR: database not found: {path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_tickers(path_conn: sqlite3.Connection, days: int) -> list[str]:
    """Return distinct tickers with ladder rows in the last N days."""
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1_000
    )
    rows = path_conn.execute(
        "SELECT DISTINCT ticker FROM market_ladder WHERE captured_ts >= ?",
        (cutoff_ms,),
    ).fetchall()
    return [r["ticker"] for r in rows]


def _fetch_ladder(path_conn: sqlite3.Connection, ticker: str, days: int) -> list[sqlite3.Row]:
    """Return all market_ladder rows for ticker in horizon, ordered by captured_ts."""
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1_000
    )
    return path_conn.execute(
        "SELECT * FROM market_ladder "
        "WHERE ticker = ? AND captured_ts >= ? "
        "ORDER BY captured_ts ASC",
        (ticker, cutoff_ms),
    ).fetchall()


def _fetch_forecast_fair(
    main_conn: sqlite3.Connection,
    ticker: str,
    market_open_ms: int,
    window_ms: int = 15 * 60 * 1_000,
) -> float | None:
    """Pull prob_yes from the nearest kalshi_crypto_evaluated audit row at market open.

    Searches audit_event for kind='kalshi_crypto_evaluated' rows matching
    `ticker` within window_ms of market_open_ms. Returns the prob_yes from
    the closest row in time.
    """
    # ISO string for the open timestamp
    open_dt = datetime.fromtimestamp(market_open_ms / 1_000, tz=timezone.utc)
    window_start = (open_dt - timedelta(milliseconds=window_ms)).isoformat()
    window_end = (open_dt + timedelta(milliseconds=window_ms)).isoformat()

    rows = main_conn.execute(
        "SELECT ts, payload_json FROM audit_event "
        "WHERE kind = 'kalshi_crypto_evaluated' "
        "AND ts BETWEEN ? AND ? "
        "AND payload_json LIKE ? "
        "ORDER BY ABS(julianday(ts) - julianday(?)) ASC "
        "LIMIT 5",
        (window_start, window_end, f'%"{ticker}"%', open_dt.isoformat()),
    ).fetchall()

    # Note: ABS(julianday diff) is used only for ordering here (not for SARGable
    # filtering), so index use on `ts` BETWEEN is preserved for the row scan.
    #
    # Only fired=true rows carry a meaningful prob_yes for the limit-order
    # counterfactual. Suppressed-fire rows zero prob_yes post-suppression
    # (verified 2026-05-22 on a live payload: prob_yes=0.0 with
    # hardcoded_prob_yes=0.563 and vol_v2_classification=suppressed_fire),
    # so a naive read would treat them as "model says YES has 0% probability"
    # which is the suppression artefact, not the forecast.
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
            if p.get("ticker") != ticker and ticker not in str(p.get("ticker", "")):
                continue
            if not p.get("fired"):
                continue
            prob = p.get("prob_yes")
            if prob is None:
                continue
            prob = float(prob)
            if prob <= 0.0:
                continue
            return prob
        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
            continue
    return None


# ── Core simulation ───────────────────────────────────────────────────────────

def _simulate_ticker(
    ladder: list[sqlite3.Row],
    forecast_fair: float,
    ttl_minutes: int,
    offset_bp: int,
) -> dict[str, Any] | None:
    """Simulate one (ticker, TTL, offset) combination.

    Returns a result dict with fill details, or None if no fill occurred.
    """
    if not ladder:
        return None

    # Market open = earliest captured_ts
    open_ms = ladder[0]["captured_ts"]
    ttl_ms = ttl_minutes * 60 * 1_000
    deadline_ms = open_ms + ttl_ms

    limit_bid = forecast_fair - offset_bp / 10_000.0

    # Walk ladder rows up to TTL deadline looking for yes_ask <= limit_bid
    fill_row: sqlite3.Row | None = None
    for row in ladder:
        if row["captured_ts"] > deadline_ms:
            break
        yes_ask = row["yes_ask"]
        if yes_ask is not None and yes_ask <= limit_bid:
            fill_row = row
            break

    if fill_row is None:
        return None

    fill_ts = fill_row["captured_ts"]
    fill_price = fill_row["yes_ask"]

    # Post-fill drift at +1, +5, +10 min and at settlement (last row)
    checkpoints = {
        "+1min": fill_ts + 1 * 60 * 1_000,
        "+5min": fill_ts + 5 * 60 * 1_000,
        "+10min": fill_ts + 10 * 60 * 1_000,
    }

    def _nearest_implied(target_ms: int) -> float | None:
        """Return implied_prob from the row nearest to target_ms."""
        best_row = None
        best_delta = float("inf")
        for row in ladder:
            delta = abs(row["captured_ts"] - target_ms)
            if delta < best_delta:
                best_delta = delta
                best_row = row
        if best_row is None:
            return None
        ip = best_row["implied_prob"]
        if ip is None:
            # Fall back to computing from yes_ask
            ya = best_row["yes_ask"]
            if ya and ya > 0:
                return float(ya)
        return float(ip) if ip is not None else None

    post_drift: dict[str, float | None] = {}
    for label, target_ms in checkpoints.items():
        post_drift[label] = _nearest_implied(target_ms)

    # Settlement: last row in the ladder
    last_row = ladder[-1]
    settlement_prob = _nearest_implied(last_row["captured_ts"])

    # Adverse-selection sign = post_fill_drift - forecast_fair
    # Positive = market moved toward forecast (model has content)
    # Negative = adverse (contra-indicator or noise)
    adverse_signs: dict[str, float | None] = {}
    for label, prob in post_drift.items():
        if prob is not None:
            adverse_signs[label] = prob - forecast_fair
        else:
            adverse_signs[label] = None

    # P&L = (settlement_prob - limit_bid) × 100 cents/contract
    pnl_cents: float | None
    if settlement_prob is not None:
        pnl_cents = (settlement_prob - limit_bid) * 100.0
    else:
        pnl_cents = None

    # Time to fill from market open (minutes)
    minutes_to_fill = (fill_ts - open_ms) / 60_000.0
    # Time remaining before settlement (minutes from fill)
    last_ts = last_row["captured_ts"]
    minutes_before_settlement = (last_ts - fill_ts) / 60_000.0

    return {
        "fill_price": fill_price,
        "limit_bid": limit_bid,
        "forecast_fair": forecast_fair,
        "fill_ts_ms": fill_ts,
        "minutes_to_fill": minutes_to_fill,
        "minutes_before_settlement": minutes_before_settlement,
        "post_drift": post_drift,
        "adverse_signs": adverse_signs,
        "settlement_prob": settlement_prob,
        "pnl_cents": pnl_cents,
    }


# ── Aggregation and reporting ─────────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _build_report(
    results: list[dict[str, Any]],
    ttl_list: list[int],
    offset_list: list[int],
) -> str:
    """Build the plain-text report in mandated section order."""
    lines: list[str] = []

    # Group results by (event_ticker, ttl, offset_bp)
    by_ttl_offset: dict[tuple[int, int], list[dict]] = defaultdict(list)
    all_fills: list[dict] = []
    all_tickers_total: dict[tuple[int, int], int] = defaultdict(int)

    for r in results:
        key = (r["ttl"], r["offset_bp"])
        all_tickers_total[key] += 1
        if r.get("filled"):
            by_ttl_offset[key].append(r)
            all_fills.append(r)

    # ── Section 1: ADVERSE-SELECTION SIGN ────────────────────────────────────
    lines.append("=" * 60)
    lines.append("=== ADVERSE-SELECTION SIGN BY (ASSET, TTL) ===")
    lines.append("=" * 60)
    lines.append(
        "Adverse sign = post_fill_implied_prob - forecast_fair. "
        "Positive = favourable (model has predictive content). "
        "Negative = adverse (model is noise or contra-indicator)."
    )
    lines.append("")

    for ttl in ttl_list:
        # Aggregate across all offsets for this TTL (adverse sign is TTL-level)
        fills_for_ttl = [r for r in all_fills if r["ttl"] == ttl]
        if not fills_for_ttl:
            lines.append(f"TTL={ttl}min: no fills")
            continue

        for label in ("+1min", "+5min", "+10min"):
            signs = [
                r["result"]["adverse_signs"].get(label)
                for r in fills_for_ttl
                if r["result"] and r["result"]["adverse_signs"].get(label) is not None
            ]
            mean_sign = _safe_mean([s for s in signs if s is not None])
            n_valid = len([s for s in signs if s is not None])
            if mean_sign is not None:
                direction = "FAVOURABLE" if mean_sign > 0 else "ADVERSE"
                lines.append(
                    f"  TTL={ttl:2d}min {label:6s}: mean_adverse_sign={mean_sign:+.4f}  "
                    f"n={n_valid}  [{direction}]"
                )
            else:
                lines.append(f"  TTL={ttl:2d}min {label:6s}: insufficient post-fill data")
        lines.append("")

    # ── Section 2: FILL RATE ──────────────────────────────────────────────────
    lines.append("=" * 60)
    lines.append("=== FILL RATE BY (ASSET, TTL, OFFSET) ===")
    lines.append("=" * 60)
    lines.append("")

    for ttl in ttl_list:
        for offset_bp in offset_list:
            key = (ttl, offset_bp)
            total = all_tickers_total[key]
            filled = len(by_ttl_offset[key])
            fill_rate = filled / total if total > 0 else 0.0
            avg_min_to_fill: float | None = None
            if filled > 0:
                avg_min_to_fill = _safe_mean(
                    [r["result"]["minutes_to_fill"] for r in by_ttl_offset[key]]
                )
            line = (
                f"  TTL={ttl:2d}min  offset={offset_bp:4d}bp: "
                f"{filled:3d}/{total:3d} fills  "
                f"rate={fill_rate:.1%}"
            )
            if avg_min_to_fill is not None:
                line += f"  avg_min_to_fill={avg_min_to_fill:.1f}"
            lines.append(line)
    lines.append("")

    # ── Section 3: BASE-RATE SEPARATION ──────────────────────────────────────
    lines.append("=" * 60)
    lines.append("=== BASE-RATE SEPARATION ===")
    lines.append("=" * 60)
    lines.append(
        "Correlation: (minutes_before_settlement at fill) vs fill outcome. "
        "If fills <5min before settlement are MORE accurate than fills >20min, "
        "the harvest is base-rate convergence, not model edge."
    )
    lines.append("")

    # Bucket fills by minutes_before_settlement
    bucket_defs = [
        ("< 5min",  0,   5),
        ("5-20min", 5,  20),
        ("> 20min", 20, float("inf")),
    ]
    for label, lo, hi in bucket_defs:
        bucket_fills = [
            r for r in all_fills
            if r["result"] and lo <= r["result"]["minutes_before_settlement"] < hi
        ]
        if not bucket_fills:
            lines.append(f"  {label:9s}: no fills")
            continue
        # 'Accuracy' = post-fill drift is positive (model was directionally correct)
        signs_1min = [
            r["result"]["adverse_signs"].get("+1min")
            for r in bucket_fills
            if r["result"]["adverse_signs"].get("+1min") is not None
        ]
        n_positive = sum(1 for s in signs_1min if s > 0)
        n_total = len(signs_1min)
        accuracy = n_positive / n_total if n_total > 0 else 0.0
        lines.append(
            f"  {label:9s}: n={len(bucket_fills):3d}  "
            f"1min_favourable_rate={accuracy:.1%}  (n_valid={n_total})"
        )
    lines.append("")

    # ── Section 4: PnL ────────────────────────────────────────────────────────
    lines.append("=" * 60)
    lines.append("=== PnL (LAST — NOT THE PRIMARY METRIC) ===")
    lines.append("=" * 60)
    lines.append("PnL = (settlement_prob - limit_bid) × 100 cents/contract.")
    lines.append("")

    for ttl in ttl_list:
        for offset_bp in offset_list:
            key = (ttl, offset_bp)
            fills = by_ttl_offset[key]
            pnls = [
                r["result"]["pnl_cents"]
                for r in fills
                if r["result"] and r["result"]["pnl_cents"] is not None
            ]
            if not pnls:
                lines.append(
                    f"  TTL={ttl:2d}min  offset={offset_bp:4d}bp: no resolved fills"
                )
                continue
            total_pnl = sum(pnls)
            avg_pnl = total_pnl / len(pnls)
            lines.append(
                f"  TTL={ttl:2d}min  offset={offset_bp:4d}bp: "
                f"n={len(pnls)}  total_pnl={total_pnl:+.1f}¢  "
                f"avg_pnl={avg_pnl:+.1f}¢/contract"
            )
    lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def _parse_int_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def main() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--path-db",
        default="data/path_logger.db",
        help="Path logger SQLite DB path (default: data/path_logger.db)",
    )
    p.add_argument(
        "--main-db",
        default="data/trading_corp.db",
        help="Main trading_corp SQLite DB path (default: data/trading_corp.db)",
    )
    p.add_argument(
        "--ttl",
        default="5,10,15,20",
        help="Comma-separated TTL values in minutes (default: 5,10,15,20)",
    )
    p.add_argument(
        "--offsets",
        default="0,-50,-100",
        help="Comma-separated offset values in basis points (default: 0,-50,-100)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=7,
        help="Horizon in days for market_ladder rows (default: 7)",
    )
    args = p.parse_args()

    ttl_list = _parse_int_list(args.ttl)
    offset_list = _parse_int_list(args.offsets)

    path_conn = _connect_ro(args.path_db)
    main_conn = _connect_ro(args.main_db)

    tickers = _fetch_tickers(path_conn, args.days)
    if not tickers:
        print(
            f"No tickers found in market_ladder for the last {args.days} days. "
            "Logger needs runtime first.",
            file=sys.stderr,
        )
        return 0

    print(
        f"Found {len(tickers)} tickers in path_logger.db (last {args.days}d). "
        f"Simulating TTLs={ttl_list} offsets={offset_list}bp ...",
        file=sys.stderr,
    )

    results: list[dict[str, Any]] = []

    for ticker in tickers:
        ladder = _fetch_ladder(path_conn, ticker, args.days)
        if not ladder:
            continue

        # Market open = first row's captured_ts
        market_open_ms = ladder[0]["captured_ts"]

        # Extract event_ticker from first row
        event_ticker = ladder[0]["event_ticker"] or ticker.split("-")[0]

        forecast_fair = _fetch_forecast_fair(main_conn, ticker, market_open_ms)
        if forecast_fair is None:
            log.debug("No forecast_fair for %s — skipping", ticker)
            continue

        for ttl in ttl_list:
            for offset_bp in offset_list:
                sim = _simulate_ticker(ladder, forecast_fair, ttl, offset_bp)
                results.append({
                    "ticker": ticker,
                    "event_ticker": event_ticker,
                    "ttl": ttl,
                    "offset_bp": offset_bp,
                    "forecast_fair": forecast_fair,
                    "filled": sim is not None,
                    "result": sim,
                })

    path_conn.close()
    main_conn.close()

    n_filled = sum(1 for r in results if r["filled"])
    print(
        f"Simulation complete: {len(results)} (ticker, TTL, offset) combos; "
        f"{n_filled} fills detected.",
        file=sys.stderr,
    )

    report = _build_report(results, ttl_list, offset_list)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
