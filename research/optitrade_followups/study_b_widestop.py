"""
STUDY B -- wide-stop trend-cross, SOL-anchored.

PRE-REGISTRATION (stated before results): the signal family is the OptiTrade
study-A EMA-cross (fast L, slow round(2.2L)) + RSI(14) bias, chosen because SOL 1h
was the 5/5-window gross-persistent cell in that study. **SOL 1h is the selection
coin; BTC/ETH/XRP 1h are the pre-registered falsification (travel test).** ONE fixed
config, NO optimization anywhere.

Signal (optitrade_bt.gen_signals): L=30 -> fast=EMA(close,30), slow=EMA(close,66);
long = crossover(fast,slow) & fast>slow & RSI(14)>55; short = mirror RSI<45;
cooldown minSep=6 (bars since last same-dir emission); entry at signal-bar close;
one position at a time.

Geometry (construct lesson, REPLACES the 4-rung scaled TPs): single wide stop
3.0*ATR(14), single TP at 3R (== entry +/- 9*ATR), no rungs, no management, SL-first.

5 equal windows over post-warmup history (fixed config -> all out-of-sample).
GROSS primary; net06/net04 = 0.06%/0.04% taker per side. Drift-controlled
permutation p per coin (200 perms: per-window direction counts preserved, entry
times shuffled, same single-TP bracket). Binance-perp corpus. Counts only.
"""
import sys, csv
import numpy as np
from numba import njit
sys.path.insert(0, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade")
import optitrade_bt as bt
import run_study as R

COINS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]   # SOL first = selection coin
L, SLOW = 30, round(30 * 2.2)   # 66
BIAS, MINSEP = 5, 6
SLMULT, RR = 3.0, 3.0
WARMUP, NWIN, NPERM = 120, 5, 200
FEES = (0.0006, 0.0004)
RNG = np.random.default_rng(31337)

@njit
def sim_single(o, h, l, c, atr, sig, slMult, rr, start, end, sl_first):
    n = c.shape[0]
    cap = 0
    for i in range(start, end):
        if sig[i] != 0:
            cap += 1
    e_idx = np.empty(cap, np.int64); e_dir = np.empty(cap, np.int8)
    g = np.empty(cap, np.float64); epx = np.empty(cap, np.float64)
    xpx = np.empty(cap, np.float64); rpx = np.empty(cap, np.float64)
    nt = 0; i = start
    while i < end:
        d = sig[i]
        if d == 0:
            i += 1; continue
        a = atr[i]
        if not (a > 0.0):
            i += 1; continue
        entry = c[i]; Rd = slMult * a
        SL = entry - d * Rd; TP = entry + d * rr * Rd
        gr = 2.0; xp = 0.0; xidx = end - 1; done = False
        j = i + 1
        while j < end:
            hi = h[j]; lo = l[j]
            stop = (lo <= SL) if d > 0 else (hi >= SL)
            tp = (hi >= TP) if d > 0 else (lo <= TP)
            if sl_first:
                if stop:
                    gr = -1.0; xp = SL; xidx = j; done = True; break
                if tp:
                    gr = rr; xp = TP; xidx = j; done = True; break
            else:
                if tp:
                    gr = rr; xp = TP; xidx = j; done = True; break
                if stop:
                    gr = -1.0; xp = SL; xidx = j; done = True; break
            j += 1
        if not done:
            lc = c[end - 1]; gr = ((lc - entry) / Rd) * d; xp = lc; xidx = end - 1
        e_idx[nt] = i; e_dir[nt] = d; g[nt] = gr; epx[nt] = entry; xpx[nt] = xp; rpx[nt] = Rd
        nt += 1; i = xidx + 1
    return e_idx[:nt], e_dir[:nt], g[:nt], epx[:nt], xpx[:nt], rpx[:nt]

def signals(o, h, l, c, N):
    fast = bt.ema(c, L); slow = bt.ema(c, SLOW)
    cu, cd = bt.cross_arrays(fast, slow); rsi = bt.rsi_wilder(c, 14)
    return bt.gen_signals(cu, cd, rsi, fast, slow, BIAS, MINSEP, WARMUP, N)

def edges(N):
    wl = (N - WARMUP) // NWIN
    return [(WARMUP + k*wl, (WARMUP + (k+1)*wl) if k < NWIN-1 else N) for k in range(NWIN)]

def perwin(tr, N):
    eidx, d, g, epx, xpx, rpx = tr
    net6 = g - 0.0006*(epx + xpx)/rpx
    net4 = g - 0.0004*(epx + xpx)/rpx
    out = []
    for (lo, hi) in edges(N):
        m = (eidx >= lo) & (eidx < hi)
        out.append(dict(n=int(m.sum()), gross=round(float(g[m].sum()), 2),
                        net06=round(float(net6[m].sum()), 2), net04=round(float(net4[m].sum()), 2)))
    return out

