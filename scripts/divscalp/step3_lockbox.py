"""Step 3: FINAL LOCKBOX (one touch) — Jun 1 -> 19, never used in the search.

Confirms the repaint conclusion on held-out data: the divergence tight-stop scalp at k=0/1
(pre-confirmation, look-ahead) vs k=2/3 (the honest post-confirmation entry where the live
'once per bar close' alert can first fire). Headline = the HONEST (k>=2) lockbox net.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
LOCKBOX_LO = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS, K, BUF, R = 40, 5, 0.0005, 2.0
SIGS = [("bull_divergence", "buy"), ("bear_divergence", "sell")]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "bull_divergence", "bear_divergence"]
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    O, H, L, C = ci["open"], ci["high"], ci["low"], ci["close"]

    def trade(i, side, k):
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

    print("=== LOCKBOX (Jun 1 -> 19) — divergence tight-stop scalp (K5,buf0.05%,R2.0) ===")
    print(f"{'signal':<18}{'k':<4}{'n':<5}{'win%':<7}{'net_R':<10}note")
    for sig, side in SIGS:
        col = ci[sig]
        for k in (0, 1, 2, 3):
            nets, win = [], 0
            for i, r in enumerate(rows):
                if r[ci["ts"]] < LOCKBOX_LO:
                    continue
                if not r[col] or float(r[col]) == 0.0:
                    continue
                t = trade(i, side, k)
                if t:
                    nets.append(t[1]); win += int(t[0] == "win")
            if not nets:
                print(f"{sig:<18}{k:<4}(none)"); continue
            note = "look-ahead (pre-confirm)" if k <= 1 else "HONEST (alert can fire)"
            print(f"{sig:<18}{k:<4}{len(nets):<5}{round(100*win/len(nets),1):<7}{round(statistics.fmean(nets),4):+.4f}    {note}")


if __name__ == "__main__":
    main()
