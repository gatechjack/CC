"""
PART 3 -- GO-LIVE MATH REBUILD (DRAFT). Current best causal stream per STUDY C:
1h, micro-aligned gate (trade direction matches micro_regime direction at entry),
frozen STUDY B config. Binance-perp, all 4 coins incl ETH.

Per coin: n, net06, net04, avgR, trades/mo, expected net06-R/mo, expected R at n=30,
rolling 30-trade net06-R distribution (kill/keep), 3*ATR stop distance as % of price
(p50/p95) + implied max-safe leverage at a 2x liquidation buffer. Whether ETH
re-enters positive when gated is called out. GROSS primary; net06/net04 shown.

DRAFT -- subject to revision if the adopted stream changes. In-sample Binance-perp
proxy (shares the live feed -> a LEAD, not OOS). Counts only, no verdicts.
"""
import sys, csv, sqlite3
import numpy as np
SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
HERE = r"C:\Users\AA Incorporado\Desktop\backtest_corpus"
for p in (SFP_DIR, HERE, r"C:\Users\AA Incorporado\cc-2026-07-31b-wt\research\optitrade"):
    if p not in sys.path:
        sys.path.insert(0, p)
import optitrade_bt as bt, run_study as R, study_b_widestop as B, _sfp_causal_macro60 as M

DB = M.DB; COINS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
DAY_MS = 86_400_000; MS15 = 900_000; MO_MS = 30.4375 * DAY_MS; W0 = B.WARMUP

def armed_trades(coin, mic):
    ts, o, h, l, c = R.load(coin, "1h"); N = len(c)
    atr = bt.atr_wilder(h, l, c, 14); sig = B.signals(o, h, l, c, N)
    tr = B.sim_single(o, h, l, c, atr, sig, B.SLMULT, B.RR, B.WARMUP, N, True)
    eidx, edir, g, epx, xpx, rpx = tr
    span_mo = (int(ts[N-1]) - int(ts[W0])) / MO_MS
    out = []
    for k in range(len(eidx)):
        ei = int(eidx[k]); d = int(edir[k]); et = int(ts[ei])
        mdir = mic.get((coin, et - et % MS15), "n/a")
        aligned = (d > 0 and mdir == "trend_up") or (d < 0 and mdir == "trend_down")
        if not aligned:
            continue
        gr = float(g[k])
        out.append(dict(et=et, gR=gr, net06=gr - 0.0006*(epx[k]+xpx[k])/rpx[k],
                        net04=gr - 0.0004*(epx[k]+xpx[k])/rpx[k],
                        stop_pct=100.0 * rpx[k] / epx[k]))
    out.sort(key=lambda r: r["et"])
    return out, span_mo

def rolling30(vals):
    if len(vals) < 30:
        return None
    csum = np.cumsum([v["net06"] for v in vals])
    windows = csum[29:] - np.concatenate([[0.0], csum[:-30]])
    q = lambda p: float(np.percentile(windows, p))
    return dict(nwin=len(windows), p5=q(5), p50=q(50), p95=q(95),
                mn=float(windows.min()), mx=float(windows.max()),
                pneg=100.0 * float((windows < 0).mean()))

