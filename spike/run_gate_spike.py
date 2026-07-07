"""GATE-VARIANT spike: pivot in {50,25} x gate in {hard,soft,none} (6 configs).
Read-only, GROSS, k=1 causal. Detector md5 91fd7672 asserted start+end. Data =
Coinbase INTX 15m 230d + local Bitunix 3m (~47d overlap). Regime = ema200_pos_slope,
STRICT last-closed 15m alignment before each 3m entry (prod-accurate k=1).
Drift-embedding direction+gate-matched null (pool + target per variant), 200x, p95.
Cell passes iff avgR>0 AND avgR>=null_p95."""
from __future__ import annotations
import hashlib, math, os, random, sys
sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar, STOP_BUFFER_PCT
import backtest as bt
from regime_filter import regime_series

MD5_CERT = "91fd76726364331c8083aaaa68fce199"
PIVOTS = [50, 25]
GATES = ["hard", "soft", "none"]
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
CB = {"BTCUSDT": "BTC-PERP", "ETHUSDT": "ETH-PERP", "SOLUSDT": "SOL-PERP", "XRPUSDT": "XRP-PERP"}
DATA = os.path.join(os.path.dirname(__file__), "data")
MAXH = bt.MAX_HOLD_BARS
NULL_RUNS, NULL_LB, _15M = 200, 20, 900_000

def md5(p): return hashlib.md5(open(p, "rb").read()).hexdigest()
def assert_detector(tag):
    m = md5(os.path.join(os.path.dirname(__file__), "bitunix_sfp.py"))
    print(f"[{tag}] detector md5={m} {'OK' if m == MD5_CERT else 'MISMATCH!!'}")
    assert m == MD5_CERT

