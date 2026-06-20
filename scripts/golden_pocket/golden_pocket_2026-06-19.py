"""Golden-pocket continuation scalp — decomposed so 'confirmation' is MEASURABLE.
Read-only backtest, corpus-only (bars_15m, Nov2025-Jun2026, regime-fair). METHOD test, NOT the bot
(15m exec != 3m scalper; nothing transfers without separate 3m work).

STRUCTURAL axis (orthogonal to the scorer's momentum monoculture):
  SETUP   : body-close BOS on 15m off a confirmed two-candle swing; impulse leg = origin swing-low
            -> leg extreme (incl wicks). Golden pocket = 0.618-0.65 retracement of that leg.
  ENTRY   : price pulls back into the GP zone (long after up-BOS / short after down-BOS).
  CONFIRM : 3 triggers tested SEPARATELY at the zone (15m, since no sub-15m exists this window):
            (a) engulfing, (b) liquidity sweep of the zone-low then reclaim, (c) micro-ChoCH.
  STRUCT+ : golden-pocket n FVG overlap; London/NY session filter (orthogonal to momentum).
  RISK    : SL beyond the 1.0 fib (origin swing) + buffer; TP = 1.5R. R-based, fee-net.

KEY TESTS: (1) does the ZONE beat a no-zone baseline? (2) does REQUIRING confirmation improve the
zone's net-R or just cut N at same/worse expectancy? (3) k=0 vs k>=1 HEADLINE (fib+ChoCH are
repaint-prone — edge only at k=0 => repaint). Walk-forward TRAIN/VAL/LOCKBOX; chop/trend; break-rate."""
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TP_R = 1.5
GP_LO, GP_HI = 0.618, 0.65            # golden-pocket retracement band
W = 3                                  # two-candle-rule neighbour window guard (uses 2 confirm bars)
VALID_WIN, INZONE_WIN, MAXHOLD = 40, 8, 160   # bars (15m): reach-zone / in-zone-confirm / max hold
MIN_LEG_PCT = 0.006                    # impulse must be >=0.6% to count as impulsive
ER_K, ER_SPLIT = 20, 0.35
TRAIN_END = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
STEP = 900
SESSIONS = [(7, 10), (12, 16)]         # London / NY (UTC hours)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute("SELECT ts,open,high,low,close,atr FROM bars_15m ORDER BY ts").fetchall()
con.close()
T = len(rows)
ts=[r[0] for r in rows]; op=[r[1] for r in rows]; hi=[r[2] for r in rows]
lo=[r[3] for r in rows]; cl=[r[4] for r in rows]; atrc=[r[5] for r in rows]

# two-candle-rule body swings, non-repainting (confirmed at close of bar p+2)
sw_hi, sw_lo = [], []     # (confirm_idx, pivot_idx, price)
for p in range(1, T-2):
    b1h = cl[p+1] < op[p+1]; b2h = cl[p+2] < op[p+2]
    b1l = cl[p+1] > op[p+1]; b2l = cl[p+2] > op[p+2]
    if b1h and b2h and hi[p] >= hi[p+1] and hi[p] >= hi[p+2] and hi[p] > hi[p-1]:
        sw_hi.append((p+2, p, hi[p]))
    if b1l and b2l and lo[p] <= lo[p+1] and lo[p] <= lo[p+2] and lo[p] < lo[p-1]:
        sw_lo.append((p+2, p, lo[p]))

er=[None]*T
for i in range(ER_K, T):
    num=abs(cl[i]-cl[i-ER_K]); den=sum(abs(cl[k]-cl[k-1]) for k in range(i-ER_K+1, i+1))
    er[i]=num/den if den>0 else 1.0

def last_before(seq, idx):
    """last (confirm_idx,pivot_idx,price) in seq with confirm_idx <= idx."""
    out=None
    for s in seq:
        if s[0] <= idx: out=s
        else: break
    return out

def fvg_overlaps(side, lo_idx, hi_idx, zlo, zhi):
    """any 3-bar FVG in [lo_idx,hi_idx] overlapping the zone."""
    for t in range(max(lo_idx+2, 2), hi_idx+1):
        if side=="buy" and lo[t] > hi[t-2]:        # bullish gap [hi[t-2], lo[t]]
            if not (lo[t] < zlo or hi[t-2] > zhi): return True
        if side=="sell" and hi[t] < lo[t-2]:       # bearish gap [hi[t], lo[t-2]]
            if not (lo[t-2] < zlo or hi[t] > zhi): return True
    return False

