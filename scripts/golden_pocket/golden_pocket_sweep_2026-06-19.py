"""Golden-pocket SWEEP-definition stress test. Re-runs the zone+sweep arm under 3 keyings to see if
the only near-positive cells (trend -0.037, lockbox -0.019) survive a stricter sweep definition or
collapse. Read-only, bars_15m, same setup/gates/rails as golden_pocket_2026-06-19.py.

  sweep_zonelow      : pierce below 0.65 zone-low + reclaim          (the version already reported)
  sweep_origin       : pierce below the 1.0 fib / ORIGIN swing + reclaim   (stricter, per spec)
  sweep_intermediate : pierce below the recent PULLBACK low + reclaim      (pull_lo, intermediate liq)
Reference arm: zone_only (no sweep). k0-vs-k1 headline, chop/trend, walk-forward, n flagged if <30."""
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TP_R = 1.5
GP_LO, GP_HI = 0.618, 0.65
VALID_WIN, INZONE_WIN, MAXHOLD = 40, 8, 160
MIN_LEG_PCT = 0.006
ER_K, ER_SPLIT = 20, 0.35
TRAIN_END = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute("SELECT ts,open,high,low,close,atr FROM bars_15m ORDER BY ts").fetchall()
con.close()
T=len(rows); ts=[r[0] for r in rows]; op=[r[1] for r in rows]; hi=[r[2] for r in rows]
lo=[r[3] for r in rows]; cl=[r[4] for r in rows]; atrc=[r[5] for r in rows]

sw_hi, sw_lo = [], []
for p in range(1, T-2):
    if cl[p+1]<op[p+1] and cl[p+2]<op[p+2] and hi[p]>=hi[p+1] and hi[p]>=hi[p+2] and hi[p]>hi[p-1]:
        sw_hi.append((p+2,p,hi[p]))
    if cl[p+1]>op[p+1] and cl[p+2]>op[p+2] and lo[p]<=lo[p+1] and lo[p]<=lo[p+2] and lo[p]<lo[p-1]:
        sw_lo.append((p+2,p,lo[p]))

er=[None]*T
for i in range(ER_K,T):
    num=abs(cl[i]-cl[i-ER_K]); den=sum(abs(cl[k]-cl[k-1]) for k in range(i-ER_K+1,i+1))
    er[i]=num/den if den>0 else 1.0

def last_before(seq, idx):
    out=None
    for s in seq:
        if s[0]<=idx: out=s
        else: break
    return out

def simulate_trade(side, ei, stop):
    if ei>=T: return None
    entry=cl[ei]; risk=(entry-stop) if side=="buy" else (stop-entry)
    if risk<=0: return None
    sp=risk/entry
    if sp<0.0015 or sp>0.05: return None
    tp=entry+TP_R*risk if side=="buy" else entry-TP_R*risk
    out=g=None
    for j in range(ei+1, min(T,ei+1+MAXHOLD)):
        h,l=hi[j],lo[j]
        sl=(l<=stop) if side=="buy" else (h>=stop); tph=(h>=tp) if side=="buy" else (l<=tp)
        if sl: out,g="sl",-1.0; break
        if tph: out,g="tp",TP_R; break
    if out is None:
        last=cl[min(T-1,ei+MAXHOLD)]; g=((last-entry) if side=="buy" else (entry-last))/risk; out="timeout"
    return (g-(ENTRY_FEE+(MK if out=="tp" else TK)+SLIP2)/sp, out)

