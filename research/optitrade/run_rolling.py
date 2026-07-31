"""
run_rolling.py -- Follow-up 1: rolling multi-window walk-forward on the cells
that survived the single-split OOS study.

Cells: ETH 4h, XRP 1h (net06-positive), + SOL 1h, XRP 4h (net04-positive tier).

Scheme (sliding walk-forward): usable series U = N-warmup.
  IS_len = 40% of U, OOS_len = 12% of U, step = OOS_len (non-overlapping OOS).
  For each window k: IS = the IS_len bars immediately BEFORE OOS block k;
  re-optimize on IS (max GROSS sum-R s.t. IS n>=30, else relax+flag), freeze,
  evaluate on OOS block k. OOS blocks tile 40% -> 100% of history (5 windows).

GROSS primary; net06/net04 at 0.06% / 0.04% per side (both sides). Same engine.
Read-only. Writes rolling_results.csv + ROLLING_RESULTS.md (LF) here only.
Run: python run_rolling.py
"""
import csv, datetime as dt
import numpy as np
import optitrade_bt as bt
import run_study as R

CELLS = [("ETHUSDT","4h"), ("XRPUSDT","1h"), ("SOLUSDT","1h"), ("XRPUSDT","4h")]
IS_FRAC, OOS_FRAC = 0.40, 0.12
FEES = R.FEES

def udate(ts, i):
    return dt.datetime.fromtimestamp(ts[i]/1000, dt.UTC).strftime("%Y-%m-%d")

def main():
    rows = []
    summary = []
    for coin, tf in CELLS:
        ts,o,h,l,c = R.load(coin, tf)
        N = len(c)
        atr = bt.atr_wilder(h,l,c,14); rsi = bt.rsi_wilder(c,14)
        cache = R.per_L_cache(c,h,l)
        U = N - R.WARMUP
        IS_len = int(IS_FRAC*U); OOS_len = int(OOS_FRAC*U)
        k = 0; oos_start = R.WARMUP + IS_len
        cell_rows = []
        while oos_start + OOS_len <= N:
            is_start, is_end = oos_start - IS_len, oos_start
            oos_end = oos_start + OOS_len
            params, relaxed = R.grid_best(o,h,l,c, atr, rsi, cache,
                                          is_start, is_end, True, R.MIN_N)
            mis = R.eval_params(o,h,l,c, atr, rsi, cache, params, is_start, is_end)
            moos = R.eval_params(o,h,l,c, atr, rsi, cache, params, oos_start, oos_end)
            rec = dict(
                coin=coin, tf=tf, window=k,
                IS_from=udate(ts,is_start), IS_to=udate(ts,is_end-1),
                OOS_from=udate(ts,oos_start), OOS_to=udate(ts,oos_end-1),
                L=params[0], slMult=params[1], RR=params[2], bias=params[3],
                IS_relaxed=relaxed, IS_n=mis["n"], OOS_n=moos["n"],
                OOS_gross=round(moos["sumR"],2),
                OOS_net06=round(moos["net_sumR_0.0006"],2),
                OOS_net04=round(moos["net_sumR_0.0004"],2))
            rows.append(rec); cell_rows.append(rec)
            k += 1; oos_start += OOS_len
        K = len(cell_rows)
        gpos = sum(1 for r in cell_rows if r["OOS_gross"] > 0)
        n6pos = sum(1 for r in cell_rows if r["OOS_net06"] > 0)
        n4pos = sum(1 for r in cell_rows if r["OOS_net04"] > 0)
        n30 = sum(1 for r in cell_rows if r["OOS_n"] >= R.MIN_N)
        n6pos_n30 = sum(1 for r in cell_rows if r["OOS_n"] >= R.MIN_N and r["OOS_net06"] > 0)
        med_g = float(np.median([r["OOS_gross"] for r in cell_rows]))
        med_6 = float(np.median([r["OOS_net06"] for r in cell_rows]))
        summary.append(dict(coin=coin, tf=tf, K=K, gross_pos=gpos, net06_pos=n6pos,
                            net04_pos=n4pos, windows_n30=n30, net06_pos_among_n30=n6pos_n30,
                            median_gross=round(med_g,2), median_net06=round(med_6,2)))
        print(f"  {coin:8s}{tf:4s} K={K} gross+={gpos}/{K} net06+={n6pos}/{K} "
              f"net04+={n4pos}/{K} (n>=30 in {n30}/{K})")

    # CSV (LF)
    with open("rolling_results.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)

    # Markdown (LF)
    O=[]
    O.append("# OptiTrade follow-up 1 -- rolling multi-window walk-forward\n")
    O.append("Cells that survived the single-split OOS study (ETH 4h, XRP 1h at 0.06%; "
             "+ SOL 1h, XRP 4h at the 0.04% tier). Same engine, same objective "
             "(max GROSS sum-R s.t. IS n>=30). **Sliding** WF: IS = 40% of usable history "
             "immediately preceding each OOS block; OOS = 12% blocks stepped by 12% "
             "(5 non-overlapping windows tiling 40%->100% of 2022-07..2026-06). "
             "GROSS primary; net06/net04 = 0.06% / 0.04% per side, both sides.\n")
    O.append("## Per cell, per window\n")
    for coin,tf in CELLS:
        cr=[r for r in rows if r["coin"]==coin and r["tf"]==tf]
        O.append(f"### {coin} {tf}\n")
        O.append("| win | IS range | OOS range | L/sl/RR/bias | IS n | OOS n | OOS gross | net06 | net04 | flag |")
        O.append("|--:|---|---|---|--:|--:|--:|--:|--:|---|")
        for r in cr:
            fl=[]
            if r["OOS_n"]<R.MIN_N: fl.append("OOS n<30")
            if r["IS_relaxed"]: fl.append("IS<30 relaxed")
            p=f"{r['L']}/{r['slMult']}/{r['RR']}/{r['bias']}"
            O.append(f"| {r['window']} | {r['IS_from']}..{r['IS_to']} | "
                     f"{r['OOS_from']}..{r['OOS_to']} | {p} | {r['IS_n']} | {r['OOS_n']} | "
                     f"{r['OOS_gross']:+} | {r['OOS_net06']:+} | {r['OOS_net04']:+} | "
                     f"{', '.join(fl)} |")
        O.append("")
    O.append("## Summary -- windows each cell stays net-positive (verdict-free counts)\n")
    O.append("| cell | K | gross+ | net06+ | net04+ | windows n>=30 | net06+ among n>=30 | median OOS gross | median OOS net06 |")
    O.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for s in summary:
        O.append(f"| {s['coin']} {s['tf']} | {s['K']} | {s['gross_pos']}/{s['K']} | "
                 f"{s['net06_pos']}/{s['K']} | {s['net04_pos']}/{s['K']} | "
                 f"{s['windows_n30']}/{s['K']} | {s['net06_pos_among_n30']}/{s['windows_n30']} | "
                 f"{s['median_gross']:+} | {s['median_net06']:+} |")
    O.append("\n_Counts are over all K windows regardless of n; the `windows n>=30` column shows "
             "how many windows carry an adequate sample, and the final column restricts the "
             "net06-positive count to those. Evidence only._")
    O.append("\n## Reproduce\n`python run_rolling.py` -> rolling_results.csv + this file.")
    open("ROLLING_RESULTS.md","w",newline="\n").write("\n".join(O)+"\n")
    print("wrote rolling_results.csv + ROLLING_RESULTS.md")

if __name__ == "__main__":
    main()
