"""Donchian re-validation — PHASE 7 (timeframe structural test: 6h/12h/1d/3d/1w).

Bar interval is a STRUCTURAL choice; test whether a slower TF beats 6h on the
survivorship-free family median + clean holdout. Same discipline as Phase 3-4.5.
Grids scaled to equal CALENDAR windows (not the reused 6h integer grid).
Fees=0; 3 bps/side (daily slippage sensitivity {0,2,5,10} reported). Read-only.
"""
from __future__ import annotations

import math
import statistics as st
import sys
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\AA Incorporado\cc")
sys.path.insert(0, str(REPO))
from scripts.donchian_binance_revalidation import load_binance_1h  # noqa: E402
from scripts.donchian_binance_phase3 import fast_backtest, maxdd, hodl, weight_portfolio  # noqa: E402
from scripts.donchian_binance_phase4 import slice_metrics, add_months  # noqa: E402

# calendar targets (days) — these reproduce the 6h grid exactly at bpd=4
ENTRY_DAYS = [2.5, 3.75, 5, 7, 10, 13.75, 18.75, 25]
TREND_DAYS = [21, 42, 84, 126, 180]
EXIT_DAYS = [0.75, 1, 1.5, 2, 3, 5]
TFS = [("6h", 6), ("12h", 12), ("1d", 24), ("3d", 72), ("1w", 168)]


