import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']
TFS = ['bars_3m','bars_15m']

# ---- params (causal/trailing) ----
MA_BARS = 200          # 200-BAR MA (3m=10h, 15m=50h)
SIG_W   = 200          # sigma = trailing 200-bar std of (price - anchor)
HI, K   = 2.5, 1.5     # onset |z|>=2.5 ; bracket +/-1.5*sigma_entry (entry@2.5 -> revert@1.0 / continue@4.0)
N_FWD   = 200          # outcome horizon (bars)
N_NULL  = 30           # null replicates
NULL_CAP = 600         # cap events sampled per null series (revert-rate is stable; keeps null fast)
RT_FEE  = 0.0008       # round-trip taker+slip (8bps) primary; VIP3 ~3.8bps optimistic

def load(c, tf):
    con = sqlite3.connect(f"{DATA}\\{c}_scalping.db")
    d = pd.read_sql_query(f"SELECT ts, high, low, close, volume FROM {tf} ORDER BY ts", con)
    con.close()
    return d

def session_vwap(ts, typ, vol):
    day = (ts // 86400)
    df = pd.DataFrame({'d': day, 'tv': typ*vol, 'v': vol})
    return (df.groupby('d')['tv'].cumsum() / df.groupby('d')['v'].cumsum()).values

def build_z(ts, high, low, close, vol, anchor):
    typ = (high+low+close)/3.0
    if anchor == 'MA200':
        anc = pd.Series(close).rolling(MA_BARS, min_periods=MA_BARS).mean().values
    else:
        anc = session_vwap(ts, typ, vol)
    dev = close - anc
    sig = pd.Series(dev).rolling(SIG_W, min_periods=SIG_W).std(ddof=0).values
    with np.errstate(invalid='ignore', divide='ignore'):
        z = dev / sig
    return z, sig

def detect(z, side):
    a = (z >= HI) if side == 1 else (z <= -HI)
    a = a & np.isfinite(z)
    prev = np.concatenate([[False], a[:-1]])
    return np.where(a & ~prev)[0]     # fresh crossings into the tail

def outcome(close, sig, idx, side):
    n = len(close)
    labs, pnl_brk, pnl_hold, mae = [], [], [], []
    for i in idx:
        se = sig[i]
        if not (np.isfinite(se) and se > 0): continue
        entry = close[i]
        j1, j2 = i+1, min(i+1+N_FWD, n)
        if j2 <= j1: continue
        seg = close[j1:j2]
        fav = entry - side*K*se
        adv = entry + side*K*se
        fav_hit = (seg <= fav) if side == 1 else (seg >= fav)
        adv_hit = (seg >= adv) if side == 1 else (seg <= adv)
        fj = np.argmax(fav_hit) if fav_hit.any() else 10**9
        aj = np.argmax(adv_hit) if adv_hit.any() else 10**9
        adv_exc = (seg - entry) if side == 1 else (entry - seg)
        mae.append(max(0.0, float(adv_exc.max()))/se)
        term = side*(entry - seg[-1])/se
        if fj < aj:
            labs.append('rev'); pnl_brk.append(K); pnl_hold.append(K)
        elif aj < fj:
            labs.append('con'); pnl_brk.append(-K); pnl_hold.append(term)
        else:
            labs.append('to'); pnl_brk.append(term); pnl_hold.append(term)
    return labs, np.array(pnl_brk), np.array(pnl_hold), np.array(mae)

def cell(close, sig, idx, side):
    labs, brk, hold, mae = outcome(close, sig, idx, side)
    if not labs: return None
    nev = len(labs)
    rev = sum(l == 'rev' for l in labs)
    con_mask = np.array([l == 'con' for l in labs])
    con_mae = mae[con_mask]
    return dict(n=nev, revpct=rev/nev,
                exp_brk=float(brk.mean()), exp_hold=float(hold.mean()),
                con_mae_mean=float(con_mae.mean()) if con_mae.size else 0.0,
                con_mae_p95=float(np.percentile(con_mae, 95)) if con_mae.size else 0.0,
                con_mae_max=float(con_mae.max()) if con_mae.size else 0.0)

def null_dist(ts, close, vol, anchor, side, base_seed):
    revs, exps = [], []
    lr = np.diff(np.log(close)); c0 = np.log(close[0])
    for k in range(N_NULL):
        rng = np.random.default_rng(base_seed + k)
        perm = rng.permutation(len(lr))
        sclose = np.exp(np.concatenate([[c0], c0 + np.cumsum(lr[perm])]))
        svol = np.concatenate([[vol[0]], vol[1:][perm]])
        z, sig = build_z(ts, sclose, sclose, sclose, svol, anchor)
        idx = detect(z, side)
        if len(idx) > NULL_CAP:
            idx = np.sort(rng.choice(idx, NULL_CAP, replace=False))
        r = cell(sclose, sig, idx, side)
        if r: revs.append(r['revpct']); exps.append(r['exp_brk'])
    return np.array(revs), np.array(exps)

for tf in TFS:
    for anchor in ['MA200', 'VWAP']:
        print(f"\n================  {tf}  |  {anchor}  ================")
        print(f"{'coin':>4} {'side':>6} {'#ev':>6} {'rev%':>6} {'expBrk':>7} {'expHold':>8} "
              f"{'cMAEmn':>7} {'cMAEp95':>8} {'cMAEmax':>8} {'sig%':>6} {'net_bps':>8} {'nRev%':>6} {'beats?':>6}")
        for c in COINS:
            d = load(c, tf)
            ts, high, low, close, vol = d.ts.values, d.high.values, d.low.values, d.close.values, d.volume.values
            z, sig = build_z(ts, high, low, close, vol, anchor)
            sigfrac = float(np.nanmean(sig/close))
            for side, sname in [(1, 'above'), (-1, 'below')]:
                idx = detect(z, side)
                r = cell(close, sig, idx, side)
                if not r:
                    print(f"{c:>4} {sname:>6}  (no events)"); continue
                nr, ne = null_dist(ts, close, vol, anchor, side, base_seed=10000*COINS.index(c)+ (0 if side==1 else 5000))
                nr_med = float(np.median(nr)) if nr.size else float('nan')
                beats = bool(nr.size and r['revpct'] > np.percentile(nr, 95)
                             and r['exp_brk'] > np.percentile(ne, 95) and r['exp_brk'] > 0)
                gross = r['exp_brk'] * sigfrac
                net_bps = (gross - RT_FEE) * 1e4
                print(f"{c:>4} {sname:>6} {r['n']:>6} {100*r['revpct']:>5.1f}% {r['exp_brk']:>7.3f} {r['exp_hold']:>8.3f} "
                      f"{r['con_mae_mean']:>7.2f} {r['con_mae_p95']:>8.2f} {r['con_mae_max']:>8.1f} {100*sigfrac:>5.2f}% "
                      f"{net_bps:>8.1f} {100*nr_med:>5.1f}% {str(beats):>6}")
print("\nexpBrk/expHold in sigma_entry units (bracket ±1.5σ stop vs TP-only-no-stop held to horizon).")
print(f"net_bps = (expBrk × sig%) − {RT_FEE*1e4:.0f}bps round-trip fee, per trade. sig%=mean σ_entry/price.")
print("conMAE = continuer max-adverse-excursion (σ); close-based (intrabar tails are WORSE). detect=fresh 2.5σ crossing.")
