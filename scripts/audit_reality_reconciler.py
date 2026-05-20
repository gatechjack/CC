"""Audit-vs-reality reconciler for closed v2 paper trades.

Purpose: independent verifier that, for each closed v2 paper_trade_record
row, replays the trade against the persisted bitunix_bar_history bars
(authoritative price truth) and compares the simulated lifecycle to the
recorded outcome. Catches the class of silent failure where the live
replay path saw the wrong bars and recorded a wrong result.

Bug context: in May 2026 the BitUnix kline fetcher silently truncated
its bar slice (server caps at 200 bars/call; legacy fetcher treated
that as end-of-data). The v2 multi-leg classifier never observed early
TP fills; trades that hit TP1+TP2 in price action were recorded as full
SL losses. This reconciler is the durable check that would have caught
that bug at trade-close time — and would catch any future bug class
where the audit pipeline disagrees with the persisted bar history.

Usage:
    py scripts/audit_reality_reconciler.py [--db sqlite:///path/to/db]
    py scripts/audit_reality_reconciler.py --json   # machine-readable

Default DB: TC_DB_URL env or sqlite:///data/trading_corp.db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.agents.paper_trade_replay import (  # noqa: E402
    _PendingRow,
    _classify_v2_multi_leg,
)
from trading_corp.persistence import db as _db  # noqa: E402


@dataclass
class _ReconcileResult:
    order_id: str
    ts: str
    division: str
    side: str
    recorded_result: str | None
    recorded_r: float | None
    simulated_result: str | None
    simulated_r: float | None
    simulated_filled_legs: list[str]
    simulated_current_sl: float | None
    bar_count: int
    matches: bool
    discrepancy: str | None


def _load_closed_v2_trades(conn) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT order_id, ts, strategy, division, symbol, side, qty,
               stop_price, tp_price, tp_r_multiple,
               entry_reference_price, expected_loss, expected_gain,
               max_hold_seconds, extra_json,
               result, result_ts, result_price, actual_r_multiple,
               bars_to_resolution
        FROM paper_trade_record
        WHERE division = 'bitunix_futures'
          AND result IS NOT NULL
          AND json_extract(extra_json, '$.tp_plan_version') = 'v2'
        ORDER BY ts ASC
    """).fetchall()
    return [dict(r) for r in rows]


