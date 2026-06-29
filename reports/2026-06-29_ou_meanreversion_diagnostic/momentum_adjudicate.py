import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']
TF = 'bars_15m'                  # FOCUS: 15m only
MA_BARS, SIG_W, HI, N_FWD = 200, 200, 2.5, 200
STOP_MULT = 2.5                  # FOCUS: 2.5σ stop
TARGETS = ['3R','5R','hold']     # FOCUS: far targets
N_NULL, NULL_CAP, RT_FEE = 200, 400, 0.0008

def load(c):
    con = sqlite3.connect(f"{DATA}\\{c}_scalping.db")
    d = pd.read_sql_query(f"SELECT ts, high, low, close, volume FROM {TF} ORDER BY ts", con)
    con.close(); return d

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
        z = dev/sig
    return z, sig

def detect(z, side):
    a = ((z >= HI) if side == 1 else (z <= -HI)) & np.isfinite(z)
    prev = np.concatenate([[False], a[:-1]])
    return np.where(a & ~prev)[0]

def event_pnls(close, sig, idx, side):
    d, n = side, len(close)
    cfg = {'3R': [], '5R': [], 'hold': []}
    for i in idx:
        se = sig[i]
        if not (np.isfinite(se) and se > 0): continue
        stop_d = STOP_MULT*se; entry = close[i]
        j1, j2 = i+1, min(i+1+N_FWD, n)
        if j2 <= j1: continue
        seg = close[j1:j2]; fav = d*(seg-entry)
        stop_hit = fav <= -stop_d
        sj = int(np.argmax(stop_hit)) if stop_hit.any() else 10**9
        for Rm in (3, 5):
            th = fav >= Rm*stop_d; tj = int(np.argmax(th)) if th.any() else 10**9
            cfg[f'{Rm}R'].append(Rm if tj < sj else (-1.0 if sj < tj else fav[-1]/stop_d))
        cfg['hold'].append(-1.0 if sj < 10**9 else fav[-1]/stop_d)
    return {k: np.array(v) for k, v in cfg.items()}

def side_exp(ts, hi, lo, cl, vo, anchor, side, cap=None, rng=None):
    z, sig = build_z(ts, cl, cl, cl, vo, anchor) if cap is not None else build_z(ts, hi, lo, cl, vo, anchor)
    idx = detect(z, side)
    if cap is not None and len(idx) > cap:
        idx = np.sort(rng.choice(idx, cap, replace=False))
    p = event_pnls(cl, sig, idx, side)
    return {t: (float(p[t].mean()) if p[t].size else np.nan) for t in TARGETS}, (len(idx))

def shuffled_price(cl, vo, rng):
    lr = np.diff(np.log(cl)); c0 = np.log(cl[0]); perm = rng.permutation(len(lr))
    return np.exp(np.concatenate([[c0], c0+np.cumsum(lr[perm])])), np.concatenate([[vo[0]], vo[1:][perm]])

def signflip_price(cl, rng):
    lr = np.diff(np.log(cl)); c0 = np.log(cl[0]); s = rng.choice([-1.0, 1.0], size=len(lr))
    return np.exp(np.concatenate([[c0], c0+np.cumsum(lr*s)]))

print(f"FOCUS: {TF} | stop={STOP_MULT}σ | targets={TARGETS} | N_NULL={N_NULL} | RT_fee={RT_FEE*1e4:.0f}bps\n")

# precompute real
real = {}
for c in COINS:
    d = load(c); ts, hi, lo, cl, vo = d.ts.values, d.high.values, d.low.values, d.close.values, d.volume.values
    real[c] = dict(ts=ts, hi=hi, lo=lo, cl=cl, vo=vo)
    real[c]['data'] = d

