"""Phase-1 T3a: run the LIFTED Bitunix SFP detectors AS-IS over our own bars.

Faithful lift = direct import of trading_corp.agents.strategies.bitunix_sfp
(default constants, no tuning). Produces, per asset x mode x BOS-timeframe:
  - the ARMED -> CONFIRMED/INVALIDATED/TIMED_OUT funnel (SFP fires vs. entries)
  - the list of BOS-confirmed LONG (UP) entry signals with entry timestamps.

The detector is long-only, so every signal predicts an UP move starting at the
entry bar's open. entry_ts_ms = bos_bar_ts_ms + tf_ms (enter at next bar open),
which is what aligns to a Kalshi up/down window (Kalshi side wired later).

READ-ONLY: reads the local CSV export; imports the detector; writes only a
signals CSV under research/. No prod, no orders.
Usage:  run_capped python research/kalshi_crypto_v2/run_signals_retro.py
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# Make the repo root importable when run as a plain script (pytest does this via rootdir).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_CONSIDERABLE,
    MODE_REAL,
    SfpBar,
    SfpDetector,
    SfpModeBDetector,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_IN = os.path.join(HERE, "bitunix_bars_export.csv")
CSV_OUT = os.path.join(HERE, "signals_retro.csv")
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TF_MS = {"3m": 180_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_bars() -> dict[tuple[str, str], list[SfpBar]]:
    bars: dict[tuple[str, str], list[SfpBar]] = defaultdict(list)
    with open(CSV_IN, newline="") as f:
        for row in csv.DictReader(f):
            bars[(row["symbol"], row["timeframe"])].append(SfpBar(
                ts_ms=int(row["ts_ms"]), open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"])))
    for k in bars:
        bars[k].sort(key=lambda b: b.ts_ms)
    return bars


def funnel(transitions) -> Counter:
    c = Counter()
    for t in transitions:
        c[t.status] += 1
    return c


def main() -> int:
    if not os.path.exists(CSV_IN):
        print(f"MISSING export CSV: {CSV_IN}\nRun kc2_pull.ps1 first.")
        return 1
    bars = load_bars()

    print("=== bar depth (loaded from export) ===")
    hdr = f"{'asset':9} {'tf':4} {'rows':>7} {'earliest(UTC)':16} {'latest(UTC)':16} {'days':>6}"
    print(hdr + "\n" + "-" * len(hdr))
    for a in ASSETS:
        for tf in ("15m", "3m", "1h", "4h", "1d"):
            b = bars.get((a, tf))
            if not b:
                continue
            days = (b[-1].ts_ms - b[0].ts_ms) / 86_400_000
            print(f"{a:9} {tf:4} {len(b):>7,} {_iso(b[0].ts_ms):16} {_iso(b[-1].ts_ms):16} {days:>6.1f}")

    out_rows: list[dict] = []
    print("\n=== SFP funnel: ARMED(fires) -> CONFIRMED(entries) / INVALID / TIMEOUT ===")
    fh = f"{'asset':9} {'mode':12} {'bos':4} {'armed':>6} {'confd':>6} {'inval':>6} {'tmout':>6}"
    print(fh + "\n" + "-" * len(fh))

    for a in ASSETS:
        b15 = bars.get((a, "15m"), [])
        b3 = bars.get((a, "3m"), [])
        # Mode A (same-TF 15m BOS): REAL + CONSIDERABLE
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            det = SfpDetector(mode=mode)
            sigs = det.warm_start(b15)
            c = funnel(det.drain_transitions())
            print(f"{a:9} {mode:12} {'15m':4} {c['ARMED']:>6} {c['CONFIRMED']:>6} "
                  f"{c['INVALIDATED']:>6} {c['TIMED_OUT']:>6}")
            for s in sigs:
                entry_ts = s.bos_bar_ts_ms + TF_MS["15m"]
                out_rows.append(dict(asset=a, sfp_mode=s.sfp_mode, bos_tf="15m",
                    entry_ts_ms=entry_ts, entry_utc=_iso(entry_ts), swept_swing_level=s.swept_swing_level,
                    swept_low=s.swept_low, bos_ref_high=s.bos_ref_high, bos_bar_ts_ms=s.bos_bar_ts_ms))
        # Mode B (15m fire -> 3m BOS): REAL + CONSIDERABLE (default pivot50)
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            det = SfpModeBDetector(mode=mode)
            sigs = det.warm_start(b15, b3)
            c = funnel(det.drain_transitions())
            print(f"{a:9} {mode:12} {'3m':4} {c['ARMED']:>6} {c['CONFIRMED']:>6} "
                  f"{c['INVALIDATED']:>6} {c['TIMED_OUT']:>6}")
            for s in sigs:
                entry_ts = s.bos_bar_ts_ms + TF_MS["3m"]
                out_rows.append(dict(asset=a, sfp_mode=s.sfp_mode, bos_tf="3m",
                    entry_ts_ms=entry_ts, entry_utc=_iso(entry_ts), swept_swing_level=s.swept_swing_level,
                    swept_low=s.swept_low, bos_ref_high=s.bos_ref_high, bos_bar_ts_ms=s.bos_bar_ts_ms))

    out_rows.sort(key=lambda r: (r["asset"], r["entry_ts_ms"]))
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    print(f"\nTotal BOS-confirmed UP signals: {len(out_rows)}  ->  {CSV_OUT}")
    by = Counter((r["asset"], r["bos_tf"]) for r in out_rows)
    print("by asset x bos_tf:", dict(sorted(by.items())))
    if out_rows:
        print(f"signal span: {out_rows[0]['entry_utc']}  ..  {max(r['entry_utc'] for r in out_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
