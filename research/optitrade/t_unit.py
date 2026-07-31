"""
t_unit.py -- validation of optitrade_bt: indicators vs references, simulator vs
hand-computed scenarios, fee math. Run: python t_unit.py
"""
import numpy as np, pandas as pd
import optitrade_bt as bt

PASS = 0; FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}   {detail}")

rng = np.random.default_rng(42)

# ---------------------------------------------------------------- indicators
print("== indicators ==")
close = np.cumsum(rng.normal(0, 1, 5000)) + 1000.0
high = close + np.abs(rng.normal(0, 1, 5000))
low  = close - np.abs(rng.normal(0, 1, 5000))

# EMA vs pandas ewm(adjust=False) -- authoritative
for L in (5, 14, 30, 66):
    mine = bt.ema(close, L)
    ref = pd.Series(close).ewm(span=L, adjust=False).mean().to_numpy()
    check(f"ema L={L} vs pandas ewm", np.allclose(mine, ref, atol=1e-9),
          f"max|d|={np.nanmax(np.abs(mine-ref)):.2e}")

# RSI vs independent Wilder (SMA-seeded) reference
def ref_rsi(c, length=14):
    d = np.diff(c)
    up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    n = len(c); out = np.full(n, np.nan)
    ag = up[:length].mean(); al = dn[:length].mean()
    out[length] = 100.0 if al == 0 else 100 - 100/(1 + ag/al)
    for i in range(length+1, n):
        ag = (ag*(length-1) + up[i-1])/length
        al = (al*(length-1) + dn[i-1])/length
        out[i] = 100.0 if al == 0 else 100 - 100/(1 + ag/al)
    return out
mine = bt.rsi_wilder(close, 14); ref = ref_rsi(close, 14)
m = ~np.isnan(ref)
check("rsi14 vs independent Wilder", np.allclose(mine[m], ref[m], atol=1e-9),
      f"max|d|={np.nanmax(np.abs(mine[m]-ref[m])):.2e}")
check("rsi14 in [0,100]", np.nanmin(mine) >= 0 and np.nanmax(mine) <= 100)

# ATR vs independent Wilder reference
def ref_atr(h, l, c, length=14):
    n = len(c); tr = np.empty(n)
    tr[0] = h[0]-l[0]
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    out = np.full(n, np.nan); out[length-1] = tr[:length].mean()
    for i in range(length, n):
        out[i] = (out[i-1]*(length-1) + tr[i])/length
    return out
mine = bt.atr_wilder(high, low, close, 14); ref = ref_atr(high, low, close, 14)
m = ~np.isnan(ref)
check("atr14 vs independent Wilder", np.allclose(mine[m], ref[m], atol=1e-9),
      f"max|d|={np.nanmax(np.abs(mine[m]-ref[m])):.2e}")
check("atr14 > 0", np.nanmin(mine) > 0)

# crossover semantics
f = np.array([1,1,2,2,1,1], np.float64); s = np.array([2,2,1,1,2,2], np.float64)
cu, cd = bt.cross_arrays(f, s)
check("crossover at i=2", cu[2] and not cd[2])
check("crossunder at i=4", cd[4] and not cu[4])

# ---------------------------------------------------------------- simulator
print("== simulator (hand-computed) ==")
# helper: build a scenario. Entry signal at index 2, close[2]=100, atr[2]=10.
def scen(bars_after, d=1, entry=100.0, atr=10.0):
    # bars_after: list of (high, low) for j=3.. ; indices 0,1 are padding, 2 = entry bar
    N = 3 + len(bars_after)
    o = np.full(N, entry); h = np.full(N, entry); l = np.full(N, entry); c = np.full(N, entry)
    a = np.zeros(N); a[2] = atr
    sig = np.zeros(N, np.int8); sig[2] = d
    for k,(hh,ll) in enumerate(bars_after):
        j = 3+k; h[j]=hh; l[j]=ll; c[j]=(hh+ll)/2.0
    return o,h,l,c,a,sig,N

def run(o,h,l,c,a,sig,N,slMult=1.0,RR=4.0,sl_first=True):
    return bt.simulate(o,h,l,c,a,sig,slMult,RR,2,N,sl_first)

