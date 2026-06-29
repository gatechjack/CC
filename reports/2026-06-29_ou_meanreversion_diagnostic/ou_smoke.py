import sqlite3, math
import numpy as np
import pandas as pd

# ---- 1) toolkit imports (confirm clean) ----
import scipy
from scipy import optimize, stats
import statsmodels
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen
print("=== IMPORTS ===")
print(f"numpy {np.__version__} | pandas {pd.__version__} | scipy {scipy.__version__} | statsmodels {statsmodels.__version__}")
print("scipy.optimize, scipy.stats, sm.OLS, adfuller, coint, coint_johansen  -> all imported OK")

# ---- 2) harness read: btc bars_1h as ts/close ----
DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
con = sqlite3.connect(DB)
df = pd.read_sql_query("SELECT ts, close FROM bars_1h ORDER BY ts", con)
con.close()
n = len(df)
deltas = np.unique(np.diff(df['ts'].values))
print("\n=== BTC bars_1h read ===")
print(f"rows={n}  uniform_delta_sec={deltas.tolist()}  contiguous_1h={deltas.tolist()==[3600]}")
print(f"close: min={df.close.min():.2f} max={df.close.max():.2f} first={df.close.iloc[0]:.2f} last={df.close.iloc[-1]:.2f}")

# ---- 3) OU closed form: OLS of dx on x_lag ----
def ou_closed_form(x, dt=1.0):
    x = np.asarray(x, float)
    x_lag, dx = x[:-1], np.diff(x)
    A = np.column_stack([np.ones_like(x_lag), x_lag])
    coef, *_ = np.linalg.lstsq(A, dx, rcond=None)
    a, b = coef
    resid = dx - A @ coef
    theta = -b / dt
    mu = -a / b if b != 0 else float('nan')
    if theta > 0:
        sigma = resid.std(ddof=2) * math.sqrt(2*theta / (1 - math.exp(-2*theta*dt)))
        half_life = math.log(2) / theta
    else:
        sigma, half_life = resid.std(ddof=2), float('inf')
    return dict(a=a, b=b, theta=theta, mu=mu, sigma=sigma, half_life=half_life)

# ---- OU MLE via scipy (exact Gaussian transition), init from closed form ----
def ou_mle(x, init, dt=1.0):
    x = np.asarray(x, float); x0, x1 = x[:-1], x[1:]
    def nll(p):
        theta, mu, sigma = p
        if theta <= 0 or sigma <= 0: return 1e18
        e = math.exp(-theta*dt)
        mean = mu + (x0 - mu)*e
        var = sigma**2 * (1 - math.exp(-2*theta*dt)) / (2*theta)
        if var <= 0: return 1e18
        return 0.5*np.sum(np.log(2*math.pi*var) + (x1-mean)**2/var)
    res = optimize.minimize(nll, init, method='Nelder-Mead',
                            options=dict(maxiter=20000, xatol=1e-10, fatol=1e-6))
    theta, mu, sigma = res.x
    return dict(theta=theta, mu=mu, sigma=sigma,
                half_life=(math.log(2)/theta if theta > 0 else float('inf')), ok=res.success)

def report(name, x):
    cf = ou_closed_form(x)
    adf_stat, adf_p, *_ = adfuller(x, autolag='AIC')
    mle = ou_mle(x, init=[max(cf['theta'],1e-6), cf['mu'], max(cf['sigma'],1e-6)])
    hl = cf['half_life']
    print(f"\n=== OU smoke: {name} (n={len(x)} bars, dt=1 bar=1h) ===")
    print(f"  OLS closed-form: theta={cf['theta']:.3e}/bar  mu={cf['mu']:.4f}  sigma={cf['sigma']:.4f}")
    print(f"                   half-life={hl:.1f} bars = {hl:.1f} h = {hl/24:.1f} d")
    print(f"  scipy MLE:       theta={mle['theta']:.3e}/bar  mu={mle['mu']:.4f}  sigma={mle['sigma']:.4f}  half-life={mle['half_life']:.1f} bars  (converged={mle['ok']})")
    print(f"  ADF (statsmodels): stat={adf_stat:.3f}  p={adf_p:.4f}  -> {'stationary/mean-reverting' if adf_p<0.05 else 'NOT stationary (unit root not rejected)'}")

report("BTC raw close (level)", df.close.values)
report("BTC log-close", np.log(df.close.values))
