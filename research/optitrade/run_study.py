"""
run_study.py -- OptiTrade honest backtest study over the Binance-perp corpus.

For each (coin, timeframe):
  * Walk-forward: grid-optimize on first 70% (IS), freeze, evaluate on last 30%
    (OOS). Objective = max GROSS sum-R subject to IS n>=30 (else relax + flag).
  * Fixed-default baseline (L=30, slMult=2.1, RR=3.5, bias=5), no optimization.
  * SL-first primary; TP-first sum-R reported as a sensitivity column.
  * GROSS primary; net columns at 0.06%/side and 0.04%/side taker (both sides).
Plus a one-off vendor-methodology reproduction on BTC 15m (full-history grid,
best combo, TP-first, ZERO fees) shown next to the honest OOS number.

Read-only. Writes results to CSV/MD in this folder only.
Run: python run_study.py
"""
import sqlite3, time, json, sys, csv
import numpy as np
import optitrade_bt as bt

CORPUS = r"C:\Users\AA Incorporado\Desktop\backtest_corpus\binance_perp_corpus.db"
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TFS = ["3m", "15m", "1h", "4h", "1d"]        # the 5 requested (1m present but not requested)

LS      = [10, 15, 20, 25, 30, 35, 40, 45, 50]
SLMULTS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
RRS     = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
BIASES  = [0, 2, 4, 6, 8, 10]
FIXED   = dict(L=30, slMult=2.1, RR=3.5, bias=5)

MINSEP = 6
WARMUP = 120           # skip until slow-EMA(max 110)+RSI/ATR are stable
SPLIT  = 0.70
FEES   = (0.0006, 0.0004)
MIN_N  = 30

def load(symbol, tf):
    con = sqlite3.connect(f"file:{CORPUS}?mode=ro", uri=True)
    cur = con.cursor()
    rows = cur.execute(
        "select ts_ms, open, high, low, close from bars "
        "where symbol=? and timeframe=? order by ts_ms", (symbol, tf)).fetchall()
    con.close()
    a = np.array(rows, np.float64)
    ts = a[:, 0].astype(np.int64)
    return ts, np.ascontiguousarray(a[:,1]), np.ascontiguousarray(a[:,2]), \
           np.ascontiguousarray(a[:,3]), np.ascontiguousarray(a[:,4])

def per_L_cache(c, high, low):
    cache = {}
    for L in LS:
        fast = bt.ema(c, L)
        slow = bt.ema(c, round(L * 2.2))
        cu, cd = bt.cross_arrays(fast, slow)
        cache[L] = (fast, slow, cu, cd)
    return cache

def grid_best(o,h,l,c, atr, rsi, cache, start, end, sl_first, objective_minn):
    """Return (best_params, best_metrics) maximizing gross sumR s.t. n>=objective_minn."""
    best_c = None; best_c_score = -1e18   # constrained (n>=minn)
    best_a = None; best_a_score = -1e18   # any
    for L in LS:
        fast, slow, cu, cd = cache[L]
        for bias in BIASES:
            sig = bt.gen_signals(cu, cd, rsi, fast, slow, bias, MINSEP, start, end)
            for slMult in SLMULTS:
                for RR in RRS:
                    tr = bt.simulate(o,h,l,c, atr, sig, slMult, RR, start, end, sl_first)
                    n = tr[0].shape[0]
                    s = float(tr[2].sum())      # gross sumR
                    if s > best_a_score:
                        best_a_score = s; best_a = (L,slMult,RR,bias)
                    if n >= objective_minn and s > best_c_score:
                        best_c_score = s; best_c = (L,slMult,RR,bias)
    if best_c is not None:
        return best_c, False
    return best_a, True   # relaxed (no combo hit n>=minn)

def eval_params(o,h,l,c, atr, rsi, cache, params, start, end):
    L,slMult,RR,bias = params
    fast, slow, cu, cd = cache[L]
    sig = bt.gen_signals(cu, cd, rsi, fast, slow, bias, MINSEP, start, end)
    tr_sl = bt.simulate(o,h,l,c, atr, sig, slMult, RR, start, end, True)
    tr_tp = bt.simulate(o,h,l,c, atr, sig, slMult, RR, start, end, False)
    m = bt.metrics(tr_sl, FEES)
    m["sumR_TPfirst"] = float(tr_tp[2].sum())
    m["n_TPfirst"] = int(tr_tp[0].shape[0])
    return m

