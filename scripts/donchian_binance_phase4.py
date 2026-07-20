"""Donchian re-validation — PHASE 4 (rolling walk-forward, OOS decision point).

Rolling WF: 12mo train / 6mo test / roll 6mo -> 6 folds (OOS 2023-07..2026-06).
Optimize Calmar on train (eligible: train-maxDD <= train-HODL-maxDD), trade fixed
on unseen test. All 4 benchmarks per fold. Fixed 20/168/6 tested separately.
Fees=0; 3 bps/side; next-bar-open fills. Read-only research.
"""
from __future__ import annotations

import statistics as st
import sys
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\AA Incorporado\cc")
sys.path.insert(0, str(REPO))
from scripts.donchian_binance_revalidation import load_binance_1h, derive_6h  # noqa: E402
from scripts.donchian_binance_phase3 import (  # noqa: E402
    fast_backtest, maxdd, hodl, weight_portfolio,
)

BPY = 365.25 * 24 / 6
ENTRY = [10, 15, 20, 28, 40, 55, 75, 100]
TREND = [None, 84, 168, 336, 504, 720]
EXIT = [3, 4, 6, 8, 12, 20]
COMBOS = [(e, x, t) for e in ENTRY for t in TREND for x in EXIT if x < e]


def add_months(dt, m):
    y = dt.year + (dt.month - 1 + m) // 12
    mo = (dt.month - 1 + m) % 12 + 1
    return dt.replace(year=y, month=mo)


def slice_metrics(eq, inmkt, ts, a, b, trades):
    sub = eq[a:b + 1]
    yrs = (ts[b] - ts[a]).total_seconds() / (365.25 * 86400)
    cagr = ((sub[-1] / sub[0]) ** (1 / yrs) - 1) * 100 if yrs > 0 and sub[0] > 0 else 0.0
    md = maxdd(sub)
    tim = 100 * st.fmean([1.0 if x else 0.0 for x in inmkt[a:b + 1]])
    rt = sum(1 for (en, ex, r) in trades if ts[a] <= ex <= ts[b])
    return {"total": (sub[-1] / sub[0] - 1) * 100, "cagr": cagr, "maxdd": md,
            "calmar": (cagr / md if md else 0.0), "tim": tim, "rt": rt}


def metrics_from_rets(rets, ts0, ts1, inmkt=None):
    v = 1.0; eq = []
    for r in rets:
        v *= (1 + r); eq.append(v)
    yrs = (ts1 - ts0).total_seconds() / (365.25 * 86400)
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    md = maxdd(eq)
    sd = st.pstdev(rets); shp = (st.fmean(rets) / sd * BPY ** 0.5) if sd > 0 else 0.0
    tim = 100 * st.fmean([1.0 if x else 0.0 for x in inmkt]) if inmkt else None
    return {"total": (eq[-1] - 1) * 100, "cagr": cagr, "maxdd": md,
            "calmar": (cagr / md if md else 0.0), "sharpe": shp, "tim": tim}


