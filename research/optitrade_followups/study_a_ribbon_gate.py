"""
STUDY A -- ribbon-alignment as an SFP with-trend gate candidate.

Evaluate a new RIBBON gate on the FROZEN SFP construct fresh-inst base stream,
EXACTLY as _sfp_gate_tiebreaker did for ema200/RD-trend/ps-trail30: arm = a
with-trend filter over the base candidates, then one-position-guard booking, then
per-coin + pooled armed avgR/sumR/n with an own-bucket opportunity-set drift-null.

RIBBON gate: 10 EMAs on hlc3, lengths [30,40,...,120]. State BULL when all 10 are
rising for 3 consecutive bars (e>e[1]>e[2]>e[3]), BEAR when all falling, else
NEUTRAL. Longs allowed in BULL, shorts in BEAR, nothing in NEUTRAL. Causal: the
state at a candidate is the last CLOSED ribbon bar with close_ts <= entry_ts.
Two variants: RIBBON computed on 15m detection bars and on 1h detection bars.

Null: own-bucket drift-embedded resample of each real trade's (side,macro60)
stratum, 200x, report p5/p50/p95 + real-sum percentile (division convention).
GROSS, in-sample Binance-perp proxy (shares the live feed -> a LEAD, not OOS).
STOP-gate: reproduce the ema200/RD/ps-trail30 anchors (n exact, avgR tol 0.004)
before reporting RIBBON. Read-only; no prod writes.
"""
import sys, os, sqlite3, time, random
from bisect import bisect_right
from collections import defaultdict, Counter

SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
HERE = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
OUT = r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade_followups\STUDY_A_RIBBON.txt"
for p in (SFP_DIR, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import _sfp_causal_macro60 as M
import _sfp_head_to_head as H2H
import _sfp_p1c_htf as P1C
import _sfp_degree_rerun as DR
import _inst_levels as IL
import _sfp_trend_gate_bakeoff as BK

DB = M.DB; COINS = M.COINS; TF_1H = M.TF_1H; DAY_MS = M.DAY_MS
YEAR_MS = M.YEAR_MS; MIN_N = M.MIN_N
INCUMBENTS = ["ema200", "RD-trend", "ps-trail30"]
RIBBON_ARMS = ["RIBBON-15m", "RIBBON-1h"]
ARMS = INCUMBENTS + RIBBON_ARMS
RLENS = [30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
MS15, MS1H = 900_000, 3_600_000

# STOP-gate anchors (SFP_GATE_TIEBREAKER sec.0)
ANCHOR = {
    "ema200":     {"BTCUSDT": (207, -0.119), "ETHUSDT": (205, 0.132), "SOLUSDT": (220, 0.139),
                   "XRPUSDT": (226, -0.055), "POOLED": (858, 0.024)},
    "RD-trend":   {"BTCUSDT": (158, -0.089), "ETHUSDT": (151, 0.286), "SOLUSDT": (176, 0.105),
                   "XRPUSDT": (149, 0.085), "POOLED": (634, 0.095)},
    "ps-trail30": {"BTCUSDT": (129, 0.067), "ETHUSDT": (142, 0.272), "SOLUSDT": (127, 0.102),
                   "XRPUSDT": (136, 0.059), "POOLED": (534, 0.128)},
}
TOL_AV = 0.004

def ema(vals, L):
    a = 2.0 / (L + 1.0); out = [0.0] * len(vals); e = None
    for i, v in enumerate(vals):
        e = v if e is None else a * v + (1.0 - a) * e
        out[i] = e
    return out

def ribbon_state_map(bars, tf_ms):
    hlc3 = [(b.high + b.low + b.close) / 3.0 for b in bars]
    emas = [ema(hlc3, L) for L in RLENS]
    n = len(bars); st = [0] * n
    for i in range(3, n):
        if all(emas[k][i] > emas[k][i-1] and emas[k][i-1] > emas[k][i-2] and emas[k][i-2] > emas[k][i-3]
               for k in range(10)):
            st[i] = 1
        elif all(emas[k][i] < emas[k][i-1] and emas[k][i-1] < emas[k][i-2] and emas[k][i-2] < emas[k][i-3]
                 for k in range(10)):
            st[i] = -1
    cts = [b.ts_ms + tf_ms for b in bars]
    return cts, st

def ribbon_passes(c, cts, st):
    j = bisect_right(cts, c["entry_ts"]) - 1
    s = st[j] if j >= 0 else 0
    return (c["side"] == "long" and s == 1) or (c["side"] == "short" and s == -1)

def null200(real, opp_by_stratum, seed, iters=200):
    groups = Counter((t["side"], t["macro60"]) for t in real)
    real_sum = sum(t["R"] for t in real)
    if not real or any(not opp_by_stratum.get(g) for g in groups):
        return None
    rng = random.Random(seed); sums = []
    for _ in range(iters):
        s = 0.0
        for g, cnt in groups.items():
            pool = opp_by_stratum[g]
            for _ in range(cnt):
                s += pool[rng.randrange(len(pool))]
        sums.append(s)
    sums.sort()
    q = lambda p: sums[min(len(sums) - 1, int(round(p * (len(sums) - 1))))]
    pct = 100.0 * sum(1 for x in sums if x < real_sum) / len(sums)
    return q(0.05), q(0.50), q(0.95), pct, real_sum

def main():
    t0 = time.time()
    con = sqlite3.connect(DB)
    m60 = {(s, d): r for s, d, r in con.execute("SELECT symbol,day_ts_ms,regime FROM macro_regime_60d")}
    booked = {g: [] for g in ARMS}
    opp_coin = {g: {c: defaultdict(list) for c in COINS} for g in ARMS}
    span_lo = span_hi = None
    print("STUDY A: base stream once/coin; arms are filters ...", flush=True)
    for coin in COINS:
        tc = time.time()
        b15 = DR.load_bars(con, coin, "15m"); b3 = DR.load_bars(con, coin, "3m")
        b1d = DR.load_bars(con, coin, "1d"); b1h = DR.load_bars(con, coin, "1h")
        labels, cts = DR.precompute_regime(b15)
        il = IL.InstLevels(coin, b15, b1d)
        rd_lookup = BK.rd_os_lookup_builder(b1h)
        lo, hi = b3[0].ts_ms, b3[-1].ts_ms
        span_lo = lo if span_lo is None else min(span_lo, lo)
        span_hi = hi if span_hi is None else max(span_hi, hi)
        day_ts = [b.ts_ms for b in b1d]; d_c = [b.close for b in b1d]
        gate_maps = {"ps-trail30": M.build_gate_map(day_ts, M.st_ps_trail(d_c, 30))}
        cands, ng = M.base_candidates(coin, b1h, TF_1H, b3, labels, cts, il, m60, rd_lookup)
        for c in cands:
            R, ex = H2H.walk_r(b3, c["ei"], c["entry"], c["stop"], c["r_unit"],
                               c["entry_ts"], c["side"], 3.0)
            c["_R"], c["_exit_ts"] = R, ex
        r15 = ribbon_state_map(b15, MS15); r1h = ribbon_state_map(b1h, MS1H)
        for g in ARMS:
            if g in INCUMBENTS:
                sub = [c for c in cands if M.arm_passes(g, c, gate_maps)]
            elif g == "RIBBON-15m":
                sub = [c for c in cands if ribbon_passes(c, r15[0], r15[1])]
            else:
                sub = [c for c in cands if ribbon_passes(c, r1h[0], r1h[1])]
            bk, opp = M.book_and_pool_cached(sub)
            booked[g] += bk
            for side, mac, R in opp:
                opp_coin[g][coin][(side, mac)].append(R)
        print("  [%s] base fresh-inst=%d  (%.0fs)" % (coin, len(cands), time.time() - tc), flush=True)
        del b15, b1d, il, labels, cts
    con.close()
    years = (span_hi - span_lo) / YEAR_MS

    L = []; W = L.append
    def hr(ch="="): W(ch * 100)
    hr()
    W("STUDY A -- RIBBON alignment as an SFP with-trend gate candidate")
    W("Evaluated on the FROZEN fresh-inst base stream exactly as SFP_GATE_TIEBREAKER (arm=filter ->")
    W("one-position booking -> per-coin+pooled avgR/sumR/n + own-bucket drift-null). 4 coins x %.2f yr." % years)
    W("GROSS, in-sample Binance-perp proxy (shares the live feed -> a LEAD, not OOS). Null=200x p5/p50/p95.")
    W("RIBBON: 10 EMA(hlc3) [30..120]; BULL=all-10 rising 3 bars, BEAR=all falling, else NEUTRAL;")
    W("long-in-BULL / short-in-BEAR / nothing-in-NEUTRAL; causal (last closed ribbon bar <= entry).")
    W("HOUSE RULE: only the operator rules things out; negatives = 'no edge on THIS test', a lever = a lead.")
    hr()

    # STOP-gate
    W("")
    W("### 0. STOP-GATE -- reproduce the incumbent anchors (n exact, avgR tol %.3f)" % TOL_AV)
    W("  %-12s %-9s %6s %10s   %-8s %-9s %s" % ("arm", "coin", "n", "avgR", "expN", "expAvgR", "verdict"))
    W("  " + "-" * 80)
    sane = True
    for g in INCUMBENTS:
        for coin in COINS + ["POOLED"]:
            sub = booked[g] if coin == "POOLED" else [t for t in booked[g] if t["coin"] == coin]
            n, sr, wr, av = H2H.agg(sub)
            en, eav = ANCHOR[g][coin]
            ok = (n == en and abs(av - eav) < TOL_AV); sane = sane and ok
            W("  %-12s %-9s %6d %+10.3f   %-8d %+9.3f %s" % (g, coin, n, av, en, eav,
                                                             "MATCH" if ok else "*** MISMATCH ***"))
        W("  " + "-" * 80)
    if not sane:
        W("  ==> *** MISMATCH -- base stream is NOT the frozen construct. STOP. ***")
        _flush(L); print("STOP: STUDY A sanity failed."); sys.exit(1)
    W("  ==> MATCH -- base stream is the frozen construct; RIBBON is comparable to the incumbents.")

    # main table
    W("")
    hr("#")
    W("### 1. ARMED avgR / sumR / n + own-bucket drift-null (200x, p5/p50/p95) -- per coin & pooled")
    hr("#")
    W("  n<%d = UNDERPOWERED. 'CLEARS-p95' iff real sumR > null p95." % MIN_N)
    for g in ARMS:
        n, sr, wr, av = H2H.agg(booked[g])
        W("")
        W("  %s  POOLED  n=%d  avgR=%+.3f  sumR=%+.1f  WR=%.1f%%" % (g, n, av, sr, wr))
        W("    %-8s %6s %10s %10s %20s %10s %s" %
          ("coin", "n", "avgR", "sumR", "null p5/p50/p95", "pctl", "flag"))
        for coin in COINS:
            sub = [t for t in booked[g] if t["coin"] == coin]
            cn, csr, cwr, cav = H2H.agg(sub)
            nl = null200(sub, opp_coin[g][coin], seed=4200 + ARMS.index(g) * 10 + COINS.index(coin))
            if nl:
                p5, p50, p95, pct, rsum = nl
                nstr = "%+.1f/%+.1f/%+.1f" % (p5, p50, p95)
                pstr = "%.0f%%%s" % (pct, " CLEARS-p95" if rsum > p95 else "")
            else:
                nstr, pstr = "n/a", "n/a"
            flag = "UNDERPOWERED" if 0 < cn < MIN_N else ("POS" if cav > 0 else "neg")
            W("    %-8s %6d %+10.3f %+10.1f %20s %10s %s" % (coin, cn, cav, csr, nstr, pstr, flag))
        # pooled null
        merged = defaultdict(list)
        for coin in COINS:
            for k, v in opp_coin[g][coin].items():
                merged[k].extend(v)
        nlp = null200(booked[g], merged, seed=9100 + ARMS.index(g))
        if nlp:
            p5, p50, p95, pct, rsum = nlp
            W("    -> POOLED null p5/p50/p95 = %+.1f/%+.1f/%+.1f  real sumR pctl=%.0f%%%s"
              % (p5, p50, p95, pct, "  CLEARS-p95" if rsum > p95 else ""))

    W("")
    W("  HONESTY: GROSS, no fees/slippage; in-sample Binance-perp proxy shares the live feed (a LEAD,")
    W("  not OOS). Per-coin n<%d = UNDERPOWERED. Nothing is ruled out here -- leads only." % MIN_N)
    hr()
    _flush(L)
    print("\n[report -> %s] %.0fs" % (OUT, time.time() - t0), flush=True)

def _flush(L):
    txt = "\n".join(L)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt + "\n")
    print(txt, flush=True)

if __name__ == "__main__":
    main()
