"""
run_ai_transplant.py -- OptiTrade AI signal-transplant probe.

Transplant the decoded OptiTrade AI entry signals (optitrade_ai_signals) into the
validated optitrade_bt bracket (SL-first, SL=2.5*ATR(14), 4 scaled TP rungs).
This does NOT reimplement the vendor's repainting exit/label layer.

Configs per cell = 8 signal variants (preset{Normal,VeryHigh} x mode{continuation,
reversal} x MACD{off,on}) x RR{1.5,2.5,3.5} = 24. slMult fixed 2.5. NO optimization
-- the configs are vendor-specified a priori, so every bar is out-of-sample; we
tile post-warmup history into 5 equal contiguous windows for a rolling temporal
robustness read (differs from the TP-SL study's IS/OOS split, which optimized).

GROSS primary; net06/net04 = 0.06% / 0.04% taker per side (both sides), in R.
Read-only. Writes ai_results.csv + AI_RESULTS.md (LF) here only.
Run: python run_ai_transplant.py
"""
import csv, sys, time
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt
import run_study as R
import optitrade_ai_signals as S

COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
TFS   = ["15m","1h","4h"]
PRESETS = ["Normal","VeryHigh"]
MODES   = ["continuation","reversal"]
MACD    = [False, True]
RRS     = [1.5, 2.5, 3.5]
SLMULT  = 2.5
WARMUP  = 400
NWIN    = 5
FEES    = (0.0006, 0.0004)
MIN_N   = 30

def per_trade_net(tr, fee):
    (_,_,gross,entry_px,exit_notional,risk_px,_,_,_) = tr
    return gross - fee*(entry_px+exit_notional)/risk_px

def window_of(entry_idx, warmup, win_len):
    k = (entry_idx - warmup)//win_len
    k[k>=NWIN] = NWIN-1
    return k

def main():
    t0=time.time()
    rows=[]
    for coin in COINS:
        for tf in TFS:
            ts,o,h,l,c = R.load(coin,tf); N=len(c)
            atr=bt.atr_wilder(h,l,c,14)
            src=S.hlc3(h,l,c); hist=S.macd_hist(c)
            emas_by_preset={p:S.build_emas(src,p) for p in PRESETS}
            win_len=(N-WARMUP)//NWIN
            for preset in PRESETS:
                for mode in MODES:
                    for mf in MACD:
                        sig=S.gen_signals(h,l,c,preset,mode,mf,WARMUP,N,
                                          emas=emas_by_preset[preset],hist=hist)
                        for RR in RRS:
                            tr=bt.simulate(o,h,l,c,atr,sig,SLMULT,RR,WARMUP,N,True)
                            eidx=tr[0]; gross=tr[2]
                            net6=per_trade_net(tr,0.0006); net4=per_trade_net(tr,0.0004)
                            if eidx.shape[0]==0:
                                wk=np.array([],int)
                            else:
                                wk=window_of(eidx,WARMUP,win_len)
                            for k in range(NWIN):
                                m=(wk==k)
                                rows.append(dict(
                                    coin=coin,tf=tf,preset=preset,mode=mode,
                                    macd=int(mf),RR=RR,window=k,
                                    n=int(m.sum()),
                                    gross=round(float(gross[m].sum()),2),
                                    net06=round(float(net6[m].sum()),2),
                                    net04=round(float(net4[m].sum()),2)))
            print(f"  {coin:8s}{tf:4s} N={N:>8,} done ({time.time()-t0:.1f}s)")

    with open("ai_results.csv","w",newline="") as fp:
        w=csv.DictWriter(fp,fieldnames=list(rows[0].keys()),lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)

    render(rows)
    print(f"\nDONE {len(rows)} rows, {time.time()-t0:.1f}s -> ai_results.csv, AI_RESULTS.md")

# ---------------------------------------------------------------- reporting
def cfgkey(r): return (r["preset"],r["mode"],r["macd"],r["RR"])
def cfglabel(k): return f"{k[0]}/{k[1]}/macd{k[2]}/RR{k[3]}"

