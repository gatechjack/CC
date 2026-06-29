import sqlite3, math, itertools
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller, coint

DATA = r"C:\Users\AA Incorporado\cc\data"
COINS = ['btc','eth','sol','xrp']
LO, HI = 1722801600, 1781830800   # validated common window (BTC binds both ends)

# ---- load aligned log-prices on the common 16,398-bar grid ----
logp = {}
for c in COINS:
    con = sqlite3.connect(f"{DATA}\\{c}_scalping.db")
    d = pd.read_sql_query("SELECT ts, close FROM bars_1h WHERE ts BETWEEN ? AND ? ORDER BY ts",
                          con, params=(LO, HI))
    con.close()
    logp[c] = np.log(d['close'].astype(float).values)
n = len(logp['btc'])
assert all(len(v) == n for v in logp.values()), "alignment broke"
print(f"aligned log-price grid: {n} bars x {len(COINS)} coins\n")

LN2 = math.log(2)
def eg_resid(yA, yB):
    # OLS yA ~ const + beta*yB  -> hedge ratio beta, residual spread
    X = np.column_stack([np.ones_like(yB), yB])
    coef, *_ = np.linalg.lstsq(X, yA, rcond=None)
    beta = coef[1]
    resid = yA - X @ coef
    return beta, resid

def ou_hl(r):
    rl, dr = r[:-1], np.diff(r)
    X = np.column_stack([np.ones_like(rl), rl]); coef,*_ = np.linalg.lstsq(X, dr, rcond=None)
    theta = -coef[1]
    return (LN2/theta) if theta > 0 else float('inf')

def ar1(r):
    return float(np.corrcoef(r[:-1], r[1:])[0, 1])

def adf_p(r, maxlag=6):
    return adfuller(r, maxlag=maxlag, autolag=None)[1]

def metrics(yA, yB):
    beta, r = eg_resid(yA, yB)
    return beta, ou_hl(r), ar1(r), adf_p(r), r

def zexc(r):
    z = (r - r.mean()) / r.std()
    hot = np.abs(z) >= 2.0
    return float(hot.mean()), int(np.sum(hot[1:] & ~hot[:-1]))

np.random.seed(7)
NULL = 100
PAIRS = list(itertools.combinations(COINS, 2))
print(f"{'pair':>9} | {'beta':>6} {'coint_p':>8} {'HL(h)':>7} {'AR1':>6} | {'%|z|>=2':>7} {'#ent':>5} | "
      f"{'nullHL_med':>10} {'nullAR1_med':>11} {'spurP<.05':>9} {'beats?':>6}")

results = []
for a, b in PAIRS:
    yA, yB = logp[a], logp[b]
    beta, hl, a1, ap, r = metrics(yA, yB)
    cp = coint(yA, yB)[1]                       # proper Engle-Granger coint p (autolag)
    frac, ent = zexc(r)

    # ---- NULL gate: shuffle leg B returns (primary) + two independent RWs (secondary) ----
    retB = np.diff(yB); retA = np.diff(yA)
    sigA, sigB = retA.std(), retB.std()
    n_hl_sh, n_ar_sh, n_p_sh = [], [], []
    n_hl_rw, n_ar_rw = [], []
    for _ in range(NULL):
        sh = retB.copy(); np.random.shuffle(sh)
        yB_sh = np.concatenate([[yB[0]], yB[0] + np.cumsum(sh)])
        _, h, q, p, _ = metrics(yA, yB_sh)
        n_hl_sh.append(h); n_ar_sh.append(q); n_p_sh.append(p)
        yA_rw = np.concatenate([[yA[0]], yA[0] + np.cumsum(np.random.normal(0, sigA, n-1))])
        yB_rw = np.concatenate([[yB[0]], yB[0] + np.cumsum(np.random.normal(0, sigB, n-1))])
        _, h2, q2, _, _ = metrics(yA_rw, yB_rw)
        n_hl_rw.append(h2); n_ar_rw.append(q2)

    n_hl_sh = np.array([x for x in n_hl_sh if np.isfinite(x)])
    nullHL_med = float(np.median(n_hl_sh)) if len(n_hl_sh) else float('inf')
    nullAR1_med = float(np.median(n_ar_sh))
    spur = float(np.mean(np.array(n_p_sh) < 0.05))         # spurious cointegration rate of null
    # beats null = real reverts faster than ~95% of nulls (lower HL) AND lower AR(1) than null median
    frac_null_faster = float(np.mean(n_hl_sh <= hl)) if len(n_hl_sh) else 1.0
    beats = (frac_null_faster < 0.05) and (a1 < nullAR1_med) and (cp < 0.05)

    hls = f"{hl:7.1f}" if math.isfinite(hl) else "    inf"
    nhls = f"{nullHL_med:10.1f}" if math.isfinite(nullHL_med) else "       inf"
    print(f"{a+'/'+b:>9} | {beta:6.3f} {cp:8.4f} {hls} {a1:6.3f} | {100*frac:6.1f}% {ent:5d} | "
          f"{nhls} {nullAR1_med:11.3f} {spur:9.2f} {str(beats):>6}")
    results.append(dict(pair=f"{a}/{b}", beta=beta, coint_p=cp, hl=hl, ar1=a1, beats=beats,
                        frac=frac, ent=ent, nullHL=nullHL_med, nullAR1=nullAR1_med, spur=spur,
                        frac_null_faster=frac_null_faster,
                        nullHL_rw=float(np.median([x for x in n_hl_rw if np.isfinite(x)])),
                        nullAR1_rw=float(np.median(n_ar_rw))))

print("\n--- null gate detail (shuffle-B primary, RW secondary) ---")
for x in results:
    print(f"{x['pair']:>9}: real HL={x['hl']:.1f}h AR1={x['ar1']:.3f}  | shufNull HL_med={x['nullHL']:.1f} AR1_med={x['nullAR1']:.3f} "
          f"frac_null_faster={x['frac_null_faster']:.2f} spurP<.05={x['spur']:.2f} | rwNull HL_med={x['nullHL_rw']:.1f} AR1_med={x['nullAR1_rw']:.3f}")
print("\nbeats? = real HL faster than 95% of nulls AND real AR1 < null AR1 median AND coint_p<0.05")
print("AR1≈0.93 was the single-asset trend signature; lower AR1 + HL well below null = genuine reversion.")