def simulate_trade(side, ei, stop):
    if ei >= T: return None
    entry = cl[ei]; risk = (entry-stop) if side=="buy" else (stop-entry)
    if risk <= 0: return None
    sp = risk/entry
    if sp < 0.0015 or sp > 0.05: return None       # avoid degenerate tiny/huge-risk fee artifacts
    tp = entry + TP_R*risk if side=="buy" else entry - TP_R*risk
    out=g=None
    for j in range(ei+1, min(T, ei+1+MAXHOLD)):
        h,l=hi[j],lo[j]
        sl=(l<=stop) if side=="buy" else (h>=stop); tph=(h>=tp) if side=="buy" else (l<=tp)
        if sl: out,g="sl",-1.0; break
        if tph: out,g="tp",TP_R; break
    if out is None:
        last=cl[min(T-1, ei+MAXHOLD)]; g=((last-entry) if side=="buy" else (entry-last))/risk; out="timeout"
    net = g - (ENTRY_FEE + (MK if out=="tp" else TK) + SLIP2)/sp
    return (net, out)

def build_trades(k):
    """For each BOS setup, emit one entry per ARM (with delay k). Returns list of
    (arm, ts, side, net_R, outcome, er)."""
    trades=[]
    def add(arm, side, ebar, stop):
        ei=ebar+k
        r=simulate_trade(side, ei, stop)
        if r: trades.append((arm, ts[ei], side, r[0], r[1], er[ei]))
    for broken, seq_break, seq_origin, side in ((sw_hi, sw_hi, sw_lo, "buy"), (sw_lo, sw_lo, sw_hi, "sell")):
        for (cidx, pidx, price) in seq_break:
            # BOS = first bar i>cidx with body close beyond the swing
            i=None
            for b in range(cidx+1, min(T, cidx+VALID_WIN)):
                if (side=="buy" and cl[b] > price) or (side=="sell" and cl[b] < price):
                    i=b; break
            if i is None: continue
            origin = last_before(seq_origin, i)
            if origin is None: continue
            if side=="buy":
                leg_lo=origin[2]; leg_hi=max(hi[origin[1]:i+1]); rng=leg_hi-leg_lo
                if rng<=0 or rng/leg_lo < MIN_LEG_PCT: continue
                zhi=leg_hi-GP_LO*rng; zlo=leg_hi-GP_HI*rng           # zlo<zhi
                buf=0.15*(atrc[i] or leg_lo*0.0004); stop=leg_lo-buf
            else:
                leg_hi=origin[2]; leg_lo=min(lo[origin[1]:i+1]); rng=leg_hi-leg_lo
                if rng<=0 or rng/leg_hi < MIN_LEG_PCT: continue
                zlo=leg_lo+GP_LO*rng; zhi=leg_lo+GP_HI*rng           # for short, retr UP into zone
                buf=0.15*(atrc[i] or leg_hi*0.0004); stop=leg_hi+buf
            # baseline (no zone): enter at BOS bar
            add("baseline", side, i, stop)
            # find first touch of zone within VALID_WIN
            j=None
            for m in range(i+1, min(T, i+1+VALID_WIN)):
                touch = (lo[m] <= zhi) if side=="buy" else (hi[m] >= zlo)
                if touch: j=m; break
            if j is None: continue
            add("zone_only", side, j, stop)
            # structural confluence (entry like zone_only, gated)
            if fvg_overlaps(side, origin[1], i, zlo, zhi): add("zone+FVG", side, j, stop)
            hr=datetime.utcfromtimestamp(ts[j]).hour
            if any(a<=hr<b for a,b in SESSIONS): add("zone+session", side, j, stop)
            # confirmation triggers: first in-zone bar (within INZONE_WIN) meeting each
            pull_lo=lo[j]; pull_hi=hi[j]
            eng=swp=cho=None
            for m in range(j, min(T, j+INZONE_WIN)):
                inz = (lo[m] <= zhi) if side=="buy" else (hi[m] >= zlo)
                if not inz: break
                pull_lo=min(pull_lo, lo[m]); pull_hi=max(pull_hi, hi[m])
                if side=="buy":
                    if eng is None and cl[m]>op[m] and cl[m-1]<op[m-1] and cl[m]>=op[m-1] and op[m]<=cl[m-1]: eng=m
                    if swp is None and lo[m]<zlo and cl[m]>zlo: swp=m
                    if cho is None and m>=2 and lo[m-1]<lo[m-2] and cl[m]>hi[m-1]: cho=m
                else:
                    if eng is None and cl[m]<op[m] and cl[m-1]>op[m-1] and cl[m]<=op[m-1] and op[m]>=cl[m-1]: eng=m
                    if swp is None and hi[m]>zhi and cl[m]<zhi: swp=m
                    if cho is None and m>=2 and hi[m-1]>hi[m-2] and cl[m]<lo[m-1]: cho=m
            if eng is not None: add("zone+engulf", side, eng, stop)
            if swp is not None: add("zone+sweep", side, swp, stop)
            if cho is not None: add("zone+choch", side, cho, stop)
    return trades

