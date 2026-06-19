"""Step E: is the SFP+divergence-confluence edge (Step D) REAL or a divergence-repaint artifact?

Step D: SFP + 'divergence fired in prior 4 bars' is net-positive train/validate/LOCKBOX, win 58-83%.
But the corpus *_divergence column REPAINTS (proven earlier): it marks the pivot in hindsight; the
live alert fires ~2-3 bars later. So 'divergence in [i-4, i]' may use info NOT known when SFP bar i
closes. Honest rule: a div marked at bar m is KNOWN at bar i only if m + LAG <= i. Slide a fixed-width
confirmed-and-recent window [i-(LAG+WIN-1), i-LAG] back by LAG: LAG=0 reproduces Step D; LAG>=3 is
honest. Real 'confirmed-div then sweep' survives LAG=3; pure repaint collapses.

Also: (A) age-distribution of the nearest qualifying div vs the SFP bar (how much sits in the
unconfirmed 0-2 zone), (B) entry-delay k re-check on the headline config.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS, WIN = 40, 5
LAGS = [0, 2, 3, 4]
# headline divergence configs from Step D (good N): (side, L, buf, R)
CFGS = [("buy", 10, 0.0010, 2.0), ("buy", 20, 0.0010, 2.0), ("buy", 20, 0.0010, 1.5),
        ("sell", 10, 0.0010, 2.0), ("sell", 20, 0.0010, 1.5)]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "bull_divergence", "bear_divergence"]
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    ts = [r[ci["ts"]] for r in rows]

    def fired(i, sig):
        v = rows[i][ci[sig]]
        return v is not None and float(v) != 0.0

    def div_in(i, sig, lag, win):
        hi_j, lo_j = i - lag, i - lag - win + 1
        for j in range(max(0, lo_j), hi_j + 1):
            if fired(j, sig):
                return True
        return False

    def sfp(i, L):
        lo, hi, cl = float(rows[i][ci["low"]]), float(rows[i][ci["high"]]), float(rows[i][ci["close"]])
        plo = min(float(rows[m][ci["low"]]) for m in range(i - L, i))
        phi = max(float(rows[m][ci["high"]]) for m in range(i - L, i))
        if lo < plo and cl > plo:
            return "buy"
        if hi > phi and cl < phi:
            return "sell"
        return None

    def trade(i, side, buf, R, k):
        ei = i + k
        if ei + 1 >= len(rows):
            return None
        lo, hi = float(rows[i][ci["low"]]), float(rows[i][ci["high"]])
        entry = float(rows[ei][ci["close"]]) if k == 0 else float(rows[ei][ci["open"]])
        if side == "buy":
            stop = lo - buf * entry; risk = entry - stop
        else:
            stop = hi + buf * entry; risk = stop - entry
        if risk <= 0:
            return None
        tp = entry + R * risk if side == "buy" else entry - R * risk
        sp = risk / entry
        out, g = "open", None
        for j in range(ei + 1, min(len(rows), ei + 1 + MAXBARS)):
            h, l = float(rows[j][ci["high"]]), float(rows[j][ci["low"]])
            sl = (l <= stop) if side == "buy" else (h >= stop)
            tph = (h >= tp) if side == "buy" else (l <= tp)
            if sl:
                out, g = "loss", -1.0; break
            if tph:
                out, g = "win", R; break
        if g is None:
            last = float(rows[min(len(rows) - 1, ei + MAXBARS)][ci["close"]])
            g = ((last - entry) if side == "buy" else (entry - last)) / risk
        return out, g - (ENTRY_FEE + (MK if out == "win" else TK) + SLIP2) / sp

    def ev(L, buf, R, sig, side, lag, lo, hi, k=0):
        nets, win = [], 0
        for i in range(L + lag + WIN, len(rows)):
            if not (lo <= ts[i] < hi):
                continue
            if sfp(i, L) != side:
                continue
            if not div_in(i, sig, lag, WIN):
                continue
            t = trade(i, side, buf, R, k)
            if t:
                nets.append(t[1]); win += int(t[0] == "win")
        if not nets:
            return None
        return len(nets), round(100 * win / len(nets), 1), round(statistics.fmean(nets), 4)

    f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"

    print("=== Step E.1: divergence-confluence LAG sweep (WIN=5). LAG0=Step D (repaint), LAG>=3=honest ===")
    print("   real 'confirmed-div then sweep' edge SURVIVES LAG>=3; repaint COLLAPSES\n")
    for (side, L, buf, R) in CFGS:
        sig = "bull_divergence" if side == "buy" else "bear_divergence"
        print(f"-- {side} L{L} buf{buf*100}% R{R} {sig} --")
        for lag in LAGS:
            tr = ev(L, buf, R, sig, side, lag, 0, TRAIN_END)
            va = ev(L, buf, R, sig, side, lag, TRAIN_END, VAL_END)
            lb = ev(L, buf, R, sig, side, lag, VAL_END, 9e12)
            print(f"   LAG{lag}  TRAIN {f(tr):<26} VALIDATE {f(va):<26} LOCKBOX {f(lb)}")
        print()

    print("=== Step E.2: age of NEAREST qualifying div vs SFP bar (window i-8..i, full corpus) ===")
    print("   ages 0/1/2 = UNCONFIRMED at SFP close (repaint zone); >=3 = confirmed-and-known\n")
    for side in ("buy", "sell"):
        sig = "bull_divergence" if side == "buy" else "bear_divergence"
        ages = {a: 0 for a in range(9)}
        tot = 0
        for i in range(28, len(rows)):
            if sfp(i, 20) != side:
                continue
            near = None
            for a in range(0, 9):
                if i - a >= 0 and fired(i - a, sig):
                    near = a; break
            if near is not None:
                ages[near] += 1; tot += 1
        if tot:
            unconf = sum(ages[a] for a in (0, 1, 2))
            dist = "  ".join(f"{a}:{ages[a]}" for a in range(9))
            print(f"  {side} (n={tot}) age-> {dist}")
            print(f"        unconfirmed(0-2)={unconf} ({100*unconf/tot:.0f}%)  confirmed(>=3)={tot-unconf} ({100*(tot-unconf)/tot:.0f}%)")

    print("\n=== Step E.3: entry-delay k on headline buy L20 buf0.1% R2.0 (LAG0 window, SFP-exec repaint re-check) ===")
    for side, L, buf, R in (("buy", 20, 0.0010, 2.0),):
        sig = "bull_divergence" if side == "buy" else "bear_divergence"
        for k in (0, 1, 2):
            tr = ev(L, buf, R, sig, side, 0, 0, TRAIN_END, k=k)
            va = ev(L, buf, R, sig, side, 0, TRAIN_END, VAL_END, k=k)
            print(f"  k{k}  TRAIN {f(tr):<26} VALIDATE {f(va)}")


if __name__ == "__main__":
    main()