# RR=4, R=10 -> SL=90 (long); TPs=110,120,130,140
# 1) pure SL, no TP
o,h,l,c,a,sig,N = scen([(105,89)])
tr = run(o,h,l,c,a,sig,N)
check("long pure SL grossR=-1", abs(tr[2][0]+1.0) < 1e-9, f"got {tr[2][0]}")
check("long pure SL reason=1", tr[7][0] == 1)
check("long pure SL ntp=0", tr[6][0] == 0)

# 2) all 4 TP in one bar -> grossR=0.625*RR=2.5
o,h,l,c,a,sig,N = scen([(145,100)])
tr = run(o,h,l,c,a,sig,N)
check("long all-TP grossR=2.5", abs(tr[2][0]-2.5) < 1e-9, f"got {tr[2][0]}")
check("long all-TP reason=2, ntp=4", tr[7][0]==2 and tr[6][0]==4)

# 3) partial (TP1,TP2) then SL, sl_first -> 0.75 - 0.5 = 0.25
o,h,l,c,a,sig,N = scen([(125,100),(105,89)])
tr = run(o,h,l,c,a,sig,N)
check("long TP1+TP2 then SL grossR=0.25", abs(tr[2][0]-0.25) < 1e-9, f"got {tr[2][0]}")
check("long partial ntp=2 reason=1", tr[6][0]==2 and tr[7][0]==1)

# 4) same bar straddles TP1 and SL -> SL-first vs TP-first differ
o,h,l,c,a,sig,N = scen([(115,88)])
tr_sl = run(o,h,l,c,a,sig,N,sl_first=True)
tr_tp = run(o,h,l,c,a,sig,N,sl_first=False)
check("straddle SL-first grossR=-1", abs(tr_sl[2][0]+1.0) < 1e-9, f"got {tr_sl[2][0]}")
check("straddle TP-first grossR=-0.5", abs(tr_tp[2][0]+0.5) < 1e-9, f"got {tr_tp[2][0]}")
check("straddle TP-first ntp=1", tr_tp[6][0]==1)

# 5) short mirror: all TP
o,h,l,c,a,sig,N = scen([(100,55)], d=-1)
tr = run(o,h,l,c,a,sig,N)
check("short all-TP grossR=2.5", abs(tr[2][0]-2.5) < 1e-9, f"got {tr[2][0]}")
# short SL
o,h,l,c,a,sig,N = scen([(111,100)], d=-1)
tr = run(o,h,l,c,a,sig,N)
check("short pure SL grossR=-1", abs(tr[2][0]+1.0) < 1e-9, f"got {tr[2][0]}")

# 6) EOD mark-to-market: no level hit, last close=104 -> grossR=0.4
o,h,l,c,a,sig,N = scen([(105,96)])
c[-1] = 104.0
tr = run(o,h,l,c,a,sig,N)
check("EOD MTM grossR=0.4 reason=3", abs(tr[2][0]-0.4) < 1e-9 and tr[7][0]==3, f"got {tr[2][0]}")

# 7) fee math via metrics: single all-TP trade
o,h,l,c,a,sig,N = scen([(145,100)])
tr = run(o,h,l,c,a,sig,N)
mt = bt.metrics(tr, fee_rates=(0.0006,))
# exit_notional = 0.25*(110+120+130+140)=125 ; entry=100 ; risk=10
# fee_R = 0.0006*(100+125)/10 = 0.0135 ; net = 2.5-0.0135
check("fee net_sumR", abs(mt["net_sumR_0.0006"] - (2.5-0.0135)) < 1e-9,
      f"got {mt['net_sumR_0.0006']}")
check("metrics sumR/wr/pf", abs(mt["sumR"]-2.5)<1e-9 and mt["wr"]==1.0)

# 8) one-position-at-a-time: 2nd signal during open trade ignored
o,h,l,c,a,sig,N = scen([(105,96),(145,100)])   # trade opens at 2, still managing
sig[3] = 1   # signal while in position (index 3) -> must be ignored
a[3] = 10.0
tr = run(o,h,l,c,a,sig,N)
check("one-position: single trade despite 2nd signal", tr[0].shape[0]==1, f"n={tr[0].shape[0]}")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
