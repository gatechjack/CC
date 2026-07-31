"""
corrected_rollup.py -- vendor-exact recomputation of the 3 CONTINUATION
best-config cells in AI_RESULTS.md, so the stale (emission-spacing) numbers are
not quoted. Binance study venue, same 5-window scheme, WARMUP=400.
Prints a markdown table + pulls the stale numbers from ai_results.csv for the delta.
"""
import csv, sys
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt, run_study as R, optitrade_ai_signals as S, run_validation as V

WARMUP, SLMULT, NWIN = 400, 2.5, 5
CELLS = [  # the continuation best-configs from AI_RESULTS.md
    dict(coin="ETHUSDT", tf="1h",  preset="Normal",   macd=0, RR=3.5),
    dict(coin="ETHUSDT", tf="4h",  preset="VeryHigh", macd=0, RR=1.5),
    dict(coin="XRPUSDT", tf="15m", preset="Normal",   macd=1, RR=3.5),
]

def gen_vendor_cont(h,l,c, preset, use_macd, start, end):
    src = S.hlc3(h,l,c); emas = S.build_emas(src, preset)
    allbull, allbear = S._isbull_stack(emas)
    n = len(c); fb = np.zeros(n, bool); fs = np.zeros(n, bool)
    for i in range(5, n):
        if allbull[i] and not allbull[i-5:i].any(): fb[i] = True
        if allbear[i] and not allbear[i-5:i].any(): fs[i] = True
    hist = S.macd_hist(c) if use_macd else None
    sig = np.zeros(n, np.int8); pb = None; ps = None
    for i in range(6, end):
        if fb[i]:
            emit = pb is not None and (i-pb-1) > 30 and i >= start
            pb = i
            if emit and ((not use_macd) or (hist[i] > hist[i-1] and hist[i] >= 0)): sig[i] = 1
        if fs[i]:
            emit = ps is not None and (i-ps-1) > 30 and i >= start
            ps = i
            if emit and ((not use_macd) or (hist[i] < hist[i-1] and hist[i] <= 0)): sig[i] = -1
    return sig

def agg(tr, N):
    pw = V.perwin(tr, N)
    return (sum(w["n"] for w in pw), sum(w["gross"] for w in pw),
            sum(w["net06"] for w in pw), sum(w["net04"] for w in pw),
            sum(1 for w in pw if w["net06"] > 0))

# stale numbers from ai_results.csv
stale = {}
for r in csv.DictReader(open("ai_results.csv")):
    k = (r["coin"], r["tf"], r["preset"], r["mode"], r["macd"], r["RR"])
    stale.setdefault(k, []).append(r)

print("| cell (continuation best-config) | stale net06 (net06+/5) | VENDOR-EXACT n / gross / net06 / net04 / net06+ |")
print("|---|--:|--:|")
for cd in CELLS:
    ts,o,h,l,c = R.load(cd["coin"], cd["tf"]); N = len(c); atr = bt.atr_wilder(h,l,c,14)
    sig = gen_vendor_cont(h,l,c, cd["preset"], bool(cd["macd"]), WARMUP, N)
    tr = bt.simulate(o,h,l,c,atr,sig,SLMULT,cd["RR"],WARMUP,N,True)
    n,g,n6,n4,p6 = agg(tr, N)
    k = (cd["coin"], cd["tf"], cd["preset"], "continuation", str(cd["macd"]), str(cd["RR"]))
    srows = stale.get(k, [])
    s6 = sum(float(x["net06"]) for x in srows); sp6 = sum(1 for x in srows if float(x["net06"]) > 0)
    label = f"{cd['coin'].replace('USDT','')} {cd['tf']} {cd['preset']}/cont/macd{cd['macd']}/RR{cd['RR']}"
    print(f"| {label} | {s6:+.1f} ({sp6}/5) | {n} / {g:+.1f} / **{n6:+.1f}** / {n4:+.1f} / {p6}/5 |")