def main():
    rows_1h, _ = load_binance_1h()
    bars, _, _ = derive_6h(rows_1h)
    ts = [b["ts"] for b in bars]

    # precompute all config full-4Y runs + HODL
    print(f"precomputing {len(COMBOS)} full-4Y backtests...")
    runs = {c: fast_backtest(bars, c[0], c[1], c[2], 3.0) for c in COMBOS}
    hts, heq, hin, hret = hodl(bars)

    base_dt = datetime(2022, 7, 1, tzinfo=timezone.utc)
    folds = []
    for k in range(6):
        tr_s = add_months(base_dt, k * 6)
        te_s = add_months(tr_s, 12)
        te_e = add_months(te_s, 6)
        folds.append((tr_s, te_s, te_e))

    def rng(dt0, dt1):
        return bisect_left(ts, dt0), bisect_left(ts, dt1) - 1

    print("\n=== FOLDS (rolling 12mo train / 6mo test) ===")
    strat_rets_agg, strat_inmkt_agg = [], []
    fixed_rets_agg, fixed_inmkt_agg = [], []
    hodl_rets_agg, static_rets_agg, vt_rets_agg = [], [], []
    fold_rows, drift, ovf = [], [], []
    FIXED = (20, 6, 168)

    for k, (tr_s, te_s, te_e) in enumerate(folds):
        ta, tb = rng(tr_s, te_s)      # train window [tr_s, te_s)
        xa, xb = rng(te_s, te_e)      # test window  [te_s, te_e)
        # optimize Calmar on train (eligible: train maxDD <= train HODL maxDD)
        train_hodl_dd = maxdd(heq[ta:tb + 1])
        best = None
        for c in COMBOS:
            r = runs[c]
            m = slice_metrics(r["eq"], r["inmkt"], ts, ta, tb, r["trades"])
            if m["maxdd"] <= train_hodl_dd and (best is None or m["calmar"] > best[1]["calmar"]):
                best = (c, m)
        cfg, train_m = best
        run = runs[cfg]
        oos = slice_metrics(run["eq"], run["inmkt"], ts, xa, xb, run["trades"])
        # benchmarks on test window
        hood = slice_metrics(heq, hin, ts, xa, xb, [])
        w = oos["tim"] / 100.0
        sub = bars[xa:xb + 1]
        _, seq, swt, sret = weight_portfolio(sub, lambda i: w)
        sm = metrics_from_rets(sret[1:], ts[xa], ts[xb])
        strat_test_rets = run["rets"][xa + 1:xb + 1]
        sig_vol = st.pstdev(strat_test_rets) * BPY ** 0.5 if len(strat_test_rets) > 2 else 0.2

        def vtw(i):
            if i < 120:
                return 0.0
            win = [sret[j] for j in range(i - 120, i)]  # placeholder; replaced below
            return None
        # vol-target on sub using trailing BTC (hodl) vol targeting sig_vol
        hsub = hret[xa:xb + 1]

        def vtw2(i):
            if i < 120:
                return 0.0
            bv = st.pstdev(hsub[i - 120:i]) * BPY ** 0.5
            return max(0.0, min(1.0, sig_vol / bv)) if bv > 0 else 0.0
        _, veq, vwt, vret = weight_portfolio(sub, vtw2)
        vm = metrics_from_rets(vret[1:], ts[xa], ts[xb]); vm["tim"] = 100 * st.fmean(vwt)

        # fixed 20/168/6
        fr = runs[FIXED]
        fixed_oos = slice_metrics(fr["eq"], fr["inmkt"], ts, xa, xb, fr["trades"])

        fold_rows.append((k, tr_s, te_s, te_e, cfg, oos, hood, sm, vm, fixed_oos))
        drift.append((k, cfg))
        ovf.append((k, cfg, train_m, oos))
        strat_rets_agg += list(run["rets"][xa + 1:xb + 1]); strat_inmkt_agg += list(run["inmkt"][xa + 1:xb + 1])
        fixed_rets_agg += list(fr["rets"][xa + 1:xb + 1]); fixed_inmkt_agg += list(fr["inmkt"][xa + 1:xb + 1])
        hodl_rets_agg += list(hret[xa + 1:xb + 1])
        static_rets_agg += list(sret[1:]); vt_rets_agg += list(vret[1:])

    # ---- per-fold table ----
    print(f"\n{'F':<2}{'test window':<20}{'chosen':<14}{'sRet%':>7}{'sMDD%':>6}{'sCal':>6}{'sTIM':>6}"
          f"{'hRet%':>7}{'statRet%':>9}{'vtRet%':>7}{'fixRet%':>8}")
    for (k, tr_s, te_s, te_e, cfg, oos, hood, sm, vm, fx) in fold_rows:
        win = f"{te_s.strftime('%Y-%m')}..{te_e.strftime('%Y-%m')}"
        print(f"{k:<2}{win:<20}{str(cfg):<14}{oos['total']:+7.1f}{oos['maxdd']:6.1f}{oos['calmar']:6.2f}"
              f"{oos['tim']:6.1f}{hood['total']:+7.1f}{sm['total']:+9.1f}{vm['total']:+7.1f}{fx['total']:+8.1f}")

    # ---- parameter drift ----
    print("\n=== H. PARAMETER DRIFT (chosen per fold) ===")
    es = [c[0] for _, c in drift]; xs = [c[1] for _, c in drift]; tt = [c[2] for _, c in drift]
    for k, c in drift:
        print(f"  fold {k}: entry={c[0]:<3} exit={c[1]:<2} trend={c[2]}")
    print(f"  entry range {min(es)}..{max(es)} | exit range {min(xs)}..{max(xs)} | "
          f"trend set {sorted(set(str(x) for x in tt))}")

    # ---- aggregate OOS ----
    t0, t1 = folds[0][1], folds[-1][2]
    agg = metrics_from_rets(strat_rets_agg, t0, ts[-1], strat_inmkt_agg)
    aggf = metrics_from_rets(fixed_rets_agg, t0, ts[-1], fixed_inmkt_agg)
    aggh = metrics_from_rets(hodl_rets_agg, t0, ts[-1])
    aggs = metrics_from_rets(static_rets_agg, t0, ts[-1])
    aggv = metrics_from_rets(vt_rets_agg, t0, ts[-1])
    print("\n=== AGGREGATE OOS (stitched 2023-07..2026-06, ~3y) ===")
    print(f"{'Portfolio':<24}{'Total%':>9}{'CAGR%':>7}{'maxDD%':>7}{'Calmar':>7}{'Sharpe':>7}{'TIM%':>6}")
    def pr(n, m):
        print(f"{n:<24}{m['total']:+9.1f}{m['cagr']:+7.1f}{m['maxdd']:7.1f}{m['calmar']:7.2f}{m['sharpe']:7.2f}{(m['tim'] or 100):6.1f}")
    pr("WF-optimized", agg); pr("Fixed 20/168/6 (I)", aggf)
    pr("Static matched-expo", aggs); pr("Vol-target", aggv); pr("HODL", aggh)

    # ---- overfit tax ----
    print("\n=== OVERFIT TAX (per-fold train[in-sample] vs test[OOS] Calmar/return of chosen cfg) ===")
    print(f"{'F':<2}{'cfg':<14}{'trainCal':>9}{'testCal':>8}{'trainRet%':>10}{'testRet%':>9}")
    tr_cal, te_cal = [], []
    for (k, cfg, tm, om) in ovf:
        print(f"{k:<2}{str(cfg):<14}{tm['calmar']:>9.2f}{om['calmar']:>8.2f}{tm['total']:>+10.1f}{om['total']:>+9.1f}")
        tr_cal.append(tm["calmar"]); te_cal.append(om["calmar"])
    print(f"  mean train Calmar={st.fmean(tr_cal):.2f}  mean test Calmar={st.fmean(te_cal):.2f}  "
          f"=> OOS/IS ratio={st.fmean(te_cal)/st.fmean(tr_cal):.2f}")
    print("  (Phase-3 full-sample in-sample optimum was Calmar 2.72 @ 20/3/336; "
          f"WF-OOS aggregate Calmar={agg['calmar']:.2f}, HODL OOS Calmar={aggh['calmar']:.2f})")


if __name__ == "__main__":
    main()
