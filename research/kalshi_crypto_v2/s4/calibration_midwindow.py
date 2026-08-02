"""MID-WINDOW CALIBRATION STUDY (2026-08-02, on-disk, no pulls). READ-ONLY.

The overreaction-fade evidence base: is the Kalshi 15m contract-implied
probability, observed MID-window, calibrated to the realized outcome -- or does
it systematically OVERSHOOT after a move and fade back by settlement?

For every settled 15m window and every minute-into-window m, the contract's
implied P(YES) = the traded price (price_mean of the minute-m candle, in [0,1]).
The realized outcome y in {0,1} is the S1 settlement (RTI 60s-avg at close vs
the open floor_strike). We compare implied_p_m to the realized YES frequency,
CONDITIONED on:
  - time-into-window  (how many minutes have elapsed),
  - move size         (signed underlying % move open->minute m, from Binance 1m),
  - regime state      (pre-window trend: up / down / range from the prior 15m).
Flat rule carries over (settlement move < 0.05% = flat window, ~coin-flip).

Calibration gap = realized_YES_freq - mean(implied_p_m) within a cell.
  gap < 0 where implied_p is high  => the market OVERSTATES YES (overreaction on
                                      up-moves that fades);
  gap > 0 where implied_p is low   => understates YES (fade on down-moves).
A monotone gap-vs-move pattern (overshoot on both tails) is the overreaction-fade
signature. EVIDENCE ONLY -- no verdict; calibration is reported, never gates.

NO order/placement surface. NO DB writes.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_S4 = os.path.dirname(os.path.abspath(__file__))
_LAB = os.path.join(os.path.dirname(_S4), "lab")
LAB_DB = os.path.join(_LAB, "kcv2_lab.db")

ASSETS = ["BTC", "ETH", "SOL", "XRP"]

# minute-into-window buckets (15m window; minute 15 = settlement convergence, excl.)
TIME_BUCKETS = [(1, 3, "1-3m"), (4, 6, "4-6m"), (7, 9, "7-9m"), (10, 13, "10-13m")]
# signed underlying move (open -> minute m) buckets, tied to the flat-rule scale
MOVE_EDGES = [-1.0, -0.0010, -0.0003, 0.0003, 0.0010, 1.0]
MOVE_LABELS = ["<-0.10%", "-0.10..-0.03%", "flat +/-0.03%", "+0.03..+0.10%", ">+0.10%"]
REGIME_THR = 0.0010          # +/-0.10% prior-15m return splits trend vs range
FLAT_SETTLE_THR = 0.0005     # |settlement move| < 0.05% = flat window


def _ro(db: str = LAB_DB):
    c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _binance_close(conn, asset: str) -> dict:
    """ts_sec -> close (Binance 1m; ts_ms stored, keyed to second here)."""
    out = {}
    for r in conn.execute("SELECT ts_ms, close FROM lab_bars_binance WHERE asset=?", (asset,)):
        out[r["ts_ms"] // 1000] = r["close"]
    return out


def build_observations(asset: str, conn) -> pd.DataFrame:
    """One row per (window, minute-into-window) with implied_p, move, regime, y."""
    markets = conn.execute(
        "SELECT market_ticker, open_ts, close_ts, floor_strike, settlement_value, result "
        "FROM lab_kalshi_markets WHERE kind='15m' AND asset=? AND result IN ('yes','no') "
        "ORDER BY open_ts", (asset,)).fetchall()
    bcl = _binance_close(conn, asset)
    rows = []
    for mk in markets:
        tkr = mk["market_ticker"]
        ot, ct = mk["open_ts"], mk["close_ts"]
        strike, settle = mk["floor_strike"], mk["settlement_value"]
        if ot is None or ct is None or strike in (None, 0) or settle is None:
            continue
        y = 1 if mk["result"] == "yes" else 0
        settle_move = (settle - strike) / abs(strike)
        flat = 1 if abs(settle_move) < FLAT_SETTLE_THR else 0
        u_open = bcl.get(ot)
        u_pre = bcl.get(ot - 900)                       # underlying 15m before open
        if u_open is None or u_open == 0:
            continue
        if u_pre and u_pre != 0:
            pre_ret = (u_open - u_pre) / u_pre
            regime = ("up" if pre_ret > REGIME_THR else
                      "down" if pre_ret < -REGIME_THR else "range")
        else:
            regime = "range"
        # minute-m implied prob from the contract candles
        cands = conn.execute(
            "SELECT end_period_ts, price_mean FROM lab_kalshi_candles "
            "WHERE market_ticker=? ORDER BY end_period_ts", (tkr,)).fetchall()
        for c in cands:
            pm = c["price_mean"]
            if pm is None or not (0.0 < pm < 1.0):
                continue
            m = round((c["end_period_ts"] - ot) / 60.0)
            if m < 1 or m > 13:                          # skip open tick + settlement conv.
                continue
            u_m = bcl.get(ot + m * 60)
            move = ((u_m - u_open) / u_open) if (u_m is not None and u_open) else np.nan
            rows.append((asset, tkr, m, pm, move, regime, flat, y))
    df = pd.DataFrame(rows, columns=["asset", "ticker", "minute", "implied_p",
                                     "move", "regime", "flat", "y"])
    tb = pd.cut(df["minute"], bins=[0, 3, 6, 9, 13],
                labels=[t[2] for t in TIME_BUCKETS])
    df["time_bucket"] = tb
    df["move_bucket"] = pd.cut(df["move"], bins=MOVE_EDGES, labels=MOVE_LABELS)
    return df


def _cell(g: pd.DataFrame) -> dict:
    n = len(g)
    mp = g["implied_p"].mean()
    yf = g["y"].mean()
    gap = yf - mp
    se = (g["y"].std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan
    return {"n": n, "mean_implied": mp, "yes_freq": yf, "gap": gap, "gap_se": se}


def analyze(df: pd.DataFrame) -> dict:
    out = {}
    dfd = df[df["flat"] == 0]                             # directional windows (primary)
    # 1. calibration by time-into-window (directional)
    out["by_time"] = {tb: _cell(g) for tb, g in dfd.groupby("time_bucket", observed=True)}
    # 2. overreaction-fade: gap by (time x signed move), directional
    ov = {}
    for (tb, mb), g in dfd.dropna(subset=["move_bucket"]).groupby(
            ["time_bucket", "move_bucket"], observed=True):
        ov[(str(tb), str(mb))] = _cell(g)
    out["overreaction"] = ov
    # 3. by regime (directional, all minutes)
    out["by_regime"] = {rg: _cell(g) for rg, g in dfd.groupby("regime", observed=True)}
    # 4. flat vs directional (all windows)
    out["flat_split"] = {("flat" if f == 1 else "directional"): _cell(g)
                         for f, g in df.groupby("flat", observed=True)}
    out["n_obs"] = len(df)
    out["n_windows"] = df["ticker"].nunique()
    return out


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _g(c: dict) -> str:
    """gap +/- se (t) from a cell dict."""
    if not c or c.get("gap") is None or (isinstance(c.get("gap"), float) and np.isnan(c["gap"])):
        return "n/a"
    se = c.get("gap_se")
    if se and not np.isnan(se):
        return f"{c['gap']:+.4f}+/-{se:.4f} (t={c['gap']/se:+.1f})"
    return f"{c['gap']:+.4f}"


def _line(c: dict) -> str:
    return (f"{c['n']} | {c['mean_implied']:.4f} | {c['yes_freq']:.4f} | {_g(c)}")


def write_report(results: list[tuple], path: str) -> None:
    L = []
    L.append("# S4 Mid-Window Calibration — overreaction-fade evidence base")
    L.append("")
    L.append("**Date:** 2026-08-02  ")
    L.append("**Scope:** Kalshi 15m up/down binaries, BTC/ETH/SOL/XRP, FULL settled "
             "corpus (not just holdout).  ")
    L.append("**Standing:** read-only; on-disk (no pulls); lab DB only; evidence only "
             "— no verdict.")
    L.append("")
    L.append("Contract-implied P(YES) at minute m = the traded price (`price_mean`) of "
             "the minute-m candle. Realized y = S1 settlement (RTI 60s-avg vs open "
             "floor_strike). **Calibration gap = realized_YES_freq − mean(implied_p)**; "
             "gap<0 where implied is high ⇒ market OVERSTATES that side (overreaction "
             "that fades). Minutes 1-13 (open tick + settlement-convergence minute "
             "excluded). Move = signed underlying %move (Binance 1m) open→minute m. "
             "Directional windows only (|settlement move|≥0.05%) unless noted; t = "
             "gap/SE.")
    L.append("")
    for asset, a in results:
        L.append(f"## {asset}")
        L.append("")
        L.append(f"Observations (window×minute): {a['n_obs']} across "
                 f"{a['n_windows']} settled windows.")
        L.append("")
        L.append("### Calibration by time-into-window (directional)")
        L.append("")
        L.append("| Time bucket | n | mean implied_p | realized YES-freq | gap (t) |")
        L.append("|---|---|---|---|---|")
        for _lo, _hi, tb in TIME_BUCKETS:
            c = a["by_time"].get(tb)
            if c:
                L.append(f"| {tb} | {_line(c)} |")
        L.append("")
        L.append("### Overreaction-fade: gap by time × signed move (directional)")
        L.append("")
        L.append("| Time | Move bucket | n | mean implied_p | YES-freq | gap (t) |")
        L.append("|---|---|---|---|---|---|")
        for _lo, _hi, tb in TIME_BUCKETS:
            for mb in MOVE_LABELS:
                c = a["overreaction"].get((tb, mb))
                if c and c["n"] >= 30:
                    L.append(f"| {tb} | {mb} | {_line(c)} |")
        L.append("")
        L.append("### By regime (prior-15m trend) and flat split")
        L.append("")
        L.append("| Cut | n | mean implied_p | YES-freq | gap (t) |")
        L.append("|---|---|---|---|---|")
        for rg in ("up", "down", "range"):
            c = a["by_regime"].get(rg)
            if c:
                L.append(f"| regime={rg} | {_line(c)} |")
        for fl in ("directional", "flat"):
            c = a["flat_split"].get(fl)
            if c:
                L.append(f"| {fl} windows | {_line(c)} |")
        L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- **Overreaction-fade** shows as: at the extreme move buckets, gap<0 on "
             "big UP moves (implied overstated YES, realized fades) AND gap>0 on big "
             "DOWN moves (implied overstated NO). A symmetric, time-decaying gap on the "
             "tails is the fade signal; a ~0 gap everywhere means the mid-window price "
             "is calibrated (no fade to harvest).")
    L.append("- **|t|<~2 ⇒ indistinguishable from calibrated.** Gaps are per "
             "observation (windows contribute multiple correlated minutes), so treat "
             "SEs as OPTIMISTIC (serial correlation not adjusted) — a follow-up would "
             "cluster by window.")
    L.append("- Flat windows (settlement move <0.05%) are ~coin-flip by construction; "
             "the directional split isolates the real moves. Regime conditions whether "
             "any fade is trend- or range-specific.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def main() -> int:
    print("=" * 70)
    print("  MID-WINDOW CALIBRATION STUDY — kalshi_crypto_v2 (on-disk, no pulls)")
    print("=" * 70)
    args = sys.argv[1:]
    assets = ASSETS
    if "--assets" in args:
        assets = [a.strip().upper() for a in args[args.index("--assets") + 1].split(",")]
    conn = _ro()
    results = []
    try:
        for asset in assets:
            print(f"\n== {asset} ==", flush=True)
            df = build_observations(asset, conn)
            a = analyze(df)
            results.append((asset, a))
            bt = a["by_time"]
            for _lo, _hi, tb in TIME_BUCKETS:
                c = bt.get(tb)
                if c:
                    print(f"  {tb}: n={c['n']} implied={c['mean_implied']:.3f} "
                          f"yes={c['yes_freq']:.3f} gap={c['gap']:+.4f}", flush=True)
    finally:
        conn.close()
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_midwindow_calibration.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

