"""Steelman follow-on: does the WEIGHTED net_score (what the bot actually trades on) track fee-net
EDGE better than raw depth did? Read-only, corpus-only. SAME trade universe + gates as the depth
test (dominant side, depth>=2, 10-bar cooldown, stop=max(1.5*ATR,0.3%), TP=2R, fee-net) — only the
BUCKETING changes (net_score bins instead of count). Reports with/without divergence, chop/trend,
walk-forward, and the depth<->net_score correlation (point 4: is score just depth re-expressed?)."""
import sqlite3, statistics, math
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
STOP_FLOOR_PCT, ATR_MULT, TP_R = 0.003, 1.5, 2.0
COOLDOWN_BARS, MAX_HOLD = 10, 480
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
ER_K, ER_SPLIT = 20, 0.35

# (name, column, side, ttl_bars, is_divergence, WEIGHT)  weights from strategies.yaml scoring block
SIG = [
 ("mc_a_bluetriangle","blue_triangle","buy",10,False,3),
 ("mc_a_longema","long_ema_signal","buy",10,False,2),
 ("mc_a_yellow_x","yellow_cross","buy",10,False,2),
 ("mc_b_gold_buy","gold_buy_gold_circle","buy",5,False,5),
 ("mc_b_buycirc_div","divergence_buy_circle","buy",5,True,4),
 ("mc_b_buy_circle","buy_circle","buy",5,False,3),
 ("otter_buy","otter_buy","buy",5,False,3),
 ("cvd_bull_flip","cvd_flip_bullish","buy",10,False,2),
 ("mc_a_red_diamond","red_diamond","sell",10,False,4),
 ("mc_a_blood_diamond","blood_diamond","sell",10,False,5),
 ("mc_a_redx","red_cross","sell",10,False,2),
 ("mc_b_sellcirc_div","divergence_sell_circle","sell",5,True,4),
 ("mc_b_sell_circle","sell_circle","sell",5,False,3),
 ("otter_sell","otter_sell","sell",5,False,3),
 ("cvd_bear_flip","cvd_flip_bearish","sell",10,False,2),
]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cols = ["ts","open","high","low","close","atr"] + [s[1] for s in SIG]
rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
con.close()
T = len(rows)
ts=[r[0] for r in rows]; hi=[r[2] for r in rows]; lo=[r[3] for r in rows]; cl=[r[4] for r in rows]; atr=[r[5] for r in rows]

live = {}
for j,s in enumerate(SIG):
    ci=6+j; ttl=s[3]; lv=[0]*T; last=-10**9
    for i in range(T):
        v=rows[i][ci]
        if v is not None and v!=0: last=i
        lv[i]=1 if (i-last)<ttl else 0
    live[s[0]]=lv

er=[None]*T
for i in range(ER_K,T):
    num=abs(cl[i]-cl[i-ER_K]); den=sum(abs(cl[k]-cl[k-1]) for k in range(i-ER_K+1,i+1))
    er[i]=num/den if den>0 else 1.0

def simulate(drop_div):
    buyset=[s for s in SIG if s[2]=="buy"  and not (drop_div and s[4])]
    sellset=[s for s in SIG if s[2]=="sell" and not (drop_div and s[4])]
    last_buy=last_sell=-10**9
    trades=[]   # (ts, side, depth, net_score, net_R, outcome, er)
    for i in range(ER_K+1, T-1):
        db=sum(live[s[0]][i] for s in buyset); ds=sum(live[s[0]][i] for s in sellset)
        bscore=sum(s[5] for s in buyset if live[s[0]][i]); sscore=sum(s[5] for s in sellset if live[s[0]][i])
        if db>=2 and db>ds: side,depth,nsc="buy",db,bscore-sscore
        elif ds>=2 and ds>db: side,depth,nsc="sell",ds,sscore-bscore
        else: continue
        if side=="buy" and (i-last_buy)<COOLDOWN_BARS: continue
        if side=="sell" and (i-last_sell)<COOLDOWN_BARS: continue
        entry=cl[i]; a=atr[i] if (atr[i] is not None and atr[i]>0) else entry*0.0004
        sd=max(ATR_MULT*a, STOP_FLOOR_PCT*entry); sp=sd/entry
        if side=="buy": stop,tp=entry-sd,entry+TP_R*sd; last_buy=i
        else: stop,tp=entry+sd,entry-TP_R*sd; last_sell=i
        out,g=None,None
        for j in range(i+1, min(T,i+1+MAX_HOLD)):
            h,l=hi[j],lo[j]
            sl=(l<=stop) if side=="buy" else (h>=stop); tph=(h>=tp) if side=="buy" else (l<=tp)
            if sl: out,g="sl",-1.0; break
            if tph: out,g="tp",TP_R; break
        if out is None:
            last=cl[min(T-1,i+MAX_HOLD)]; g=((last-entry) if side=="buy" else (entry-last))/sd; out="timeout"
        net=g-(ENTRY_FEE+(MK if out=="tp" else TK)+SLIP2)/sp
        trades.append((ts[i],side,depth,nsc,net,out,er[i]))
    return trades

