"""
STUDY E -- timeframe extension (pre-registered). Frozen STUDY B config + the
micro-aligned gate adopted in STUDY C (trade direction must match micro_regime
direction at entry). Run on 15m and 4h, Binance, all 4 coins. NO per-TF parameter
changes of any kind.

PRE-REGISTRATION: 1h is the anchor. 15m tests whether wide-stop fee math
(~0.08 R/trade drag) plus chop-removal (the micro gate) rescues the low TF.
4h tests the avgR-rises-with-TF precedent and is expected n-thin (~50/coin).

Per TF per coin + pooled: n, gross, net06, net04, avgR, own-bucket drift-null pctl
(200x, seeds pinned, on GROSS R), per-window net06, ungated-vs-gated side-by-side.
Flag n<30. Counts only, no verdicts.
"""
import sys, csv, sqlite3, random
from collections import defaultdict, Counter
import numpy as np
SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
HERE = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
for p in (SFP_DIR, HERE, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade"):
    if p not in sys.path:
        sys.path.insert(0, p)
import optitrade_bt as bt, run_study as R, study_b_widestop as B, _sfp_causal_macro60 as M

DB = M.DB; COINS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]; TFS = ["15m", "4h"]
DAY_MS = 86_400_000; MS15 = 900_000; NWIN = 5; W0 = B.WARMUP; MIN_N = 30

def load_ctx():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    m60 = {(s, d): r for s, d, r in con.execute("SELECT symbol,day_ts_ms,regime FROM macro_regime_60d")}
    mic = {(s, t): dr for s, t, dr in con.execute("SELECT symbol,ts_ms_15m,direction FROM micro_regime")}
    con.close(); return m60, mic

