"""Step 2 (DECISIVE): is the divergence-scalp edge real-time or a pivot REPAINT look-ahead?

A TradingView regular divergence uses ta.pivot(lbL,lbR): the pivot is only CONFIRMED lbR bars
after it prints, and the label is drawn back AT the pivot bar. So the corpus column may mark the
PIVOT bar while the "once per bar close" alert actually fires lbR bars LATER. If so, entering at
the marked bar is look-ahead; the real live entry is i+lbR.

Two tests, tight-stop operator model (stop = local extreme over [i-K+1..i], anchored to the pivot,
NO future bars):
  (A) PIVOT-STRUCTURE: fraction of fires where the signal bar's extreme is the min/max over a
      symmetric +/-W window (i.e. a future-confirmed pivot => repaint signature).
  (B) ENTRY-DELAY net: enter at i+k (next_open) for k=0,1,2,3,5,8 with the pivot-anchored stop;
      if net stays positive out to a realistic lbR (~3-5), the edge is tradeable at the live alert;
      if it collapses by k>=2-3, the k=0 result was repaint look-ahead. TRAIN+VALIDATE (lockbox reserved).
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS, K, BUF = 40, 5, 0.0005
RS = [1.5, 2.0, 3.0]
SIGS = [("bull_divergence", "buy"), ("bear_divergence", "sell")]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "bull_divergence", "bear_divergence"]
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    O, H, L, C = ci["open"], ci["high"], ci["low"], ci["close"]

    # (A) pivot-structure: is the signal bar a future-confirmed extreme?
    print("=== (A) PIVOT-STRUCTURE (signal bar is the extreme over +/-W) — repaint signature ===")
    for sig, side in SIGS:
        col = ci[sig]
        for W in (3, 5):
            tot = conf = 0
            for i, r in enumerate(rows):
                if r[ci["ts"]] >= VAL_END:
                    continue
                if not r[col] or float(r[col]) == 0.0:
                    continue
                if i - W < 0 or i + W >= len(rows):
                    continue
                tot += 1
                if side == "buy":
                    if float(rows[i][L]) == min(float(rows[m][L]) for m in range(i - W, i + W + 1)):
                        conf += 1
                else:
                    if float(rows[i][H]) == max(float(rows[m][H]) for m in range(i - W, i + W + 1)):
                        conf += 1
            print(f"  {sig:<18} W=+/-{W}: {conf}/{tot} = {100*conf/max(1,tot):.1f}% are future-confirmed pivots")

    def trade(i, side, R, k):
        seg = rows[max(0, i - K + 1): i + 1]
        lo_k = min(float(b[L]) for b in seg); hi_k = max(float(b[H]) for b in seg)
        ei = i + k
        if ei + 1 >= len(rows):
            return None
        entry = float(rows[ei][O]) if k > 0 else float(rows[i][C])
        if side == "buy":
            stop = lo_k - BUF * entry; risk = entry - stop
        else:
            stop = hi_k + BUF * entry; risk = stop - entry
        if risk <= 0:
            return None
        tp = entry + R * risk if side == "buy" else entry - R * risk
        sp = risk / entry
        out, g = "open", None
        for j in range(ei + 1, min(len(rows), ei + 1 + MAXBARS)):
            hi, lo = float(rows[j][H]), float(rows[j][L])
            sl = (lo <= stop) if side == "buy" else (hi >= stop)
            tph = (hi >= tp) if side == "buy" else (lo <= tp)
            if sl:
                out, g = "loss", -1.0; break
            if tph:
                out, g = "win", R; break
        if g is None:
            last = float(rows[min(len(rows) - 1, ei + MAXBARS)][C])
            g = ((last - entry) if side == "buy" else (entry - last)) / risk
        return out, g - (ENTRY_FEE + (MK if out == "win" else TK) + SLIP2) / sp

    def ev(sig, side, R, k, lo, hi):
        col = ci[sig]; nets, win = [], 0
        for i, r in enumerate(rows):
            if r[ci["ts"]] < lo or r[ci["ts"]] >= hi:
                continue
            if not r[col] or float(r[col]) == 0.0:
                continue
            t = trade(i, side, R, k)
            if t:
                nets.append(t[1]); win += int(t[0] == "win")
        if not nets:
            return None
        return len(nets), round(100*win/len(nets), 1), round(statistics.fmean(nets), 4)

    print("\n=== (B) ENTRY-DELAY net (K=5, buf=0.05%, pivot-anchored stop) ===")
    for sig, side in SIGS:
        print(f"\n{sig} ({side}):")
        print(f"{'k':<4}{'R':<5}{'TRAIN n/win%/NET':<26}{'VALIDATE n/win%/NET'}")
        for R in RS:
            for k in (0, 1, 2, 3, 5, 8):
                tr = ev(sig, side, R, k, 0, TRAIN_END)
                va = ev(sig, side, R, k, TRAIN_END, VAL_END)
                f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
                print(f"{k:<4}{R:<5}{f(tr):<26}{f(va)}")
            print()


if __name__ == "__main__":
    main()
