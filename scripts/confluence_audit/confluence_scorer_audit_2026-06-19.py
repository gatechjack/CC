"""Read-only confluence-scorer audit: role coverage feeds + signal firing correlation.
Computes, on btc_scalping.db bars_3m, the pairwise correlation (phi) of the scorer's
signals as the scorer actually sees them: 'live within TTL' (fired in the last ttl_bars).
Effective independent dimensions via participation ratio PR = N^2 / sum_ij C_ij^2."""
import sqlite3, math

con = sqlite3.connect(r"file:C:\Users\AA Incorporado\cc\data\btc_scalping.db?mode=ro", uri=True)

# factor: (column, side, weight, ttl_bars on 3m)   ttl: mc_a/cvd=10 (30min), mc_b/otter=5 (15min)
SIG = [
 ("mc_a_bluetriangle","blue_triangle","buy",3,10),
 ("mc_a_longema","long_ema_signal","buy",2,10),
 ("mc_a_yellow_x","yellow_cross","buy",2,10),
 ("mc_b_gold_buy","gold_buy_gold_circle","buy",5,5),
 ("mc_b_buycirc_div","divergence_buy_circle","buy",4,5),
 ("mc_b_buy_circle","buy_circle","buy",3,5),
 ("otter_buy","otter_buy","buy",3,5),
 ("cvd_bull_flip","cvd_flip_bullish","buy",2,10),
 ("mc_a_red_diamond","red_diamond","sell",4,10),
 ("mc_a_blood_diamond","blood_diamond","sell",5,10),
 ("mc_a_redx","red_cross","sell",2,10),
 ("mc_b_sellcirc_div","divergence_sell_circle","sell",4,5),
 ("mc_b_sell_circle","sell_circle","sell",3,5),
 ("otter_sell","otter_sell","sell",3,5),
 ("cvd_bear_flip","cvd_flip_bearish","sell",2,10),
]
N = len(SIG)
rows = con.execute("SELECT ts," + ",".join(s[1] for s in SIG) + " FROM bars_3m ORDER BY ts").fetchall()
con.close()
T = len(rows)

# firing boolean per signal
fire = [[0]*T for _ in range(N)]
for j in range(N):
    for i in range(T):
        v = rows[i][j+1]
        fire[j][i] = 1 if (v is not None and v != 0) else 0

# live-within-TTL boolean (scorer's view): fired within last ttl_bars (inclusive)
live = [[0]*T for _ in range(N)]
for j in range(N):
    ttl = SIG[j][4]
    last = -10**9
    for i in range(T):
        if fire[j][i]:
            last = i
        live[j][i] = 1 if (i - last) < ttl else 0

def phi(a, b):
    n11=n10=n01=n00=0
    for i in range(T):
        x,y=a[i],b[i]
        if x and y: n11+=1
        elif x and not y: n10+=1
        elif y: n01+=1
        else: n00+=1
    den = math.sqrt((n11+n10)*(n01+n00)*(n11+n01)*(n10+n00))
    return (n11*n00 - n10*n01)/den if den>0 else 0.0

def jacc(a,b):
    inter=sum(1 for i in range(T) if a[i] and b[i]); uni=sum(1 for i in range(T) if a[i] or b[i])
    return inter/uni if uni else 0.0

# correlation matrices (on live series = scorer-relevant)
C = [[0.0]*N for _ in range(N)]
for i in range(N):
    C[i][i]=1.0
    for k in range(i+1,N):
        c=phi(live[i],live[k]); C[i][k]=c; C[k][i]=c

names=[s[0] for s in SIG]; sides=[s[2] for s in SIG]; wts=[s[3] for s in SIG]
print("="*100)
print("SIGNAL BASE FIRING RATES (exact-bar) and LIVE-COVERAGE (within TTL) over",T,"bars (3m Mar30-Jun19)")
print(f"{'signal':20}{'side':5}{'wt':4}{'ttl_b':6}{'fires':8}{'fire%':8}{'live%':8}")
for j,s in enumerate(SIG):
    fr=sum(fire[j]); lv=sum(live[j])
    print(f"{s[0]:20}{s[2]:5}{s[3]:<4}{s[4]:<6}{fr:<8}{100*fr/T:<8.2f}{100*lv/T:<8.2f}")

# pretty matrix with short codes
codes=[f"{i:02d}" for i in range(N)]
print("\n"+"="*100)
print("LIVE-WITHIN-TTL CORRELATION MATRIX (phi). Rows/cols = signal index below.")
for i,s in enumerate(SIG): print(f"  {i:02d} {s[0]:20} ({s[2]})")
hdr="     "+"".join(f"{c:>6}" for c in codes); print(hdr)
for i in range(N):
    line=f"{codes[i]:>4} "+"".join(f"{C[i][k]:>6.2f}" for k in range(N))
    print(line)

# flag within-side pairs
print("\n"+"="*100)
print("WITHIN-SIDE PAIRS, ranked by |phi| (live). [R]=redundant phi>=0.70  [n]=notable 0.50-0.70")
pairs=[]
for i in range(N):
    for k in range(i+1,N):
        if sides[i]==sides[k]:
            pairs.append((abs(C[i][k]),C[i][k],i,k))
pairs.sort(reverse=True)
for ab,c,i,k in pairs:
    tag="[R]" if ab>=0.70 else ("[n]" if ab>=0.50 else "   ")
    jc=jacc(live[i],live[k]); ph_exact=phi(fire[i],fire[k])
    print(f"  {tag} phi={c:+.2f}  jacc={jc:.2f}  exactbar_phi={ph_exact:+.2f}  {names[i]}({sides[i]}) ~ {names[k]}")

# participation ratio -> effective independent dimensions
def PR(idx):
    s=0.0
    for i in idx:
        for k in idx:
            s+=C[i][k]**2
    n=len(idx)
    return n, n*n/s if s>0 else n
buy=[i for i in range(N) if sides[i]=="buy"]; sell=[i for i in range(N) if sides[i]=="sell"]
print("\n"+"="*100)
print("EFFECTIVE INDEPENDENT DIMENSIONS  (participation ratio PR = N^2 / sum_ij phi_ij^2)")
for lbl,idx in (("BUY block",buy),("SELL block",sell),("ALL",list(range(N)))):
    n,pr=PR(idx)
    print(f"  {lbl:12}: {n} signals  ->  ~{pr:.1f} independent dimensions   ({100*pr/n:.0f}% of nominal)")

# weight concentration: how much score-weight sits in correlated clusters
print("\n"+"="*100)
print("WEIGHT vs INDEPENDENCE  (weights as configured; redundant weight = double-counted confluence)")
for lbl,idx in (("BUY",buy),("SELL",sell)):
    tw=sum(wts[i] for i in idx)
    print(f"  {lbl}: total configured weight={tw} across {len(idx)} signals")
