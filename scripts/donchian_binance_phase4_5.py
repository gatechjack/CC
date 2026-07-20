"""Donchian re-validation — PHASE 4.5 (is 20/168/6 special, or survivorship?).

1. Fixed-config cohort: all 264 configs held fixed across the OOS window.
2. True holdout: select on 2022-07..2023-12 only, trade forward 2024-01..2026-06.
3. Fold-level fixed-config alpha vs HODL (concentration check).
Read-only research; reuses the validated fast harness.
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
from scripts.donchian_binance_phase3 import fast_backtest, maxdd, hodl  # noqa: E402
from scripts.donchian_binance_phase4 import slice_metrics, add_months, COMBOS  # noqa: E402


def main():
    rows_1h, _ = load_binance_1h()
    bars, _, _ = derive_6h(rows_1h)
    ts = [b["ts"] for b in bars]
    runs = {c: fast_backtest(bars, c[0], c[1], c[2], 3.0) for c in COMBOS}
    hts, heq, hin, hret = hodl(bars)

    def idx(dt):
        return bisect_left(ts, dt.replace(tzinfo=timezone.utc))

    # ---- OOS span 2023-07 .. end (2026-06-30) ----
    oa, ob = idx(datetime(2023, 7, 1)), len(bars) - 1
    hood = slice_metrics(heq, hin, ts, oa, ob, [])
    print(f"OOS span {ts[oa]}..{ts[ob]}  HODL: total={hood['total']:+.1f}% Calmar={hood['calmar']:.2f} maxDD={hood['maxdd']:.1f}%")

    # ---- 1. FIXED-CONFIG COHORT ----
    cohort = []
    for c in COMBOS:
        r = runs[c]
        m = slice_metrics(r["eq"], r["inmkt"], ts, oa, ob, r["trades"])
        cohort.append((c, m))
    cals = sorted(m["calmar"] for _, m in cohort)
    tots = sorted(m["total"] for _, m in cohort)
    n = len(cohort)

    def q(sorted_vals, p):
        return sorted_vals[int(p * (len(sorted_vals) - 1))]

    inc = next(m for c, m in cohort if c == (20, 6, 168))
    inc_pct = 100 * sum(1 for c, m in cohort if m["calmar"] < inc["calmar"]) / n
    beat_cal = sum(1 for c, m in cohort if m["calmar"] > hood["calmar"])
    beat_tot = sum(1 for c, m in cohort if m["total"] > hood["total"])
    print("\n=== 1. FIXED-CONFIG COHORT (all 264, held fixed across OOS) ===")
    print(f"OOS Calmar distribution: min={cals[0]:.2f} Q1={q(cals,.25):.2f} median={q(cals,.5):.2f} "
          f"Q3={q(cals,.75):.2f} max={cals[-1]:.2f}")
    print(f"OOS total%  distribution: min={tots[0]:+.0f} Q1={q(tots,.25):+.0f} median={q(tots,.5):+.0f} "
          f"Q3={q(tots,.75):+.0f} max={tots[-1]:+.0f}")
    print(f"20/168/6: OOS Calmar={inc['calmar']:.2f} total={inc['total']:+.1f}%  -> percentile rank {inc_pct:.0f}th")
    print(f"beat HODL on OOS Calmar ({hood['calmar']:.2f}): {beat_cal}/{n} ({100*beat_cal/n:.0f}%)")
    print(f"beat HODL on OOS total% ({hood['total']:+.0f}): {beat_tot}/{n} ({100*beat_tot/n:.0f}%)")
    print(f"MEDIAN config Calmar {q(cals,.5):.2f} vs HODL {hood['calmar']:.2f} -> "
          f"{'FAMILY ROBUST (median beats HODL)' if q(cals,.5) > hood['calmar'] else 'median does NOT beat HODL'}")

    # ---- 2. TRUE HOLDOUT: select pre-2024, trade forward ----
    ta, tb = idx(datetime(2022, 7, 1)), idx(datetime(2024, 1, 1)) - 1
    fa, fb = idx(datetime(2024, 1, 1)), len(bars) - 1
    train_hodl_dd = maxdd(heq[ta:tb + 1])
    best = None
    for c in COMBOS:
        r = runs[c]
        m = slice_metrics(r["eq"], r["inmkt"], ts, ta, tb, r["trades"])
        if m["maxdd"] <= train_hodl_dd and (best is None or m["calmar"] > best[1]["calmar"]):
            best = (c, m)
    sel_cfg, sel_train = best
    sel_fwd = slice_metrics(runs[sel_cfg]["eq"], runs[sel_cfg]["inmkt"], ts, fa, fb, runs[sel_cfg]["trades"])
    inc_fwd = slice_metrics(runs[(20, 6, 168)]["eq"], runs[(20, 6, 168)]["inmkt"], ts, fa, fb, runs[(20, 6, 168)]["trades"])
    hodl_fwd = slice_metrics(heq, hin, ts, fa, fb, [])
    print("\n=== 2. TRUE HOLDOUT (select 2022-07..2023-12, trade forward 2024-01..2026-06) ===")
    print(f"pre-2024 selected config: {sel_cfg}  (train Calmar {sel_train['calmar']:.2f}, train total {sel_train['total']:+.1f}%)")
    print(f"{'':<18}{'total%':>9}{'CAGR%':>7}{'maxDD%':>7}{'Calmar':>7}{'TIM%':>6}")
    for name, m in [(f"selected {sel_cfg}", sel_fwd), ("fixed 20/168/6", inc_fwd), ("HODL", hodl_fwd)]:
        print(f"{name:<18}{m['total']:+9.1f}{m['cagr']:+7.1f}{m['maxdd']:7.1f}{m['calmar']:7.2f}{m['tim']:6.1f}")

    # ---- 3. FOLD-LEVEL fixed-config alpha vs HODL ----
    base_dt = datetime(2022, 7, 1)
    print("\n=== 3. FIXED 20/168/6 per-fold alpha vs HODL (concentration) ===")
    print(f"{'F':<2}{'test window':<20}{'fixRet%':>8}{'hodlRet%':>9}{'alpha':>8}")
    fr = runs[(20, 6, 168)]
    up_f, dn_f = [], []
    for k in range(6):
        te_s = add_months(base_dt, k * 6 + 12); te_e = add_months(te_s, 6)
        xa, xb = idx(te_s.replace(tzinfo=None) if te_s.tzinfo is None else te_s), idx(te_e) - 1
        fm = slice_metrics(fr["eq"], fr["inmkt"], ts, xa, xb, fr["trades"])
        hm = slice_metrics(heq, hin, ts, xa, xb, [])
        a = fm["total"] - hm["total"]
        (dn_f if a > 0 else up_f).append((fm["total"], hm["total"]))
        print(f"{k:<2}{te_s.strftime('%Y-%m')+'..'+te_e.strftime('%Y-%m'):<20}{fm['total']:+8.1f}{hm['total']:+9.1f}{a:+8.1f}")

    # counterfactual: chain fixed vs hodl over the 4 lag-folds vs the 2 win-folds
    def chain(fold_ret_pairs, which):
        s = h = 1.0
        for f, hh in fold_ret_pairs:
            s *= (1 + f / 100); h *= (1 + hh / 100)
        return (s - 1) * 100, (h - 1) * 100
    sf, hf = chain(up_f, 0); sd, hd = chain(dn_f, 0)
    print(f"lag-folds (fix beats HODL=NO): fixed {sf:+.1f}% vs HODL {hf:+.1f}%")
    print(f"win-folds (fix beats HODL=YES): fixed {sd:+.1f}% vs HODL {hd:+.1f}%")


if __name__ == "__main__":
    main()