SCORE_BINS=[("<5",-99,4),("5-7",5,7),("8-10",8,10),("11+",11,999)]
def bucket(trades, lo_ts=0, hi_ts=9e12, regime=None):
    out={}
    for lab,a,b in SCORE_BINS:
        sub=[t for t in trades if lo_ts<=t[0]<hi_ts and a<=t[3]<=b
             and (regime is None or (regime=="chop" and t[6] is not None and t[6]<=ER_SPLIT)
                  or (regime=="trend" and t[6] is not None and t[6]>ER_SPLIT))]
        if not sub: out[lab]=None; continue
        nets=[t[4] for t in sub]; win=sum(1 for t in sub if t[5]=="tp")
        out[lab]=(len(sub),round(100*win/len(sub),1),round(statistics.fmean(nets),3))
    return out
def fmt(b): return "  ".join(f"s{lab}: n={v[0]:<4} win={v[1]:<5} netR={v[2]:+.3f}" if v else f"s{lab}: (none)         " for lab,v in b.items())
def mono(b):
    seq=[v[2] for v in b.values() if v]
    if len(seq)<2: return "n/a"
    return f"{'MONOTONIC UP' if all(seq[k+1]>=seq[k] for k in range(len(seq)-1)) else 'NOT monotonic'}  netR seq={['%+.2f'%x for x in seq]}"
def pearson(xs,ys):
    n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys)); sx=math.sqrt(sum((x-mx)**2 for x in xs)); sy=math.sqrt(sum((y-my)**2 for y in ys))
    return cov/(sx*sy) if sx>0 and sy>0 else 0.0

print("="*98)
print("WEIGHTED net_score -> EDGE  (steelman; MECHANICS test, regime-bounded; NOT a live verdict)")
print(f"  bins by net_score (bot tiers: weak3/standard5/premium10, min_fire5). Same trades+gates as depth test.")
print("="*98)
for drop,lbl in ((False,"WITH divergence"),(True,"WITHOUT divergence")):
    tr=simulate(drop)
    depths=[t[2] for t in tr]; scores=[t[3] for t in tr]
    print(f"\n##### {lbl} — {len(tr)} entries | depth<->net_score corr (Pearson) = {pearson(depths,scores):+.3f} #####")
    full=bucket(tr); print("  FULL SAMPLE   "+fmt(full)); print("    monotonicity:", mono(full))
    print("  CHOP          "+fmt(bucket(tr,regime="chop")))
    print("  TREND         "+fmt(bucket(tr,regime="trend")))
    print("  TRAIN<=May15  "+fmt(bucket(tr,0,TRAIN_END)))
    print("  VAL<=Jun1     "+fmt(bucket(tr,TRAIN_END,VAL_END)))
print("\n"+"="*98)
print("Read: net-R rising with net_score => weighting recovers edge raw count couldn't. Flat/inverted =>")
print("the scorer's CENTRAL OUTPUT doesn't track expectancy here. High depth<->score corr => score is")
print("mostly depth re-expressed (same trades, same null).")
