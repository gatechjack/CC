import sqlite3, math
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

# ---- load BTC 1h close ----
DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
con = sqlite3.connect(DB)
df = pd.read_sql_query("SELECT ts, close FROM bars_1h ORDER BY ts", con)
con.close()
close = df['close'].astype(float)
print(f"BTC bars_1h: n={len(close)}  (dt=1 bar=1h)")

# ---- OU closed form (OLS of d resid on resid_lag); proven pipeline ----
def ou_closed_form(x, dt=1.0):
    x = np.asarray(x, float)
    x_lag, dx = x[:-1], np.diff(x)
    A = np.column_stack([np.ones_like(x_lag), x_lag])
    coef, *_ = np.linalg.lstsq(A, dx, rcond=None)
    a, b = coef
    resid = dx - A @ coef
    theta = -b / dt
    if theta > 0:
        sigma = resid.std(ddof=2) * math.sqrt(2*theta / (1 - math.exp(-2*theta*dt)))
        half_life = math.log(2) / theta
    else:
        sigma, half_life = resid.std(ddof=2), float('inf')
    return theta, half_life, sigma

# ---- CAUSAL detrend: residual_t = close_t - mean(close[t-W+1 .. t]) ; trailing, inclusive of t, only bars <= t ----
WINDOWS = [24, 48, 96, 168, 336]   # hours
rows = []
for W in WINDOWS:
    tmean = close.rolling(W, min_periods=W).mean()          # causal trailing SMA (<= t)
    tstd  = close.rolling(W, min_periods=W).std(ddof=0)      # causal trailing std (<= t)
    resid = (close - tmean)
    z = (resid / tstd)
    r = resid.dropna().values
    zc = z.dropna().values

    theta, hl, sigma = ou_closed_form(r)
    adf_stat, adf_p, *_ = adfuller(r, autolag='AIC')

    # z excursions: fraction of bars |z|>=2, and # distinct entry events (upcross into |z|>=2)
    hot = np.abs(zc) >= 2.0
    frac = hot.mean()
    entries = int(np.sum(hot[1:] & ~hot[:-1]))   # transitions from calm -> |z|>=2
    rows.append(dict(W=W, theta=theta, hl=hl, adf_p=adf_p, sigma=sigma,
                     hl_vs_W=hl/W, frac=frac, entries=entries, n=len(r)))

print("\n=== CAUSAL trailing-SMA residual: OU + ADF + z-excursion ===")
print(f"detrend: residual_t = close_t - SMA_W(close)[<= t]   |   z_t = residual_t / trailingStd_W[<= t]")
print(f"{'W(h)':>5} {'theta/bar':>11} {'half-life(h)':>12} {'HL/W':>6} {'ADF_p':>8} {'resid_sigma':>12} {'%|z|>=2':>8} {'#entries':>9}")
for x in rows:
    hl = x['hl']; hls = f"{hl:9.1f}" if math.isfinite(hl) else "     inf "
    print(f"{x['W']:>5} {x['theta']:>11.3e} {hls:>12} {x['hl_vs_W']:>6.2f} {x['adf_p']:>8.4f} {x['sigma']:>12.2f} {100*x['frac']:>7.1f}% {x['entries']:>9}")

print("\nnote: ADF p<0.05 => residual rejects unit root (stationary). #entries = distinct |z|>=2 onset events over ~",
      f"{rows[0]['n']} bars (~{rows[0]['n']/24:.0f} days).")
