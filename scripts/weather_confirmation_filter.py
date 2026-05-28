"""kalshi_weather MODEL-CONFIRMATION FILTER test (read-only, real prices).

Distinct from prior avenue 4 (real-price segment scan by station/kind/price/side,
which found no robust +EV). Here the segmentation is by *agreement between our
model and the market on FAVORITES*:

  Among markets where the MARKET strongly favors one side (implied >= band), does
  OUR model's agreement (our prob for that side also >= band) identify a subset
  that wins MORE than the market price (gap>0 => underpriced => edge)? And does
  DISAGREEMENT (our prob materially below the market's, < market-implied - 0.10)
  flag shaky favorites that win LESS?

This requires the market price to be an INDEPENDENT signal from our model, so it
is meaningful ONLY on REAL Kalshi prices (the deep-corpus proxy market is derived
from NBM, the same input our model uses, so "agreement" there is mechanical, not
informative). Real Kalshi settled-market history is ~2 months => spring-2026 only.
A true 2021-2024 train / 2025-26 holdout split is therefore IMPOSSIBLE on real
prices. We do the best available out-of-sample discipline:
  (A) chronological early/late split within the holdout, and
  (B) interleaved even/odd market-day split (seasonality-robust alternative),
and flag a cell as real ONLY if a positive effect survives the split AND is
monotone (agree gap > middle gap > disagree gap), not an isolated spike.

Reuses the exact leak-safe real-price join from weather_realprice_ev:
  M  = market mid (implied YES prob) at the leak-safe evening-before moment
  E  = WX-EMP-1 (our frozen empirical model) YES prob for the bucket
  YA/YB = real yes ask/bid (fillable)
  WON = Kalshi's own settlement (1=yes)

Run capped per CLAUDE.md:
    .\\scripts\\run_capped.ps1 python scripts\\weather_confirmation_filter.py
"""
from __future__ import annotations

import json
import math
from datetime import datetime

import numpy as np

import weather_edge_analysis as W
from weather_realprice_ev import FrozenModel, build_nbm_lookup, price_at_decision, ncdf

CANDLES = "tmp/kalshi_realprice_candles.jsonl"
MODEL = "data/weather_emp_model_WX-EMP-1.json"
BANDS = (0.65, 0.75, 0.85, 0.90)
DISAGREE_MARGIN = 0.10   # our prob < market implied - this => DISAGREE


def fee(p):
    """Kalshi fee per contract at price p (vectorized-safe via np)."""
    return np.ceil(0.07 * p * (1.0 - p) * 100.0) / 100.0


def collect():
    """Return per-market arrays for interior B-buckets, leak-safe real quote."""
    fm = FrozenModel(MODEL)
    lut = build_nbm_lookup()
    rows = [json.loads(l) for l in open(CANDLES, encoding="utf-8")]

    # detect Kalshi B-bucket resolution rule (incl [floor,cap] vs excl [floor,cap))
    incl = [0, 0]; excl = [0, 0]
    for r in rows:
        if r["floor"] is None or r["cap"] is None or r["result"] not in ("yes", "no"):
            continue
        nb = lut.get((r["icao"], r["date"], r["kind"]))
        if not nb:
            continue
        a = round(nb["actual"])
        incl[1] += 1; excl[1] += 1
        incl[0] += int((r["floor"] <= a <= r["cap"]) == (r["result"] == "yes"))
        excl[0] += int((r["floor"] <= a < r["cap"]) == (r["result"] == "yes"))
    rule = "incl" if incl[0] >= excl[0] else "excl"
    print(f"[resolve-rule] incl={incl[0]}/{incl[1]} excl={excl[0]}/{excl[1]} -> {rule}")

    def bucket_prob(cdf_fn, floor, cap):
        hi = cap + 0.5 if rule == "incl" else cap - 0.5
        lo = floor - 0.5
        return max(0.0, cdf_fn(hi) - cdf_fn(lo))

    M, E, YB, YA, WON, ST, KI, SE, DATE = [], [], [], [], [], [], [], [], []
    for r in rows:
        if r["floor"] is None or r["cap"] is None or r["result"] not in ("yes", "no"):
            continue
        nb = lut.get((r["icao"], r["date"], r["kind"]))
        if not nb:
            continue
        pa = price_at_decision(r["candles"], nb["cycle_dt"], r["date"])
        if pa is None:
            continue
        yb, ya = pa
        mid = 0.5 * (yb + ya)
        if mid < 0.05 or mid > 0.95:   # far-tail illiquid skip (same as realprice_ev)
            continue
        st, se, ki = r["icao"], nb["season"], r["kind"]
        p50, sig = nb["p50"], nb["sig"]
        p_emp = bucket_prob(lambda x: fm.cdf_emp(x, p50, sig, st, se, ki), r["floor"], r["cap"])
        M.append(mid); E.append(p_emp); YB.append(yb); YA.append(ya)
        WON.append(int(r["result"] == "yes")); ST.append(st); KI.append(ki); SE.append(se)
        DATE.append(r["date"])
    out = dict(
        M=np.array(M), E=np.array(E), YB=np.array(YB), YA=np.array(YA),
        WON=np.array(WON), ST=np.array(ST), KI=np.array(KI), SE=np.array(SE),
        DATE=np.array(DATE),
    )
    print(f"[join] {len(out['M'])} interior B-markets w/ real decision-time quote; "
          f"dates {min(out['DATE'])}..{max(out['DATE'])}; "
          f"{len(set(out['DATE']))} market-days")
    return out