def load_cb15(coin):
    out = []
    for row in open(os.path.join(DATA, f"COINBASE_INTX_{CB[coin]}_15m_230d.csv")):
        p = row.strip().split(",")
        if len(p) >= 5 and p[0].isdigit():
            out.append(SfpBar(int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return out

def neg(bars): return [SfpBar(b.ts_ms, -b.open, -b.low, -b.high, -b.close) for b in bars]

def reg_causal(lab, ts):  # last-closed 15m regime strictly before the 3m entry
    return lab.get((ts // _15M - 1) * _15M)

def allow(gate, side, reg):
    if gate != "hard": return True
    return (reg in ("up", "range")) if side == "long" else (reg in ("down", "range"))

def tgt(gate, side, reg):
    if gate == "soft":
        counter = (side == "long" and reg == "down") or (side == "short" and reg == "up")
        return 1.0 if counter else 2.0
    return 2.0

def sim(bars3, idx, swept, side, tp_r):
    if idx >= len(bars3): return None
    entry = bars3[idx].open
    if side == "long":
        stop = swept - STOP_BUFFER_PCT * entry; r = entry - stop
        if r <= 0: return None
        tp = entry + tp_r * r
        for i in range(idx + 1, min(idx + MAXH + 1, len(bars3))):
            b = bars3[i]
            if b.low <= stop: return (-1.0, i - idx)
            if b.high >= tp: return (tp_r, i - idx)
        last = bars3[min(idx + MAXH, len(bars3) - 1)]; return ((last.close - entry) / r, MAXH)
    else:
        stop = swept + STOP_BUFFER_PCT * entry; r = stop - entry
        if r <= 0: return None
        tp = entry - tp_r * r
        for i in range(idx + 1, min(idx + MAXH + 1, len(bars3))):
            b = bars3[i]
            if b.high >= stop: return (-1.0, i - idx)
            if b.low <= tp: return (tp_r, i - idx)
        last = bars3[min(idx + MAXH, len(bars3) - 1)]; return ((entry - last.close) / r, MAXH)

def collect(bars3, sigs, lab, side, gate):
    rs, open_until, gatepass = [], -1, 0
    for s in sigs:
        idx = s.entry_bar_index
        if idx >= len(bars3): continue
        reg = reg_causal(lab, bars3[idx].ts_ms)
        if not allow(gate, side, reg): continue
        gatepass += 1
        if idx <= open_until: continue
        swept = s.swept_low if side == "long" else -s.swept_low
        res = sim(bars3, idx, swept, side, tgt(gate, side, reg))
        if res is None: continue
        r, hold = res; rs.append((r, reg)); open_until = idx + hold
    return rs, gatepass, len(sigs)

def null_dist(bars3, lab, side, gate, n, seed0):
    if n == 0: return []
    pool = [i for i in range(NULL_LB, len(bars3) - 1)
            if allow(gate, side, reg_causal(lab, bars3[i].ts_ms))]
    if len(pool) < n: return []
    out = []
    for run in range(NULL_RUNS):
        rng = random.Random(seed0 * 100003 + run); chosen = sorted(rng.sample(pool, n))
        rs, open_until = [], -1
        for idx in chosen:
            if idx <= open_until: continue
            reg = reg_causal(lab, bars3[idx].ts_ms)
            sw = min(b.low for b in bars3[idx-NULL_LB:idx]) if side == "long" else max(b.high for b in bars3[idx-NULL_LB:idx])
            res = sim(bars3, idx, sw, side, tgt(gate, side, reg))
            if res is None: continue
            r, hold = res; rs.append(r); open_until = idx + hold
        if rs: out.append(sum(rs) / len(rs))
    return out

def pctile(xs, p):
    if not xs: return float("nan")
    s = sorted(xs); k = (len(s) - 1) * p / 100.0; f = int(k); c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)

def main():
    assert_detector("START")
    cb15 = {c: load_cb15(c) for c in COINS}
    bit3 = {c: bt.load_3m(c) for c in COINS}
    labs = {c: regime_series(cb15[c], "ema200_pos_slope") for c in COINS}
    wk = {c: (bit3[c][-1].ts_ms - bit3[c][0].ts_ms) / (7 * 86400000) for c in COINS}
    drift = {c: (bit3[c][-1].close - bit3[c][0].close) / bit3[c][0].close for c in COINS}
    sig = {}  # (pl, coin, side) -> signals
    for pl in PIVOTS:
        for c in COINS:
            sig[(pl, c, "long")] = bt.get_signals(cb15[c], bit3[c], pl)
            sig[(pl, c, "short")] = bt.get_signals(neg(cb15[c]), neg(bit3[c]), pl)
    cells = {}
    for pl in PIVOTS:
        for g in GATES:
            for c in COINS:
                for side in ("long", "short"):
                    rs, gp, ns = collect(bit3[c], sig[(pl, c, side)], labs[c], side, g)
                    n = len(rs); rr = [r for r, _ in rs]
                    avg = sum(rr)/n if n else float("nan")
                    wr = sum(1 for r in rr if r > 0)/n if n else float("nan")
                    nd = null_dist(bit3[c], labs[c], side, g, n, hash((pl, g, c, side)) & 0xffff)
                    np95 = pctile(nd, 95)
                    beats = n > 0 and avg > 0 and not math.isnan(np95) and avg >= np95
                    cells[(pl, g, c, side)] = dict(n=n, gp=gp, ns=ns, avg=avg, wr=wr, tot=sum(rr),
                        np95=np95, beats=beats, rs=rs)
    # PER-CELL
    print("\n=== PER-CELL (config x coin x side) ===")
    print(f"{'piv':>3} {'gate':4} {'coin':7} {'side':5} {'n':>4} {'sig>gp':>8} {'avgR':>7} {'WR%':>4} {'totR':>7} {'nullp95':>8} {'beats':>5}")
    for pl in PIVOTS:
        for g in GATES:
            for c in COINS:
                for side in ("long", "short"):
                    x = cells[(pl, g, c, side)]
                    a = f"{x['avg']:+.3f}" if not math.isnan(x['avg']) else "  nan"
                    w = f"{x['wr']*100:3.0f}" if not math.isnan(x['wr']) else "nan"
                    p = f"{x['np95']:+.3f}" if not math.isnan(x['np95']) else "  nan"
                    print(f"{pl:>3} {g:4} {c:7} {side:5} {x['n']:>4} {str(x['ns'])+'>'+str(x['gp']):>8} {a:>7} {w:>4} {x['tot']:+7.2f} {p:>8} {str(x['beats']):>5}")
    # FIRE-RATE
    print("\n=== FIRE-RATE/wk (pre-gate sig / post-gate) per coin ===")
    for pl in PIVOTS:
        for g in GATES:
            row = " ".join(f"{c[:3]}:{(cells[(pl,g,c,'long')]['ns']+cells[(pl,g,c,'short')]['ns'])/wk[c]:.1f}/{(cells[(pl,g,c,'long')]['gp']+cells[(pl,g,c,'short')]['gp'])/wk[c]:.1f}" for c in COINS)
            print(f"  piv{pl} {g:4}: {row}")
    # BEAR-BETA
    print("\n=== BEAR-BETA (3m drift; short edge <= passive-short = beta) ===")
    for c in COINS: print(f"  {c}: drift={drift[c]*100:+.1f}% passive-short~{-drift[c]*100:+.1f}%")
    # REGIME SPLIT
    print("\n=== REGIME SPLIT avgR by bucket (pooled coins) ===")
    for pl in PIVOTS:
        for g in GATES:
            b = {}
            for c in COINS:
                for side in ("long","short"):
                    for r, reg in cells[(pl,g,c,side)]["rs"]:
                        b.setdefault((side,reg), []).append(r)
            def a(k):
                v=b.get(k,[]); return f"{sum(v)/len(v):+.2f}(n{len(v)})" if v else "--"
            print(f"  piv{pl} {g:4}: Lup{a(('long','up'))} Lrng{a(('long','range'))} Ldn{a(('long','down'))} | Sup{a(('short','up'))} Srng{a(('short','range'))} Sdn{a(('short','down'))}")
    # helpers for pooled + bar
    def pool(pl, g):
        rs = [r for c in COINS for side in ("long","short") for r,_ in cells[(pl,g,c,side)]["rs"]]
        return len(rs), sum(rs)
    def bcount(pl, g): return sum(1 for c in COINS for side in ("long","short") if cells[(pl,g,c,side)]["beats"])
    # 5 COMPARISONS
    print("\n=== THE 5 COMPARISONS (pooled totalR / n) ===")
    def tot(pl,g): n,t=pool(pl,g); return f"totR={t:+.2f}(n={n})"
    print(f"  1) p50 hard {tot(50,'hard')}  vs  p50 none {tot(50,'none')}")
    print(f"  2) p50 hard {tot(50,'hard')}  vs  p50 soft {tot(50,'soft')}")
    print(f"  3) p25 hard {tot(25,'hard')}  vs  p25 none {tot(25,'none')}")
    print(f"  4) p25 none {tot(25,'none')}  vs  p50 none {tot(50,'none')}")
    # 5) Bucket-A counter-trend cells (long-down, short-up) under soft/none, pooled, n>=30
    print("  5) BUCKET-A counter-trend (long-DOWN + short-UP) under non-hard gates:")
    for pl in PIVOTS:
        for g in ("soft", "none"):
            ld = [r for c in COINS for r,reg in cells[(pl,g,c,'long')]["rs"] if reg=="down"]
            su = [r for c in COINS for r,reg in cells[(pl,g,c,'short')]["rs"] if reg=="up"]
            for nm, rs, side in (("long-DOWN", ld, "long"), ("short-UP", su, "short")):
                n=len(rs); avg=sum(rs)/n if n else float("nan")
                # null for this bucket: restrict pool to that regime bucket
                bp = [i for c in COINS for i in range(NULL_LB,len(bit3[c])-1)
                      if reg_causal(labs[c], bit3[c][i].ts_ms)==("down" if side=="long" else "up")]
                # approximate null via first coin's bars (bucket-level, directional)
                nd=[]
                if n>0:
                    for run in range(NULL_RUNS):
                        rng=random.Random(hash((pl,g,nm,run))&0xffffff)
                        acc=[]
                        for c in COINS:
                            poolc=[i for i in range(NULL_LB,len(bit3[c])-1) if reg_causal(labs[c],bit3[c][i].ts_ms)==("down" if side=="long" else "up")]
                            k=max(1,n//4)
                            if len(poolc)<k: continue
                            for idx in rng.sample(poolc,k):
                                sw=min(x.low for x in bit3[c][idx-NULL_LB:idx]) if side=="long" else max(x.high for x in bit3[c][idx-NULL_LB:idx])
                                res=sim(bit3[c],idx,sw,side,tgt(g,side,("down" if side=="long" else "up")))
                                if res: acc.append(res[0])
                        if acc: nd.append(sum(acc)/len(acc))
                p95=pctile(nd,95)
                clr = n>=30 and not math.isnan(avg) and avg>0 and not math.isnan(p95) and avg>=p95
                av=f"{avg:+.3f}" if not math.isnan(avg) else "nan"
                pp=f"{p95:+.3f}" if not math.isnan(p95) else "nan"
                print(f"     piv{pl} {g:4} {nm:9}: n={n:3} avgR={av} null_p95={pp} clears(n>=30)={clr}")
    # 5-PART BAR
    print("\n=== 5-PART SUCCESS BAR (each config vs baseline p50-hard) ===")
    bn, bt_ = pool(50, "hard"); bbeat = bcount(50, "hard")
    print(f"  baseline p50-hard: n={bn} totalR={bt_:+.2f} beats_cells={bbeat}")
    for pl in PIVOTS:
        for g in GATES:
            if (pl, g) == (50, "hard"): continue
            cb_ = len({c for c in COINS for side in ("long","short") if cells[(pl,g,c,side)]["beats"]})
            lp = any(cells[(pl,g,c,"long")]["beats"] for c in COINS)
            sp = any(cells[(pl,g,c,"short")]["beats"] for c in COINS)
            n, t = pool(pl, g)
            regs = {(side,reg) for c in COINS for side in ("long","short") if cells[(pl,g,c,side)]["beats"] for _r,reg in cells[(pl,g,c,side)]["rs"]}
            c1, c2 = cb_ >= 2, lp and sp
            c3 = n >= 100 and t >= 1.5 * bt_
            c4 = len(regs) > 1
            c5 = bcount(pl, g) >= bbeat
            v = "ADOPT" if all([c1,c2,c3,c4,c5]) else "no"
            print(f"  p{pl} {g:4}: 1coins>=2={c1}({cb_}) 2both={c2} 3totR>=1.5xbase={c3}(t={t:+.1f}/n{n}) 4regimes={c4} 5beats>=base={c5}({bcount(pl,g)}) => {v}")
    assert_detector("END")

if __name__ == "__main__":
    main()
