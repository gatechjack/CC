"""
optitrade_ai_signals.py -- entry signals for the "OptiTrade AI" ribbon script,
decoded from the vendor spec, for transplant into the optitrade_bt bracket.

All EMAs on hlc3 = (high+low+close)/3.
Ribbon presets (shortest -> longest):
  Normal    = [30,40,50,60,70,80,90,100,110,120]
  VeryHigh  = [60,80,100,120,140,160,180,200,220,240]
isbull(e) at i:  e[i]>e[i-1]>e[i-2]>e[i-3]   ;  isbear mirrored.

Mode CONTINUATION buy: ALL 10 EMAs isbull at i, "fresh" (all-bull condition
  false on each of the prior 5 bars), and > minsep(=30) bars since the previous
  emitted continuation buy. Sell mirrored (all isbear).
Mode REVERSAL buy: crossover(shortest EMA, longest EMA), > minsep(=15) bars since
  previous emitted reversal buy. Sell = crossunder.
MACD filter (12/26/9 on CLOSE): if on, buys also require hist rising & hist>=0;
  sells require hist falling & hist<=0.

Entry emitted at bar close (fed to optitrade_bt.simulate). Spacing is continuous
across the series (not reset per window). Indicators are causal.
"""
import numpy as np
import optitrade_bt as bt

PRESETS = {
    "Normal":   [30,40,50,60,70,80,90,100,110,120],
    "VeryHigh": [60,80,100,120,140,160,180,200,220,240],
}
CONT_SEP = 30
REV_SEP  = 15

def hlc3(h, l, c):
    return (h + l + c) / 3.0

def macd_hist(close, fast=12, slow=26, sig=9):
    m = bt.ema(close, fast) - bt.ema(close, slow)
    s = bt.ema(m, sig)
    return m - s

def build_emas(src, preset):
    return [bt.ema(src, L) for L in PRESETS[preset]]

def _isbull_stack(emas):
    n = emas[0].shape[0]
    allbull = np.ones(n, bool); allbear = np.ones(n, bool)
    for e in emas:
        cb = np.zeros(n, bool); cr = np.zeros(n, bool)
        cb[3:] = (e[3:] > e[2:-1]) & (e[2:-1] > e[1:-2]) & (e[1:-2] > e[:-3])
        cr[3:] = (e[3:] < e[2:-1]) & (e[2:-1] < e[1:-2]) & (e[1:-2] < e[:-3])
        allbull &= cb; allbear &= cr
    return allbull, allbear

def gen_signals(h, l, c, preset, mode, use_macd, start, end,
                emas=None, hist=None):
    """Return int8 sig array (+1 buy / -1 sell / 0) over [start,end)."""
    src = hlc3(h, l, c)
    if emas is None:
        emas = build_emas(src, preset)
    n = src.shape[0]
    sig = np.zeros(n, np.int8)

    if mode == "continuation":
        allbull, allbear = _isbull_stack(emas)
        fresh_b = np.zeros(n, bool); fresh_s = np.zeros(n, bool)
        for i in range(5, n):
            if allbull[i] and not allbull[i-5:i].any(): fresh_b[i] = True
            if allbear[i] and not allbear[i-5:i].any(): fresh_s[i] = True
        cand_b, cand_s, sep = fresh_b, fresh_s, CONT_SEP
    elif mode == "reversal":
        s, L = emas[0], emas[-1]
        cand_b = np.zeros(n, bool); cand_s = np.zeros(n, bool)
        cand_b[1:] = (s[1:] > L[1:]) & (s[:-1] <= L[:-1])
        cand_s[1:] = (s[1:] < L[1:]) & (s[:-1] >= L[:-1])
        sep = REV_SEP
    else:
        raise ValueError(mode)

    if use_macd and hist is None:
        hist = macd_hist(c)

    last_b = -10**9; last_s = -10**9
    lo = max(start, 4)
    for i in range(lo, end):
        if cand_b[i]:
            ok = (not use_macd) or (hist[i] > hist[i-1] and hist[i] >= 0.0)
            if ok and i - last_b > sep:
                sig[i] = 1; last_b = i
        elif cand_s[i]:
            ok = (not use_macd) or (hist[i] < hist[i-1] and hist[i] <= 0.0)
            if ok and i - last_s > sep:
                sig[i] = -1; last_s = i
    return sig