def drift_p(o, h, l, c, atr, sig, N):
    idx = np.where(sig != 0)[0]; dirs = sig[idx].astype(np.int8)
    eg = edges(N)
    def tot_net06(s):
        tr = sim_single(o, h, l, c, atr, s, SLMULT, RR, WARMUP, N, True)
        _, _, g, epx, xpx, rpx = tr
        return float((g - 0.0006*(epx + xpx)/rpx).sum())
    obs = tot_net06(sig)
    ge = 0
    for _ in range(NPERM):
        s = np.zeros(N, np.int8)
        for (lo, hi) in eg:
            mm = (idx >= lo) & (idx < hi); k = int(mm.sum())
            if k == 0: continue
            pos = RNG.choice(np.arange(lo, hi-1), size=k, replace=False)
            s[pos] = dirs[mm]
        if tot_net06(s) >= obs: ge += 1
    return obs, ge / NPERM

def main():
    rows = []; blocks = []
    print("STUDY B: SOL selection coin; BTC/ETH/XRP travel test. One fixed config, no optimization.\n")
    for coin in COINS:
        ts, o, h, l, c = R.load(coin, "1h"); N = len(c)
        atr = bt.atr_wilder(h, l, c, 14)
        sig = signals(o, h, l, c, N)
        tr = sim_single(o, h, l, c, atr, sig, SLMULT, RR, WARMUP, N, True)
        pw = perwin(tr, N)
        obs, p = drift_p(o, h, l, c, atr, sig, N)
        tot = dict(n=sum(w["n"] for w in pw), gross=sum(w["gross"] for w in pw),
                   net06=sum(w["net06"] for w in pw), net04=sum(w["net04"] for w in pw))
        role = "SELECTION" if coin == "SOLUSDT" else "travel"
        blocks.append((coin, role, pw, tot, p))
        for k, w in enumerate(pw):
            rows.append(dict(coin=coin.replace("USDT",""), role=role, window=k, **w))
        print(f"  {coin.replace('USDT',''):4s} [{role:9s}] n={tot['n']:>4} gross={tot['gross']:+7.1f} "
              f"net06={tot['net06']:+7.1f} p={p:.3f}")

    with open("study_b_results.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows: w.writerow(r)
    render(blocks)
    print("\nwrote study_b_results.csv + STUDY_B.md")

def render(blocks):
    O = []
    O.append("# STUDY B -- wide-stop trend-cross, SOL-anchored (counts only)\n")
    O.append("**Pre-registration:** signal = OptiTrade study-A EMA-cross (L=30 -> EMA30/EMA66) + RSI(14) "
             "bias 5, minSep 6, entry at signal close, one position at a time. Geometry REPLACED with a "
             "single wide stop 3.0*ATR(14) + single TP at 3R (no rungs, no management), SL-first. "
             "**SOL 1h = selection coin; BTC/ETH/XRP 1h = pre-registered falsification.** ONE fixed config, "
             "NO optimization. 5 equal windows over post-warmup history (all out-of-sample). Binance-perp. "
             "GROSS primary; net06/net04 = 0.06%/0.04% per side. p = drift-controlled permutation "
             "(200 perms, per-window direction counts preserved, times shuffled).\n")
    O.append("## Rollup (per coin)\n")
    O.append("| coin | role | n | gross | net06 | net04 | drift p |")
    O.append("|---|---|--:|--:|--:|--:|--:|")
    for coin, role, pw, tot, p in blocks:
        O.append(f"| {coin.replace('USDT','')} | {role} | {tot['n']} | {tot['gross']:+.1f} | "
                 f"{tot['net06']:+.1f} | {tot['net04']:+.1f} | {p:.3f} |")
    O.append("\n## Per-window (5 windows): n / gross / net06 / net04\n")
    for coin, role, pw, tot, p in blocks:
        O.append(f"**{coin.replace('USDT','')} ({role})**\n")
        O.append("| window | n | gross | net06 | net04 |")
        O.append("|--:|--:|--:|--:|--:|")
        for k, w in enumerate(pw):
            O.append(f"| {k} | {w['n']} | {w['gross']:+} | {w['net06']:+} | {w['net04']:+} |")
        O.append("")
    O.append("_Counts only, no verdicts. Full per-window in study_b_results.csv._")
    O.append("\n## Reproduce\n`python study_b_widestop.py` -> study_b_results.csv + this file.")
    open("STUDY_B.md", "w", newline="\n").write("\n".join(O) + "\n")

if __name__ == "__main__":
    main()
