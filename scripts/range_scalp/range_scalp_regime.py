"""Range-fade + REGIME GATE — does fading only when the market is actually ranging rescue it?

Baseline (range_scalp.py): unconditional range-fade is net-negative because the range BREAKS 58% of
the time (you fade into trends). The SMC discipline is "don't fade a trend". Gate entries by the
Kaufman Efficiency Ratio over the prior K closes (real-time, past-only):
    ER = |close[i]-close[i-K]| / sum|close[j]-close[j-1]|   (0..1)
Low ER = choppy/ranging (mean-revert OK); high ER = trending (stand aside). Only fade when ER<=thr.
If the fade clears fees inside genuine ranges, that's a real (regime-conditional) edge; if it stays
negative even when filtered to ranges, the thesis is robustly null on this corpus.
Reports: ranging-fraction, BREAK-rate inside the gate (mechanism proof), TRAIN/VALIDATE, LOCKBOX(flagged).
"""
from __future__ import annotations
import statistics
from datetime import datetime, timezone
from range_scalp import (load, pivots, cluster, ENTRY_FEE, MK, TK, SLIP2,
                         W, M, BAND, MAXBARS, WMIN, WMAX)

TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
ER_K = 20
ER_THRS = [0.25, 0.35, 0.50]
CFGS = [(0.0015, 0.0010, "sweep", "opp"), (0.0015, 0.0010, "sweep", "R2"),
        (0.0008, 0.0010, "sweep", "opp"), (0.0015, 0.0010, "touch", "opp")]


def eff_ratio(rows, K):
    cl = [r[4] for r in rows]
    er = [None] * len(rows)
    for i in range(K, len(rows)):
        num = abs(cl[i] - cl[i - K])
        den = sum(abs(cl[j] - cl[j - 1]) for j in range(i - K + 1, i + 1))
        er[i] = (num / den) if den > 0 else 1.0
    return er


def simulate_gated(rows, hi, lo, er, tol, buf, entry, target, er_thr):
    n = len(rows)
    res = []
    ahi, alo, pi_h, pi_l = [], [], 0, 0
    for i in range(W + 1, n):
        while pi_h < len(hi) and hi[pi_h][0] <= i:
            ahi.append((hi[pi_h][0], hi[pi_h][2])); pi_h += 1
        while pi_l < len(lo) and lo[pi_l][0] <= i:
            alo.append((lo[pi_l][0], lo[pi_l][2])); pi_l += 1
        ahi = [x for x in ahi if x[0] >= i - M]
        alo = [x for x in alo if x[0] >= i - M]
        if er[i] is None or er[i] > er_thr:
            continue
        o, h, l, c = rows[i][1], rows[i][2], rows[i][3], rows[i][4]
        mid = c; tol_abs = tol * mid
        resis = cluster([x[1] for x in ahi], tol_abs)
        supp = cluster([x[1] for x in alo], tol_abs)
        if not resis or not supp:
            continue
        sb = [s for s in supp if s < c]; ra = [r for r in resis if r > c]
        if not sb or not ra:
            continue
        S = max(sb); R = min(ra)
        if not (WMIN <= (R - S) / mid <= WMAX):
            continue
        side = None
        if entry == "sweep":
            if l < S and c > S: side = "buy"
            elif h > R and c < R: side = "sell"
        else:
            if l <= S + BAND * mid and c > S: side = "buy"
            elif h >= R - BAND * mid and c < R: side = "sell"
        if side is None:
            continue
        ep = c
        if side == "buy":
            stop = (l if entry == "sweep" else S) - buf * mid; risk = ep - stop
            tp = R if target == "opp" else ep + 2 * risk
            if tp <= ep: continue
        else:
            stop = (h if entry == "sweep" else R) + buf * mid; risk = stop - ep
            tp = S if target == "opp" else ep - 2 * risk
            if tp >= ep: continue
        if risk <= 0:
            continue
        sp = risk / ep
        Rm = (tp - ep) / risk if side == "buy" else (ep - tp) / risk
        out, g = "open", None
        for j in range(i + 1, min(n, i + 1 + MAXBARS)):
            hh, ll = rows[j][2], rows[j][3]
            sl = (ll <= stop) if side == "buy" else (hh >= stop)
            tph = (hh >= tp) if side == "buy" else (ll <= tp)
            if sl: out, g = "loss", -1.0; break
            if tph: out, g = "win", Rm; break
        if g is None:
            last = rows[min(n - 1, i + MAXBARS)][4]
            g = ((last - ep) if side == "buy" else (ep - last)) / risk
        net = g - (ENTRY_FEE + (MK if out == "win" else TK) + SLIP2) / sp
        res.append((rows[i][0], side, net, out))
    return res


def agg(res, side, lo, hi):
    sub = [r for r in res if r[1] == side and lo <= r[0] < hi]
    if not sub:
        return None
    nets = [r[2] for r in sub]; win = sum(1 for r in sub if r[3] == "win")
    brk = sum(1 for r in sub if r[3] == "loss")
    return len(nets), round(100 * win / len(nets), 1), round(statistics.fmean(nets), 4), round(100 * brk / len(nets), 0)


def main():
    rows = load()
    hi, lo = pivots(rows)
    er = eff_ratio(rows, ER_K)
    vals = [e for e in er if e is not None]
    for thr in ER_THRS:
        frac = 100 * sum(1 for e in vals if e <= thr) / len(vals)
        print(f"ER<={thr}: {frac:.0f}% of bars classed 'ranging'")
    print(f"\n(ER_K={ER_K}) TRAIN<=May15 | VALIDATE<=Jun1 ; brk%=range-break/stop rate; * = positive both (N>=20/8)\n")
    f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f} brk{d[3]:.0f}%" if d else "(none)"
    flagged = []
    cache = {}
    for (tol, buf, e, t) in CFGS:
        for thr in ER_THRS:
            res = simulate_gated(rows, hi, lo, er, tol, buf, e, t, thr)
            cache[(tol, buf, e, t, thr)] = res
            for side in ("buy", "sell"):
                tr = agg(res, side, 0, TRAIN_END); va = agg(res, side, TRAIN_END, VAL_END)
                flag = ""
                if tr and va and tr[2] > 0 and va[2] > 0 and tr[0] >= 20 and va[0] >= 8:
                    flag = " *"; flagged.append((tol, buf, e, t, thr, side))
                print(f"  tol{tol*100:.2f}% {e:<5} {t:<3} ER<={thr} {side:<4} TRAIN {f(tr):<34} VAL {f(va)}{flag}")
        print()
    print(f"=== positive on BOTH train+validate: {len(flagged)} ===")
    for (tol, buf, e, t, thr, side) in flagged:
        lb = agg(cache[(tol, buf, e, t, thr)], side, VAL_END, 9e12)
        print(f"  tol{tol*100:.2f}% {e} {t} ER<={thr} {side}: LOCKBOX {f(lb)}")


if __name__ == "__main__":
    main()
