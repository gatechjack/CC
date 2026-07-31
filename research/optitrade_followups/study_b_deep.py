"""
STUDY B deepening -- items 1-3 (same frozen config, no optimization).
 1. Per-window table (5 windows, all 4 coins): n, gross, net06 -- Binance 1h.
 2. Pooled significance PRE-REGISTERED as ALL FOUR coins (ETH included, no post-hoc
    exclusion): pooled net06 + drift-controlled p (each coin shuffled independently
    within its windows, dir-counts preserved, summed; 200 perms).
 3. Bybit cross-venue replay, 1h, all 4 coins, same table format.
Counts only. (Item 4 = construct-overlap, separate script after the RD dump.)
"""
import sys, csv, sqlite3
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt, run_study as R
import study_b_widestop as B   # sim_single, signals, edges, perwin, consts

BYBIT = {"BTCUSDT":"btc_scalping.db","ETHUSDT":"eth_scalping.db",
         "SOLUSDT":"sol_scalping.db","XRPUSDT":"xrp_scalping.db"}
DATA = r"C:\Users\AA Incorporado\cc\data"
RNG = np.random.default_rng(99001)

def load_bybit(db):
    con = sqlite3.connect(f"file:{DATA}\\{db}?mode=ro", uri=True)
    rows = con.execute("select ts,open,high,low,close from bars_1h order by ts").fetchall()
    con.close()
    a = np.array(rows, np.float64)
    return (a[:,0].astype(np.int64), np.ascontiguousarray(a[:,1]), np.ascontiguousarray(a[:,2]),
            np.ascontiguousarray(a[:,3]), np.ascontiguousarray(a[:,4]))

def cell(o,h,l,c):
    N = len(c); atr = bt.atr_wilder(h,l,c,14); sig = B.signals(o,h,l,c,N)
    tr = B.sim_single(o,h,l,c,atr,sig,B.SLMULT,B.RR,B.WARMUP,N,True)
    return N, atr, sig, tr

def net06(tr):
    _,_,g,epx,xpx,rpx = tr
    return float((g - 0.0006*(epx+xpx)/rpx).sum())

def perm_sig(sig, N):
    idx = np.where(sig != 0)[0]; dirs = sig[idx].astype(np.int8)
    s = np.zeros(N, np.int8)
    for (lo,hi) in B.edges(N):
        mm = (idx >= lo) & (idx < hi); k = int(mm.sum())
        if k == 0: continue
        pos = RNG.choice(np.arange(lo, hi-1), size=k, replace=False); s[pos] = dirs[mm]
    return s

def pooled_p(cells):
    obs = sum(net06(cd["tr"]) for cd in cells)
    ge = 0
    for _ in range(B.NPERM):
        tot = 0.0
        for cd in cells:
            s = perm_sig(cd["sig"], cd["N"])
            tot += net06(B.sim_single(cd["o"],cd["h"],cd["l"],cd["c"],cd["atr"],s,B.SLMULT,B.RR,B.WARMUP,cd["N"],True))
        if tot >= obs: ge += 1
    return obs, ge / B.NPERM

def build(venue):
    cells = []
    for coin in B.COINS:
        if venue == "Binance":
            ts,o,h,l,c = R.load(coin, "1h")
        else:
            ts,o,h,l,c = load_bybit(BYBIT[coin])
        N, atr, sig, tr = cell(o,h,l,c)
        pw = B.perwin(tr, N)
        obs, p = B.drift_p(o,h,l,c,atr,sig,N)   # per-coin drift p (own RNG in module)
        cells.append(dict(coin=coin.replace("USDT",""), o=o,h=h,l=l,c=c,atr=atr,sig=sig,tr=tr,
                          N=N, pw=pw, p=p))
    return cells

def main():
    binance = build("Binance"); bybit = build("Bybit")
    b_obs, b_p = pooled_p(binance)
    y_obs, y_p = pooled_p(bybit)

    rows = []
    for venue, cells in [("Binance", binance), ("Bybit", bybit)]:
        for cd in cells:
            for k,w in enumerate(cd["pw"]):
                rows.append(dict(venue=venue, coin=cd["coin"], window=k, **w))
    with open("study_b_deep_results.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)

    O = []
    O.append("# STUDY B deepening (items 1-3) -- same frozen config, counts only\n")
    O.append("Wide-stop trend-cross: EMA30/66 + RSI bias5 minSep6 signal, single 3.0*ATR stop + "
             "single 3R TP, SL-first, 1h. 5 equal windows over post-warmup history. GROSS + "
             "net06/net04 (0.06%/0.04% per side). Per-coin drift p = 200-perm within-window "
             "direction-preserving shuffle. NO optimization; ETH retained (no post-hoc exclusion).\n")

    def wtable(cells, venue):
        O.append(f"### Per-window n / gross / net06 -- {venue} 1h (window 4 = most recent)\n")
        O.append("| coin | metric | w0 | w1 | w2 | w3 | w4 | total |")
        O.append("|---|---|--:|--:|--:|--:|--:|--:|")
        for cd in cells:
            pw = cd["pw"]
            O.append(f"| {cd['coin']} | n | " + " | ".join(str(w['n']) for w in pw) +
                     f" | {sum(w['n'] for w in pw)} |")
            O.append(f"| {cd['coin']} | gross | " + " | ".join(f"{w['gross']:+.1f}" for w in pw) +
                     f" | {sum(w['gross'] for w in pw):+.1f} |")
            O.append(f"| {cd['coin']} | net06 | " + " | ".join(f"{w['net06']:+.1f}" for w in pw) +
                     f" | {sum(w['net06'] for w in pw):+.1f} |")
        O.append("")

    O.append("## 1. Binance per-window (all 4 coins)\n")
    wtable(binance, "Binance")

    O.append("## 2. Pooled significance -- pre-registered ALL FOUR coins (ETH included)\n")
    O.append("| venue | pooled net06 | drift p (pooled) |")
    O.append("|---|--:|--:|")
    O.append(f"| Binance | {b_obs:+.1f} | {b_p:.3f} |")
    O.append(f"| Bybit | {y_obs:+.1f} | {y_p:.3f} |")
    O.append("")

    O.append("## 3. Bybit cross-venue replay (1h, all 4 coins)\n")
    O.append("| coin | n | gross | net06 | net04 | drift p |")
    O.append("|---|--:|--:|--:|--:|--:|")
    for cd in bybit:
        pw = cd["pw"]
        n = sum(w['n'] for w in pw); g = sum(w['gross'] for w in pw)
        n6 = sum(w['net06'] for w in pw); n4 = sum(w['net04'] for w in pw)
        O.append(f"| {cd['coin']} | {n} | {g:+.1f} | {n6:+.1f} | {n4:+.1f} | {cd['p']:.3f} |")
    O.append("\n### Bybit per-window\n")
    wtable(bybit, "Bybit")

    O.append("_Counts only. Full per-window in study_b_deep_results.csv._")
    O.append("\n## Reproduce\n`python study_b_deep.py` -> study_b_deep_results.csv + this file.")
    open("STUDY_B_DEEP.md","w",newline="\n").write("\n".join(O)+"\n")
    print("wrote STUDY_B_DEEP.md + study_b_deep_results.csv")
    print(f"  Binance pooled net06={b_obs:+.1f} p={b_p:.3f} | Bybit pooled net06={y_obs:+.1f} p={y_p:.3f}")
    for cd in bybit:
        pw=cd["pw"]; print(f"  Bybit {cd['coin']:4s} net06={sum(w['net06'] for w in pw):+7.1f} p={cd['p']:.3f}")

if __name__ == "__main__":
    main()
