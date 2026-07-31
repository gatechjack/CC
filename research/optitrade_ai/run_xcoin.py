"""
run_xcoin.py -- PRE-REGISTERED cross-coin falsification.

ONE config, fixed a priori, ZERO selection freedom:
  emission-clock continuation, Normal preset, MACD off,
  SL = 2.5*ATR(14), RR = 3.5, SL-first, 1h.

Run on BTC, SOL, XRP (never touched by this config) + ETH (restated), both
venues (Binance, Bybit), same 5-window protocol. Per cell: n, per-window sumR,
long/short split totals, drift-controlled magnitude p (200 perms, per-window
direction multiset preserved + times shuffled, same one-position bracket).

Counts only. No optimization of anything. Read-only. Writes XCOIN.md + CSV (LF).
"""
import csv, sys
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt, run_study as R, run_validation as V
import run_item3 as I3   # fresh_events(Normal), gen_emission, null_split, edges, trades_net, consts

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
BYBIT_DB = {"BTCUSDT":"btc_scalping.db","ETHUSDT":"eth_scalping.db",
            "SOLUSDT":"sol_scalping.db","XRPUSDT":"xrp_scalping.db"}

def perwin_full(tr, N):
    eidx, d, gross, net6 = I3.trades_net(tr)
    net4 = tr[2] - 0.0004*(tr[3]+tr[4])/tr[5]
    out = []
    for k,(lo,hi) in enumerate(I3.edges(N)):
        m = (eidx >= lo) & (eidx < hi)
        out.append(dict(n=int(m.sum()), gross=round(float(gross[m].sum()),2),
                        net06=round(float(net6[m].sum()),2), net04=round(float(net4[m].sum()),2)))
    return out

def main():
    rows_csv = []; cells = []
    for coin in COINS:
        for venue in ["Binance", "Bybit"]:
            if venue == "Binance":
                ts,o,h,l,c = R.load(coin, "1h")
            else:
                ts,o,h,l,c = V.load_bybit(BYBIT_DB[coin], "bars_1h")
            N = len(c); atr = bt.atr_wilder(h,l,c,14)
            fb, fs = I3.fresh_events(h,l,c)
            sig = I3.gen_emission(fb, fs, I3.WARMUP, N)
            tr = bt.simulate(o,h,l,c,atr,sig,I3.SLMULT,I3.RR,I3.WARMUP,N,True)
            pw = perwin_full(tr, N)
            nul = I3.null_split(o,h,l,c,atr,sig,N)
            tot = dict(n=sum(w["n"] for w in pw), gross=sum(w["gross"] for w in pw),
                       net06=sum(w["net06"] for w in pw), net04=sum(w["net04"] for w in pw),
                       n6pos=sum(1 for w in pw if w["net06"] > 0))
            cells.append(dict(coin=coin.replace("USDT",""), venue=venue, pw=pw, tot=tot,
                              Lnet06=nul["obsL"], Snet06=nul["obsS"], p=nul["p_overall"]))
            for k,w in enumerate(pw):
                rows_csv.append(dict(coin=coin.replace("USDT",""), venue=venue, window=k, **w))
            print(f"  {coin.replace('USDT',''):4s} {venue:8s} n={tot['n']:>4} "
                  f"net06={tot['net06']:+7.1f} net06+={tot['n6pos']}/5 p={nul['p_overall']:.3f}")

    with open("xcoin_results.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows_csv[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows_csv: w.writerow(r)
    render(cells)
    print("wrote xcoin_results.csv + XCOIN.md")

def render(cells):
    O = []
    O.append("# OptiTrade AI -- pre-registered cross-coin falsification (1h)\n")
    O.append("**One config, fixed a priori, zero selection freedom:** emission-clock "
             "continuation, Normal preset, MACD off, SL=2.5*ATR(14), RR=3.5, SL-first, 1h. "
             "Selected originally on ETH 1h; run unchanged on BTC/SOL/XRP (never touched) + "
             "ETH restated. Both venues. 5 equal windows, WARMUP=400. GROSS + net06/net04 "
             "(0.06%/0.04% per side). p = drift-controlled magnitude null (200 perms, per-window "
             "direction counts preserved, times shuffled). **Counts only. No optimization.**\n")
    O.append("## Rollup -- all 8 cells (4 coins x 2 venues)\n")
    O.append("| coin | venue | n | gross | net06 | net04 | net06+/5 | LONG net06 | SHORT net06 | p_overall |")
    O.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for c in cells:
        t = c["tot"]
        O.append(f"| {c['coin']} | {c['venue']} | {t['n']} | {t['gross']:+.1f} | "
                 f"{t['net06']:+.1f} | {t['net04']:+.1f} | {t['n6pos']}/5 | "
                 f"{c['Lnet06']:+.1f} | {c['Snet06']:+.1f} | {c['p']:.3f} |")
    O.append("\n## Per-window net06 (sumR per window)\n")
    O.append("| coin | venue | w0 | w1 | w2 | w3 | w4 |")
    O.append("|---|---|--:|--:|--:|--:|--:|")
    for c in cells:
        cells_w = " | ".join(f"{w['net06']:+.1f}" for w in c["pw"])
        O.append(f"| {c['coin']} | {c['venue']} | {cells_w} |")
    O.append("\n_Full per-window n/gross/net06/net04 in `xcoin_results.csv`. Counts only._")
    O.append("\n## Reproduce\n`python run_xcoin.py` -> xcoin_results.csv + this file.")
    open("XCOIN.md","w",newline="\n").write("\n".join(O)+"\n")

if __name__ == "__main__":
    main()
