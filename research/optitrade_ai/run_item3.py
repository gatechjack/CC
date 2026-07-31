"""
run_item3.py -- ETH 1h Normal/continuation/RR3.5 drift-control + long/short split.

Runs BOTH signal sets side by side:
  vendor   = vendor-exact spacing (buy = buy2 and ta.barssince(buy2[1])>30;
             clock resets on every fresh event)
  emission = emission-clock spacing (i - last_EMITTED > 30)  [the set we own]

Per venue (Binance, Bybit) x signal-set:
  (a) long/short split per window: n, sumR gross, sumR net06 per side.
  (b) per-side matched random-direction baseline: per window keep the observed
      #long and #short, place uniformly at random, same one-position bracket,
      200 draws -> percentile of observed net06 per side and overall.
  (c) recomputed overall magnitude p (drift-controlled null = same as before:
      per-window direction multiset preserved, times shuffled) = P(null total
      net06 >= observed total).

Counts only. Bracket SL=2.5*ATR, RR3.5, sl-first, 5 equal windows, WARMUP=400.
GROSS shown; net06 = 0.06%/side both sides. Read-only. Writes ITEM3.md + CSV (LF).
"""
import csv, sys
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt
import run_study as R
import optitrade_ai_signals as S
import run_validation as V

WARMUP, SLMULT, RR, NWIN, NPERM = 400, 2.5, 3.5, 5, 200
RNG = np.random.default_rng(2026)

# ---- continuation signal generators (Normal preset, MACD off) ----
def fresh_events(h,l,c):
    src = S.hlc3(h,l,c); emas = S.build_emas(src, "Normal")
    allbull, allbear = S._isbull_stack(emas)
    n = len(c); fb = np.zeros(n, bool); fs = np.zeros(n, bool)
    for i in range(5, n):
        if allbull[i] and not allbull[i-5:i].any(): fb[i] = True
        if allbear[i] and not allbear[i-5:i].any(): fs[i] = True
    return fb, fs

def gen_emission(fb, fs, start, end, sep=30):
    n = fb.shape[0]; sig = np.zeros(n, np.int8); lb = -10**9; ls = -10**9
    for i in range(max(start,6), end):
        if fb[i]:
            if i - lb > sep: sig[i] = 1; lb = i
        elif fs[i]:
            if i - ls > sep: sig[i] = -1; ls = i
    return sig

def gen_vendor(fb, fs, start, end):
    n = fb.shape[0]; sig = np.zeros(n, np.int8); pb = None; ps = None
    for i in range(6, end):
        if fb[i]:
            if pb is not None and (i - pb - 1) > 30 and i >= start: sig[i] = 1
            pb = i
        if fs[i]:
            if ps is not None and (i - ps - 1) > 30 and i >= start: sig[i] = -1
            ps = i
    return sig

def edges(N):
    wl = (N - WARMUP)//NWIN
    return [(WARMUP + k*wl, (WARMUP + (k+1)*wl) if k < NWIN-1 else N) for k in range(NWIN)]

def trades_net(tr):
    d = tr[1]; gross = tr[2]
    net6 = gross - 0.0006*(tr[3]+tr[4])/tr[5]
    return tr[0], d, gross, net6

def split_per_window(tr, N):
    eidx, d, gross, net6 = trades_net(tr)
    rows = []
    for k,(lo,hi) in enumerate(edges(N)):
        inw = (eidx >= lo) & (eidx < hi)
        for side, sm in (("L", d > 0), ("S", d < 0)):
            m = inw & sm
            rows.append(dict(window=k, side=side, n=int(m.sum()),
                             gross=round(float(gross[m].sum()),2),
                             net06=round(float(net6[m].sum()),2)))
    return rows

def null_split(o,h,l,c,atr,sig,N):
    idx = np.where(sig != 0)[0]; dirs = sig[idx].astype(np.int8)
    eg = edges(N)
    def ev(s):
        tr = bt.simulate(o,h,l,c,atr,s,SLMULT,RR,WARMUP,N,True)
        _, d, _, net6 = trades_net(tr)
        return float(net6[d>0].sum()), float(net6[d<0].sum()), float(net6.sum())
    obsL, obsS, obsT = ev(sig)
    nL = np.empty(NPERM); nS = np.empty(NPERM); nT = np.empty(NPERM)
    for p in range(NPERM):
        s = np.zeros(N, np.int8)
        for (lo,hi) in eg:
            mm = (idx >= lo) & (idx < hi); k = int(mm.sum())
            if k == 0: continue
            pos = RNG.choice(np.arange(lo, hi-1), size=k, replace=False)
            s[pos] = dirs[mm]
        nL[p], nS[p], nT[p] = ev(s)
    pct = lambda obs, null: float(np.mean(null < obs))   # share of draws below observed
    return dict(obsL=obsL, obsS=obsS, obsT=obsT,
                pctL=pct(obsL,nL), pctS=pct(obsS,nS), pctT=pct(obsT,nT),
                medL=float(np.median(nL)), medS=float(np.median(nS)), medT=float(np.median(nT)),
                p_overall=float(np.mean(nT >= obsT)))