def favorite_view(d, band):
    """Project every market onto its FAVORED side at this band.

    Returns a dict of arrays restricted to markets where the market favors a side
    at >= band, with everything expressed from the buyer-of-the-favorite POV:
      fp_mkt  market implied prob of the favored side (>= band)
      fp_mod  OUR model prob of the favored side
      ask     price you PAY to buy the favored side (fillable)
      won     1 if the favored side settled true
      plus passthrough DATE/ST/KI/SE/side.
    """
    M, E, YB, YA, WON = d["M"], d["E"], d["YB"], d["YA"], d["WON"]
    yes_fav = M >= band
    no_fav = M <= (1.0 - band)
    sel = yes_fav | no_fav
    # favored-side market prob
    fp_mkt = np.where(yes_fav, M, 1.0 - M)
    # favored-side model prob
    fp_mod = np.where(yes_fav, E, 1.0 - E)
    # price to BUY the favored side: YES costs the yes-ask; NO costs (1 - yes_bid)
    ask = np.where(yes_fav, YA, 1.0 - YB)
    won = np.where(yes_fav, WON, 1 - WON)
    side = np.where(yes_fav, "YES", "NO")
    return dict(
        fp_mkt=fp_mkt[sel], fp_mod=fp_mod[sel], ask=ask[sel], won=won[sel],
        side=side[sel], DATE=d["DATE"][sel], ST=d["ST"][sel],
        KI=d["KI"][sel], SE=d["SE"][sel],
    )


def cell_stats(fv, mask):
    """n, n_days, fav-win rate, mean implied, gap_mid, gap_ask, ev/ct, 2*SE(day-clustered)."""
    n = int(mask.sum())
    if n == 0:
        return None
    won = fv["won"][mask].astype(float)
    mkt = fv["fp_mkt"][mask]
    ask = fv["ask"][mask]
    dates = fv["DATE"][mask]
    ndays = len(set(dates))
    winrate = won.mean()
    gap_mid = winrate - mkt.mean()          # vs implied prob (statistical underpricing)
    gap_ask = winrate - ask.mean()          # vs price actually paid (tradeable, pre-fee)
    pnl = won - ask - fee(ask)              # per-contract PnL buying the favorite, net fee
    evct = pnl.mean()
    # day-clustered SE: intra-day favorites co-move (a calm day wins many at once)
    se = pnl.std() / math.sqrt(max(1, ndays))
    return dict(n=n, ndays=ndays, winrate=winrate, mkt=mkt.mean(),
                gap_mid=gap_mid, gap_ask=gap_ask, evct=evct, se2=2 * se)


def split_label(fv, mask, band):
    """AGREE / MIDDLE / DISAGREE masks within `mask` for this band."""
    mod = fv["fp_mod"]
    mkt = fv["fp_mkt"]
    agree = mask & (mod >= band)
    disagree = mask & (mod < (mkt - DISAGREE_MARGIN))
    middle = mask & ~agree & ~disagree
    return agree, middle, disagree


def print_grid(d, title):
    print(f"\n{'='*92}\n{title}\n{'='*92}")
    print(f"{'band':>5} {'cell':>9} {'n':>6} {'days':>5} {'winrate':>8} {'implied':>8} "
          f"{'gap_mid':>8} {'gap_ask':>8} {'ev/ct':>8} {'2*SE':>7} flag")
    for band in BANDS:
        fv = favorite_view(d, band)
        base_mask = np.ones(len(fv["won"]), dtype=bool)
        agree, middle, disagree = split_label(fv, base_mask, band)
        for name, m in (("ALL-fav", base_mask), ("AGREE", agree),
                        ("MIDDLE", middle), ("DISAGREE", disagree)):
            s = cell_stats(fv, m)
            if s is None:
                print(f"{band:>5.2f} {name:>9} {'0':>6}")
                continue
            robust = s["evct"] > s["se2"] and s["ndays"] >= 15
            flag = "ROBUST+EV" if robust else ("+ev(weak)" if s["evct"] > 0 else "")
            print(f"{band:>5.2f} {name:>9} {s['n']:>6} {s['ndays']:>5} "
                  f"{s['winrate']:>8.3f} {s['mkt']:>8.3f} {s['gap_mid']:>+8.3f} "
                  f"{s['gap_ask']:>+8.3f} {s['evct']:>+8.4f} {s['se2']:>7.4f} {flag}")
        print()


