import sqlite3, math
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
con = sqlite3.connect(DB)
close = pd.read_sql_query("SELECT close FROM bars_1h ORDER BY ts", con)['close'].astype(float).values
con.close()
n = len(close)

def ou_hl(x, dt=1.0):
    x = np.asarray(x, float); xl, dx = x[:-1], np.diff(x)
    A = np.column_stack([np.ones_like(xl), xl]); coef,*_ = np.linalg.lstsq(A, dx, rcond=None)
    theta = -coef[1]/dt
    return (math.log(2)/theta) if theta > 0 else float('inf')

def diag(series, W):
    s = pd.Series(series)
    resid = (s - s.rolling(W, min_periods=W).mean())
    tstd = s.rolling(W, min_periods=W).std(ddof=0)
    z = (resid/tstd).dropna().values
    r = resid.dropna().values
    hl = ou_hl(r)
    _, p, *_ = adfuller(r, autolag='AIC')
    frac = float((np.abs(z) >= 2).mean())
    return hl, p, frac

# null 1: shuffle BTC's own log-returns (kills serial/reversion structure, keeps return distribution)
np.random.seed(0)
logret = np.diff(np.log(close))
sh = logret.copy(); np.random.shuffle(sh)
shuffled = np.exp(np.concatenate([[np.log(close[0])], np.cumsum(sh)]))
# null 2: gaussian random walk, matched per-bar log-return std
rw = np.exp(np.concatenate([[np.log(close[0])], np.cumsum(np.random.normal(0, logret.std(), n-1))]))

WINDOWS = [24, 48, 96, 168, 336]
print(f"BTC n={n} | matched nulls: shuffled-returns & gaussian RW (no intrinsic reversion by construction)\n")
print(f"{'W':>4} | {'HL_real':>8} {'HL_shuf':>8} {'HL_rw':>8} | {'p_real':>7} {'p_shuf':>7} {'p_rw':>7} | {'z2_real':>7} {'z2_shuf':>7} {'z2_rw':>7}")
for W in WINDOWS:
    hr, pr, fr = diag(close, W)
    hs, ps, fs = diag(shuffled, W)
    hw, pw, fw = diag(rw, W)
    f = lambda v: f"{v:8.1f}" if math.isfinite(v) else "     inf"
    print(f"{W:>4} | {f(hr)} {f(hs)} {f(hw)} | {pr:7.4f} {ps:7.4f} {pw:7.4f} | {100*fr:6.1f}% {100*fs:6.1f}% {100*fw:6.1f}%")
print("\nIf HL_real ~= HL_shuf ~= HL_rw and all ADF p~0 => the stationarity/half-life is a DETREND ARTIFACT (no edge over a random walk).")
