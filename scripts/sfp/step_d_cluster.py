"""Step D: SFP + Otter exhaustion-cluster confluence — does it filter the SFP into an edge?

SFP-alone is net-negative noise. Operator's real setup: SFP at a level WITH exhaustion/reversal
confluence. Require a cluster signal to have fired in [i-N, i] (prior N bars incl SFP bar = known
at close, real-time). Bull SFP -> bull cluster; bear SFP -> bear cluster. Cypher excluded.
Compare SFP-alone vs SFP+cluster (lift), train/validate; lockbox computed for positive-both only.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS, N = 40, 4
BULL_CLUSTER = ["otter_buy", "super_buy_high", "super_buy_std", "bottom_signal", "bull_divergence"]
BEAR_CLUSTER = ["otter_sell", "super_sell_high", "super_sell_std", "top_signal", "bear_divergence"]
CONFS = {"none": None, "any_cluster": "any", "divergence": ["bull_divergence", "bear_divergence"],
         "otter": ["otter_buy", "otter_sell"], "super": ["super_buy_high", "super_sell_high", "super_buy_std", "super_sell_std"],
         "topbottom": ["bottom_signal", "top_signal"]}
CFGS = [(10, 0.0005, 1.5), (10, 0.0005, 2.0), (10, 0.0010, 1.5), (10, 0.0010, 2.0),
        (20, 0.0010, 1.5), (20, 0.0010, 2.0)]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close"] + sorted(set(BULL_CLUSTER + BEAR_CLUSTER))
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    ts = [r[ci["ts"]] for r in rows]

    def fired(i, sig):
        v = rows[i][ci[sig]]
        return v is not None and float(v) != 0.0

    def cluster_present(i, side, conf):
        sigs = (BULL_CLUSTER if side == "buy" else BEAR_CLUSTER) if conf == "any" else \
               [s for s in conf if (s in BULL_CLUSTER) == (side == "buy")]
        for j in range(max(0, i - N), i + 1):
            if any(fired(j, s) for s in sigs):
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

    def trade(i, side, buf, R):
        lo, hi = float(rows[i][ci["low"]]), float(rows[i][ci["high"]])
        entry = float(rows[i][ci["close"]])
        if side == "buy":
            stop = lo - buf * entry; risk = entry - stop
        else:
            stop = hi + buf * entry; risk = stop - entry
        if risk <= 0:
            return None
        tp = entry + R * risk if side == "buy" else entry - R * risk
        sp = risk / entry
        out, g = "open", None
        for j in range(i + 1, min(len(rows), i + 1 + MAXBARS)):
            h, l = float(rows[j][ci["high"]]), float(rows[j][ci["low"]])
            sl = (l <= stop) if side == "buy" else (h >= stop)
            tph = (h >= tp) if side == "buy" else (l <= tp)
            if sl:
                out, g = "loss", -1.0; break
            if tph:
                out, g = "win", R; break
        if g is None:
            last = float(rows[min(len(rows) - 1, i + MAXBARS)][ci["close"]])
            g = ((last - entry) if side == "buy" else (entry - last)) / risk
        return out, g - (ENTRY_FEE + (MK if out == "win" else TK) + SLIP2) / sp

    def ev(L, buf, R, conf, side, lo, hi):
        nets, win = [], 0
        for i in range(L, len(rows)):
            if not (lo <= ts[i] < hi):
                continue
            s = sfp(i, L)
            if s != side:
                continue
            if conf is not None and not cluster_present(i, side, conf):
                continue
            t = trade(i, side, buf, R)
            if t:
                nets.append(t[1]); win += int(t[0] == "win")
        if not nets:
            return None
        return len(nets), round(100 * win / len(nets), 1), round(statistics.fmean(nets), 4)

    print(f"SFP + cluster confluence (signal in prior {N} bars). TRAIN | VALIDATE; * = positive both (N>=15/6)")
    flagged = []
    for side in ("buy", "sell"):
        for (L, buf, R) in CFGS:
            print(f"\n-- {side} L{L} buf{buf*100}% R{R} --")
            for cname, conf in CONFS.items():
                tr = ev(L, buf, R, conf, side, 0, TRAIN_END)
                va = ev(L, buf, R, conf, side, TRAIN_END, VAL_END)
                f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
                flag = ""
                if tr and va and tr[2] > 0 and va[2] > 0 and tr[0] >= 15 and va[0] >= 6:
                    flag = " *"; flagged.append((side, L, buf, R, cname))
                print(f"   {cname:<12} TRAIN {f(tr):<26} VALIDATE {f(va)}{flag}")
    print(f"\n=== positive on BOTH train+validate: {len(flagged)} ===")
    for (side, L, buf, R, cname) in flagged:
        lb = ev(L, buf, R, CONFS[cname], side, VAL_END, 9e12)
        print(f"  {side} L{L} buf{buf*100}% R{R} {cname}: LOCKBOX {('n=%d w=%s N=%+.3f' % (lb[0], lb[1], lb[2])) if lb else '(none)'}")


if __name__ == "__main__":
    main()
