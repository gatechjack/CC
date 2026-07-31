"""
STUDY D -- RD_range anomaly (pre-registered). Frozen STUDY B config, Binance 1h, 4 coins.

HYPOTHESIS (stated before compute): trend-cross entries during RD_range capture range
BREAKOUTS -- R concentrates in trades where the RD break-state (os) flips WITH the trade
direction during the trade's lifetime, and evaporates where the range holds or os flips
against. Split the RD_range-entry trades (os==0 at entry) by the FIRST os change over
[entry, exit]: (a) flips WITH direction, (b) flips AGAINST, (c) range holds (os stays 0).

Bucket x coin: n, net06, avgR; per-window counts; per-coin consistency of bucket (a).
Total RD_range-entry n ~73 -> ALL cells are thin; n<30 flagged, and the thinness is the
reportable result. Counts only, no verdicts, seeds pinned (none needed here).
"""
import sys, sqlite3
import numpy as np
SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
HERE = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
for p in (SFP_DIR, HERE, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade"):
    if p not in sys.path:
        sys.path.insert(0, p)
import optitrade_bt as bt, _sfp_degree_rerun as DR, _sfp_trend_gate_bakeoff as BK, study_b_widestop as B
import _sfp_causal_macro60 as M

DB = M.DB; COINS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]; NWIN = 5; W0 = B.WARMUP; MIN_N = 30

def trades_with_exit(o, h, l, c, atr, sig, N):
    out = []; i = W0
    while i < N:
        d = sig[i]
        if d == 0: i += 1; continue
        a = atr[i]
        if not (a > 0): i += 1; continue
        entry = c[i]; Rd = B.SLMULT * a; SL = entry - d*Rd; TP = entry + d*B.RR*Rd
        j = i + 1; gr = None; xp = None; xidx = N - 1
        while j < N:
            hi, lo = h[j], l[j]
            stop = (lo <= SL) if d > 0 else (hi >= SL); tp = (hi >= TP) if d > 0 else (lo <= TP)
            if stop: gr = -1.0; xp = SL; xidx = j; break
            if tp: gr = B.RR; xp = TP; xidx = j; break
            j += 1
        if gr is None: gr = ((c[N-1]-entry)/Rd)*d; xp = c[N-1]; xidx = N-1
        out.append((i, xidx, d, gr, entry, xp, Rd)); i = xidx + 1
    return out

def agg(rows):
    n = len(rows); gr = sum(r["gR"] for r in rows); n6 = sum(r["net06"] for r in rows)
    return n, gr, n6, (gr / n if n else 0.0)

def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    allrows = []
    for coin in COINS:
        b1h = DR.load_bars(con, coin, "1h")
        ts = np.array([b.ts_ms for b in b1h], np.int64)
        o = np.array([b.open for b in b1h]); h = np.array([b.high for b in b1h])
        l = np.array([b.low for b in b1h]); c = np.array([b.close for b in b1h])
        N = len(c); atr = bt.atr_wilder(h, l, c, 14); sig = B.signals(o, h, l, c, N)
        rd = BK.rd_os_lookup_builder(b1h)
        win_len = (N - W0) // NWIN
        for (ei, xi, d, gr, epx, xpx, rpx) in trades_with_exit(o, h, l, c, atr, sig, N):
            if rd(int(ts[ei])) != 0:
                continue                                   # RD_range entries only
            bucket = "c_holds"
            for j in range(ei + 1, xi + 1):
                os = rd(int(ts[j]))
                if os != 0:
                    aligned = (d > 0 and os == 1) or (d < 0 and os == -1)
                    bucket = "a_with" if aligned else "b_against"
                    break
            allrows.append(dict(coin=coin.replace("USDT",""), side="long" if d > 0 else "short",
                                bucket=bucket, gR=gr, net06=gr - 0.0006*(epx+xpx)/rpx,
                                window=min(NWIN-1, (ei - W0)//win_len)))
    con.close()

    BUCKETS = ["a_with", "b_against", "c_holds"]
    O = []; W = O.append
    W("# STUDY D -- RD_range breakout anomaly (frozen STUDY B, 1h; counts only)\n")
    W("**PRE-REGISTERED HYPOTHESIS:** among trend-cross trades ENTERED during RD_range (os==0), R "
      "concentrates where the RD break-state flips WITH the trade direction during the trade's "
      "lifetime (a), and evaporates where it flips AGAINST (b) or the range HOLDS to exit (c). "
      "Bucket = the FIRST os change over [entry, exit]. GROSS primary (net06 shown). "
      "Total RD_range-entry n is small (~73) -> every cell is thin; n<30 flagged throughout.\n")
    tot_n = len(allrows)
    W(f"Total RD_range-entry trades: **{tot_n}** (this is the whole population; treat all splits as thin).\n")
    W("**STRUCTURAL NOTE (not a verdict):** bucket (a) is defined by a POST-ENTRY event -- the RD "
      "os flipping WITH the trade direction means price broke the range in the trade's favour, which "
      "is the same move that makes the trade win. So the a/b/c split conditions on the trade's own "
      "outcome and is DESCRIPTIVE, not a predictive/tradeable signal (a near-tautology: 'trades that "
      "went my way went my way'). Reported for shape only. n<30 on every per-coin cell.\n")

    W("## Bucket x coin: n, net06, avgR  (flag n<30)\n")
    W("| bucket | coin | n | net06 | avgR | flag |")
    W("|---|---|--:|--:|--:|---|")
    for b in BUCKETS:
        for coin in [c.replace("USDT","") for c in COINS] + ["POOLED"]:
            rows = [r for r in allrows if r["bucket"] == b and (coin == "POOLED" or r["coin"] == coin)]
            n, gr, n6, av = agg(rows)
            W(f"| {b} | {coin} | {n} | {n6:+.1f} | {av:+.3f} | {'n<30' if 0 < n < MIN_N else ('EMPTY' if n==0 else '')} |")
        W("| | | | | | |")

    W("\n## Per-window counts (n) by bucket (pooled)\n")
    W("| bucket | w0 | w1 | w2 | w3 | w4 | total |")
    W("|---|--:|--:|--:|--:|--:|--:|")
    for b in BUCKETS:
        cnt = [sum(1 for r in allrows if r["bucket"] == b and r["window"] == w) for w in range(NWIN)]
        W(f"| {b} | " + " | ".join(str(x) for x in cnt) + f" | {sum(cnt)} |")

    W("\n## Per-coin consistency of bucket (a) = os-flips-WITH\n")
    W("| coin | a_with n | a_with net06 | a_with avgR | flag |")
    W("|---|--:|--:|--:|---|")
    for coin in [c.replace("USDT","") for c in COINS]:
        rows = [r for r in allrows if r["bucket"] == "a_with" and r["coin"] == coin]
        n, gr, n6, av = agg(rows)
        W(f"| {coin} | {n} | {n6:+.1f} | {av:+.3f} | {'n<30' if 0 < n < MIN_N else ('EMPTY' if n==0 else '')} |")

    W("\n_Counts only. Every cell here is below n=30; the split is reported for shape, not significance._")
    open("STUDY_D.md", "w", newline="\n").write("\n".join(O) + "\n")
    # console summary
    for b in BUCKETS:
        rows = [r for r in allrows if r["bucket"] == b]
        n, gr, n6, av = agg(rows)
        print(f"  {b}: n={n} net06={n6:+.1f} avgR={av:+.3f}")
    print("wrote STUDY_D.md")

if __name__ == "__main__":
    main()
