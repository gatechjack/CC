"""STEP 3 - pivot-sensitivity spike (GROSS, k=1 causal, read-only).
15m = Coinbase INTX 230d; 3m BOS = local Bitunix (spike CSVs, ~47d overlap).
Detector = certified SfpModeBDetector (md5 asserted 91fd7672 start+end).
ONE variable: pivot_len in {5,10,20,50}. Regime side-gate = live ema200_pos_slope.
Short = M2=0 negation for DETECTION (coord-consistent cross-venue), levels un-reflected,
short sim in REAL coords (prod geometry_short: stop above, tp below). Drift-embedding
direction+regime-matched null, 200x, p95. Cell passes iff avgR>0 AND avgR>=null_p95."""
from __future__ import annotations
import hashlib, math, os, random, sys
sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar, STOP_BUFFER_PCT, TP_R
import backtest as bt
from regime_filter import regime_series

MD5_CERT = "91fd76726364331c8083aaaa68fce199"
PIVOTS = [5, 10, 20, 50]
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
CB = {"BTCUSDT": "BTC-PERP", "ETHUSDT": "ETH-PERP", "SOLUSDT": "SOL-PERP", "XRPUSDT": "XRP-PERP"}
DATA = os.path.join(os.path.dirname(__file__), "data")
MAXH = bt.MAX_HOLD_BARS
NULL_RUNS, NULL_LB, P95 = 200, 20, 95
_15M = 900_000

def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()

def assert_detector(tag):
    m = md5(os.path.join(os.path.dirname(__file__), "bitunix_sfp.py"))
    print(f"[{tag}] detector md5={m} {'OK' if m == MD5_CERT else 'MISMATCH!!'}")
    assert m == MD5_CERT, f"detector md5 {m} != {MD5_CERT}"

