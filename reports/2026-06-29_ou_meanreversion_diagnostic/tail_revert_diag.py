import sqlite3, math, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']

# ---- params (all causal/trailing; stated explicitly) ----
MA_W   = 4800     # 200-day MA (4800 1h bars), trailing SMA
VWAP_W = 4800     # trailing-window VWAP over same 200d (typical=(h+l+c)/3); causal rolling
SIG_W  = 720      # sigma = trailing std of (price - anchor), 30-day window
HI, REARM = 2.5, 2.0   # stretch onset >=2.5 sigma; re-arm after |z| drops below 2.0 (hysteresis)
K      = 1.5      # price bracket: +/-1.5*sigma_entry  (entry@2.5 -> fav ~1.0sigma, adv ~4.0sigma; symmetric R)
N_FWD  = 720      # outcome horizon = 30 days forward
N_NULL = 100      # null replicates

def load(c):
    con = sqlite3.connect(f"{DATA}\\{c}_scalping.db")
    d = pd.read_sql_query("SELECT ts, high, low, close, volume FROM bars_1h ORDER BY ts", con)
    con.close()
    return d

def roll_mean(x, w):  return pd.Series(x).rolling(w, min_periods=w).mean().values
def roll_std(x, w):   return pd.Series(x).rolling(w, min_periods=w).std(ddof=0).values
def roll_vwap(typ, vol, w):
    num = pd.Series(typ*vol).rolling(w, min_periods=w).sum().values
    den = pd.Series(vol).rolling(w, min_periods=w).sum().values
    return num/den

def zscore(price, anchor):
    sig = roll_std(price - anchor, SIG_W)
    with np.errstate(invalid='ignore', divide='ignore'):
        z = (price - anchor) / sig
    return z, sig

def detect_events(z):
    ev = []  # (idx, side)
    armed = True
    for i in range(len(z)):
        zi = z[i]
        if not np.isfinite(zi): continue
        if armed and abs(zi) >= HI:
            ev.append((i, 1 if zi > 0 else -1)); armed = False
        elif (not armed) and abs(zi) < REARM:
            armed = True
    return ev

def outcome(close, sig, events):
    """price-based first-passage, close-to-close (fair vs shuffled null). side=+1 above-anchor (fade short),
       -1 below (fade long). fav = price moves K*sigma_e toward anchor; adv = K*sigma_e further."""
    out = []
    n = len(close)
    for i, s in events:
        se = sig[i]
        if not (np.isfinite(se) and se > 0): continue
        entry = close[i]
        j1, j2 = i+1, min(i+1+N_FWD, n)
        if j2 <= j1: continue
        seg = close[j1:j2]
        fav_lvl = entry - s*K*se          # toward anchor
        adv_lvl = entry + s*K*se          # further from anchor
        fav_hit = (seg <= fav_lvl) if s == 1 else (seg >= fav_lvl)
        adv_hit = (seg >= adv_lvl) if s == 1 else (seg <= adv_lvl)
        fj = np.argmax(fav_hit) if fav_hit.any() else 10**9
        aj = np.argmax(adv_hit) if adv_hit.any() else 10**9
        adv_exc = (seg - entry) if s == 1 else (entry - seg)   # adverse excursion in price
        mae = max(0.0, float(adv_exc.max())) / se
        if fj < aj:
            out.append(('revert',  K,  fj+1, mae))
        elif aj < fj:
            out.append(('continue', -K, aj+1, mae))
        else:  # neither within horizon -> settle at terminal close
            pnl = s*(entry - seg[-1]) / se
            out.append(('timeout', pnl, len(seg), mae))
    return out

def agg(out):
    if not out: return None
    pnl = np.array([o[1] for o in out])
    lab = [o[0] for o in out]
    mae = np.array([o[3] for o in out])
    nrev = sum(l == 'revert' for l in lab); ncon = sum(l == 'continue' for l in lab); nto = sum(l == 'timeout' for l in lab)
    rev_pnl = pnl[[l == 'revert' for l in lab]]; con_pnl = pnl[[l == 'continue' for l in lab]]
    con_mae = mae[[l == 'continue' for l in lab]]
    return dict(n=len(out), revpct=nrev/len(out), conpct=ncon/len(out), topct=nto/len(out),
                avg_rev=float(rev_pnl.mean()) if len(rev_pnl) else 0.0,
                avg_con=float(con_pnl.mean()) if len(con_pnl) else 0.0,
                con_mae=float(con_mae.mean()) if len(con_mae) else 0.0,
                exp=float(pnl.mean()))