def _load_bars_for_trade(conn, trade: dict[str, Any], timeframe: str = "3m") -> list[list[float]]:
    """Pull bars from bitunix_bar_history covering [entry, result_ts]."""
    start_iso = trade["ts"]
    end_iso = trade["result_ts"]
    # Convert ISO to ms via SQLite strftime for portability.
    rows = conn.execute("""
        SELECT ts_ms, open, high, low, close, volume
        FROM bitunix_bar_history
        WHERE timeframe = ?
          AND ts_ms >= CAST(strftime('%s', ?) AS INTEGER) * 1000
          AND ts_ms <= CAST(strftime('%s', ?) AS INTEGER) * 1000
        ORDER BY ts_ms ASC
    """, (timeframe, start_iso, end_iso)).fetchall()
    return [[r["ts_ms"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in rows]


def _build_pending_row(trade: dict[str, Any]) -> _PendingRow:
    return _PendingRow(
        order_id=trade["order_id"],
        ts=trade["ts"],
        strategy=trade["strategy"],
        division=trade["division"],
        symbol=trade["symbol"],
        side=trade["side"],
        qty=trade["qty"],
        stop_price=trade["stop_price"],
        tp_price=trade["tp_price"],
        tp_r_multiple=trade["tp_r_multiple"],
        entry_reference_price=trade["entry_reference_price"],
        expected_loss=trade["expected_loss"],
        expected_gain=trade["expected_gain"],
        max_hold_seconds=trade["max_hold_seconds"],
        extra_json=trade["extra_json"],
    )


def _reconcile_one(conn, trade: dict[str, Any]) -> _ReconcileResult:
    bars = _load_bars_for_trade(conn, trade)
    # Reset extra state to a fresh-replay starting point (mimic what a
    # working replay-tick would see if it walked the full bar window
    # in one pass). The recorded extra_json may carry stale state from
    # the buggy live path; we ignore filled_legs/current_sl in extra
    # and let the classifier start from the original SL.
    extra = json.loads(trade["extra_json"]) if trade["extra_json"] else {}
    extra = {
        **extra,
        "filled_legs": [],
        "current_sl": trade["stop_price"],
    }
    row = _build_pending_row(trade)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    sim_result = verdict.result
    sim_r = verdict.actual_r_multiple
    sim_filled = (verdict.extra_json_updates or {}).get("filled_legs", [])
    sim_sl = (verdict.extra_json_updates or {}).get("current_sl")

    rec_result = trade["result"]
    rec_r = trade["actual_r_multiple"]

    # Match criteria: result string AND R within tolerance.
    r_tol = 0.05
    matches = (
        sim_result == rec_result
        and rec_r is not None
        and sim_r is not None
        and abs(float(sim_r) - float(rec_r)) <= r_tol
    )
    discrepancy = None
    if not matches:
        deltas = []
        if sim_result != rec_result:
            deltas.append(f"result: recorded={rec_result!r} sim={sim_result!r}")
        if sim_r is not None and rec_r is not None and abs(float(sim_r) - float(rec_r)) > r_tol:
            deltas.append(f"R: recorded={rec_r} sim={sim_r} (delta={float(sim_r)-float(rec_r):+.4f})")
        if sim_filled:
            deltas.append(f"missed_legs: {sim_filled}")
        discrepancy = "; ".join(deltas) or "match-criteria-failed"

    return _ReconcileResult(
        order_id=trade["order_id"],
        ts=trade["ts"],
        division=trade["division"],
        side=trade["side"],
        recorded_result=rec_result,
        recorded_r=rec_r,
        simulated_result=sim_result,
        simulated_r=sim_r,
        simulated_filled_legs=sim_filled,
        simulated_current_sl=sim_sl,
        bar_count=len(bars),
        matches=matches,
        discrepancy=discrepancy,
    )


def reconcile_all(db_url: str) -> list[_ReconcileResult]:
    with _db.connect(db_url) as conn:
        trades = _load_closed_v2_trades(conn)
        return [_reconcile_one(conn, t) for t in trades]


def _format_text(results: list[_ReconcileResult]) -> str:
    lines = []
    lines.append("=" * 80)
    lines.append(f"audit_reality_reconciler — {len(results)} closed v2 trades scanned")
    lines.append("=" * 80)
    if not results:
        lines.append("No closed v2 trades found.")
        return "\n".join(lines)
    n_match = sum(1 for r in results if r.matches)
    n_mismatch = len(results) - n_match
    lines.append(f"matches: {n_match}/{len(results)}   mismatches: {n_mismatch}")
    lines.append("")
    for r in results:
        tag = "✓ MATCH" if r.matches else "✗ MISMATCH"
        lines.append(f"{tag}  {r.order_id}  {r.ts}  {r.side}")
        lines.append(f"  recorded: result={r.recorded_result} R={r.recorded_r}")
        lines.append(f"  simulated: result={r.simulated_result} R={r.simulated_r} "
                     f"filled_legs={r.simulated_filled_legs} "
                     f"final_sl={r.simulated_current_sl}")
        lines.append(f"  bars_walked: {r.bar_count}")
        if r.discrepancy:
            lines.append(f"  DISCREPANCY: {r.discrepancy}")
        lines.append("")
    return "\n".join(lines)


def _format_json(results: list[_ReconcileResult]) -> str:
    return json.dumps([
        {
            "order_id": r.order_id, "ts": r.ts, "division": r.division, "side": r.side,
            "recorded_result": r.recorded_result, "recorded_r": r.recorded_r,
            "simulated_result": r.simulated_result, "simulated_r": r.simulated_r,
            "simulated_filled_legs": r.simulated_filled_legs,
            "simulated_current_sl": r.simulated_current_sl,
            "bar_count": r.bar_count,
            "matches": r.matches,
            "discrepancy": r.discrepancy,
        }
        for r in results
    ], indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.environ.get("TC_DB_URL", "sqlite:///data/trading_corp.db"),
        help="SQLAlchemy DB URL (default: sqlite:///data/trading_corp.db)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    results = reconcile_all(args.db)
    if args.json:
        print(_format_json(results))
    else:
        print(_format_text(results))

    # Exit code: 0 if all match, 1 if any mismatch (CI gate)
    return 0 if all(r.matches for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
