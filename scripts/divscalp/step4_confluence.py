"""Step 4: S/R-confluence on the HONEST (post-confirmation, k=2) divergence entry.

The operator's real edge is structure (order blocks / S&R / supply-demand). Mechanical honest
entry (k=2) is train/validate-negative; does requiring the divergence to fire AT a prior S/R
level rescue it? S/R proxy: a prior swing extreme formed GAP..LOOKBACK bars ago; require entry
within THRESH% of it (long: bouncing off prior support; short: rejecting prior resistance).
ONE config (no sweep -> avoid p-hacking a failing base). Report unfiltered vs filtered,
train/validate/lockbox. Honest read; a rescue on tiny N is flagged as noise-risk, not a win.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS, K, BUF, R, KDELAY = 40, 5, 0.0005, 2.0, 2     # honest post-confirmation entry
LOOKBACK, GAP, THRESH = 200, 20, 0.003                  # prior S/R: 20..200 bars ago, within 0.3%
SIGS = [("bull_divergence", "buy"), ("bear_divergence", "sell")]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "bull_divergence", "bear_divergence"]
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    O, H, L, C = ci["open"], ci["high"], ci["low"], ci["close"]

    def near_sr(i, side, entry):
        a, b = max(0, i - LOOKBACK), max(0, i - GAP)
        if b <= a:
            return False
        if side == "buy":
            lvl = min(float(rows[m][L]) for m in range(a, b))      # prior support
        else:
            lvl = max(float(rows[m][H]) for m in range(a, b))      # prior resistance
        return abs(entry - lvl) / entry < THRESH

    def trade(i, side, require_sr):
        seg = rows[max(0, i - K + 1): i + 1]
        lo_k = min(float(x[L]) for x in seg); hi_k = max(float(x[H]) for x in seg)
        ei = i + KDELAY
        if ei + 1 >= len(rows):
            return None
        entry = float(rows[ei][O])
        if require_sr and not near_sr(i, side, entry):
            return None
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

    def ev(sig, side, require_sr, lo, hi):
        col = ci[sig]; nets, win = [], 0
        for i, r in enumerate(rows):
            if r[ci["ts"]] < lo or r[ci["ts"]] >= hi:
                continue
            if not r[col] or float(r[col]) == 0.0:
                continue
            t = trade(i, side, require_sr)
            if t:
                nets.append(t[1]); win += int(t[0] == "win")
        if not nets:
            return None
        return len(nets), round(100*win/len(nets), 1), round(statistics.fmean(nets), 4)

    print(f"=== honest k={KDELAY} entry, R={R}: unfiltered vs prior-S/R confluence ({GAP}-{LOOKBACK} bars, <{THRESH*100}%) ===")
    for sig, side in SIGS:
        print(f"\n{sig} ({side}):")
        for rs, lbl in ((False, "unfiltered"), (True, "near-S/R")):
            tr = ev(sig, side, rs, 0, TRAIN_END)
            va = ev(sig, side, rs, TRAIN_END, VAL_END)
            lb = ev(sig, side, rs, VAL_END, 9e12)
            f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
            print(f"  {lbl:<11} TRAIN {f(tr):<26} VALIDATE {f(va):<26} LOCKBOX {f(lb)}")


if __name__ == "__main__":
    main()