def build(k):
    trades=[]
    def add(arm, side, ebar, stop):
        ei=ebar+k; r=simulate_trade(side, ei, stop)
        if r: trades.append((arm, ts[ei], side, r[0], r[1], er[ei]))
    for seq_break, seq_origin, side in ((sw_hi, sw_lo, "buy"),(sw_lo, sw_hi, "sell")):
        for (cidx,pidx,price) in seq_break:
            i=None
            for b in range(cidx+1, min(T,cidx+VALID_WIN)):
                if (side=="buy" and cl[b]>price) or (side=="sell" and cl[b]<price): i=b; break
            if i is None: continue
            origin=last_before(seq_origin, i)
            if origin is None: continue
            if side=="buy":
                leg_lo=origin[2]; leg_hi=max(hi[origin[1]:i+1]); rng=leg_hi-leg_lo
                if rng<=0 or rng/leg_lo<MIN_LEG_PCT: continue
                zhi=leg_hi-GP_LO*rng; zlo=leg_hi-GP_HI*rng
                buf=0.15*(atrc[i] or leg_lo*0.0004); stop=leg_lo-buf
            else:
                leg_hi=origin[2]; leg_lo=min(lo[origin[1]:i+1]); rng=leg_hi-leg_lo
                if rng<=0 or rng/leg_hi<MIN_LEG_PCT: continue
                zlo=leg_lo+GP_LO*rng; zhi=leg_lo+GP_HI*rng
                buf=0.15*(atrc[i] or leg_hi*0.0004); stop=leg_hi+buf
            j=None
            for m in range(i+1, min(T,i+1+VALID_WIN)):
                if (lo[m]<=zhi) if side=="buy" else (hi[m]>=zlo): j=m; break
            if j is None: continue
            add("zone_only", side, j, stop)
            # intermediate pullback extreme up to & incl first touch
            if side=="buy": pull=min(lo[i+1:j+1])
            else: pull=max(hi[i+1:j+1])
            s_zl=s_or=s_in=None
            for m in range(j, min(T,j+INZONE_WIN)):
                inz=(lo[m]<=zhi) if side=="buy" else (hi[m]>=zlo)
                if not inz: break
                if side=="buy":
                    if s_zl is None and lo[m]<zlo and cl[m]>zlo: s_zl=m
                    if s_or is None and lo[m]<leg_lo and cl[m]>leg_lo: s_or=m
                    if s_in is None and lo[m]<pull and cl[m]>pull: s_in=m
                    pull=min(pull, lo[m])
                else:
                    if s_zl is None and hi[m]>zhi and cl[m]<zhi: s_zl=m
                    if s_or is None and hi[m]>leg_hi and cl[m]<leg_hi: s_or=m
                    if s_in is None and hi[m]>pull and cl[m]<pull: s_in=m
                    pull=max(pull, hi[m])
            if s_zl is not None: add("sweep_zonelow", side, s_zl, stop)
            if s_or is not None: add("sweep_origin", side, s_or, stop)
            if s_in is not None: add("sweep_intermediate", side, s_in, stop)
    return trades

ARMS=["zone_only","sweep_zonelow","sweep_origin","sweep_intermediate"]
def agg(tr, arm, lo_ts=0, hi_ts=9e12, regime=None):
    sub=[t for t in tr if t[0]==arm and lo_ts<=t[1]<hi_ts
         and (regime is None or (regime=="chop" and t[5] is not None and t[5]<=ER_SPLIT)
              or (regime=="trend" and t[5] is not None and t[5]>ER_SPLIT))]
    if not sub: return None
    nets=[t[3] for t in sub]; win=sum(1 for t in sub if t[4]=="tp"); brk=sum(1 for t in sub if t[4]=="sl")
    return len(sub), round(100*win/len(sub),1), round(statistics.fmean(nets),3), round(100*brk/len(sub),0)
def line(d):
    if not d: return "(none)"
    flag=" <30!" if d[0]<30 else ""
    return f"n={d[0]:<4} win={d[1]:<5} netR={d[2]:+.3f} brk{d[3]:.0f}%{flag}"

tk0=build(0); tk1=build(1)
print("="*100)
print("GOLDEN-POCKET SWEEP-DEFINITION STRESS TEST — does the near-edge survive a stricter sweep keying?")
print("  bars_15m Nov2025-Jun2026; METHOD-not-bot; mechanics test, regime-bounded, candidate-not-verdict.")
print("="*100)
ev=[e for e in er if e is not None]
print(f"corpus N={T}  chop={100*sum(1 for e in ev if e<=ER_SPLIT)/len(ev):.0f}% trend={100*sum(1 for e in ev if e>ER_SPLIT)/len(ev):.0f}%")
print("\n# HEADLINE k=0 vs k=1 (must stay repaint-clean)")
for arm in ARMS:
    a0,a1=agg(tk0,arm),agg(tk1,arm)
    print(f"  {arm:20} k0 {line(a0):<42} k1 {line(a1)}")
print("\n# ARM TABLE (k=1)")
for arm in ARMS: print(f"  {arm:20} {line(agg(tk1,arm))}")
print("\n# REGIME SPLIT (k=1)")
for arm in ARMS:
    print(f"  {arm:20} CHOP {line(agg(tk1,arm,regime='chop')):<42} TREND {line(agg(tk1,arm,regime='trend'))}")
print("\n# WALK-FORWARD (k=1) TRAIN<=Mar1 | VAL<=May1 | LOCKBOX>May1")
for arm in ARMS:
    print(f"  {arm:20} TRAIN {line(agg(tk1,arm,0,TRAIN_END)):<42} VAL {line(agg(tk1,arm,TRAIN_END,VAL_END)):<42} LOCK {line(agg(tk1,arm,VAL_END,9e12))}")
print("\nRead: if sweep_origin / sweep_intermediate net-R >= sweep_zonelow's near-positive cells, the")
print("candidate survives a stricter keying. If they collapse to clearly-negative, the null is complete.")
