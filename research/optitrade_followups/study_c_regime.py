"""
STUDY C -- regime attribution + gating for the frozen STUDY B wide-stop trend-cross.
Binance 1h, all 4 coins (ETH included). NO parameter changes; existing classifiers only.

PRE-REGISTERED HYPOTHESIS (stated before compute):
  net R concentrates in TREND regimes (RD non-range at entry; micro_regime
  trend_up/trend_down; macro60 bull/bear) and is flat-to-negative in range/neutral
  buckets. Long R concentrates in up-regimes, short R in down-regimes.

Part 1 ATTRIBUTION: join each STUDY B trade to entry-bar regime tags (RD state,
  micro direction+vol_state, macro60). Per bucket: n, gross, net06, avgR. No gating.
Part 2 GATED RE-RUN (ablation, one gate at a time, NO combinations): (a) RD non-range,
  (b) micro trend-aligned, (c) macro60-aligned, (d) ps_trail30-aligned. Per gate/coin+
  pooled: armed n, armed avgR/sumR/net06, blocked avgR, own-bucket drift-null pctl
  (200x, seeds pinned; on GROSS R per convention).
Part 3 HONESTY PANEL: per-window (5) net06 of the best-armed gate vs ungated; flag n<30.

Causality note: RD (rd_os at entry) and ps_trail30 (D-1 stale) are causal; micro (entry
15m bucket) and macro60 (entry-DAY final) match the SFP-harness convention and are
marginally forward at the entry bar. GROSS primary; in-sample Binance-perp -> a LEAD.
Counts only, no verdicts, no gate combinations, no threshold tuning.
"""
import sys, csv, sqlite3, random
from collections import defaultdict, Counter
import numpy as np
SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
HERE = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
for p in (SFP_DIR, HERE, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade"):
    if p not in sys.path:
        sys.path.insert(0, p)
import optitrade_bt as bt
import _sfp_causal_macro60 as M
import _sfp_degree_rerun as DR
import _sfp_trend_gate_bakeoff as BK
import study_b_widestop as B

DB = M.DB; COINS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
DAY_MS = 86_400_000; MS15 = 900_000; NWIN = 5; MIN_N = 30

def load_trades():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    m60 = {(s, d): r for s, d, r in con.execute("SELECT symbol,day_ts_ms,regime FROM macro_regime_60d")}
    mic = {(s, t): (dr, vs) for s, t, dr, vs in
           con.execute("SELECT symbol,ts_ms_15m,direction,vol_state FROM micro_regime")}
    trades = {}
    for coin in COINS:
        b1h = DR.load_bars(con, coin, "1h"); b1d = DR.load_bars(con, coin, "1d")
        ts = np.array([b.ts_ms for b in b1h], np.int64)
        o = np.array([b.open for b in b1h]); h = np.array([b.high for b in b1h])
        l = np.array([b.low for b in b1h]); c = np.array([b.close for b in b1h])
        N = len(c); atr = bt.atr_wilder(h, l, c, 14)
        sig = B.signals(o, h, l, c, N)
        tr = B.sim_single(o, h, l, c, atr, sig, B.SLMULT, B.RR, B.WARMUP, N, True)
        rd_lookup = BK.rd_os_lookup_builder(b1h)
        gate_map = M.build_gate_map([b.ts_ms for b in b1d], M.st_ps_trail([b.close for b in b1d], 30))
        win_len = (N - B.WARMUP) // NWIN
        tl = []
        eidx, edir, g, epx, xpx, rpx = tr
        for k in range(len(eidx)):
            ei = int(eidx[k]); d = int(edir[k]); et = int(ts[ei])
            gr = float(g[k]); n6 = gr - 0.0006 * (epx[k] + xpx[k]) / rpx[k]
            n4 = gr - 0.0004 * (epx[k] + xpx[k]) / rpx[k]
            mdir, mvol = mic.get((coin, et - et % MS15), ("n/a", "n/a"))
            w = min(NWIN - 1, (ei - B.WARMUP) // win_len)
            tl.append(dict(coin=coin, side="long" if d > 0 else "short",
                           gR=gr, net06=float(n6), net04=float(n4),
                           rd=rd_lookup(et), macro60=m60.get((coin, et - et % DAY_MS), "n/a"),
                           mdir=mdir, mvol=mvol,
                           pst=gate_map.get(et - et % DAY_MS), window=w))
        trades[coin] = tl
    con.close()
    return trades

def agg(ts):
    n = len(ts); gr = sum(t["gR"] for t in ts); n6 = sum(t["net06"] for t in ts)
    return n, gr, n6, (gr / n if n else 0.0)

def null200(armed, pool_by_stratum, seed, iters=200):
    groups = Counter((t["side"], t["macro60"]) for t in armed)
    real = sum(t["gR"] for t in armed)
    if not armed or any(not pool_by_stratum.get(g) for g in groups):
        return None
    rng = random.Random(seed); sums = []
    for _ in range(iters):
        s = 0.0
        for g, cnt in groups.items():
            pool = pool_by_stratum[g]
            for _ in range(cnt):
                s += pool[rng.randrange(len(pool))]
        sums.append(s)
    sums.sort()
    q = lambda p: sums[min(len(sums) - 1, int(round(p * (len(sums) - 1))))]
    pct = 100.0 * sum(1 for x in sums if x < real) / len(sums)
    return q(0.05), q(0.50), q(0.95), pct, real

# gate predicates
def g_rd_nonrange(t): return t["rd"] != 0
def g_micro(t): return (t["side"] == "long" and t["mdir"] == "trend_up") or \
                       (t["side"] == "short" and t["mdir"] == "trend_down")
def g_macro(t): return (t["side"] == "long" and t["macro60"] == "bull") or \
                       (t["side"] == "short" and t["macro60"] == "bear")
def g_pst(t): return (t["side"] == "long" and t["pst"] == "bull") or \
                     (t["side"] == "short" and t["pst"] == "bear")
GATES = [("a_RD_nonrange", g_rd_nonrange), ("b_micro_aligned", g_micro),
         ("c_macro60_aligned", g_macro), ("d_ps_trail30_aligned", g_pst)]

def main():
    trades = load_trades()
    allt = [t for coin in COINS for t in trades[coin]]
    O = []; W = O.append
    W("# STUDY C -- regime attribution + gating (frozen STUDY B config, counts only)\n")
    W("**PRE-REGISTERED HYPOTHESIS:** net R concentrates in TREND regimes (RD non-range; micro "
      "trend_up/down; macro60 bull/bear), flat-to-negative in range/neutral; long R in up-regimes, "
      "short R in down-regimes. Binance 1h, 4 coins (ETH incl). GROSS primary (net06 shown); "
      "existing classifiers only; no parameter/threshold changes. RD & ps_trail30 causal; micro & "
      "macro60 use the SFP-harness entry-bucket/day convention (marginally forward at entry).\n")

    # -------- Part 1: attribution (pooled tables + per-coin in CSV) --------
    W("## Part 1 -- ATTRIBUTION (pooled; per-coin in study_c_attribution.csv)\n")
    csv_rows = []
    def attr_table(title, keyfn, order):
        W(f"### {title}\n")
        W("| bucket | n | gross | net06 | avgR |")
        W("|---|--:|--:|--:|--:|")
        for b in order:
            sub = [t for t in allt if keyfn(t) == b]
            n, gr, n6, av = agg(sub)
            W(f"| {b} | {n} | {gr:+.1f} | {n6:+.1f} | {av:+.3f} |")
        for coin in COINS:
            for b in order:
                sub = [t for t in trades[coin] if keyfn(t) == b]
                n, gr, n6, av = agg(sub)
                csv_rows.append(dict(classifier=title, coin=coin.replace("USDT",""), bucket=b,
                                     n=n, gross=round(gr,2), net06=round(n6,2), avgR=round(av,4)))
        W("")
    attr_table("RD state", lambda t: {1:"RD_up",0:"RD_range",-1:"RD_down"}.get(t["rd"],"n/a"),
               ["RD_up","RD_range","RD_down"])
    attr_table("micro_regime direction", lambda t: t["mdir"],
               ["trend_up","trend_down","range","ambiguous","warmup"])
    attr_table("micro_regime vol_state", lambda t: t["mvol"], ["low","normal","high","warmup"])
    attr_table("macro60 regime", lambda t: t["macro60"], ["bull","bear","neutral"])

    # directional cross-tab (hypothesis part 2)
    W("### Directional cross-tab (side x regime-direction) -- pooled net06 / avgR / n\n")
    W("| classifier | long in UP | long in DOWN | short in UP | short in DOWN |")
    W("|---|---|---|---|---|")
    def cross(name, upval, downval, keyfn):
        def cell(side, val):
            s = [t for t in allt if t["side"] == side and keyfn(t) == val]
            n, gr, n6, av = agg(s); return f"{n6:+.1f}/{av:+.2f}/n{n}"
        W(f"| {name} | {cell('long',upval)} | {cell('long',downval)} | "
          f"{cell('short',upval)} | {cell('short',downval)} |")
    cross("RD", 1, -1, lambda t: t["rd"])
    cross("micro", "trend_up", "trend_down", lambda t: t["mdir"])
    cross("macro60", "bull", "bear", lambda t: t["macro60"])
    W("")

    # -------- Part 2: gated re-run (ablation) --------
    W("## Part 2 -- GATED RE-RUN (ablation, one gate at a time; null on GROSS R, 200x, pinned)\n")
    # ungated own-bucket pools
    pool_coin = {coin: defaultdict(list) for coin in COINS}
    for coin in COINS:
        for t in trades[coin]:
            pool_coin[coin][(t["side"], t["macro60"])].append(t["gR"])
    pool_all = defaultdict(list)
    for coin in COINS:
        for k, v in pool_coin[coin].items():
            pool_all[k].extend(v)
    W("_Own-bucket null is stratified by (side, macro60); therefore the macro60 gate (c) is "
      "DEGENERATE against this null (it resamples its own strata -> pctl ~50 regardless), and its "
      "armed avgR (+0.70..+1.00) reflects the NON-CAUSAL entry-day macro60 label (peeks at the day's "
      "own direction). Treat gate (c) as a contaminated reference, not a deployable gate. Gates (a) RD "
      "and (d) ps_trail30 are causal; (b) micro uses the entry 15m bucket._\n")
    W("| gate | coin | armed n | armed avgR | armed sumR | armed net06 | blocked avgR | null p5/p50/p95 | pctl | flag |")
    W("|---|---|--:|--:|--:|--:|--:|---|--:|---|")
    gate_pooled_net06 = {}; gate_pooled_pctl = {}
    for gi, (gname, gp) in enumerate(GATES):
        for coin in COINS + ["POOLED"]:
            src = allt if coin == "POOLED" else trades[coin]
            armed = [t for t in src if gp(t)]; blocked = [t for t in src if not gp(t)]
            an, agr, an6, aav = agg(armed); bn, bgr, bn6, bav = agg(blocked)
            pool = pool_all if coin == "POOLED" else pool_coin[coin]
            seed = (6000 + gi) if coin == "POOLED" else (5000 + gi * 10 + COINS.index(coin))
            nl = null200(armed, pool, seed)
            if nl:
                p5, p50, p95, pct, real = nl
                nstr = "%+.1f/%+.1f/%+.1f" % (p5, p50, p95)
                pstr = "%.0f%%%s" % (pct, " CLEARS-p95" if real > p95 else "")
            else:
                nstr, pstr = "n/a", "n/a"
            flag = "n<30" if 0 < an < MIN_N else ""
            if coin == "POOLED":
                gate_pooled_net06[gname] = an6
                gate_pooled_pctl[gname] = nl[3] if nl else 0.0
            W(f"| {gname} | {coin.replace('USDT','') if coin!='POOLED' else 'POOLED'} | {an} | "
              f"{aav:+.3f} | {agr:+.1f} | {an6:+.1f} | {bav:+.3f} | {nstr} | {pstr} | {flag} |")
        W("")

    # -------- Part 3: honesty panel --------
    # 'best' = highest pooled null percentile (significance-aware). This auto-excludes the
    # contaminated macro60 gate (degenerate vs its own stratified null); raw-net06 would pick it.
    best = max(gate_pooled_pctl, key=gate_pooled_pctl.get)
    bgp = dict(GATES)[best]
    W("## Part 3 -- HONESTY PANEL: best-armed gate (by pooled null percentile) vs ungated, per-window\n")
    W(f"'Best' = highest pooled drift-null percentile (significance-aware, not raw net06 -- raw net06 "
      f"would pick the contaminated macro60 gate). Best = **{best}** (pooled null pctl "
      f"{gate_pooled_pctl[best]:.0f}%, pooled armed net06 {gate_pooled_net06[best]:+.1f}). For "
      f"reference the pooled net06/pctl by gate: "
      + ", ".join(f"{g}={gate_pooled_net06[g]:+.0f}/{gate_pooled_pctl[g]:.0f}%" for g, _ in GATES) + ".\n")
    W("| window | ungated n | ungated net06 | armed n | armed net06 | flag |")
    W("|--:|--:|--:|--:|--:|---|")
    for w in range(NWIN):
        ung = [t for t in allt if t["window"] == w]
        arm = [t for t in ung if bgp(t)]
        un_n, _, un6, _ = agg(ung); ar_n, _, ar6, _ = agg(arm)
        W(f"| {w} | {un_n} | {un6:+.1f} | {ar_n} | {ar6:+.1f} | {'armed n<30' if 0 < ar_n < MIN_N else ''} |")
    W("\n_w4 = most recent window. Counts only; no verdicts.\n")

    with open("study_c_attribution.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(csv_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in csv_rows: w.writerow(r)
    open("STUDY_C.md", "w", newline="\n").write("\n".join(O) + "\n")
    print("wrote STUDY_C.md + study_c_attribution.csv")
    print("gate pooled net06:", {k: round(v,1) for k,v in gate_pooled_net06.items()}, "best=", best)

if __name__ == "__main__":
    main()