ARMS=["baseline","zone_only","zone+engulf","zone+sweep","zone+choch","zone+FVG","zone+session"]
def agg(trades, arm, lo_ts=0, hi_ts=9e12, regime=None):
    sub=[t for t in trades if t[0]==arm and lo_ts<=t[1]<hi_ts
         and (regime is None or (regime=="chop" and t[5] is not None and t[5]<=ER_SPLIT)
              or (regime=="trend" and t[5] is not None and t[5]>ER_SPLIT))]
    if not sub: return None
    nets=[t[3] for t in sub]; win=sum(1 for t in sub if t[4]=="tp"); brk=sum(1 for t in sub if t[4]=="sl")
    return len(sub), round(100*win/len(sub),1), round(statistics.fmean(nets),3), round(100*brk/len(sub),0)

def line(d): return f"n={d[0]:<4} win={d[1]:<5} netR={d[2]:+.3f} brk{d[3]:.0f}%" if d else "(none)"

tk0=build_trades(0); tk1=build_trades(1)
print("="*100)
print("GOLDEN-POCKET CONTINUATION — STRUCTURAL axis (orthogonal to momentum). METHOD test, NOT the bot.")
print("  15m exec, Nov2025-Jun2026 (regime-fair). Confirmation on 15m (no sub-15m data this window).")
print("  SL beyond 1.0 fib +buf; TP=1.5R; fee-net; mechanics test, regime-bounded (null=inconclusive).")
print("="*100)
ev=[e for e in er if e is not None]
print(f"corpus 15m N={T}  {datetime.utcfromtimestamp(ts[0]):%Y-%m-%d}..{datetime.utcfromtimestamp(ts[-1]):%Y-%m-%d}  "
      f"swings hi={len(sw_hi)} lo={len(sw_lo)}")
print(f"regime mix: chop(ER<={ER_SPLIT})={100*sum(1 for e in ev if e<=ER_SPLIT)/len(ev):.0f}%  trend={100*sum(1 for e in ev if e>ER_SPLIT)/len(ev):.0f}%")

print("\n# HEADLINE — k=0 vs k=1 (repaint test). Positive only at k=0 => repaint, dead.")
for arm in ARMS:
    a0,a1=agg(tk0,arm),agg(tk1,arm)
    flag=""
    if a0 and a1 and a0[2]>0>=a1[2]: flag="  <-- REPAINT"
    elif a1 and a1[2]>0: flag="  <-- survives k=1"
    print(f"  {arm:13} k0 {line(a0):<40} k1 {line(a1):<40}{flag}")

print("\n# ARM TABLE (k=1, repaint-honest) — does the ZONE beat baseline? does CONFIRM earn its delay?")
for arm in ARMS:
    print(f"  {arm:13} {line(agg(tk1,arm))}")

print("\n# REGIME SPLIT (k=1) — zone_only + each confirmation")
for arm in ["zone_only","zone+engulf","zone+sweep","zone+choch"]:
    print(f"  {arm:13} CHOP {line(agg(tk1,arm,regime='chop')):<40} TREND {line(agg(tk1,arm,regime='trend'))}")

print("\n# WALK-FORWARD (k=1)  TRAIN<=Mar1 | VAL<=May1 | LOCKBOX>May1")
for arm in ["baseline","zone_only","zone+engulf","zone+sweep","zone+choch"]:
    tr=agg(tk1,arm,0,TRAIN_END); va=agg(tk1,arm,TRAIN_END,VAL_END); lb=agg(tk1,arm,VAL_END,9e12)
    print(f"  {arm:13} TRAIN {line(tr):<38} VAL {line(va):<38} LOCK {line(lb)}")
print("\nRead: zone_only netR > baseline => the structural zone adds something. zone+confirm netR >")
print("zone_only (not just lower n) => confirmation earns its delay. Else it's selection w/o edge.")