def derive_tf(rows_1h, hours):
    bms = hours * 3600 * 1000
    b, order = {}, []
    for ot, o, h, l, c, v in rows_1h:
        k = (ot // bms) * bms
        if k not in b:
            b[k] = {"open": o, "high": h, "low": l, "close": c, "vol": v, "n": 1}; order.append(k)
        else:
            d = b[k]; d["high"] = max(d["high"], h); d["low"] = min(d["low"], l)
            d["close"] = c; d["vol"] += v; d["n"] += 1
    bars = []
    for k in order:
        d = b[k]
        if d["n"] != hours:
            continue
        bars.append({"ts": datetime.fromtimestamp(k / 1000, tz=timezone.utc),
                     "open": d["open"], "high": d["high"], "low": d["low"],
                     "close": d["close"], "volume": d["vol"]})
    return bars


def grid_for(bpd):
    ent = sorted({max(2, round(d * bpd)) for d in ENTRY_DAYS})
    trn = [None] + sorted({max(2, round(d * bpd)) for d in TREND_DAYS})
    ext = sorted({max(1, round(d * bpd)) for d in EXIT_DAYS})
    return [(e, x, t) for e in ent for t in trn for x in ext if x < e], ent, trn, ext


def qtile(vals, p):
    v = sorted(vals); return v[int(p * (len(v) - 1))]


def daily_regime(daily_bars, lb=60, band=10.0):
    dc = {b["ts"].date(): b["close"] for b in daily_bars}
    days = sorted(dc); lab = {}
    for i, d in enumerate(days):
        if i < lb:
            lab[d] = "warmup"; continue
        r = dc[d] / dc[days[i - lb]] - 1
        lab[d] = "bull" if r > band / 100 else ("bear" if r < -band / 100 else "chop")
    return lab


def main():
    rows_1h, _ = load_binance_1h()
    daily = derive_tf(rows_1h, 24)
    lab = daily_regime(daily)

    OOS0 = datetime(2023, 7, 1, tzinfo=timezone.utc)
    HOLD_TR0 = datetime(2022, 7, 1, tzinfo=timezone.utc)
    HOLD_TE0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    base_dt = datetime(2022, 7, 1, tzinfo=timezone.utc)

    summary = []
    for name, hours in TFS:
        bars = derive_tf(rows_1h, hours)
        ts = [b["ts"] for b in bars]
        bpd = 24 / hours
        combos, ent, trn, ext = grid_for(bpd)

        def ix(dt):
            return bisect_left(ts, dt)
        oa, ob = ix(OOS0), len(bars) - 1
        runs = {c: fast_backtest(bars, c[0], c[1], c[2], 3.0) for c in combos}
        hts, heq, hin, hret = hodl(bars)
        hood = slice_metrics(heq, hin, ts, oa, ob, [])

        # cohort (fixed across OOS)
        cal, tot, tim, rt = [], [], [], []
        for c in combos:
            r = runs[c]; m = slice_metrics(r["eq"], r["inmkt"], ts, oa, ob, r["trades"])
            cal.append(m["calmar"]); tot.append(m["total"]); tim.append(m["tim"]); rt.append(m["rt"])
        bc = sum(1 for x in cal if x > hood["calmar"]); bt = sum(1 for x in tot if x > hood["total"])
        n = len(combos)

        # clean holdout: select 2022-07..2023-12, forward 2024-01..end
        ta, tb = ix(HOLD_TR0), ix(HOLD_TE0) - 1
        fa, fb = ix(HOLD_TE0), len(bars) - 1
        thdd = maxdd(heq[ta:tb + 1])
        best = None
        for c in combos:
            r = runs[c]; m = slice_metrics(r["eq"], r["inmkt"], ts, ta, tb, r["trades"])
            if m["maxdd"] <= thdd and (best is None or m["calmar"] > best[1]["calmar"]):
                best = (c, m)
        scfg = best[0]
        sfwd = slice_metrics(runs[scfg]["eq"], runs[scfg]["inmkt"], ts, fa, fb, runs[scfg]["trades"])
        hfwd = slice_metrics(heq, hin, ts, fa, fb, [])
        # matched benchmarks over forward window
        sub = bars[fa:fb + 1]; w = sfwd["tim"] / 100.0
        _, _, _, sret = weight_portfolio(sub, lambda i: w)
        stat_tot = (math.prod([1 + r for r in sret[1:]]) - 1) * 100 if sret[1:] else 0.0

        # WF param stability (6 folds)
        chosen = []
        for k in range(6):
            trs = add_months(base_dt, k * 6); tes = add_months(trs, 12); tee = add_months(tes, 6)
            wta, wtb = ix(trs), ix(tes) - 1
            whdd = maxdd(heq[wta:wtb + 1]) if wtb > wta else 999
            wb = None
            for c in combos:
                r = runs[c]; m = slice_metrics(r["eq"], r["inmkt"], ts, wta, wtb, r["trades"])
                if m["maxdd"] <= whdd and (wb is None or m["calmar"] > wb[1]):
                    wb = (c, m["calmar"])
            if wb:
                chosen.append(wb[0])
        es = [c[0] for c in chosen]; xs = [c[1] for c in chosen]; tset = sorted({str(c[2]) for c in chosen})

        # bull-capture + decline concentration for the clean-holdout config, over forward window
        srun = runs[scfg]
        bull_idx = [i for i in range(fa, fb + 1) if lab.get(bars[i]["ts"].date()) == "bull"]
        sb = hb = 1.0
        for i in bull_idx:
            sb *= (1 + (srun["eq"][i] / srun["eq"][i - 1] - 1)); hb *= (1 + hret[i])
        cap = ((sb - 1) / (hb - 1) * 100) if (hb - 1) != 0 else 0.0
        # per-fold alpha of the clean-holdout config vs HODL (folds 4-5 are the decline folds)
        fold_alpha = []
        for k in range(6):
            tes = add_months(base_dt, k * 6 + 12); tee = add_months(tes, 6)
            xa, xb = ix(tes), ix(tee) - 1
            if xb <= xa:
                continue
            fm = slice_metrics(srun["eq"], srun["inmkt"], ts, xa, xb, srun["trades"])
            hm = slice_metrics(heq, hin, ts, xa, xb, [])
            fold_alpha.append(round(fm["total"] - hm["total"], 1))

        print(f"\n########## {name} (bpd={bpd:g}) — grid {n} combos; entry{ent} trend{trn} exit{ext}")
        print(f"HODL OOS: total={hood['total']:+.1f}% Calmar={hood['calmar']:.2f}")
        print(f"COHORT OOS Calmar: min={min(cal):.2f} Q1={qtile(cal,.25):.2f} MEDIAN={qtile(cal,.5):.2f} "
              f"Q3={qtile(cal,.75):.2f} max={max(cal):.2f}")
        print(f"COHORT OOS total%: MEDIAN={qtile(tot,.5):+.0f}  |  median TIM={qtile(tim,.5):.1f}%  "
              f"median RT={qtile(rt,.5):.0f}  (RT range {min(rt)}..{max(rt)})")
        print(f"beat HODL: Calmar {bc}/{n} ({100*bc/n:.0f}%) | total {bt}/{n} ({100*bt/n:.0f}%)")
        print(f"CLEAN-HOLDOUT select={scfg}: fwd total={sfwd['total']:+.1f}% Calmar={sfwd['calmar']:.2f} "
              f"maxDD={sfwd['maxdd']:.1f}% TIM={sfwd['tim']:.1f}% RT={sfwd['rt']} | HODL {hfwd['total']:+.1f}%/{hfwd['calmar']:.2f} | static-matched {stat_tot:+.1f}%")
        print(f"WF chosen/fold: {chosen}  -> entry {min(es)}..{max(es)}, trend {tset}, exit {min(xs)}..{max(xs)}")
        print(f"bull-capture(clean cfg fwd)={cap:.0f}% of HODL upside | per-fold alpha vs HODL {fold_alpha}")
        summary.append((name, qtile(cal, .5), 100 * bc / n, sfwd["calmar"], sfwd["tim"], qtile(tim, .5),
                        qtile(rt, .5), cap, scfg))

    # slippage sensitivity on DAILY family median Calmar
    print("\n########## DAILY slippage sensitivity (family MEDIAN OOS Calmar) ##########")
    dbars = derive_tf(rows_1h, 24); dts = [b["ts"] for b in dbars]
    dcombos, _, _, _ = grid_for(1)
    doa = bisect_left(dts, OOS0); dob = len(dbars) - 1
    for s in [0, 2, 3, 5, 10]:
        cals = []
        for c in dcombos:
            r = fast_backtest(dbars, c[0], c[1], c[2], s)
            cals.append(slice_metrics(r["eq"], r["inmkt"], dts, doa, dob, r["trades"])["calmar"])
        print(f"  slip={s:>2}bps: median Calmar={qtile(cals,.5):.2f}")

    print("\n########## CROSS-TF SUMMARY ##########")
    print(f"{'TF':<5}{'medCal':>7}{'%beatHODLcal':>13}{'cleanCal':>9}{'cleanTIM':>9}{'medTIM':>7}{'medRT':>6}{'bullCap%':>9}{'cleanCfg':>14}")
    for (nm, mc, bh, cc, ct, mt, mr, cap, cfg) in summary:
        print(f"{nm:<5}{mc:>7.2f}{bh:>12.0f}%{cc:>9.2f}{ct:>8.1f}%{mt:>6.1f}%{mr:>6.0f}{cap:>8.0f}%{str(cfg):>14}")


if __name__ == "__main__":
    main()
