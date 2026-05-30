"""Export paper_trade_record rows to a Pine-Script paste block.

Display-only review tool — touches nothing in the live engine.

Pulls bitunix_futures paper trades from `paper_trade_record` over a
configurable time window (default last 30 days), joins audit_event for
the PA validator-pair tag (see Phase 1 diagnostic
reports/2026-05-30_paper_trade_visualizer_phase1_diagnostic.md for the
join recipe), and emits a Pine v6 paste block of `var array<...>`
declarations the operator drops into
scripts/pinescript/paper_trade_visualizer.pine between the
`// === BEGIN GENERATED PASTE BLOCK ===` / `// === END GENERATED PASTE
BLOCK ===` markers.

Output goes to `paper_trades_pine.txt` next to the .pine file by default.

Usage:
  python scripts/export_paper_trades_to_pinescript.py \
      --db data/trading_corp.db \
      --since 30d \
      --division bitunix_futures \
      --out scripts/pinescript/paper_trades_pine.txt

The Pine script reads the arrays as `g_entry_ts`, `g_entry_price`,
`g_sl_price`, `g_tp1_price`, `g_tp2_price`, `g_tp3_price`,
`g_result_ts`, `g_result_price`, `g_result`, `g_side`,
`g_r_multiple`, `g_pnl_dollars`, `g_validator_pair`,
`g_order_id_short`, and the scalar `g_count`.

Sentinels for open trades / missing TP legs:
  result_ts        : 0  (treat as "still open" — render to current_time)
  result_price     : 0.0
  actual_r_multiple: 0.0
  actual_pnl_dollars: 0.0
  tp1/tp2/tp3_price: 0.0 when not present (pre-v2 single-leg rows)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


VALIDATOR_TAGS = {
    "vwap_alignment": "V",
    "volume_confirmation": "VOL",
    "structure_alignment": "S",
}


@dataclass(frozen=True)
class Trade:
    order_id: str
    entry_ts: str
    side: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    result: str  # 'win'|'loss'|'expired'|'open'
    result_ts: str  # ISO or '' for open
    result_price: float
    actual_r_multiple: float
    actual_pnl_dollars: float
    trigger_signal: str
    validator_pair: str  # e.g. 'V+VOL/-S' or '' when no decision found


def iso_to_unix_ms(iso: str) -> int:
    """Convert ISO-8601 UTC ts to UNIX milliseconds (Pine `time` units)."""
    if not iso:
        return 0
    s = iso.replace("Z", "+00:00")
    return int(datetime.fromisoformat(s).timestamp() * 1000)


def parse_since(spec: str) -> datetime:
    """Parse '30d', '7d', '24h', or an ISO timestamp into a UTC datetime."""
    spec = spec.strip()
    if spec.endswith("d"):
        return datetime.now(timezone.utc) - timedelta(days=int(spec[:-1]))
    if spec.endswith("h"):
        return datetime.now(timezone.utc) - timedelta(hours=int(spec[:-1]))
    return datetime.fromisoformat(spec.replace("Z", "+00:00"))


def _extract_tp_prices(extra: dict) -> tuple[float, float, float]:
    """Read TP1/TP2/TP3 prices from extra_json.

    Prefers flat keys (`tp1_price`, `tp2_price`, `tp3_price` — written by
    the v2 builder); falls back to `tp_plan` list entries; defaults to
    0.0 sentinel when missing.
    """
    flat = (
        float(extra.get("tp1_price") or 0.0),
        float(extra.get("tp2_price") or 0.0),
        float(extra.get("tp3_price") or 0.0),
    )
    if any(flat):
        return flat
    out = [0.0, 0.0, 0.0]
    for entry in extra.get("tp_plan") or []:
        leg = entry.get("leg")
        price = entry.get("price")
        if price is None:
            continue
        if leg == "tp1":
            out[0] = float(price)
        elif leg == "tp2":
            out[1] = float(price)
        elif leg == "tp3":
            out[2] = float(price)
    return tuple(out)  # type: ignore[return-value]


def build_validator_pair_tag(passed: list[str] | None, failed: list[str] | None) -> str:
    """Render a compact 'V+VOL/-S' style tag from passed/failed validator lists."""
    p = "+".join(VALIDATOR_TAGS.get(n, n[:3].upper()) for n in (passed or []))
    f = "+".join(VALIDATOR_TAGS.get(n, n[:3].upper()) for n in (failed or []))
    if p and f:
        return f"{p}/-{f}"
    if p:
        return p
    if f:
        return f"-{f}"
    return ""


def fetch_validator_pair(
    conn: sqlite3.Connection,
    trigger_signal: str,
    pt_ts: str,
    *,
    window_seconds: int = 600,
) -> str:
    """Resolve the validator-pair tag for one trade.

    Recipe (from Phase 1 diagnostic): match the most recent
    `pa_validation_decision` audit row with the same `trigger_signal`
    whose `ts` is in `(pt_ts - window_seconds, pt_ts]`. Falls back to
    `trigger_signal` itself when no decision row found in the window.

    The lower bound is computed in Python and passed as a same-format
    ISO 'T' string. Using SQLite's `datetime(?, '-N seconds')` would
    return a space-separated string that compares falsely against the
    ISO-'T' `ts` values (see memory feedback_sqlite_iso_datetime_comparison).
    """
    if not trigger_signal or not pt_ts:
        return trigger_signal or ""
    upper_dt = datetime.fromisoformat(pt_ts.replace("Z", "+00:00"))
    lower_iso = (upper_dt - timedelta(seconds=window_seconds)).isoformat()
    row = conn.execute(
        """
        SELECT payload_json
        FROM audit_event
        WHERE kind = 'pa_validation_decision'
          AND json_extract(payload_json, '$.trigger_signal') = ?
          AND ts <= ?
          AND ts >= ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (trigger_signal, pt_ts, lower_iso),
    ).fetchone()
    if not row:
        return trigger_signal
    payload = json.loads(row[0])
    tag = build_validator_pair_tag(payload.get("passed"), payload.get("failed"))
    return tag or trigger_signal


