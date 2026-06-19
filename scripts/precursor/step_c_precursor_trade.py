"""Step C: empirically seal the precursor null — trade the best REAL-TIME (non-repainting)
precursors with the operator model and show they don't clear fees.

Step A: no precursor reliably leads the div (best precision ~5% vs 2% base = 95%+ false positives,
unstable on validate; the higher-precision Cypher ones are divergences that repaint -> Step B drops).
Here we ENTER on each surviving real-time precursor (anticipating the div), operator model: tight
local-extreme stop + scalp R=2, CORRECTED fees, train/validate/LOCKBOX. If net-negative (as the
base-rate math predicts -> ~random scalp), the precursor idea is a confirmed null on this data.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS, K, BUF, R = 40, 5, 0.0005, 2.0
# real-time, non-Cypher precursors -> trade direction toward the anticipated div
PRECURSORS = [("ema_dn", "buy"), ("macd_dn", "buy"), ("rsi_exit_os", "buy"),
              ("rsi_exit_ob", "sell"), ("macd_up", "sell"), ("ema_up", "sell")]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "rsi", "histogram", "ema_8"]
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    O, H, L, C = ci["open"], ci["high"], ci["low"], ci["close"]
    N = len(rows)

    def fn(i, c):
        v = rows[i][ci[c]]
        return float(v) if v is not None else None

    fired = {p: set() for p, _ in PRECURSORS}
    for i in range(1, N):
        r0, r1 = fn(i - 1, "rsi"), fn(i, "rsi")
        if None not in (r0, r1):
            if r0 < 30 <= r1: fired["rsi_exit_os"].add(i)
            if r0 > 70 >= r1: fired["rsi_exit_ob"].add(i)
        h0, h1 = fn(i - 1, "histogram"), fn(i, "histogram")
        if None not in (h0, h1):
            if h0 < 0 <= h1: fired["macd_up"].add(i)
            if h0 > 0 >= h1: fired["macd_dn"].add(i)
        c0, c1, e0, e1 = fn(i - 1, "close"), fn(i, "close"), fn(i - 1, "ema_8"), fn(i, "ema_8")
        if None not in (c0, c1, e0, e1):
            if c0 < e0 and c1 >= e1: fired["ema_up"].add(i)
            if c0 > e0 and c1 <= e1: fired["ema_dn"].add(i)

    def trade(i, side):
        seg = rows[max(0, i - K + 1): i + 1]
        lo_k = min(float(b[L]) for b in seg); hi_k = max(float(b[H]) for b in seg)
        ei = i + 1
        if ei + 1 >= N:
            return None
        entry = float(rows[ei][O])
        if side == "buy":
            stop = lo_k - BUF * entry; risk = entry - stop
        else:
            stop = hi_k + BUF * entry; risk = stop - entry
        if risk <= 0:
            return None
        tp = entry + R * risk if side == "buy" else entry - R * risk
        sp = risk / entry
        out, g = "open", None
        for j in range(ei + 1, min(N, ei + 1 + MAXBARS)):
            hi, lo = float(rows[j][H]), float(rows[j][L])
            sl = (lo <= stop) if side == "buy" else (hi >= stop)
            tph = (hi >= tp) if side == "buy" else (lo <= tp)
            if sl:
                out, g = "loss", -1.0; break
            if tph:
                out, g = "win", R; break
        if g is None:
            last = float(rows[min(N - 1, ei + MAXBARS)][C])
            g = ((last - entry) if side == "buy" else (entry - last)) / risk
        return out, g - (ENTRY_FEE + (MK if out == "win" else TK) + SLIP2) / sp

    def ev(p, side, lo, hi):
        nets, win = [], 0
        for i in fired[p]:
            if not (lo <= rows[i][ci["ts"]] < hi):
                continue
            t = trade(i, side)
            if t:
                nets.append(t[1]); win += int(t[0] == "win")
        if not nets:
            return None
        return len(nets), round(100 * win / len(nets), 1), round(statistics.fmean(nets), 4)

    print("Step C — trade the real-time precursor (operator model: tight stop, R=2, corrected fees)")
    print(f"{'precursor':<14}{'side':<5}{'TRAIN n/win/NET':<26}{'VALIDATE n/win/NET':<26}{'LOCKBOX n/win/NET'}")
    for p, side in PRECURSORS:
        tr = ev(p, side, 0, TRAIN_END); va = ev(p, side, TRAIN_END, VAL_END); lb = ev(p, side, VAL_END, 9e12)
        f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
        print(f"{p:<14}{side:<5}{f(tr):<26}{f(va):<26}{f(lb)}")


if __name__ == "__main__":
    main()