def load_cb15(coin):
    bars = []
    with open(os.path.join(DATA, f"COINBASE_INTX_{CB[coin]}_15m_230d.csv")) as f:
        for row in f:
            p = row.strip().split(",")
            if len(p) < 5 or not p[0].isdigit():
                continue
            bars.append(SfpBar(int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return bars

def neg(bars):  # M2=0: reflected=-real, high/low swap
    return [SfpBar(b.ts_ms, -b.open, -b.low, -b.high, -b.close) for b in bars]

def regime_at(lab, ts):
    return lab.get(ts - (ts % _15M))

def long_sim(bars3, idx, swept_low):
    if idx >= len(bars3): return None
    entry = bars3[idx].open
    stop = swept_low - STOP_BUFFER_PCT * entry
    r = entry - stop
    if r <= 0: return None
    tp = entry + TP_R * r
    for i in range(idx + 1, min(idx + MAXH + 1, len(bars3))):
        b = bars3[i]
        if b.low <= stop: return (-1.0, i - idx)
        if b.high >= tp: return (TP_R, i - idx)
    last = bars3[min(idx + MAXH, len(bars3) - 1)]
    return ((last.close - entry) / r, MAXH)

def short_sim(bars3, idx, swept_high):
    if idx >= len(bars3): return None
    entry = bars3[idx].open
    stop = swept_high + STOP_BUFFER_PCT * entry
    r = stop - entry
    if r <= 0: return None
    tp = entry - TP_R * r
    for i in range(idx + 1, min(idx + MAXH + 1, len(bars3))):
        b = bars3[i]
        if b.high >= stop: return (-1.0, i - idx)
        if b.low <= tp: return (TP_R, i - idx)
    last = bars3[min(idx + MAXH, len(bars3) - 1)]
    return ((entry - last.close) / r, MAXH)

def gated_trades(bars3, sigs, lab, side):
    """Apply regime side-gate, then one-open-at-a-time. Returns (rs, n_gatepass, n_signals)."""
    allow = ("up", "range") if side == "long" else ("down", "range")
    sim = long_sim if side == "long" else short_sim
    rs, open_until, gatepass = [], -1, 0
    for s in sigs:
        idx = s.entry_bar_index
        if idx >= len(bars3): continue
        reg = regime_at(lab, bars3[idx].ts_ms)
        if reg not in allow: continue
        gatepass += 1
        if idx <= open_until: continue
        swept = s.swept_low if side == "long" else -s.swept_low  # un-reflect for short
        res = sim(bars3, idx, swept)
        if res is None: continue
        r, hold = res
        rs.append((r, reg))
        open_until = idx + hold
    return rs, gatepass, len(sigs)

def null_dist(bars3, lab, side, n, seed0):
    """Drift-embedding, direction+regime-matched random-entry null. Returns [avgR]*runs."""
    if n == 0: return []
    allow = ("up", "range") if side == "long" else ("down", "range")
    sim = long_sim if side == "long" else short_sim
    pool = [i for i in range(NULL_LB, len(bars3) - 1) if regime_at(lab, bars3[i].ts_ms) in allow]
    if len(pool) < n: return []
    out = []
    for run in range(NULL_RUNS):
        rng = random.Random(seed0 * 100003 + run)
        chosen = sorted(rng.sample(pool, n))
        rs, open_until = [], -1
        for idx in chosen:
            if idx <= open_until: continue
            lo = min(b.low for b in bars3[idx-NULL_LB:idx]); hi = max(b.high for b in bars3[idx-NULL_LB:idx])
            res = sim(bars3, idx, lo if side == "long" else hi)
            if res is None: continue
            r, hold = res
            rs.append(r); open_until = idx + hold
        if rs: out.append(sum(rs) / len(rs))
    return out

def pctile(xs, p):
    if not xs: return float("nan")
    s = sorted(xs); k = (len(s) - 1) * p / 100.0
    f = int(k); c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)

def main():
    assert_detector("START")
    cb15 = {c: load_cb15(c) for c in COINS}
    bit3 = {c: bt.load_3m(c) for c in COINS}
    labs = {c: regime_series(cb15[c], "ema200_pos_slope") for c in COINS}
    # window weeks from the 3m (signal) window
    wk = {c: (bit3[c][-1].ts_ms - bit3[c][0].ts_ms) / (7 * 86400000) for c in COINS}
    # bear-beta: close-to-close drift over 3m window + passive-short R-equiv (per unit stop ~ n/a; use pct)
    drift = {c: (bit3[c][-1].close - bit3[c][0].close) / bit3[c][0].close for c in COINS}

    cells = {}  # (pl, coin, side) -> dict
    for pl in PIVOTS:
        for c in COINS:
            lsig = bt.get_signals(cb15[c], bit3[c], pl)
            ssig = bt.get_signals(neg(cb15[c]), neg(bit3[c]), pl)
            for side, sig in (("long", lsig), ("short", ssig)):
                rs, gatepass, nsig = gated_trades(bit3[c], sig, labs[c], side)
                n = len(rs)
                rr = [r for r, _ in rs]
                avg = sum(rr) / n if n else float("nan")
                wr = sum(1 for r in rr if r > 0) / n if n else float("nan")
                tot = sum(rr)
                nd = null_dist(bit3[c], labs[c], side, n, hash((pl, c, side)) & 0xffff)
                np95 = pctile(nd, P95)
                beats = (n > 0) and (avg > 0) and (not math.isnan(np95)) and (avg >= np95)
                cells[(pl, c, side)] = dict(n=n, gatepass=gatepass, nsig=nsig, avg=avg, wr=wr,
                    tot=tot, np95=np95, beats=beats, fire_pre=nsig/wk[c], fire_post=gatepass/wk[c],
                    rs=rs)
    # ---- PER-CELL REPORT ----
    print("\n=== PER-CELL (regime-gated) ===")
    hdr = f"{'piv':>3} {'coin':7} {'side':5} {'n':>4} {'fillGate':>8} {'avgR':>7} {'WR%':>5} {'totR':>7} {'null_p95':>8} {'beats':>5}"
    print(hdr)
    for pl in PIVOTS:
        for c in COINS:
            for side in ("long", "short"):
                x = cells[(pl, c, side)]
                fill = f"{x['nsig']}->{x['gatepass']}"
                a = f"{x['avg']:+.3f}" if not math.isnan(x['avg']) else "  nan"
                w = f"{x['wr']*100:4.0f}" if not math.isnan(x['wr']) else " nan"
                p = f"{x['np95']:+.3f}" if not math.isnan(x['np95']) else "  nan"
                print(f"{pl:>3} {c:7} {side:5} {x['n']:>4} {fill:>8} {a:>7} {w:>5} {x['tot']:+7.2f} {p:>8} {str(x['beats']):>5}")
    # ---- FIRE RATE / week ----
    print("\n=== FIRE-RATE per week (pre-gate signals / post-gate) per coin ===")
    for pl in PIVOTS:
        row = " ".join(f"{c}:{cells[(pl,c,'long')]['fire_pre']+cells[(pl,c,'short')]['fire_pre']:.1f}/{cells[(pl,c,'long')]['fire_post']+cells[(pl,c,'short')]['fire_post']:.1f}" for c in COINS)
        print(f"  piv{pl:>3}: {row}")
    # ---- BEAR-BETA ----
    print("\n=== BEAR-BETA (3m-window close-to-close drift; flag short edge <= passive-short) ===")
    for c in COINS:
        print(f"  {c}: drift={drift[c]*100:+.1f}%  passive-short~{-drift[c]*100:+.1f}%")
    # ---- REGIME SPLIT ----
    print("\n=== REGIME SPLIT (avgR by bucket, pooled coins) ===")
    for pl in PIVOTS:
        buck = {("long","up"):[],("long","range"):[],("short","down"):[],("short","range"):[]}
        for c in COINS:
            for side in ("long","short"):
                for r, reg in cells[(pl,c,side)]["rs"]:
                    if (side,reg) in buck: buck[(side,reg)].append(r)
        def ar(k):
            v=buck[k]; return f"n={len(v):3} avgR={sum(v)/len(v):+.3f}" if v else "n=  0  --"
        print(f"  piv{pl:>3}: L-up[{ar(('long','up'))}] L-rng[{ar(('long','range'))}] S-dn[{ar(('short','down'))}] S-rng[{ar(('short','range'))}]")
    # ---- SUCCESS BAR ----
    print("\n=== PRE-REGISTERED SUCCESS BAR (vs pivot50) ===")
    def pooled(pl):
        allrs = [r for c in COINS for side in ("long","short") for r,_ in cells[(pl,c,side)]["rs"]]
        return len(allrs), sum(allrs)
    p50n, p50tot = pooled(50)
    print(f"  pivot50 pooled: n={p50n} totalR={p50tot:+.2f}")
    for pl in PIVOTS:
        coins_beat = len({c for c in COINS for side in ("long","short") if cells[(pl,c,side)]["beats"]})
        long_pass = any(cells[(pl,c,"long")]["beats"] for c in COINS)
        short_pass = any(cells[(pl,c,"short")]["beats"] for c in COINS)
        n, tot = pooled(pl)
        c1 = coins_beat >= 2
        c2 = long_pass and short_pass
        c3 = (n >= 100) and (tot >= 1.5 * p50tot) and (p50tot > 0 or tot > 0)
        # regime spread: passing cells span >1 regime bucket
        regs = set()
        for c in COINS:
            for side in ("long","short"):
                if cells[(pl,c,side)]["beats"]:
                    for _r, reg in cells[(pl,c,side)]["rs"]: regs.add((side,reg))
        c4 = len(regs) > 1
        verdict = "ADOPT-CANDIDATE" if (c1 and c2 and c3 and c4) else "no"
        print(f"  piv{pl:>3}: coins_beat={coins_beat}({c1}) both_sides={c2} totalR={tot:+.2f}(n={n}) 1.5xp50={1.5*p50tot:+.2f}->{c3} regimes={len(regs)}({c4}) => {verdict}")
    assert_detector("END")

if __name__ == "__main__":
    main()
