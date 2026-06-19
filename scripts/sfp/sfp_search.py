"""SFP (Swing Failure / liquidity-sweep) scalp — Steps A/B/C + repaint-check.

SFP (real-time, OHLC, close-confirmed -> no repaint):
  bullish/long : bar LOW < min(low over prior L bars)  AND  bar CLOSE > that prior-low  (swept liq below, closed back above)
  bearish/short: bar HIGH > max(high over prior L bars) AND  bar CLOSE < that prior-high
The swept level (prior-L extreme) is the liquidity/key-level proxy; it uses ONLY past bars, so the
signal is known at the SFP bar's close. Stop = the sweep wick (bar low/high) +/- buffer = operator's
tight stop. Corrected fees. TRAIN/VALIDATE; LOCKBOX (>=Jun 1) reserved.
A) detect, B) model-free MFE/MAE, C) tradeable K/buf/R sweep, + entry-delay REPAINT-CHECK
(a real close-confirmed SFP should NOT inflate at k=0 vs k=1).
"""
from __future__ import annotations
import json, sqlite3, statistics
from datetime import datetime, timezone
from pathlib import Path

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS = 40
LS = [5, 10, 20]
BUFS = [0.0003, 0.0005, 0.0010]
RS = [1.0, 1.5, 2.0, 3.0]
HOR = [1, 3, 5, 10]


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT ts,open,high,low,close FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    return rows


def sfp_bars(rows, L):
    """Return dict bar_index -> side ('buy'/'sell') for SFPs with lookback L."""
    out = {}
    for i in range(L, len(rows)):
        lo = float(rows[i][3]); hi = float(rows[i][2]); cl = float(rows[i][4])
        prior_lo = min(float(rows[m][3]) for m in range(i - L, i))
        prior_hi = max(float(rows[m][2]) for m in range(i - L, i))
        if lo < prior_lo and cl > prior_lo:
            out[i] = "buy"
        elif hi > prior_hi and cl < prior_hi:
            out[i] = "sell"
    return out


def cnet(g, win, opn, sp):
    return g - (ENTRY_FEE + (MK if (win and not opn) else TK) + SLIP2) / sp


def trade(rows, i, side, L, buf, R, k):
    lo = float(rows[i][3]); hi = float(rows[i][2])
    ei = i + k
    if ei + 1 >= len(rows):
        return None
    entry = float(rows[ei][4]) if k == 0 else float(rows[ei][1])  # close at k=0, open if delayed
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
        h, l = float(rows[j][2]), float(rows[j][3])
        sl = (l <= stop) if side == "buy" else (h >= stop)
        tph = (h >= tp) if side == "buy" else (l <= tp)
        if sl:
            out, g = "loss", -1.0; break
        if tph:
            out, g = "win", R; break
    if g is None:
        last = float(rows[min(len(rows) - 1, ei + MAXBARS)][4])
        g = ((last - entry) if side == "buy" else (entry - last)) / risk
    return out, cnet(g, out == "win", g is None, sp)


def main():
    rows = load()
    ts = [r[0] for r in rows]

    # ---- B: model-free MFE/MAE (entry=close, L=10) ----
    sfps10 = sfp_bars(rows, 10)
    print("=== B: model-free MFE/MAE of SFP entries (L=10, entry=close, train+validate) ===")
    for side in ("buy", "sell"):
        per = {h: {"mfe": [], "mae": []} for h in HOR}
        nn = 0
        for i, s in sfps10.items():
            if s != side or ts[i] >= VAL_END or i + max(HOR) >= len(rows):
                continue
            entry = float(rows[i][4]); nn += 1
            for h in HOR:
                seg = rows[i + 1:i + 1 + h]
                if side == "buy":
                    per[h]["mfe"].append(max((float(b[2]) - entry) / entry for b in seg) * 100)
                    per[h]["mae"].append(min((float(b[3]) - entry) / entry for b in seg) * 100)
                else:
                    per[h]["mfe"].append(max((entry - float(b[3])) / entry for b in seg) * 100)
                    per[h]["mae"].append(min((entry - float(b[2])) / entry for b in seg) * 100)
        cells = "  ".join(f"h{h}:{statistics.median(per[h]['mfe']):+.2f}/{statistics.median(per[h]['mae']):+.2f}" for h in HOR)
        print(f"  {side:<5} n={nn:<5} (median MFE/MAE %)  {cells}")

    # ---- repaint-check: entry-delay (L=10, buf0.0005, R2) ----
    print("\n=== repaint-check: entry-delay k=0/1/2 (L=10, buf0.05%, R2) — SFP should NOT inflate at k=0 ===")
    for side in ("buy", "sell"):
        print(f"  {side}:", end="")
        for k in (0, 1, 2):
            nets, win = [], 0
            for i, s in sfps10.items():
                if s != side or ts[i] >= VAL_END:
                    continue
                t = trade(rows, i, side, 10, 0.0005, 2.0, k)
                if t:
                    nets.append(t[1]); win += int(t[0] == "win")
            if nets:
                print(f"  k{k}: n={len(nets)} w={100*win/len(nets):.0f}% N={statistics.fmean(nets):+.3f}", end="")
        print()

    # ---- C: tradeable K/buf/R sweep (entry=close) ----
    out = {}
    print("\n=== C: SFP tradeable sweep (entry=close, corrected fees) — TRAIN | VALIDATE ===")
    print(f"{'side':<5}{'L':<4}{'buf%':<6}{'R':<5}{'TRAIN n/win/NET':<26}{'VALIDATE n/win/NET'}")
    robust = []
    for side in ("buy", "sell"):
        for L in LS:
            sf = sfp_bars(rows, L)
            for buf in BUFS:
                for R in RS:
                    def ev(lo, hi):
                        nets, win = [], 0
                        for i, s in sf.items():
                            if s != side or not (lo <= ts[i] < hi):
                                continue
                            t = trade(rows, i, side, L, buf, R, 0)
                            if t:
                                nets.append(t[1]); win += int(t[0] == "win")
                        if not nets:
                            return None
                        return len(nets), round(100*win/len(nets), 1), round(statistics.fmean(nets), 4)
                    tr = ev(0, TRAIN_END); va = ev(TRAIN_END, VAL_END)
                    out[f"{side}|L{L}|b{buf}|R{R}"] = {"train": tr, "validate": va}
                    f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
                    flag = ""
                    if tr and va and tr[2] > 0 and va[2] > 0 and tr[0] >= 20 and va[0] >= 8:
                        flag = " *"; robust.append(f"{side}|L{L}|b{buf}|R{R}")
                    print(f"{side:<5}{L:<4}{buf*100:<6}{R:<5}{f(tr):<26}{f(va)}{flag}")
    print(f"\n=== positive on BOTH train+validate (N-gated): {len(robust)} ===")
    for k in robust:
        print(f"  {k}  tr {out[k]['train'][2]:+.3f} / va {out[k]['validate'][2]:+.3f}")
    Path(r"C:\Users\AA Incorporado\cc-sfp-wt\data\sfp").mkdir(parents=True, exist_ok=True)
    Path(r"C:\Users\AA Incorporado\cc-sfp-wt\data\sfp\stepC.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