def fetch_trades(
    conn: sqlite3.Connection,
    *,
    division: str,
    since_iso: str,
    until_iso: str | None = None,
    window_seconds: int = 600,
) -> list[Trade]:
    """Pull paper trades for `division` between `since_iso` and `until_iso`."""
    conn.row_factory = sqlite3.Row
    if until_iso is None:
        sql = (
            "SELECT order_id, ts, side, entry_reference_price, stop_price, "
            "tp_price, result, result_ts, result_price, actual_r_multiple, "
            "actual_pnl_dollars, extra_json "
            "FROM paper_trade_record "
            "WHERE division = ? AND ts >= ? "
            "ORDER BY ts ASC"
        )
        rows = conn.execute(sql, (division, since_iso)).fetchall()
    else:
        sql = (
            "SELECT order_id, ts, side, entry_reference_price, stop_price, "
            "tp_price, result, result_ts, result_price, actual_r_multiple, "
            "actual_pnl_dollars, extra_json "
            "FROM paper_trade_record "
            "WHERE division = ? AND ts >= ? AND ts <= ? "
            "ORDER BY ts ASC"
        )
        rows = conn.execute(sql, (division, since_iso, until_iso)).fetchall()
    out: list[Trade] = []
    for r in rows:
        extra = json.loads(r["extra_json"] or "{}")
        tp1, tp2, tp3 = _extract_tp_prices(extra)
        # If flat tp1/2/3 missing AND tp_plan empty, fall back to top-level
        # tp_price as the single TP1 (pre-v2 single-leg rows).
        if tp1 == 0.0 and r["tp_price"] is not None:
            tp1 = float(r["tp_price"])
        trigger = extra.get("trigger_signal") or ""
        validator_pair = fetch_validator_pair(
            conn, trigger, r["ts"], window_seconds=window_seconds
        )
        result = r["result"] or "open"
        out.append(
            Trade(
                order_id=r["order_id"],
                entry_ts=r["ts"],
                side=r["side"],
                entry_price=float(r["entry_reference_price"] or 0.0),
                sl_price=float(r["stop_price"] or 0.0),
                tp1_price=float(tp1),
                tp2_price=float(tp2),
                tp3_price=float(tp3),
                result=result,
                result_ts=r["result_ts"] or "",
                result_price=float(r["result_price"] or 0.0),
                actual_r_multiple=float(r["actual_r_multiple"] or 0.0),
                actual_pnl_dollars=float(r["actual_pnl_dollars"] or 0.0),
                trigger_signal=trigger,
                validator_pair=validator_pair,
            )
        )
    return out


def _pine_int_array(name: str, values: Iterable[int]) -> str:
    body = ", ".join(str(int(v)) for v in values)
    return f"var array<int> {name} = array.from({body})"


def _pine_float_array(name: str, values: Iterable[float]) -> str:
    # Pine v6 prints floats with full precision; we round to 8 dp to keep
    # the paste block readable without losing tick precision.
    body = ", ".join(f"{float(v):.8f}".rstrip("0").rstrip(".") or "0" for v in values)
    return f"var array<float> {name} = array.from({body})"


def _pine_string_array(name: str, values: Iterable[str]) -> str:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    body = ", ".join(f'"{esc(s)}"' for s in values)
    return f"var array<string> {name} = array.from({body})"