def trades(coin, tf, m60, mic):
    ts, o, h, l, c = R.load(coin, tf); N = len(c)
    atr = bt.atr_wilder(h, l, c, 14); sig = B.signals(o, h, l, c, N)
    tr = B.sim_single(o, h, l, c, atr, sig, B.SLMULT, B.RR, B.WARMUP, N, True)
    eidx, edir, g, epx, xpx, rpx = tr; win_len = (N - W0) // NWIN; out = []
    for k in range(len(eidx)):
        ei = int(eidx[k]); d = int(edir[k]); et = int(ts[ei]); gr = float(g[k])
        out.append(dict(coin=coin, side="long" if d > 0 else "short", gR=gr,
                        net06=gr - 0.0006*(epx[k]+xpx[k])/rpx[k],
                        net04=gr - 0.0004*(epx[k]+xpx[k])/rpx[k],
                        macro60=m60.get((coin, et - et % DAY_MS), "n/a"),
                        mdir=mic.get((coin, et - et % MS15), "n/a"),
                        window=min(NWIN - 1, (ei - W0)//win_len)))
    return out

def micro_ok(t):
    return (t["side"] == "long" and t["mdir"] == "trend_up") or \
           (t["side"] == "short" and t["mdir"] == "trend_down")

def agg(rows):
    n = len(rows); gr = sum(r["gR"] for r in rows); n6 = sum(r["net06"] for r in rows)
    n4 = sum(r["net04"] for r in rows)
    return n, gr, n6, n4, (gr / n if n else 0.0)

def null200(armed, pool, seed, iters=200):
    groups = Counter((t["side"], t["macro60"]) for t in armed); real = sum(t["gR"] for t in armed)
    if not armed or any(not pool.get(g) for g in groups): return None
    rng = random.Random(seed); sums = []
    for _ in range(iters):
        s = 0.0
        for g, cnt in groups.items():
            pl = pool[g]
            for _ in range(cnt): s += pl[rng.randrange(len(pl))]
        sums.append(s)
    sums.sort()
    return 100.0 * sum(1 for x in sums if x < real) / len(sums)

def main():
    m60, mic = load_ctx()
    O = []; W = O.append
    W("# STUDY E -- timeframe extension (frozen STUDY B + micro-aligned gate; counts only)\n")
    W("**PRE-REGISTRATION:** 1h is the anchor. 15m tests whether wide-stop fee math (~0.08 R/trade "
      "drag) + chop-removal (micro gate) rescues the low TF; 4h tests avgR-rises-with-TF and is "
      "expected n-thin (~50/coin). Gate = trade direction matches micro_regime direction at entry. "
      "GROSS primary; net06/net04 = 0.06%/0.04% per side. Own-bucket drift-null (side,macro60) on "
      "GROSS R, 200x, seeds pinned. NO per-TF parameter changes. Flag n<30.\n")

    csv_rows = []
    for ti, tf in enumerate(TFS):
        data = {coin: trades(coin, tf, m60, mic) for coin in COINS}
        allu = [t for coin in COINS for t in data[coin]]
        allg = [t for t in allu if micro_ok(t)]
        pool_coin = {coin: defaultdict(list) for coin in COINS}
        for coin in COINS:
            for t in data[coin]: pool_coin[coin][(t["side"], t["macro60"])].append(t["gR"])
        pool_all = defaultdict(list)
        for coin in COINS:
            for k, v in pool_coin[coin].items(): pool_all[k].extend(v)

        W(f"## {tf}\n")
        W("| coin | ung n | ung net06 | ung avgR | GATED n | gated gross | gated net06 | gated net04 | gated avgR | null pctl | flag |")
        W("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|")
        for ci, coin in enumerate(COINS + ["POOLED"]):
            if coin == "POOLED":
                u = allu; gt = allg; pool = pool_all; seed = 7000 + ti
            else:
                u = data[coin]; gt = [t for t in u if micro_ok(t)]; pool = pool_coin[coin]
                seed = 7100 + ti * 10 + ci
            un, ugr, un6, un4, uav = agg(u); gn, ggr, gn6, gn4, gav = agg(gt)
            pct = null200(gt, pool, seed)
            flag = "gated n<30" if 0 < gn < MIN_N else ""
            nm = coin.replace("USDT", "") if coin != "POOLED" else "POOLED"
            W(f"| {nm} | {un} | {un6:+.1f} | {uav:+.3f} | {gn} | {ggr:+.1f} | {gn6:+.1f} | {gn4:+.1f} | "
              f"{gav:+.3f} | {'n/a' if pct is None else f'{pct:.0f}%'} | {flag} |")
            csv_rows.append(dict(tf=tf, coin=nm, ung_n=un, ung_net06=round(un6,2),
                                 gated_n=gn, gated_net06=round(gn6,2), gated_net04=round(gn4,2),
                                 gated_avgR=round(gav,4), null_pctl=("" if pct is None else round(pct,1))))
        # per-window net06 ungated vs gated (pooled)
        W(f"\n### {tf} per-window net06 (pooled): ungated vs gated (w4 = most recent)\n")
        W("| stream | w0 | w1 | w2 | w3 | w4 | total |")
        W("|---|--:|--:|--:|--:|--:|--:|")
        for label, rows in [("ungated", allu), ("gated", allg)]:
            vals = [sum(t["net06"] for t in rows if t["window"] == w) for w in range(NWIN)]
            W(f"| {label} | " + " | ".join(f"{v:+.1f}" for v in vals) + f" | {sum(vals):+.1f} |")
        W("")

    with open("study_e_results.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(csv_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in csv_rows: w.writerow(r)
    W("_Counts only, no verdicts. 1h anchor is in STUDY_C.md (micro-aligned pooled net06 +79, pctl 58%)._")
    open("STUDY_E.md", "w", newline="\n").write("\n".join(O) + "\n")
    print("wrote STUDY_E.md + study_e_results.csv")
    for r in csv_rows:
        if r["coin"] == "POOLED":
            print(f"  {r['tf']:3s} POOLED ung_net06={r['ung_net06']:+7.1f} gated_net06={r['gated_net06']:+7.1f} "
                  f"gated_n={r['gated_n']} pctl={r['null_pctl']}")

if __name__ == "__main__":
    main()