def run_series(high, low, close, vol, anchor_name):
    typ = (high+low+close)/3.0
    anchor = roll_mean(close, MA_W) if anchor_name == 'MA200' else roll_vwap(typ, vol, VWAP_W)
    z, sig = zscore(close, anchor)
    ev = detect_events(z)
    res = {}
    for s, name in [(1, 'above'), (-1, 'below')]:
        es = [(i, ss) for (i, ss) in ev if ss == s]
        res[name] = agg(outcome(close, sig, es))
    return res

def shuffled_close(close, vol, seed):
    rng = np.random.default_rng(seed)
    lr = np.diff(np.log(close))
    perm = rng.permutation(len(lr))
    sclose = np.exp(np.concatenate([[np.log(close[0])], np.log(close[0]) + np.cumsum(lr[perm])]))
    svol = np.concatenate([[vol[0]], vol[1:][perm]])   # carry (ret,vol) pairs to keep joint dist for VWAP
    return sclose, svol

print(f"params: MA200={MA_W}b  VWAP200={VWAP_W}b  sigma={SIG_W}b(30d)  onset={HI}σ rearm={REARM}σ  "
      f"bracket=±{K}σ_entry  horizon={N_FWD}b(30d)  nulls={N_NULL}\n")

for c in COINS:
    d = load(c)
    high, low, close, vol = d.high.values, d.low.values, d.close.values, d.volume.values
    print(f"================  {c.upper()}  (n={len(close)})  ================")
    for anchor in ['MA200', 'VWAP200']:
        real = run_series(high, low, close, vol, anchor)
        # null: shuffle returns(+vol), close-only proxy for high/low/typ
        null_rev = {'above': [], 'below': []}
        null_exp = {'above': [], 'below': []}
        for k in range(N_NULL):
            sclose, svol = shuffled_close(close, vol, seed=1000*COINS.index(c)+k)
            nres = run_series(sclose, sclose, sclose, svol, anchor)   # high=low=close for null
            for side in ('above', 'below'):
                if nres[side]:
                    null_rev[side].append(nres[side]['revpct']); null_exp[side].append(nres[side]['exp'])
        print(f"  [{anchor}]")
        print(f"  {'side':>6} {'#ev':>4} {'rev%':>6} {'avgRev':>7} {'avgCon':>7} {'exp(σ)':>7} {'conMAE':>7} {'to%':>5} | {'nullRev%':>8} {'nullExp':>8} {'beats?':>6}")
        for side in ('above', 'below'):
            r = real[side]
            if not r:
                print(f"  {side:>6}  (no events)"); continue
            nr = np.array(null_rev[side]); ne = np.array(null_exp[side])
            nr_med = float(np.median(nr)) if len(nr) else float('nan')
            ne_med = float(np.median(ne)) if len(ne) else float('nan')
            # beats = real revert% above 95th pct of null AND real expectancy above 95th pct of null AND exp>0
            beats = bool(len(nr) and r['revpct'] > np.percentile(nr, 95) and r['exp'] > np.percentile(ne, 95) and r['exp'] > 0)
            print(f"  {side:>6} {r['n']:>4} {100*r['revpct']:>5.1f}% {r['avg_rev']:>7.2f} {r['avg_con']:>7.2f} {r['exp']:>7.3f} {r['con_mae']:>7.2f} {100*r['topct']:>4.0f}% | "
                  f"{100*nr_med:>7.1f}% {ne_med:>8.3f} {str(beats):>6}")
    print()
print("rev%=revert rate (price reaches anchor-ward 1.5σ before extending 1.5σ, within 30d). exp in σ_entry units "
      "(win=+1.5, loss=-1.5, timeout=terminal). conMAE=avg max adverse excursion of continuers (the unbounded tail).")
