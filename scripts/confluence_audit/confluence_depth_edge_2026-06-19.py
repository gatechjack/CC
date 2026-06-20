"""Conditional-outcome test: does confluence DEPTH (number of same-side scoring signals
agreeing) improve fee-net EDGE, or only conviction? Read-only, corpus-only (bars_3m).

Entry: at a bar, count same-side scoring signals LIVE within TTL (scorer's view). If the
dominant side has depth>=2 and beats the other side, and the per-side cooldown (10 bars =
30min, the scorer's cooldown_seconds) has elapsed, open a trade. Standard gates (mirrors
scripts/backtest_bitunix_confluence.py): stop = max(1.5*ATR, 0.3% floor), TP = 2R, single
TP. Walk 3m bars to TP/SL/timeout. Fee-net R uses the range-fade corrected fee model.

Bucket by entry depth (2,3,4,5+): n, win%, gross avgR, NET avgR. KEY TEST = does NET-R rise
monotonically with depth? Run WITH and WITHOUT the repaint-suspect divergence markers. Split
chop vs trend (ER). Walk-forward TRAIN<=May15 / VAL<=Jun1. Mechanics test, regime-bounded."""
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001   # range-fade fee model
STOP_FLOOR_PCT, ATR_MULT, TP_R = 0.003, 1.5, 2.0               # prod sim gates
COOLDOWN_BARS, MAX_HOLD = 10, 480                              # 30min cooldown; 24h max hold
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
ER_K, ER_SPLIT = 20, 0.35     # chop = ER<=split, trend = ER>split

# scorer factor -> (column, side, ttl_bars). DIV = repaint-suspect divergence markers.
SIG = [
 ("mc_a_bluetriangle","blue_triangle","buy",10,False),
 ("mc_a_longema","long_ema_signal","buy",10,False),
 ("mc_a_yellow_x","yellow_cross","buy",10,False),
 ("mc_b_gold_buy","gold_buy_gold_circle","buy",5,False),
 ("mc_b_buycirc_div","divergence_buy_circle","buy",5,True),
 ("mc_b_buy_circle","buy_circle","buy",5,False),
 ("otter_buy","otter_buy","buy",5,False),
 ("cvd_bull_flip","cvd_flip_bullish","buy",10,False),
 ("mc_a_red_diamond","red_diamond","sell",10,False),
 ("mc_a_blood_diamond","blood_diamond","sell",10,False),
 ("mc_a_redx","red_cross","sell",10,False),
 ("mc_b_sellcirc_div","divergence_sell_circle","sell",5,True),
 ("mc_b_sell_circle","sell_circle","sell",5,False),
 ("otter_sell","otter_sell","sell",5,False),
 ("cvd_bear_flip","cvd_flip_bearish","sell",10,False),
]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cols = ["ts","open","high","low","close","atr"] + [s[1] for s in SIG]
rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
con.close()
T = len(rows)
ts = [r[0] for r in rows]; op=[r[1] for r in rows]; hi=[r[2] for r in rows]
lo=[r[3] for r in rows]; cl=[r[4] for r in rows]; atr=[r[5] for r in rows]

# firing + live-within-TTL per signal
live = {}
for j,s in enumerate(SIG):
    col_i = 6 + j
    ttl = s[3]
    lv = [0]*T; last = -10**9
    for i in range(T):
        v = rows[i][col_i]
        if v is not None and v != 0:
            last = i
        lv[i] = 1 if (i - last) < ttl else 0
    live[s[0]] = lv

# efficiency ratio (chop/trend) — past-only
er = [None]*T
for i in range(ER_K, T):
    num = abs(cl[i]-cl[i-ER_K]); den = sum(abs(cl[k]-cl[k-1]) for k in range(i-ER_K+1, i+1))
    er[i] = num/den if den>0 else 1.0

def depth_at(i, names_buy, names_sell):
    db = sum(live[n][i] for n in names_buy); ds = sum(live[n][i] for n in names_sell)
    if db >= 2 and db > ds: return "buy", db
    if ds >= 2 and ds > db: return "sell", ds
    return None, 0

