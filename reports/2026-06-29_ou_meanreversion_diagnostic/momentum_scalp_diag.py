import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']
TFS = ['bars_3m','bars_15m']

MA_BARS = 200
SIG_W   = 200
HI      = 2.5
N_FWD   = 200
N_NULL  = 20
NULL_CAP = 400
RT_FEE  = 0.0008          # 8bps round-trip taker+slip
STOPS   = [1.0, 2.5]      # stop = price retraces X·σ_entry from entry (1.0≈"inside 1.5σ", 2.5≈"back to anchor")
RMULTS  = [2, 3, 5]

def load(c, tf):
    con = sqlite3.connect(f"{DATA}\\{c}_scalping.db")
    d = pd.read_sql_query(f"SELECT ts, high, low, close, volume FROM {tf} ORDER BY ts", con)
    con.close()
    return d

def session_vwap(ts, typ, vol):
    day = ts // 86400
    df = pd.DataFrame({'d': day, 'tv': typ*vol, 'v': vol})
    return (df.groupby('d')['tv'].cumsum() / df.groupby('d')['v'].cumsum()).values

def build_z(ts, high, low, close, vol, anchor):
    typ = (high+low+close)/3.0
    anc = (pd.Series(close).rolling(MA_BARS, min_periods=MA_BARS).mean().values
           if anchor == 'MA200' else session_vwap(ts, typ, vol))
    dev = close - anc
    sig = pd.Series(dev).rolling(SIG_W, min_periods=SIG_W).std(ddof=0).values
    with np.errstate(invalid='ignore', divide='ignore'):
        z = dev / sig
    return z, sig

def detect(z, side):
    a = ((z >= HI) if side == 1 else (z <= -HI)) & np.isfinite(z)
    prev = np.concatenate([[False], a[:-1]])
    return np.where(a & ~prev)[0]

def event_pnls(close, sig, idx, side, stop_mult):
    """side=+1 above→LONG(d=+1); side=-1 below→SHORT(d=-1). pnl in R (R=stop_dist=stop_mult·σ_e)."""
    d = side
    n = len(close)
    cfg = {f'{Rm}R': [] for Rm in RMULTS}; cfg['trail'] = []; cfg['hold'] = []
    for i in idx:
        se = sig[i]
        if not (np.isfinite(se) and se > 0): continue
        stop_d = stop_mult*se
        entry = close[i]
        j1, j2 = i+1, min(i+1+N_FWD, n)
        if j2 <= j1: continue
        seg = close[j1:j2]
        fav = d*(seg-entry)                       # favorable excursion (price)
        stop_hit = fav <= -stop_d
        sj = int(np.argmax(stop_hit)) if stop_hit.any() else 10**9
        for Rm in RMULTS:
            th = fav >= Rm*stop_d
            tj = int(np.argmax(th)) if th.any() else 10**9
            cfg[f'{Rm}R'].append(Rm if tj < sj else (-1.0 if sj < tj else fav[-1]/stop_d))
        peak = np.maximum.accumulate(np.maximum(fav, 0.0))
        ex = fav <= (peak - stop_d)
        tk = int(np.argmax(ex)) if ex.any() else 10**9
        cfg['trail'].append((fav[tk] if tk < 10**9 else fav[-1])/stop_d)
        cfg['hold'].append(-1.0 if sj < 10**9 else fav[-1]/stop_d)
    return {k: np.array(v) for k, v in cfg.items()}

def stats(arr):
    if arr.size == 0: return None
    win = arr > 0
    return dict(n=arr.size, winpct=float(win.mean()),
                awin=float(arr[win].mean()) if win.any() else 0.0,
                aloss=float(arr[~win].mean()) if (~win).any() else 0.0,
                exp=float(arr.mean()))

def null_exp(ts, close, vol, anchor, side, stop_mult, base_seed):
    out = {f'{Rm}R': [] for Rm in RMULTS}; out['trail'] = []; out['hold'] = []
    lr = np.diff(np.log(close)); c0 = np.log(close[0])
    for k in range(N_NULL):
        rng = np.random.default_rng(base_seed+k)
        perm = rng.permutation(len(lr))
        sc = np.exp(np.concatenate([[c0], c0+np.cumsum(lr[perm])]))
        sv = np.concatenate([[vol[0]], vol[1:][perm]])
        z, sig = build_z(ts, sc, sc, sc, sv, anchor)
        idx = detect(z, side)
        if len(idx) > NULL_CAP:
            idx = np.sort(rng.choice(idx, NULL_CAP, replace=False))
        p = event_pnls(sc, sig, idx, side, stop_mult)
        for kk in out:
            if p[kk].size: out[kk].append(float(p[kk].mean()))
    return {kk: np.array(v) for kk, v in out.items()}

CFGS = [f'{Rm}R' for Rm in RMULTS] + ['trail', 'hold']
for stop_mult in STOPS:
    for tf in TFS:
        for anchor in ['MA200', 'VWAP']:
            print(f"\n===== {tf} | {anchor} | stop={stop_mult}σ (R={stop_mult}σ_entry) =====")
            print(f"{'coin':>4}{'side':>6}{'n':>5} | {'tWin%':>6}{'tAwR':>6}{'tAlR':>6}{'tExpR':>6}{'tNet':>6}{'B':>2} |"
                  f"{'2Rnet':>6}{'B':>2}{'3Rnet':>6}{'B':>2}{'5Rnet':>6}{'B':>2}{'hNet':>6}{'B':>2}")
            for c in COINS:
                dd = load(c, tf)
                ts, hi, lo, cl, vo = dd.ts.values, dd.high.values, dd.low.values, dd.close.values, dd.volume.values
                z, sig = build_z(ts, hi, lo, cl, vo, anchor)
                sigfrac = float(np.nanmean(sig/cl))
                sdf = stop_mult*sigfrac                      # stop distance as fraction of price
                for side, sname in [(1, 'above'), (-1, 'below')]:
                    idx = detect(z, side)
                    p = event_pnls(cl, sig, idx, side, stop_mult)
                    st = {k: stats(p[k]) for k in CFGS}
                    if st['trail'] is None:
                        print(f"{c:>4}{sname:>6}  (no events)"); continue
                    ne = null_exp(ts, cl, vo, anchor, side, stop_mult,
                                  base_seed=100000*COINS.index(c)+(0 if side == 1 else 50000)+int(stop_mult*10))
                    def net(k):  return (st[k]['exp']*sdf - RT_FEE)*1e4
                    def beats(k):
                        nd = ne[k]
                        return 'Y' if (nd.size and st[k]['exp'] > 0 and st[k]['exp'] > np.percentile(nd, 95)) else '.'
                    t = st['trail']
                    print(f"{c:>4}{sname:>6}{t['n']:>5} | {100*t['winpct']:>5.0f}%{t['awin']:>6.2f}{t['aloss']:>6.2f}"
                          f"{t['exp']:>6.2f}{net('trail'):>6.0f}{beats('trail'):>2} |"
                          f"{net('2R'):>6.0f}{beats('2R'):>2}{net('3R'):>6.0f}{beats('3R'):>2}"
                          f"{net('5R'):>6.0f}{beats('5R'):>2}{net('hold'):>6.0f}{beats('hold'):>2}")
print("\nR=stop distance (stop_mult·σ_entry). tWin%/tAwR/tAlR/tExpR = trailing-stop win%, avgWin R, avgLoss R, expectancy R.")
print(f"*Net = net bps/trade after {RT_FEE*1e4:.0f}bps RT fee. B=beats null (exp>0 & > null 95th pct). configs: 2R/3R/5R fixed, trail, hold.")