def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    mic = {(s, t): dr for s, t, dr in con.execute("SELECT symbol,ts_ms_15m,direction FROM micro_regime")}
    con.close()

    per = {}; pooled = []
    for coin in COINS:
        tr, span = armed_trades(coin, mic); per[coin] = (tr, span); pooled += tr
    span_all = max(s for _, s in per.values())

    O = []; W = O.append
    W("# GO-LIVE MATH (DRAFT) -- 1h micro-aligned wide-stop trend-cross\n")
    W("**DRAFT -- subject to revision if the adopted stream changes.** Adopted stream = 1h + "
      "micro-aligned gate (STUDY C best causal). Frozen STUDY B config. In-sample Binance-perp "
      "proxy (shares the live feed -> a LEAD, not OOS). GROSS primary; net06/net04 = 0.06%/0.04% "
      "per side. Counts only, no verdicts.\n")

    W("## R economics (per coin + pooled)\n")
    W("| coin | armed n | net06 | net04 | avgR gross | trades/mo | net06 R/mo | E[net06 R]@n=30 |")
    W("|---|--:|--:|--:|--:|--:|--:|--:|")
    csv_rows = []
    for coin in COINS + ["POOLED"]:
        if coin == "POOLED":
            tr = pooled; span = span_all
        else:
            tr, span = per[coin]
        n = len(tr); n6 = sum(t["net06"] for t in tr); n4 = sum(t["net04"] for t in tr)
        gr = sum(t["gR"] for t in tr); av = gr / n if n else 0.0
        tpm = n / span if span else 0.0; r_mo = n6 / span if span else 0.0
        e30 = (n6 / n) * 30 if n else 0.0
        nm = coin.replace("USDT", "") if coin != "POOLED" else "POOLED"
        W(f"| {nm} | {n} | {n6:+.1f} | {n4:+.1f} | {av:+.3f} | {tpm:.1f} | {r_mo:+.2f} | {e30:+.2f} |")
        csv_rows.append(dict(coin=nm, n=n, net06=round(n6,2), net04=round(n4,2),
                             avgR=round(av,4), trades_per_mo=round(tpm,2),
                             net06_R_per_mo=round(r_mo,3), E_net06R_at30=round(e30,3)))
    W(f"\n_Corpus span ~{span_all:.1f} months. **ETH gated net06 = "
      f"{sum(t['net06'] for t in per['ETHUSDT'][0]):+.1f}** (ungated was -16.8 in STUDY B -> ETH "
      f"re-enters marginally positive when gated).\n")

    W("## Rolling 30-trade net06-R distribution (kill/keep envelope)\n")
    W("| coin | #windows | p5 | p50 | p95 | min | max | % windows <0 |")
    W("|---|--:|--:|--:|--:|--:|--:|--:|")
    for coin in COINS + ["POOLED"]:
        tr = pooled if coin == "POOLED" else per[coin][0]
        r = rolling30(tr); nm = coin.replace("USDT", "") if coin != "POOLED" else "POOLED"
        if r is None:
            W(f"| {nm} | n<30 | - | - | - | - | - | - |")
        else:
            W(f"| {nm} | {r['nwin']} | {r['p5']:+.1f} | {r['p50']:+.1f} | {r['p95']:+.1f} | "
              f"{r['mn']:+.1f} | {r['mx']:+.1f} | {r['pneg']:.0f}% |")

    W("\n## Stop distance & implied leverage (per coin; 3*ATR stop as % of price)\n")
    W("| coin | stop% p50 | stop% p95 | max-safe lev @stop-p50 | max-safe lev @stop-p95 |")
    W("|---|--:|--:|--:|--:|")
    for coin in COINS:
        tr = per[coin][0]
        sp = np.array([t["stop_pct"] for t in tr])
        p50 = float(np.percentile(sp, 50)); p95 = float(np.percentile(sp, 95))
        # 2x liq buffer: liquidation at 2x the stop distance -> 1/lev = 2*(stop/100) -> lev = 50/stop%
        lev50 = 50.0 / p50; lev95 = 50.0 / p95
        W(f"| {coin.replace('USDT','')} | {p50:.2f}% | {p95:.2f}% | {lev50:.1f}x | {lev95:.1f}x |")
    W("\n_max-safe leverage = the leverage at which liquidation sits 2x the stop distance away "
      "(lev = 50 / stop%%); @p95 uses the widest 5%% of stops (conservative bound). Isolated-margin "
      "first-order approximation (ignores funding/fees/maintenance-margin curve).\n")

    with open("go_live_math_draft.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(csv_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in csv_rows: w.writerow(r)
    open("GO_LIVE_MATH_DRAFT.md", "w", newline="\n").write("\n".join(O) + "\n")
    print("wrote GO_LIVE_MATH_DRAFT.md + go_live_math_draft.csv")
    for coin in COINS + ["POOLED"]:
        tr = pooled if coin == "POOLED" else per[coin][0]
        n6 = sum(t["net06"] for t in tr)
        print(f"  {coin.replace('USDT',''):6s} n={len(tr):4d} net06={n6:+7.1f}")

if __name__ == "__main__":
    main()