def simulate(drop_div):
    nb = [s[0] for s in SIG if s[2]=="buy"  and not (drop_div and s[4])]
    ns = [s[0] for s in SIG if s[2]=="sell" and not (drop_div and s[4])]
    last_buy = last_sell = -10**9
    trades = []   # (ts, side, depth, net_R, outcome, er_at_entry)
    for i in range(ER_K+1, T-1):
        side, depth = depth_at(i, nb, ns)
        if side is None: continue
        if side=="buy" and (i-last_buy) < COOLDOWN_BARS: continue
        if side=="sell" and (i-last_sell) < COOLDOWN_BARS: continue
        entry = cl[i]
        a = atr[i] if (atr[i] is not None and atr[i] > 0) else entry*0.0004
        stop_dist = max(ATR_MULT*a, STOP_FLOOR_PCT*entry)
        if stop_dist <= 0: continue
        sp = stop_dist/entry
        if side=="buy":
            stop, tp = entry-stop_dist, entry+TP_R*stop_dist; last_buy=i
        else:
            stop, tp = entry+stop_dist, entry-TP_R*stop_dist; last_sell=i
        out, g = None, None
        for j in range(i+1, min(T, i+1+MAX_HOLD)):
            h, l = hi[j], lo[j]
            sl = (l<=stop) if side=="buy" else (h>=stop)
            tph = (h>=tp) if side=="buy" else (l<=tp)
            if sl: out, g = "sl", -1.0; break        # worst-case: stop first if both
            if tph: out, g = "tp", TP_R; break
        if out is None:
            last = cl[min(T-1, i+MAX_HOLD)]
            g = ((last-entry) if side=="buy" else (entry-last))/stop_dist; out="timeout"
        net = g - (ENTRY_FEE + (MK if out=="tp" else TK) + SLIP2)/sp
        trades.append((ts[i], side, depth, net, out, er[i]))
    return trades

def bucket(trades, lo_ts=0, hi_ts=9e12, regime=None):
    """regime: None|'chop'|'trend'. Returns {depth_label: (n,win%,gross? ,netR)}."""
    out = {}
    for lab, lohi in (("2",(2,2)),("3",(3,3)),("4",(4,4)),("5+",(5,99))):
        sub = [t for t in trades if lo_ts<=t[0]<hi_ts and lohi[0]<=t[2]<=lohi[1]
               and (regime is None
                    or (regime=="chop" and t[5] is not None and t[5]<=ER_SPLIT)
                    or (regime=="trend" and t[5] is not None and t[5]>ER_SPLIT))]
        if not sub: out[lab]=None; continue
        nets=[t[3] for t in sub]; win=sum(1 for t in sub if t[4]=="tp")
        out[lab]=(len(sub), round(100*win/len(sub),1), round(statistics.fmean(nets),3))
    return out

def fmt(b):
    return "  ".join(f"d{lab}: n={v[0]:<4} win={v[1]:<5} netR={v[2]:+.3f}" if v else f"d{lab}: (none)         "
                     for lab,v in b.items())

def mono(b):
    seq=[v[2] for v in b.values() if v]
    if len(seq)<2: return "n/a"
    rising = all(seq[k+1]>=seq[k] for k in range(len(seq)-1))
    return f"{'MONOTONIC UP' if rising else 'NOT monotonic'}  netR seq={['%+.2f'%x for x in seq]}"

print("="*96)
print("CONFLUENCE DEPTH -> EDGE  (MECHANICS TEST, regime-bounded; NOT a live verdict)")
print(f"  corpus bars_3m N={T}  {datetime.utcfromtimestamp(ts[0]):%Y-%m-%d}..{datetime.utcfromtimestamp(ts[-1]):%Y-%m-%d}")
ervals=[e for e in er if e is not None]
print(f"  regime mix: chop(ER<={ER_SPLIT})={100*sum(1 for e in ervals if e<=ER_SPLIT)/len(ervals):.0f}%  "
      f"trend={100*sum(1 for e in ervals if e>ER_SPLIT)/len(ervals):.0f}%")
print(f"  gates: stop=max(1.5*ATR,0.3%) TP=2R cooldown={COOLDOWN_BARS}bars maxhold={MAX_HOLD}; fee-net (corrected)")
print("="*96)

for drop, lbl in ((False,"WITH divergence markers"),(True,"WITHOUT divergence (buycirc_div/sellcirc_div dropped)")):
    tr = simulate(drop)
    print(f"\n##### {lbl} — {len(tr)} entries #####")
    full = bucket(tr)
    print("  FULL SAMPLE   "+fmt(full))
    print("    monotonicity:", mono(full))
    print("  -- regime split --")
    print("  CHOP          "+fmt(bucket(tr, regime="chop")))
    print("  TREND         "+fmt(bucket(tr, regime="trend")))
    print("  -- walk-forward (net-R) --")
    print("  TRAIN<=May15  "+fmt(bucket(tr, 0, TRAIN_END)))
    print("  VAL<=Jun1     "+fmt(bucket(tr, TRAIN_END, VAL_END)))

print("\n"+"="*96)
print("Read: if win% rises with depth but netR does NOT -> conviction, not edge (the monoculture trap).")
print("If netR rises with depth in TREND but flat/inverts in CHOP -> confirms caveat #1 (co-agree+co-fail in chop).")
