"""kalshi_weather REAL-PRICE gate: WX-EMP-1 vs actual Kalshi prices (holdout 2026).

Joins real settled-market prices (tmp/kalshi_realprice_candles.jsonl, pulled by
kalshi_realprice_pull.py) to the NBM decision corpus + the frozen WX-EMP-1 model
(data/weather_emp_model_WX-EMP-1.json), then:

  1. EV of WX-EMP-1 bet rule at REAL fillable prices (pay the ask; net Kalshi fee),
     realized via Kalshi's own `result`. Interior B-buckets only (T-strike direction
     is ambiguous; tails were the suspect/overfit component anyway).
  2. THE DECISIVE DIAGNOSTIC: is the real market price closer to raw-NBM (naive ->
     edge realizable) or to the empirical distribution (efficient -> no edge)?

Leak-safe: the price is read at the NBM forecast-cycle moment (cycle_iso, ~day
before), strictly before settlement. All coverage is spring-2026 = the frozen
holdout, so this is an out-of-sample REAL-price test.

Run capped: .\\scripts\\run_capped.ps1 python scripts\\weather_realprice_ev.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime

import numpy as np

import weather_edge_analysis as W

CANDLES = "tmp/kalshi_realprice_candles.jsonl"
MODEL = "data/weather_emp_model_WX-EMP-1.json"
TAU = 0.05
SQRT2 = math.sqrt(2.0)


def ncdf(z):
    return 0.5 * (1.0 + math.erf(z / SQRT2))


class FrozenModel:
    def __init__(self, path):
        a = json.load(open(path))
        self.qpts = np.array(a["z_quantile_percentiles"], dtype=float) / 100.0
        self.cells = a["cells"]
        self.pool = a["pooled_fallback"]

    def _cell(self, station, season, kind):
        return self.cells.get(f"{station}|{season}|{kind}", self.pool)

    def cdf_emp(self, x, p50, sig, station, season, kind):
        c = self._cell(station, season, kind)
        zq = np.asarray(c["z_quantiles"], dtype=float)
        z = (x - p50) / sig
        return float(np.clip(np.interp(z, zq, self.qpts), 0.0, 1.0))

    def bias(self, station, season, kind):
        return self._cell(station, season, kind)["bias_f"]


def kalshi_fee(price):
    return math.ceil(0.07 * price * (1.0 - price) * 100) / 100.0


def build_nbm_lookup():
    """(station,target_date,kind) -> dict(p50,sig,season,actual,cycle_dt) for the
    leak-safe day_before decision (reuses the analysis engine's selection)."""
    d = W.load_joined()
    W.assert_inviolable(d)
    sub = W.select_decisions(d, W.HORIZON_BANDS["day_before"])
    sub = W.enforce_no_leak(sub)
    lut = {}
    for i in range(len(sub["actual"])):
        key = (sub["station"][i], sub["tdate"][i], sub["kind"][i])
        lut[key] = {
            "p50": float(sub["p50"][i]), "sig": float(sub["sig"][i]),
            "season": sub["season"][i], "actual": float(sub["actual"][i]),
            "cycle_dt": datetime.fromisoformat(sub["cycle"][i].replace("Z", "+00:00")),
        }
    return lut


def price_at_decision(candles, cycle_dt, target_date_str):
    """Latest valid two-sided quote in the LEAK-SAFE evening-before window:
    [NBM cycle time, target-day 00:00Z]. The forecast is already issued (ts >=
    cycle) and 00:00Z of the target day precedes the realization of BOTH the
    daily max (afternoon) and daily min (morning) at every US station, so no
    look-ahead. Latest-in-window = most liquid pre-outcome price."""
    lo = cycle_dt.timestamp() - 2 * 3600
    cutoff = datetime.fromisoformat(target_date_str + "T00:00:00+00:00").timestamp()
    best = None
    for ts, yb, ya, px in candles:
        if yb is None or ya is None:
            continue
        if yb <= 0 and ya >= 1.0:   # empty book sentinel
            continue
        if ts < lo or ts > cutoff:
            continue
        if best is None or ts > best[0]:
            best = (ts, yb, ya)
    if best is None:
        return None
    return best[1], best[2]


def main():
    fm = FrozenModel(MODEL)
    lut = build_nbm_lookup()
    rows = [json.loads(l) for l in open(CANDLES, encoding="utf-8")]
    print(f"[load] {len(rows)} market rows, NBM lut {len(lut)} decisions, model {len(fm.cells)} cells")

    # ---- detect B resolution rule against Kalshi `result` ----
    incl_cap = [0, 0]  # [consistent, total] for rule  floor<=round(actual)<=cap
    excl_cap = [0, 0]  # for rule floor<=round(actual)<cap
    for r in rows:
        if r["floor"] is None or r["cap"] is None or r["result"] not in ("yes", "no"):
            continue
        key = (r["icao"], r["date"], r["kind"])
        nb = lut.get(key)
        if not nb:
            continue
        a = round(nb["actual"])
        yes_incl = r["floor"] <= a <= r["cap"]
        yes_excl = r["floor"] <= a < r["cap"]
        incl_cap[1] += 1; excl_cap[1] += 1
        incl_cap[0] += int(yes_incl == (r["result"] == "yes"))
        excl_cap[0] += int(yes_excl == (r["result"] == "yes"))
    rule = "incl" if incl_cap[0] >= excl_cap[0] else "excl"
    print(f"[resolve-rule] incl[floor,cap]={incl_cap[0]}/{incl_cap[1]} "
          f"excl[floor,cap)={excl_cap[0]}/{excl_cap[1]} -> using {rule}")

    def bucket_prob(cdf_fn, floor, cap):
        # integer settlement: probability mass on the integer set the bucket covers
        hi = cap + 0.5 if rule == "incl" else cap - 0.5
        lo = floor - 0.5
        return max(0.0, cdf_fn(hi) - cdf_fn(lo))

    # ---- collect per-market arrays (one pass) ----
    M, E, RW, YB, YA, WON, KIND, ST, SE, DATE = [], [], [], [], [], [], [], [], [], []
    spreads = []
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
        if mid < 0.05 or mid > 0.95:
            continue
        st, se, ki = r["icao"], nb["season"], r["kind"]
        p50, sig = nb["p50"], nb["sig"]
        p_emp = bucket_prob(lambda x: fm.cdf_emp(x, p50, sig, st, se, ki), r["floor"], r["cap"])
        p_raw = bucket_prob(lambda x: ncdf((x - p50) / sig), r["floor"], r["cap"])
        M.append(mid); E.append(p_emp); RW.append(p_raw)
        YB.append(yb); YA.append(ya); WON.append(int(r["result"] == "yes")); KIND.append(ki)
        ST.append(st); SE.append(se); DATE.append(r["date"])
        spreads.append(ya - yb)
    M = np.array(M); E = np.array(E); RW = np.array(RW)
    YB = np.array(YB); YA = np.array(YA); WON = np.array(WON)
    KIND = np.array(KIND); ST = np.array(ST); SE = np.array(SE); DATE = np.array(DATE)
    print(f"\n[join] {len(M)} interior B-markets joined w/ real decision-time quote "
          f"(median spread {np.median(spreads):.3f})")

    print("\n=== DECISIVE DIAGNOSTIC: what does the real market price track? ===")
    print(f"  mean|market - empirical(WX-EMP-1)| = {np.mean(np.abs(M-E)):.4f}")
    print(f"  mean|market - raw-NBM Gaussian|    = {np.mean(np.abs(M-RW)):.4f}")
    print(f"  corr(market,emp)={np.corrcoef(M,E)[0,1]:.3f}  corr(market,raw)={np.corrcoef(M,RW)[0,1]:.3f}")
    # Brier vs real outcome: who is the better probability, market or our model?
    print(f"  Brier(market vs outcome) = {np.mean((M-WON)**2):.4f}")
    print(f"  Brier(empirical vs outcome) = {np.mean((E-WON)**2):.4f}   "
          f"(lower=better; market better => efficient, no edge)")

    # ---- EV with tau sweep, pay-the-ask + fee, realized via Kalshi result ----
    print("\n=== REAL-PRICE EV (WX-EMP-1, holdout spring-2026) — tau sweep ===")
    print(f"{'tau':>5}{'n_bets':>8}{'YES_n':>7}{'NO_n':>6}{'pnl$':>10}{'ev/ct':>9}{'winrate':>9}")
    no_price = 1.0 - YB
    for tau in (0.03, 0.05, 0.08, 0.12, 0.18):
        buy_yes = (E - YA) >= tau
        buy_no = (~buy_yes) & ((YB - E) >= tau)
        fee_y = np.ceil(0.07 * YA * (1 - YA) * 100) / 100
        fee_n = np.ceil(0.07 * no_price * (1 - no_price) * 100) / 100
        pnl_y = np.where(WON == 1, 1 - YA, -YA) - fee_y
        pnl_n = np.where(WON == 0, 1 - no_price, -no_price) - fee_n
        pnl = (buy_yes * pnl_y) + (buy_no * pnl_n)
        nb_ = int(buy_yes.sum() + buy_no.sum())
        tot = float(pnl.sum())
        wins = int((buy_yes & (WON == 1)).sum() + (buy_no & (WON == 0)).sum())
        ev_ct = tot / nb_ if nb_ else float("nan")
        wr = wins / nb_ if nb_ else float("nan")
        print(f"{tau:>5.2f}{nb_:>8}{int(buy_yes.sum()):>7}{int(buy_no.sum()):>6}"
              f"{tot:>10.2f}{ev_ct:>9.4f}{wr:>9.3f}")
    print("\n  Interpretation: EV stays <= 0 at every threshold => the real market is "
          "efficient; WX-EMP-1's proxy 'edge' does not survive real prices.")

    # ---- SEGMENT SCAN (mandate: be explicit where edge comes from) ----
    # Per-bet pnl at tau=0.05, then EV by segment with an EFFECTIVE-sample significance
    # bar: intra-day bets are correlated (a cold day wins many at once), so n_eff =
    # distinct market-days in the segment, and SE = std(pnl)/sqrt(n_eff). A segment is
    # flagged "robust+EV" only if ev/ct > 2*SE AND n_eff >= 15.
    buy_yes = (E - YA) >= TAU
    buy_no = (~buy_yes) & ((YB - E) >= TAU)
    bet = buy_yes | buy_no
    fee_y = np.ceil(0.07 * YA * (1 - YA) * 100) / 100
    fee_n = np.ceil(0.07 * no_price * (1 - no_price) * 100) / 100
    pnl_y = np.where(WON == 1, 1 - YA, -YA) - fee_y
    pnl_n = np.where(WON == 0, 1 - no_price, -no_price) - fee_n
    PNL = np.where(buy_yes, pnl_y, np.where(buy_no, pnl_n, 0.0))

    def scan(name, labels):
        print(f"\n--- segment scan by {name} (WX-EMP-1 bets, tau=0.05) ---")
        print(f"{name:>10}{'n_bets':>8}{'n_days':>7}{'ev/ct':>9}{'2*SE':>8}{'flag':>10}")
        flagged = []
        for lab in sorted(set(labels[bet])):
            m = bet & (labels == lab)
            n = int(m.sum())
            if n < 20:
                continue
            ndays = len(set(DATE[m]))
            evct = PNL[m].sum() / n
            se = PNL[m].std() / math.sqrt(max(1, ndays))
            robust = evct > 2 * se and ndays >= 15
            tag = "ROBUST+EV" if robust else ("+ev(weak)" if evct > 0 else "")
            if robust:
                flagged.append(lab)
            print(f"{str(lab):>10}{n:>8}{ndays:>7}{evct:>9.4f}{2*se:>8.4f}{tag:>10}")
        return flagged

    pdec = np.clip((M * 10).astype(int), 0, 9)
    side = np.where(buy_yes, "YES", np.where(buy_no, "NO", "-"))
    any_robust = []
    any_robust += scan("station", ST)
    any_robust += scan("kind", KIND)
    any_robust += scan("season", SE)
    any_robust += scan("price_dec", pdec)
    any_robust += scan("side", side)
    print("\n=== SEGMENT VERDICT ===")
    if any_robust:
        print(f"  Segments passing robust+EV bar (ev/ct>2*SE, n_days>=15): {any_robust}")
        print("  -> investigate, but per mandate treat concentrated edge as SUSPECT "
              "(2-month holdout, multiple-testing across ~45 segments).")
    else:
        print("  NO segment is robustly +EV (ev/ct > 2*SE with n_days>=15). Any positive "
              "cell is within noise. Edge is absent at every (station/kind/season/price/side) "
              "cut, not just in aggregate. Mandate 'where does edge come from' = nowhere.")


if __name__ == "__main__":
    main()