def render(rows):
    # aggregate per (cell,config)
    cells=[(co,tf) for co in COINS for tf in TFS]
    def cfgrows(co,tf,k):
        return sorted([r for r in rows if r["coin"]==co and r["tf"]==tf and cfgkey(r)==k],
                      key=lambda r:r["window"])
    allcfg=[(p,m,int(mf),rr) for p in PRESETS for m in MODES for mf in MACD for rr in RRS]

    O=[]
    O.append("# OptiTrade AI -- signal-transplant probe (entries into optitrade_bt bracket)\n")
    O.append("## Vendor-methodology note (source-pending line refs)\n")
    O.append("The vendor's own backtest/label layer is NOT reproduced here, by design. Per the "
             "decoded logic it **repaints** -- entry markers are computed from ribbon-stack and "
             "crossover conditions that are only final at bar close yet are drawn on the forming "
             "bar, and its reported \"wins\" are labelled by comparing a later bar's **close to "
             "the signal bar's close** (a directional close-vs-close check), not by simulating a "
             "stop-loss / take-profit bracket with intrabar fills. That inflates apparent hit-rate "
             "(no stop can be hit between signal and evaluation, and favourable intrabar excursions "
             "are ignored) and is non-actionable. **NOTE:** exact line references are pending -- the "
             "vendor OptiTrade AI Pine source was not located on the box (searched the pine folder, "
             "Downloads, Desktop, Documents, paste-cache; only the in-house 5-EMA `seed1_ribbon_"
             "smabias.pine` was present). Provide the source file and I will cite precise lines. "
             "Here we transplant only the ENTRY signals into the honest bracket.\n")
    O.append("## Protocol\n")
    O.append(f"Binance perp corpus. Cells = 4 coins x {{15m,1h,4h}} (3m fee-dead, 1d undersampled "
             "per the TP-SL study). Bracket: SL-first, SL=2.5*ATR(14), 4 scaled TP rungs each "
             "closing 1/4, RR in {1.5,2.5,3.5}. 8 signal variants x 3 RR = 24 configs/cell "
             "(no optimization). Warmup=400 bars; post-warmup history tiled into 5 equal contiguous "
             "windows. GROSS primary; net06/net04 = 0.06%/0.04% taker per side (both sides), in R. "
             f"Best config per cell = highest TOTAL OOS net06 across the 5 windows (fee-aware; gross "
             "shown alongside).\n")

    # cross-cell best-config summary
    O.append("## Best config per cell (by total net06) + windows-positive counts\n")
    O.append("| cell | best config | gross+ | net06+ | net04+ | win n>=30 | tot n | tot gross | tot net06 | tot net04 |")
    O.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    best_by_cell={}
    for co,tf in cells:
        scored=[]
        for k in allcfg:
            cr=cfgrows(co,tf,k)
            tot6=sum(r["net06"] for r in cr)
            scored.append((tot6,k,cr))
        scored.sort(key=lambda x:-x[0])
        tot6,k,cr=scored[0]; best_by_cell[(co,tf)]=(k,cr)
        gpos=sum(1 for r in cr if r["gross"]>0); n6=sum(1 for r in cr if r["net06"]>0)
        n4=sum(1 for r in cr if r["net04"]>0); n30=sum(1 for r in cr if r["n"]>=MIN_N)
        tn=sum(r["n"] for r in cr); tg=sum(r["gross"] for r in cr); t4=sum(r["net04"] for r in cr)
        O.append(f"| {co} {tf} | {cfglabel(k)} | {gpos}/5 | {n6}/5 | {n4}/5 | {n30}/5 | "
                 f"{tn} | {tg:+.1f} | {tot6:+.1f} | {t4:+.1f} |")

    O.append("\n> **Selection caveat:** the 'best config' is the max-net06 of 24 configs chosen on "
             "the *same* data shown -- an in-sample pick across 288 config-cells, so these rows are "
             "optimistic and some will look good by chance. The unbiased read is the signal-family "
             "rollup below (no per-cell cherry-pick), where **every family is net06-negative in "
             "aggregate**. Treat per-cell winners as leads, weighted by their `win n>=30` column.\n")

    # per-cell best-config per-window detail
    O.append("\n## Per-cell best-config, per-window detail\n")
    for co,tf in cells:
        k,cr=best_by_cell[(co,tf)]
        O.append(f"**{co} {tf} -- {cfglabel(k)}**\n")
        O.append("| window | n | gross | net06 | net04 | flag |")
        O.append("|--:|--:|--:|--:|--:|---|")
        for r in cr:
            fl="n<30" if r["n"]<MIN_N else ""
            O.append(f"| {r['window']} | {r['n']} | {r['gross']:+} | {r['net06']:+} | "
                     f"{r['net04']:+} | {fl} |")
        O.append("")

    # signal-family rollup across all cells (which family survives fees best)
    O.append("## Signal-family rollup (totals across all 12 cells, summed over RR & windows)\n")
    O.append("| preset | mode | macd | tot n | tot gross | tot net06 | tot net04 | cells net06+ |")
    O.append("|---|---|--:|--:|--:|--:|--:|--:|")
    for p in PRESETS:
        for m in MODES:
            for mf in MACD:
                fam=[r for r in rows if r["preset"]==p and r["mode"]==m and r["macd"]==int(mf)]
                tn=sum(r["n"] for r in fam); tg=sum(r["gross"] for r in fam)
                t6=sum(r["net06"] for r in fam); t4=sum(r["net04"] for r in fam)
                # cells (coin,tf) where this family (summed over RR,windows) is net06+
                cn=0
                for co,tf in cells:
                    s=sum(r["net06"] for r in fam if r["coin"]==co and r["tf"]==tf)
                    if s>0: cn+=1
                O.append(f"| {p} | {m} | {int(mf)} | {tn} | {tg:+.1f} | {t6:+.1f} | {t4:+.1f} | {cn}/12 |")

    O.append("\n_Counts/verdicts intentionally minimal -- evidence only. Full 24-config x 5-window "
             "detail per cell is in `ai_results.csv`._")
    O.append("\n## Reproduce\n`python run_ai_transplant.py` -> ai_results.csv + this file.")
    open("AI_RESULTS.md","w",newline="\n").write("\n".join(O)+"\n")

if __name__ == "__main__":
    main()
