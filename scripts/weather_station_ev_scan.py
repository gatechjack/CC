"""kalshi_weather PER-STATION +EV scan with multiple-comparisons discipline (read-only).

Re-runs avenue-4's per-station cut at the STATION level with EXPLICIT Bonferroni
correction + train/holdout split. The "systematic bet" is the FIXED WX-EMP-1 rule
(tau=0.05): buy YES if model_prob - yes_ask >= tau, else buy NO if yes_bid -
model_prob >= tau; pay the fillable price, net Kalshi fee. NO per-station tau/side
optimization (that would itself inflate the multiple-comparisons problem).

Real Kalshi prices are spring-2026 only, so the same defensible splits as the
favorite-filter test are used:
  - chronological: train < 2026-05-01 / holdout >= 2026-05-01  (PRIMARY holdout)
  - interleaved:   even / odd day-of-month  (seasonality-robust secondary)

A station is a CANDIDATE only if it is +EV on BOTH chronological train AND holdout.
It SURVIVES only if additionally it clears Bonferroni-corrected significance on the
full sample (critical z for two-sided alpha = 0.05 / n_stations_tested) AND has a
plausible physical mechanism. Day-clustered (CLUSTER-ROBUST) SE: intra-day bets
co-move (a cold day wins many at once), so each market-day is one cluster.

Run capped:
    .\\scripts\\run_capped.ps1 <python> scripts\\weather_station_ev_scan.py
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

from weather_confirmation_filter import collect

TAU = 0.05
MIN_BETS = 30          # station needs >= this many fired bets to be a testable cell
CHRONO_CUT = "2026-05-01"

# Known microclimate / mechanism annotations for the 19 settlement ICAOs.
# (mechanism public point models are known to systematically miss; from the
#  bias-offset work KMSY/KDEN/KSEA were live cells.)
MECH = {
    "KSEA": "marine layer / Puget Sound (Pac NW marine)",
    "KSF O": "marine layer / SF Bay",
    "KSFO": "marine layer / SF Bay cool-summer",
    "KLAX": "marine layer / coastal SoCal",
    "KSAN": "marine layer / coastal SoCal",
    "KNYC": "urban heat island (Central Park siting)",
    "KDEN": "Front Range / elevation / downslope",
    "KDFW": "—",
    "KMSY": "humid Gulf / lake-breeze (New Orleans)",
    "KMIA": "coastal subtropical / sea-breeze",
    "KPHX": "desert UHI (Phoenix)",
    "KLAS": "desert basin (Las Vegas)",
    "KATL": "Piedmont UHI (Atlanta)",
    "KORD": "lake-breeze (Lake Michigan)",
    "KMDW": "lake-breeze (Lake Michigan)",
    "KBOS": "coastal NE / sea-breeze",
    "KIAH": "humid Gulf coastal (Houston)",
    "KAUS": "—",
    "KMSP": "continental / radiational cooling",
    "KPHL": "—",
    "KBNA": "—",
    "KSTL": "—",
    "KMCI": "—",
    "KSAT": "—",
}


def fee_arr(p):
    return np.ceil(0.07 * p * (1.0 - p) * 100.0) / 100.0


def wx_emp1_pnl(d):
    """Per-market PnL of the fixed WX-EMP-1 tau=0.05 rule + a 'bet' mask."""
    E, YA, YB, WON = d["E"], d["YA"], d["YB"], d["WON"]
    no_price = 1.0 - YB
    buy_yes = (E - YA) >= TAU
    buy_no = (~buy_yes) & ((YB - E) >= TAU)
    fee_y = fee_arr(YA)
    fee_n = fee_arr(no_price)
    pnl_y = np.where(WON == 1, 1 - YA, -YA) - fee_y
    pnl_n = np.where(WON == 0, 1 - no_price, -no_price) - fee_n
    pnl = np.where(buy_yes, pnl_y, np.where(buy_no, pnl_n, 0.0))
    bet = buy_yes | buy_no
    return pnl, bet


def cluster_se(pnl_bets, dates_bets):
    """Cluster-robust SE of the mean PnL/ct, clustering by market-day.
    Var(mean) = sum_day (sum_{i in day}(pnl_i - mean))^2 / N^2."""
    n = len(pnl_bets)
    if n == 0:
        return float("nan")
    mean = pnl_bets.mean()
    resid = pnl_bets - mean
    sums = {}
    for r, dt in zip(resid, dates_bets):
        sums[dt] = sums.get(dt, 0.0) + r
    var = sum(s * s for s in sums.values()) / (n * n)
    return math.sqrt(max(var, 0.0))


def cell(pnl, bet, mask, dates):
    m = bet & mask
    n = int(m.sum())
    if n == 0:
        return dict(n=0, ndays=0, evct=float("nan"), se=float("nan"), z=float("nan"))
    p = pnl[m]
    dts = dates[m]
    ndays = len(set(dts))
    evct = float(p.mean())
    se = cluster_se(p, dts)
    z = evct / se if se and se > 0 else float("nan")
    return dict(n=n, ndays=ndays, evct=evct, se=se, z=z)


def two_sided_p(z):
    if not np.isfinite(z):
        return float("nan")
    return 2.0 * (1.0 - NormalDist().cdf(abs(z)))


def main():
    d = collect()
    pnl, bet = wx_emp1_pnl(d)
    ST, DATE = d["ST"], d["DATE"]
    dom = np.array([int(s.split("-")[2]) for s in DATE])

    stations = sorted(set(ST))
    tot_bets = int(bet.sum())
    print(f"\n[rule] fixed WX-EMP-1 tau={TAU}; {tot_bets} bets fired of {len(ST)} markets; "
          f"overall EV/ct = {pnl[bet].mean():+.4f}")

    # masks
    full = np.ones(len(ST), dtype=bool)
    chrono_tr = DATE < CHRONO_CUT
    chrono_ho = DATE >= CHRONO_CUT
    even = (dom % 2) == 0
    odd = (dom % 2) == 1

    # determine the multiple-comparisons family: stations with >= MIN_BETS full bets
    rows = []
    for st in stations:
        smask = ST == st
        c_full = cell(pnl, bet, smask, DATE)
        rows.append((st, smask, c_full))
    tested = [r for r in rows if r[2]["n"] >= MIN_BETS]
    n_cells = len(tested)
    alpha = 0.05
    z_bonf = NormalDist().inv_cdf(1.0 - (alpha / n_cells) / 2.0)
    print(f"[multiple-comparisons] {n_cells} stations with >= {MIN_BETS} bets tested "
          f"(family size = {n_cells}). Bonferroni two-sided alpha={alpha} -> per-test "
          f"alpha={alpha/n_cells:.5f}, critical |z| = {z_bonf:.3f} (vs raw 1.960).")

    # full table, sorted by full EV/ct descending (see top of the noise distribution)
    rows_sorted = sorted(rows, key=lambda r: (-(r[2]["evct"] if np.isfinite(r[2]["evct"]) else -9)))
    hdr = (f"{'stn':>5}{'n':>5}{'days':>5}{'EVfull':>9}{'z':>7}{'rawSig':>7}{'bonfSig':>8}"
           f"{'EVtrain':>9}{'EVhold':>9}{'EVeven':>9}{'EVodd':>9}{'+both?':>7}  mechanism")
    print("\n" + "=" * len(hdr.expandtabs()))
    print("PER-STATION EV TABLE (fixed WX-EMP-1 bet, real spring-2026 prices, net fee+spread)")
    print("=" * len(hdr.expandtabs()))
    print(hdr)
    survivors = []
    for st, smask, cf in rows_sorted:
        if cf["n"] == 0:
            continue
        ctr = cell(pnl, bet, smask & chrono_tr, DATE)
        cho = cell(pnl, bet, smask & chrono_ho, DATE)
        cev = cell(pnl, bet, smask & even, DATE)
        cod = cell(pnl, bet, smask & odd, DATE)
        rawsig = np.isfinite(cf["z"]) and abs(cf["z"]) > 1.960
        bonfsig = np.isfinite(cf["z"]) and abs(cf["z"]) > z_bonf
        pos_both = (np.isfinite(ctr["evct"]) and ctr["evct"] > 0
                    and np.isfinite(cho["evct"]) and cho["evct"] > 0)
        low = "(low-n)" if cf["n"] < MIN_BETS else ""
        cand = pos_both and cf["evct"] > 0
        survive = cand and bonfsig
        if survive:
            survivors.append(st)
        def fmt(x):
            return f"{x['evct']:+.4f}" if np.isfinite(x["evct"]) else "   n/a "
        mech = MECH.get(st, "?")
        print(f"{st:>5}{cf['n']:>5}{cf['ndays']:>5}{fmt(cf):>9}"
              f"{(cf['z'] if np.isfinite(cf['z']) else float('nan')):>7.2f}"
              f"{('YES' if rawsig else '-'):>7}{('YES' if bonfsig else '-'):>8}"
              f"{fmt(ctr):>9}{fmt(cho):>9}{fmt(cev):>9}{fmt(cod):>9}"
              f"{('YES' if pos_both else '-'):>7}  {mech} {low}")

    print("\n" + "-" * 60)
    print("VERDICT")
    print("-" * 60)
    print(f"  Cells tested (family): {n_cells} stations.  Bonferroni |z| bar: {z_bonf:.3f}.")
    cands = [st for st, sm, cf in rows
             if cf["n"] >= MIN_BETS
             and cell(pnl, bet, sm & chrono_tr, DATE)["evct"] > 0
             and cell(pnl, bet, sm & chrono_ho, DATE)["evct"] > 0
             and cf["evct"] > 0]
    print(f"  +EV on BOTH chrono train & holdout (candidates): {cands or 'NONE'}")
    print(f"  ...of those, clearing Bonferroni-corrected significance (SURVIVORS): "
          f"{survivors or 'NONE'}")
    if not survivors:
        print("  => No station survives holdout + multiple-comparisons correction. "
              "Consistent with avenue 4: the market is efficient at the station level. "
              "Any positive station EV is the top of a noise distribution, not edge.")
    else:
        print(f"  => {survivors} survive holdout + Bonferroni. CHECK MECHANISM before "
              "calling it edge; an edge with no microclimate story is still suspect.")


if __name__ == "__main__":
    main()
