"""
spec_diff_spacing.py -- code-level check of the ONE residual from the spec-diff:
the continuation spacing condition `buy = buy2 and ta.barssince(buy2[1])>30`.

Two candidate readings, quantified on ETH 1h (Binance + Bybit), Normal preset:

  MINE (as-implemented): emit a fresh event (buy2) if (i - last_EMITTED) > 30;
        the spacing clock resets on EMISSION.
  VENDOR-EXACT: buy = buy2 and ta.barssince(buy2[1])>30. barssince(buy2[1]) at
        bar i = i - p - 1 where p = the most recent PRIOR buy2 (fresh) bar. So a
        fresh event emits iff (i - p - 1) > 30, i.e. gap-from-previous-FRESH >= 32,
        and the clock resets on every buy2 (fresh) event, emitted or not. The
        first fresh event (no prior) does not emit (barssince = na).

Reports signal counts both ways + bracket net06 (SL 2.5ATR, RR3.5, sl-first,
5 windows) to judge materiality.
"""
import sys
import numpy as np
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt
import run_study as R
import optitrade_ai_signals as S
import run_validation as V   # load_bybit, perwin, WARMUP, etc.

WARMUP, SLMULT, RR = 400, 2.5, 3.5

def fresh_events(h,l,c,preset):
    src = S.hlc3(h,l,c)
    emas = S.build_emas(src, preset)
    allbull, allbear = S._isbull_stack(emas)
    n = len(c)
    fb = np.zeros(n, bool); fs = np.zeros(n, bool)
    for i in range(5, n):
        if allbull[i] and not allbull[i-5:i].any(): fb[i] = True
        if allbear[i] and not allbear[i-5:i].any(): fs[i] = True
    return fb, fs

def gen_mine(fb, fs, start, end, sep=30):
    n = fb.shape[0]; sig = np.zeros(n, np.int8)
    last_b = -10**9; last_s = -10**9
    for i in range(max(start,6), end):
        if fb[i]:
            if i - last_b > sep: sig[i] = 1; last_b = i
        elif fs[i]:
            if i - last_s > sep: sig[i] = -1; last_s = i
    return sig

def gen_vendor(fb, fs, start, end):
    n = fb.shape[0]; sig = np.zeros(n, np.int8)
    prev_b = None; prev_s = None
    for i in range(6, end):                      # clock tracks full history
        if fb[i]:
            if prev_b is not None and (i - prev_b - 1) > 30 and i >= start:
                sig[i] = 1
            prev_b = i
        if fs[i]:
            if prev_s is not None and (i - prev_s - 1) > 30 and i >= start:
                sig[i] = -1
            prev_s = i
    return sig

def net06_windows(o,h,l,c,atr,sig,N):
    pw = V.perwin(bt.simulate(o,h,l,c,atr,sig,SLMULT,RR,WARMUP,N,True), N)
    return (sum(w["n"] for w in pw), sum(w["gross"] for w in pw),
            sum(w["net06"] for w in pw), sum(1 for w in pw if w["net06"] > 0))

def run(label, ts,o,h,l,c):
    N = len(c); atr = bt.atr_wilder(h,l,c,14)
    fb, fs = fresh_events(h,l,c,"Normal")
    raw_b = int(fb[WARMUP:N].sum()); raw_s = int(fs[WARMUP:N].sum())
    sm = gen_mine(fb,fs,WARMUP,N); sv = gen_vendor(fb,fs,WARMUP,N)
    nb_m=int((sm==1).sum()); ns_m=int((sm==-1).sum())
    nb_v=int((sv==1).sum()); ns_v=int((sv==-1).sum())
    # overlap of emitted bars
    em_m=set(np.where(sm!=0)[0]); em_v=set(np.where(sv!=0)[0])
    print(f"\n=== {label} (ETH 1h Normal/continuation) ===")
    print(f"  raw fresh events in-window: buys={raw_b} sells={raw_s}")
    print(f"  MINE  emitted: buys={nb_m} sells={ns_m} total={nb_m+ns_m}")
    print(f"  VENDOR emitted: buys={nb_v} sells={ns_v} total={nb_v+ns_v}")
    print(f"  emitted-bar overlap: {len(em_m & em_v)} shared "
          f"({len(em_m-em_v)} only-mine, {len(em_v-em_m)} only-vendor)")
    tm = net06_windows(o,h,l,c,atr,sm,N)
    tv = net06_windows(o,h,l,c,atr,sv,N)
    print(f"  MINE   bracket: n={tm[0]} gross={tm[1]:+.1f} net06={tm[2]:+.1f} net06+ {tm[3]}/5")
    print(f"  VENDOR bracket: n={tv[0]} gross={tv[1]:+.1f} net06={tv[2]:+.1f} net06+ {tv[3]}/5")

# Binance
ts,o,h,l,c = R.load("ETHUSDT","1h"); run("BINANCE", ts,o,h,l,c)
# Bybit
tsY,oY,hY,lY,cY = V.load_bybit("eth_scalping.db","bars_1h"); run("BYBIT", tsY,oY,hY,lY,cY)
