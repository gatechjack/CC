"""Native BitUnix signal-ledger -> redeem-cap engine prod-cache alert JSON.

Converts a read-only export of prod `bitunix_signal_ledger` into the EXACT JSON
shape the redeem-cap engine consumes via `--prod-alerts-cache`: a JSON ARRAY of
  {"ts": <tz-aware ISO>, "signal": <name>, "tf": <tf>, ...}.

The engine (scripts/backtest_bitunix_confluence.py:_load_bybit_hybrid_inputs) reads
only `ts`, `signal`, and `tf` per record:
  - ts  -> datetime.fromisoformat(r["ts"])   MUST be tz-aware (it is compared against
           tz-aware start/end; a naive ts raises TypeError). We normalize trailing
           'Z' to '+00:00' and force UTC so fromisoformat succeeds on every Python.
  - signal -> matched against the bitunix_futures factor vocab (case-insensitive,
           with _bull/_bear suffix stripping). Unknown signals are silently dropped
           by the engine -- we pass names through verbatim (the ledger already uses
           the same vocab: mc_a_red_diamond, otter_buy, cvd_bear_flip, spoon_bull, ...).
  - tf  -> r.get("tf"); "3m"/"15m"/"30m" count toward score.
`actor` and `symbol` are emitted for fidelity with the existing Bybit prod-cache
files but are IGNORED by the engine.

Input CSV columns (header required): ts,signal[,source,tf]
  produce via scripts/native_etl/extract_bitunix_signal_ledger.sql (read-only).

Usage:
  python scripts/export_bitunix_alerts.py ledger.csv --out cache_alerts_bitunix_native.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SYMBOL = "BTC/USDT.P"  # single-symbol prod; the ledger has no symbol column


def _norm_ts(raw: str) -> str:
    """Parse an ISO ts (accepts trailing 'Z') -> tz-aware UTC isoformat ('+00:00')."""
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.isoformat()


def convert(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        out.append({
            "ts": _norm_ts(r["ts"]),
            "actor": (r.get("source") or "").strip() or None,
            "signal": (r["signal"] or "").strip(),
            "symbol": SYMBOL,
            "tf": (r.get("tf") or "").strip() or None,
        })
    out.sort(key=lambda a: a["ts"])
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("csv", help="signal-ledger CSV (ts,signal[,source,tf])")
    p.add_argument("--out", required=True, help="output alert-JSON path")
    args = p.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"!! NOT FOUND: {csv_path}", file=sys.stderr)
        return 1

    with csv_path.open(newline="") as f:
        rdr = csv.DictReader(f)
        missing = {"ts", "signal"} - set(rdr.fieldnames or [])
        if missing:
            print(f"!! missing columns {sorted(missing)}; got {rdr.fieldnames}", file=sys.stderr)
            return 1
        rows = list(rdr)

    alerts = convert(rows)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(alerts), encoding="utf-8")

    sig = Counter(a["signal"] for a in alerts)
    print(f"  [ok] wrote {len(alerts)} alerts -> {out_path}")
    if alerts:
        print(f"       span {alerts[0]['ts']} -> {alerts[-1]['ts']}")
        top = ", ".join(f"{k}={v}" for k, v in sig.most_common(8))
        print(f"       distinct signals={len(sig)}; top: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