def main():
    venues = [("Binance", R.load("ETHUSDT","1h")),
              ("Bybit",   V.load_bybit("eth_scalping.db","bars_1h"))]
    csv_rows = []; blocks = []
    for vname, (ts,o,h,l,c) in venues:
        N = len(c); atr = bt.atr_wilder(h,l,c,14); fb, fs = fresh_events(h,l,c)
        for sset, sig in (("vendor",   gen_vendor(fb,fs,WARMUP,N)),
                          ("emission", gen_emission(fb,fs,WARMUP,N))):
            tr = bt.simulate(o,h,l,c,atr,sig,SLMULT,RR,WARMUP,N,True)
            sp = split_per_window(tr, N)
            nul = null_split(o,h,l,c,atr,sig,N)
            blocks.append((vname, sset, sp, nul))
            for r in sp:
                csv_rows.append(dict(venue=vname, signal_set=sset, **r))
            print(f"  {vname:8s} {sset:9s} obsT_net06={nul['obsT']:+.1f} "
                  f"p_overall={nul['p_overall']:.3f} pctL={nul['pctL']:.2f} pctS={nul['pctS']:.2f}")

    with open("item3_results.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(csv_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in csv_rows: w.writerow(r)

    render(blocks)
    print("wrote item3_results.csv + ITEM3.md")

def render(blocks):
    O = []
    O.append("# OptiTrade AI item 3 -- ETH 1h Normal/continuation/RR3.5: "
             "drift-control + long/short split\n")
    O.append("Two signal sets side by side: **vendor** (spec-exact `barssince(buy2[1])>30`, "
             "clock on every fresh event) and **emission** (clock on emission -- the looser set "
             "we own). Bracket SL=2.5*ATR, RR3.5, sl-first, 5 equal windows, WARMUP=400. "
             "GROSS shown; net06 = 0.06%/side both sides. Null = matched random-direction "
             "(per window keep observed #long & #short, random times, same one-position bracket, "
             "200 draws). `pctile` = share of 200 draws with net06 BELOW observed (higher = "
             "observed above random). `p_overall` = P(null total net06 >= observed). Counts only.\n")
    for vname, sset, sp, nul in blocks:
        O.append(f"## {vname} -- {sset} spacing\n")
        O.append("| window | L n | L gross | L net06 | S n | S gross | S net06 |")
        O.append("|--:|--:|--:|--:|--:|--:|--:|")
        bywin = {}
        for r in sp: bywin.setdefault(r["window"], {})[r["side"]] = r
        for k in range(NWIN):
            L = bywin[k]["L"]; Sr = bywin[k]["S"]
            O.append(f"| {k} | {L['n']} | {L['gross']:+} | {L['net06']:+} | "
                     f"{Sr['n']} | {Sr['gross']:+} | {Sr['net06']:+} |")
        totL = sum(r["net06"] for r in sp if r["side"]=="L")
        totS = sum(r["net06"] for r in sp if r["side"]=="S")
        nL = sum(r["n"] for r in sp if r["side"]=="L")
        nS = sum(r["n"] for r in sp if r["side"]=="S")
        O.append(f"\n_Totals: **LONG** n={nL} net06={totL:+.1f} (null median {nul['medL']:+.1f}, "
                 f"observed pctile **{nul['pctL']:.2f}**) | **SHORT** n={nS} net06={totS:+.1f} "
                 f"(null median {nul['medS']:+.1f}, observed pctile **{nul['pctS']:.2f}**) | "
                 f"**OVERALL** net06={nul['obsT']:+.1f} (pctile {nul['pctT']:.2f}, "
                 f"**p_overall={nul['p_overall']:.3f}**)._\n")
    O.append("## Reproduce\n`python run_item3.py` -> item3_results.csv + this file.")
    open("ITEM3.md","w",newline="\n").write("\n".join(O)+"\n")

if __name__ == "__main__":
    main()
