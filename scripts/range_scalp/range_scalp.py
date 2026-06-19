"""Range / level mean-reversion scalp — the mechanical version of "bounce between the boxes".

Operator thesis (from the SMC chart): price oscillates between multi-touch S/R levels (EQH/EQL,
supply/demand boxes); fade the edges, stop on the break (BOS). UNLIKE the divergence/SFP studies
this is REAL-TIME and NON-REPAINTING by construction:
  * Levels are clusters of CONFIRMED past pivots only. A pivot at bar p (local extreme over +/-w)
    is KNOWN only at bar p+w; at decision bar i we use pivots with confirm_idx <= i. No future leak.
  * A "level" = >=ktouch confirmed pivots clustered within tol% (a real multi-touch S/R, not a lone
    noise wick — that is the fix vs SFP-alone, which used any prior-L extreme).
  * Entry at bar i uses bar i's confirmed OHLC. Stop = just beyond the level (range-break = the BOS
    invalidation). Target = opposite edge (mean reversion) or fixed R.

The break (BOS) is the real loss and is NOT capped artificially. Corrected fees. Walk-forward
TRAIN<=May15 / VALIDATE<=Jun1 / LOCKBOX>=Jun1; lockbox computed only for positive-both candidates.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
W, M, KTOUCH, BAND, MAXBARS = 3, 100, 2, 0.0010, 60
WMIN, WMAX = 0.0025, 0.020          # tradeable range width (R-S)/mid
TOLS, BUFS = [0.0008, 0.0015], [0.0005, 0.0010]
ENTRIES, TARGETS = ["sweep", "touch"], ["opp", "R2"]


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT ts,open,high,low,close FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def pivots(rows):
    """Return (hi, lo): lists of (confirm_idx, pivot_idx, price), sorted by confirm_idx."""
    hi, lo = [], []
    n = len(rows)
    for p in range(W, n - W):
        h = rows[p][2]; l = rows[p][3]
        win = rows[p - W:p + W + 1]
        if h == max(b[2] for b in win) and h > rows[p - 1][2] and h > rows[p + 1][2]:
            hi.append((p + W, p, h))
        if l == min(b[3] for b in win) and l < rows[p - 1][3] and l < rows[p + 1][3]:
            lo.append((p + W, p, l))
    return hi, lo


def cluster(prices, tol_abs):
    """Greedy 1-D cluster; return level means for clusters with >=KTOUCH members."""
    if not prices:
        return []
    sp = sorted(prices)
    out, grp = [], [sp[0]]
    for x in sp[1:]:
        if x - grp[0] <= tol_abs:
            grp.append(x)
        else:
            if len(grp) >= KTOUCH:
                out.append(statistics.fmean(grp))
            grp = [x]
    if len(grp) >= KTOUCH:
        out.append(statistics.fmean(grp))
    return out


def simulate(rows, hi, lo, tol, buf, entry, target):
    """One config -> list of (ts, net_R, outcome). Real-time, non-repainting."""
    n = len(rows)
    res = []
    # sliding active-pivot windows keyed by confirm_idx in [i-M, i]
    ahi, alo = [], []          # active (confirm_idx, price)
    pi_h = pi_l = 0
    for i in range(W + 1, n):
        # add pivots confirmed at <= i
        while pi_h < len(hi) and hi[pi_h][0] <= i:
            ahi.append((hi[pi_h][0], hi[pi_h][2])); pi_h += 1
        while pi_l < len(lo) and lo[pi_l][0] <= i:
            alo.append((lo[pi_l][0], lo[pi_l][2])); pi_l += 1
        # drop stale (confirm_idx < i-M)
        ahi = [x for x in ahi if x[0] >= i - M]
        alo = [x for x in alo if x[0] >= i - M]
        o, h, l, c = rows[i][1], rows[i][2], rows[i][3], rows[i][4]
        mid = c
        tol_abs = tol * mid
        resis = cluster([x[1] for x in ahi], tol_abs)
        supp = cluster([x[1] for x in alo], tol_abs)
        if not resis or not supp:
            continue
        sup_below = [s for s in supp if s < c]
        res_above = [r for r in resis if r > c]
        if not sup_below or not res_above:
            continue
        S = max(sup_below); R = min(res_above)
        width = (R - S) / mid
        if not (WMIN <= width <= WMAX):
            continue
        side = None
        if entry == "sweep":
            if l < S and c > S:
                side = "buy"
            elif h > R and c < R:
                side = "sell"
        else:  # touch
            if l <= S + BAND * mid and c > S:
                side = "buy"
            elif h >= R - BAND * mid and c < R:
                side = "sell"
        if side is None:
            continue
        entry_px = c
        if side == "buy":
            stop = (l if entry == "sweep" else S) - buf * mid
            risk = entry_px - stop
            tp = R if target == "opp" else entry_px + 2 * risk
            if tp <= entry_px:
                continue
        else:
            stop = (h if entry == "sweep" else R) + buf * mid
            risk = stop - entry_px
            tp = S if target == "opp" else entry_px - 2 * risk
            if tp >= entry_px:
                continue
        if risk <= 0:
            continue
        sp = risk / entry_px
        Rmult = (tp - entry_px) / risk if side == "buy" else (entry_px - tp) / risk
        out, g = "open", None
        for j in range(i + 1, min(n, i + 1 + MAXBARS)):
            hh, ll = rows[j][2], rows[j][3]
            sl = (ll <= stop) if side == "buy" else (hh >= stop)
            tph = (hh >= tp) if side == "buy" else (ll <= tp)
            if sl:
                out, g = "loss", -1.0; break
            if tph:
                out, g = "win", Rmult; break
        if g is None:
            last = rows[min(n - 1, i + MAXBARS)][4]
            g = ((last - entry_px) if side == "buy" else (entry_px - last)) / risk
        net = g - (ENTRY_FEE + (MK if out == "win" else TK) + SLIP2) / sp
        res.append((rows[i][0], side, net, out))
    return res


def agg(res, side, lo, hi):
    sub = [r for r in res if r[1] == side and lo <= r[0] < hi]
    if not sub:
        return None
    nets = [r[2] for r in sub]
    win = sum(1 for r in sub if r[3] == "win")
    return len(nets), round(100 * win / len(nets), 1), round(statistics.fmean(nets), 4)


def main():
    rows = load()
    hi, lo = pivots(rows)
    print(f"bars={len(rows)}  confirmed pivots: hi={len(hi)} lo={len(lo)}  (w={W},M={M},ktouch={KTOUCH})")
    print(f"TRAIN<=May15 | VALIDATE<=Jun1 ; * = positive both (N>=20/8). LOCKBOX for flagged only.\n")
    f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
    flagged = []
    cfgs = [(tol, buf, e, t) for tol in TOLS for buf in BUFS for e in ENTRIES for t in TARGETS]
    cache = {}
    for (tol, buf, e, t) in cfgs:
        res = simulate(rows, hi, lo, tol, buf, e, t)
        cache[(tol, buf, e, t)] = res
        for side in ("buy", "sell"):
            tr = agg(res, side, 0, TRAIN_END)
            va = agg(res, side, TRAIN_END, VAL_END)
            flag = ""
            if tr and va and tr[2] > 0 and va[2] > 0 and tr[0] >= 20 and va[0] >= 8:
                flag = " *"; flagged.append((tol, buf, e, t, side))
            print(f"  tol{tol*100:.2f}% buf{buf*100:.2f}% {e:<5} {t:<3} {side:<4} TRAIN {f(tr):<26} VALIDATE {f(va)}{flag}")
        print()
    print(f"=== positive on BOTH train+validate: {len(flagged)} ===")
    for (tol, buf, e, t, side) in flagged:
        lb = agg(cache[(tol, buf, e, t)], side, VAL_END, 9e12)
        print(f"  tol{tol*100:.2f}% buf{buf*100:.2f}% {e} {t} {side}: LOCKBOX {f(lb)}")
    # diagnostic: break/win/timeout mix for one representative config
    rep = (0.0015, 0.0010, "sweep", "opp")
    res = cache[rep]
    for side in ("buy", "sell"):
        sub = [r for r in res if r[1] == side]
        if sub:
            w_ = sum(1 for r in sub if r[3] == "win"); ls = sum(1 for r in sub if r[3] == "loss")
            to = sum(1 for r in sub if r[3] == "open")
            print(f"\n[diag {rep} {side}] n={len(sub)} win={w_} break/stop={ls} timeout={to}  netN={statistics.fmean([r[2] for r in sub]):+.3f}")


if __name__ == "__main__":
    main()