def main():
    t0 = time.time()
    rows = []
    vendor = {}
    for coin in COINS:
        for tf in TFS:
            tc = time.time()
            ts,o,h,l,c = load(coin, tf)
            N = len(c)
            atr = bt.atr_wilder(h,l,c,14)
            rsi = bt.rsi_wilder(c,14)
            cache = per_L_cache(c,h,l)
            split = int(N * SPLIT)
            is_start, is_end = WARMUP, split
            oos_start, oos_end = split, N

            # --- walk-forward: optimize on IS (SL-first), freeze ---
            wf_params, relaxed = grid_best(o,h,l,c, atr, rsi, cache,
                                           is_start, is_end, True, MIN_N)
            wf_is  = eval_params(o,h,l,c, atr, rsi, cache, wf_params, is_start, is_end)
            wf_oos = eval_params(o,h,l,c, atr, rsi, cache, wf_params, oos_start, oos_end)

            # --- fixed-default baseline (no optimization) ---
            fx = (FIXED["L"], FIXED["slMult"], FIXED["RR"], FIXED["bias"])
            fx_is  = eval_params(o,h,l,c, atr, rsi, cache, fx, is_start, is_end)
            fx_oos = eval_params(o,h,l,c, atr, rsi, cache, fx, oos_start, oos_end)

            def daterange(a,b):
                import datetime as dt
                f=lambda x: dt.datetime.utcfromtimestamp(ts[x]/1000).strftime("%Y-%m-%d")
                return f(a), f(min(b, N-1))

            is_from, is_to = daterange(is_start, is_end-1)
            oos_from, oos_to = daterange(oos_start, oos_end-1)

            for cfg, params, mis, moos, extra in [
                ("WF-winner", wf_params, wf_is, wf_oos, {"relaxed_IS": relaxed}),
                ("Fixed-default", fx, fx_is, fx_oos, {}),
            ]:
                rows.append(dict(
                    coin=coin, tf=tf, config=cfg,
                    L=params[0], slMult=params[1], RR=params[2], bias=params[3],
                    IS_from=is_from, IS_to=is_to, OOS_from=oos_from, OOS_to=oos_to,
                    IS_n=mis["n"], IS_WR=mis["wr"], IS_avgR=mis["avgR"],
                    IS_sumR=mis["sumR"], IS_PF=mis["pf"], IS_maxDD=mis["maxdd"],
                    IS_net06=mis["net_sumR_0.0006"], IS_net04=mis["net_sumR_0.0004"],
                    IS_sumR_TPfirst=mis["sumR_TPfirst"],
                    OOS_n=moos["n"], OOS_WR=moos["wr"], OOS_avgR=moos["avgR"],
                    OOS_sumR=moos["sumR"], OOS_PF=moos["pf"], OOS_maxDD=moos["maxdd"],
                    OOS_net06=moos["net_sumR_0.0006"], OOS_net04=moos["net_sumR_0.0004"],
                    OOS_sumR_TPfirst=moos["sumR_TPfirst"],
                    OOS_insufficient=(moos["n"] < MIN_N),
                    survives_OOS=(moos["n"] >= MIN_N and moos["sumR"] > 0),
                    **extra))

            # --- vendor reproduction: BTC 15m only ---
            if coin == "BTCUSDT" and tf == "15m":
                vp, vrelax = grid_best(o,h,l,c, atr, rsi, cache,
                                       WARMUP, N, False, MIN_N)  # full history, TP-first
                # vendor headline: best combo, TP-first, ZERO fees (gross==net)
                fast,slow,cu,cd = cache[vp[0]]
                sig = bt.gen_signals(cu,cd,rsi,fast,slow,vp[3],MINSEP,WARMUP,N)
                tr = bt.simulate(o,h,l,c,atr,sig,vp[1],vp[2],WARMUP,N,False)
                vm = bt.metrics(tr, FEES)
                vendor = dict(params=vp, relaxed=vrelax, full_from=daterange(WARMUP,N-1)[0],
                              full_to=daterange(WARMUP,N-1)[1],
                              n=vm["n"], sumR_TPfirst_zerofee=float(tr[2].sum()),
                              WR=vm["wr"], PF=vm["pf"],
                              honest_OOS_sumR=wf_oos["sumR"],
                              honest_OOS_n=wf_oos["n"], honest_OOS_params=wf_params,
                              honest_OOS_net06=wf_oos["net_sumR_0.0006"])
            print(f"  {coin:8s} {tf:4s} N={N:>8,} split={split:>7,} "
                  f"WF{wf_params} OOS_n={wf_oos['n']:>4} OOS_sumR={wf_oos['sumR']:+.2f} "
                  f"({time.time()-tc:.1f}s)")

    # write CSV
    keys = list(rows[0].keys())
    with open("results_full.csv","w",newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=keys, lineterminator="\n"); w.writeheader()
        for r in rows: w.writerow(r)
    with open("results_vendor.json","w",newline="\n") as fp:
        json.dump(vendor, fp, indent=2, default=str); fp.write("\n")
    print(f"\nDONE {len(rows)} rows, {time.time()-t0:.1f}s total. "
          f"-> results_full.csv, results_vendor.json")

if __name__ == "__main__":
    main()
