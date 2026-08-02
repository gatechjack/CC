"""T5 BASIS CHARACTERIZATION (2026-08-02, on-disk). READ-ONLY. Small scope.

Every 15m study measured MOVE on Binance but the contract SETTLES on CF-Benchmarks
RTI. This quantifies the resulting label noise so every study in the record
carries a MEASURED (not assumed) proxy-error number: the fraction of settled
windows where the Binance direction over the window disagrees with the RTI
settlement direction, by move size and flat bucket.

Per settled 15m window:
  RTI settle direction = y (1=up/yes, 0=down/no) from settlement_value vs
    floor_strike (strike = open 60s-avg RTI; settle = close 60s-avg RTI; the S1
    rule). RTI move = (settle - strike)/|strike|.
  Binance direction = sign of the Binance close move over [open_ts, close_ts]
    (1m bar closes at the window boundaries; a 60s-avg refinement would be
    marginally cleaner and is noted).
  DISAGREE = Binance direction != RTI direction.

Bucketed by |Binance move| and split flat vs directional (|RTI move| < 0.05%).
The headline is the disagreement rate on DIRECTIONAL windows -- the label noise
the proxy injects into every directional study. Evidence only -- no verdict.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_S4 = os.path.dirname(os.path.abspath(__file__))
LAB_DB = os.path.join(os.path.dirname(_S4), "lab", "kcv2_lab.db")
ASSETS = ["BTC", "ETH", "SOL", "XRP"]

# |Binance move over the window| buckets (fractional)
MOVE_EDGES = [0.0, 0.0002, 0.0005, 0.0010, 0.0020, 1.0]
MOVE_LABELS = ["<0.02%", "0.02-0.05%", "0.05-0.10%", "0.10-0.20%", ">0.20%"]
FLAT_THR = 0.0005          # |RTI settle move| < 0.05% = flat window


def _ro(db: str = LAB_DB):
    c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _binance_close(conn, asset: str) -> dict:
    out = {}
    for r in conn.execute("SELECT ts_ms, close FROM lab_bars_binance WHERE asset=?", (asset,)):
        out[r["ts_ms"] // 1000] = r["close"]
    return out


def build(asset: str, conn) -> pd.DataFrame:
    mkts = conn.execute(
        "SELECT open_ts, close_ts, floor_strike, settlement_value, result "
        "FROM lab_kalshi_markets WHERE kind='15m' AND asset=? AND result IN ('yes','no')",
        (asset,)).fetchall()
    bcl = _binance_close(conn, asset)
    rows = []
    for mk in mkts:
        ot, ct, strike, settle = mk["open_ts"], mk["close_ts"], mk["floor_strike"], mk["settlement_value"]
        if ot is None or ct is None or strike in (None, 0) or settle is None:
            continue
        b_open, b_close = bcl.get(ot), bcl.get(ct)
        if not b_open or b_close is None:
            continue
        y = 1 if mk["result"] == "yes" else 0
        rti_move = (settle - strike) / abs(strike)
        b_move = (b_close - b_open) / b_open
        b_dir = 1 if b_move > 0 else 0                    # up / down
        disagree = int(b_dir != y)
        rows.append((asset, b_move, abs(b_move), rti_move, y, b_dir, disagree,
                     int(abs(rti_move) < FLAT_THR)))
    df = pd.DataFrame(rows, columns=["asset", "b_move", "abs_b_move", "rti_move",
                                     "y", "b_dir", "disagree", "flat"])
    df["move_bucket"] = pd.cut(df["abs_b_move"], bins=MOVE_EDGES, labels=MOVE_LABELS)
    return df


def analyze(df: pd.DataFrame) -> dict:
    n = len(df)
    out = {"n": n, "overall": df["disagree"].mean()}
    out["by_move"] = {mb: (len(g), g["disagree"].mean())
                      for mb, g in df.groupby("move_bucket", observed=True)}
    dfd = df[df["flat"] == 0]
    dff = df[df["flat"] == 1]
    out["directional"] = (len(dfd), dfd["disagree"].mean() if len(dfd) else np.nan)
    out["flat"] = (len(dff), dff["disagree"].mean() if len(dff) else np.nan)
    return out


def write_report(results: list[tuple], path: str) -> None:
    L = []
    L.append("# T5 Basis Characterization — Binance-move vs RTI-settle label noise")
    L.append("")
    L.append("**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; "
             "evidence only — no verdict. Small-scope accounting.")
    L.append("")
    L.append("Disagreement = the Binance close direction over the 15m window "
             "(open→close, 1m bars) differs from the RTI settlement direction (y). "
             "The RTI settle is a 60s-avg vs the open 60s-avg strike; the Binance side "
             "here is point close-to-close (a 60s-avg version would be marginally "
             "cleaner). **The directional-window disagreement rate is the label noise "
             "every directional 15m study carries.**")
    L.append("")
    L.append("| Asset | n | overall disagree | **directional (|RTI move|>=0.05%)** "
             "| flat (<0.05%) |")
    L.append("|---|---|---|---|---|")
    for asset, a in results:
        dN, dR = a["directional"]
        fN, fR = a["flat"]
        L.append(f"| {asset} | {a['n']} | {a['overall']*100:.1f}% | "
                 f"**{dR*100:.1f}%** (n={dN}) | {fR*100:.1f}% (n={fN}) |")
    L.append("")
    L.append("### Disagreement by |Binance move| bucket")
    L.append("")
    L.append("| Asset | " + " | ".join(MOVE_LABELS) + " |")
    L.append("|---|" + "|".join(["---"] * len(MOVE_LABELS)) + "|")
    for asset, a in results:
        cells = []
        for mb in MOVE_LABELS:
            nm = a["by_move"].get(mb)
            cells.append(f"{nm[1]*100:.1f}% (n={nm[0]})" if nm else "n/a")
        L.append(f"| {asset} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- The **directional** column is the operative label noise: on windows "
             "with a real move (the ones every directional study conditions on), this "
             "fraction settled the OPPOSITE way on RTI vs Binance. Prior 15m results "
             "should be read as carrying ~this much proxy error in their labels.")
    L.append("- Disagreement should be HIGH in the flat / small-move buckets (near "
             "coin-flip, the proxy sign is noise) and DECAY as |move| grows (a large "
             "Binance move rarely settles the other way on RTI). A large-move "
             "disagreement that stays high would flag a real Binance↔RTI divergence.")
    L.append("- This measures direction agreement only; it does not quantify magnitude "
             "basis (the two indices can agree on sign but differ on the 60s-avg "
             "level). Sufficient for label-noise accounting on binary up/down studies.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def main() -> int:
    print("=" * 70)
    print("  T5 BASIS CHARACTERIZATION — kalshi_crypto_v2 (on-disk)")
    print("=" * 70)
    conn = _ro()
    results = []
    try:
        for asset in ASSETS:
            df = build(asset, conn)
            a = analyze(df)
            results.append((asset, a))
            dN, dR = a["directional"]
            print(f"  {asset}: n={a['n']} overall_disagree={a['overall']*100:.1f}% "
                  f"directional={dR*100:.1f}% (n={dN})", flush=True)
    finally:
        conn.close()
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_T5_basis.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