def monotonicity(d, title):
    """Is the agree>middle>disagree ordering on gap_mid present? (the real-pattern test)"""
    print(f"\n--- monotonicity check ({title}) — gap_mid by band, agree vs middle vs disagree ---")
    print("  (real pattern => AGREE gap > MIDDLE gap > DISAGREE gap, consistently across bands)")
    for band in BANDS:
        fv = favorite_view(d, band)
        base = np.ones(len(fv["won"]), dtype=bool)
        a, mi, di = split_label(fv, base, band)
        ga = cell_stats(fv, a); gm = cell_stats(fv, mi); gd = cell_stats(fv, di)
        def g(x):
            return f"{x['gap_mid']:+.3f}(n{x['n']})" if x else "n/a"
        mono = ""
        if ga and gd:
            mono = "MONO+" if (ga["gap_mid"] > gd["gap_mid"]) else "inverted"
        print(f"  band {band:.2f}: agree {g(ga):>14}  middle {g(gm):>14}  "
              f"disagree {g(gd):>14}   {mono}")


def main():
    d = collect()

    # ---- FULL real-price grid (all spring-2026) ----
    print_grid(d, "FULL real-price grid (spring-2026 holdout, all dates) — favorites, both sides")
    monotonicity(d, "FULL")

    # ---- YES-only and NO-only robustness (user framed it as YES; show the split) ----
    print_grid_side(d, "YES")
    print_grid_side(d, "NO")

    # ---- TIME-SPLIT (A): chronological early/late within holdout ----
    cut = "2026-05-01"
    train = subset(d, d["DATE"] < cut)
    hold = subset(d, d["DATE"] >= cut)
    print(f"\n\n########## SPLIT A: chronological  TRAIN(<{cut})  vs  HOLDOUT(>={cut}) ##########")
    print(f"  train {min(train['DATE'])}..{max(train['DATE'])} n={len(train['M'])} "
          f"days={len(set(train['DATE']))} | holdout {min(hold['DATE'])}..{max(hold['DATE'])} "
          f"n={len(hold['M'])} days={len(set(hold['DATE']))}")
    print_grid(train, "SPLIT A — TRAIN (chronological early)")
    print_grid(hold, "SPLIT A — HOLDOUT (chronological late)")
    monotonicity(train, "SPLIT A TRAIN")
    monotonicity(hold, "SPLIT A HOLDOUT")

    # ---- TIME-SPLIT (B): interleaved even/odd day-of-month (seasonality-robust) ----
    dom = np.array([int(s.split("-")[2]) for s in d["DATE"]])
    even = subset(d, (dom % 2) == 0)
    odd = subset(d, (dom % 2) == 1)
    print(f"\n\n########## SPLIT B: interleaved  TRAIN(even day-of-month)  vs  HOLDOUT(odd) ##########")
    print(f"  even n={len(even['M'])} days={len(set(even['DATE']))} | "
          f"odd n={len(odd['M'])} days={len(set(odd['DATE']))}")
    print_grid(even, "SPLIT B — TRAIN (even day-of-month)")
    print_grid(odd, "SPLIT B — HOLDOUT (odd day-of-month)")
    monotonicity(even, "SPLIT B TRAIN")
    monotonicity(odd, "SPLIT B HOLDOUT")


def subset(d, mask):
    return {k: v[mask] for k, v in d.items()}


def print_grid_side(d, want):
    """Grid restricted to favorites on a single side (YES or NO)."""
    print(f"\n{'='*92}\nFULL grid — {want}-favorites only\n{'='*92}")
    print(f"{'band':>5} {'cell':>9} {'n':>6} {'days':>5} {'winrate':>8} {'implied':>8} "
          f"{'gap_mid':>8} {'gap_ask':>8} {'ev/ct':>8} {'2*SE':>7} flag")
    M = d["M"]
    for band in BANDS:
        if want == "YES":
            keep = M >= band
        else:
            keep = M <= (1.0 - band)
        ds = subset(d, keep)
        fv = favorite_view(ds, band)
        base = np.ones(len(fv["won"]), dtype=bool)
        agree, middle, disagree = split_label(fv, base, band)
        for name, m in (("ALL-fav", base), ("AGREE", agree),
                        ("MIDDLE", middle), ("DISAGREE", disagree)):
            s = cell_stats(fv, m)
            if s is None:
                print(f"{band:>5.2f} {name:>9} {'0':>6}")
                continue
            robust = s["evct"] > s["se2"] and s["ndays"] >= 15
            flag = "ROBUST+EV" if robust else ("+ev(weak)" if s["evct"] > 0 else "")
            print(f"{band:>5.2f} {name:>9} {s['n']:>6} {s['ndays']:>5} "
                  f"{s['winrate']:>8.3f} {s['mkt']:>8.3f} {s['gap_mid']:>+8.3f} "
                  f"{s['gap_ask']:>+8.3f} {s['evct']:>+8.4f} {s['se2']:>7.4f} {flag}")
        print()


if __name__ == "__main__":
    main()