print("="*96)
print("(a) MAGNITUDE GATE — downside SHORT expectancy vs N=200 SHUFFLED null (preserves fat marginal dist)")
print("="*96)
print(f"{'coin':>4}{'anchor':>7}{'nShort':>7} | " + "".join(f"{t+'exp':>7}{t+'net':>7}{'B':>2}" for t in TARGETS))
for c in COINS:
    r = real[c]
    for anchor in ['MA200', 'VWAP']:
        z, sig = build_z(r['ts'], r['hi'], r['lo'], r['cl'], r['vo'], anchor)
        sigfrac = float(np.nanmean(sig/r['cl'])); sdf = STOP_MULT*sigfrac
        idx = detect(z, -1); p = event_pnls(r['cl'], sig, idx, -1)
        rexp = {t: float(p[t].mean()) for t in TARGETS}; nshort = len(idx)
        # shuffled null distribution of short exp
        nd = {t: [] for t in TARGETS}
        for k in range(N_NULL):
            rng = np.random.default_rng(7000+1000*COINS.index(c)+k)
            sc, sv = shuffled_price(r['cl'], r['vo'], rng)
            e, _ = side_exp(r['ts'], None, None, sc, sv, anchor, -1, cap=NULL_CAP, rng=rng)
            for t in TARGETS:
                if np.isfinite(e[t]): nd[t].append(e[t])
        row = f"{c:>4}{anchor:>7}{nshort:>7} | "
        for t in TARGETS:
            arr = np.array(nd[t]); p95 = np.percentile(arr, 95)
            net = (rexp[t]*sdf - RT_FEE)*1e4
            B = 'Y' if (rexp[t] > 0 and rexp[t] > p95) else '.'
            row += f"{rexp[t]:>7.2f}{net:>7.0f}{B:>2}"
        print(row)

print("\n" + "="*96)
print("(b) ★ PAIRED ASYMMETRY — real (SHORT − LONG) expectancy vs N=200 DIRECTION-RANDOMIZED (sign-flip) null")
print("    sign-flip null CANNOT produce up/down asymmetry by construction -> null diff ~ 0. real diff > null p95 = REAL.")
print("="*96)
print(f"{'coin':>4}{'anchor':>7} | " + "".join(f"{t+':S':>6}{':L':>6}{':dif':>6}{'np95':>6}{'nmn':>6}{'B':>2}  " for t in TARGETS))
for c in COINS:
    r = real[c]
    for anchor in ['MA200', 'VWAP']:
        z, sig = build_z(r['ts'], r['hi'], r['lo'], r['cl'], r['vo'], anchor)
        ps = event_pnls(r['cl'], sig, detect(z, -1), -1)
        pl = event_pnls(r['cl'], sig, detect(z, 1), 1)
        rS = {t: float(ps[t].mean()) for t in TARGETS}; rL = {t: float(pl[t].mean()) for t in TARGETS}
        rdiff = {t: rS[t]-rL[t] for t in TARGETS}
        nd = {t: [] for t in TARGETS}
        for k in range(N_NULL):
            rng = np.random.default_rng(9000+1000*COINS.index(c)+k)
            sc = signflip_price(r['cl'], rng)
            zS, sgS = build_z(r['ts'], sc, sc, sc, r['vo'], anchor)
            iS, iL = detect(zS, -1), detect(zS, 1)
            if len(iS) < 5 or len(iL) < 5: continue
            if len(iS) > NULL_CAP: iS = np.sort(rng.choice(iS, NULL_CAP, replace=False))
            if len(iL) > NULL_CAP: iL = np.sort(rng.choice(iL, NULL_CAP, replace=False))
            eS = event_pnls(sc, sgS, iS, -1); eL = event_pnls(sc, sgS, iL, 1)
            for t in TARGETS:
                if eS[t].size and eL[t].size: nd[t].append(float(eS[t].mean())-float(eL[t].mean()))
        row = f"{c:>4}{anchor:>7} | "
        for t in TARGETS:
            arr = np.array(nd[t]); p95 = np.percentile(arr, 95); mn = float(arr.mean())
            B = 'Y' if rdiff[t] > p95 else '.'
            row += f"{rS[t]:>6.2f}{rL[t]:>6.2f}{rdiff[t]:>6.2f}{p95:>6.2f}{mn:>6.2f}{B:>2}  "
        print(row)
print("\nexp in R (R=2.5σ_entry). net=bps/trade after 8bps RT fee. (a)B=beats stable shuffled null. (b)B=short−long beats sign-flip null.")