def format_pine_paste_block(
    trades: list[Trade],
    *,
    division: str,
    since_iso: str,
    until_iso: str,
    generated_at: str,
) -> str:
    """Render the paste block. Empty trades list → empty arrays + count=0.

    The Pine script declares each array with `var`, so re-paste is a
    one-shot reassignment per chart load (no per-bar work).
    """
    n = len(trades)
    win = sum(1 for t in trades if t.result == "win")
    loss = sum(1 for t in trades if t.result == "loss")
    expired = sum(1 for t in trades if t.result == "expired")
    open_ct = sum(1 for t in trades if t.result == "open")

    header = (
        "// === BEGIN GENERATED PASTE BLOCK ===\n"
        "// Generator: scripts/export_paper_trades_to_pinescript.py\n"
        f"// Generated: {generated_at}\n"
        f"// Division : {division}\n"
        f"// Window   : {since_iso} → {until_iso}\n"
        f"// Trades   : {n} ({win} win / {loss} loss / {expired} expired / {open_ct} open)\n"
    )
    footer = "// === END GENERATED PASTE BLOCK ==="

    if n == 0:
        body = (
            "var int g_count = 0\n"
            "var array<int> g_entry_ts = array.new<int>()\n"
            "var array<int> g_result_ts = array.new<int>()\n"
            "var array<float> g_entry_price = array.new<float>()\n"
            "var array<float> g_sl_price = array.new<float>()\n"
            "var array<float> g_tp1_price = array.new<float>()\n"
            "var array<float> g_tp2_price = array.new<float>()\n"
            "var array<float> g_tp3_price = array.new<float>()\n"
            "var array<float> g_result_price = array.new<float>()\n"
            "var array<float> g_r_multiple = array.new<float>()\n"
            "var array<float> g_pnl_dollars = array.new<float>()\n"
            "var array<string> g_side = array.new<string>()\n"
            "var array<string> g_result = array.new<string>()\n"
            "var array<string> g_validator_pair = array.new<string>()\n"
            "var array<string> g_order_id_short = array.new<string>()\n"
        )
        return f"{header}{body}{footer}\n"

    lines = [
        f"var int g_count = {n}",
        _pine_int_array("g_entry_ts", (iso_to_unix_ms(t.entry_ts) for t in trades)),
        _pine_int_array("g_result_ts", (iso_to_unix_ms(t.result_ts) for t in trades)),
        _pine_float_array("g_entry_price", (t.entry_price for t in trades)),
        _pine_float_array("g_sl_price", (t.sl_price for t in trades)),
        _pine_float_array("g_tp1_price", (t.tp1_price for t in trades)),
        _pine_float_array("g_tp2_price", (t.tp2_price for t in trades)),
        _pine_float_array("g_tp3_price", (t.tp3_price for t in trades)),
        _pine_float_array("g_result_price", (t.result_price for t in trades)),
        _pine_float_array("g_r_multiple", (t.actual_r_multiple for t in trades)),
        _pine_float_array("g_pnl_dollars", (t.actual_pnl_dollars for t in trades)),
        _pine_string_array("g_side", (t.side for t in trades)),
        _pine_string_array("g_result", (t.result for t in trades)),
        _pine_string_array("g_validator_pair", (t.validator_pair for t in trades)),
        _pine_string_array("g_order_id_short", (t.order_id[:8] for t in trades)),
    ]
    return header + "\n".join(lines) + "\n" + footer + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/trading_corp.db", help="SQLite DB path")
    p.add_argument(
        "--since",
        default="30d",
        help="Window start: '30d' / '7d' / '24h' or ISO ts (default: 30d)",
    )
    p.add_argument(
        "--until",
        default=None,
        help="Window end (ISO ts). Default: now.",
    )
    p.add_argument("--division", default="bitunix_futures")
    p.add_argument(
        "--out",
        default="scripts/pinescript/paper_trades_pine.txt",
        help="Output file for the Pine paste block",
    )
    p.add_argument(
        "--validator-window-seconds",
        type=int,
        default=600,
        help="How far before pt.ts to look for a matching pa_validation_decision (default 600s)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    since_dt = parse_since(args.since)
    since_iso = since_dt.isoformat()
    until_iso = args.until or datetime.now(timezone.utc).isoformat()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 2

    with sqlite3.connect(str(db_path)) as conn:
        trades = fetch_trades(
            conn,
            division=args.division,
            since_iso=since_iso,
            until_iso=until_iso,
            window_seconds=args.validator_window_seconds,
        )

    generated_at = datetime.now(timezone.utc).isoformat()
    paste_block = format_pine_paste_block(
        trades,
        division=args.division,
        since_iso=since_iso,
        until_iso=until_iso,
        generated_at=generated_at,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(paste_block, encoding="utf-8")
    print(f"Wrote {len(trades)} trades → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
