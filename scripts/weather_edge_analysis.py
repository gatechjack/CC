"""kalshi_weather +EV discovery — deep-corpus calibration + idealized EV backtest.

Substrate: weather_nbm_observations (NBM percentile vectors p10/p20/p50/p70/p90,
sigma, mean) JOINED to weather_forecast_residuals (CLI actual_temp_f), keyed to
the VERIFIED settlement station (registry_yaml). 2021-01-16 .. 2026-05-25.

INVIOLABLE constraints honored here (asserted in code):
  - station = verified registry ICAO (icao_source='registry_yaml' only).
  - ground truth = IEM CLI actual_temp_f (the residuals table's actual).
  - logic_era != 'pre_station_fix' (contamination filter).
  - no look-ahead: forecast cycle_iso strictly < target_iso (asserted).
  - empirical model parameters fit on TRAIN ONLY; holdout frozen.

The deep corpus has NO real Kalshi prices. EV is therefore measured against two
PRICE PROXIES, stated honestly as bounds:
  - SOPHISTICATED market = NBM's own percentile-implied bracket probs (M2). If we
    cannot beat THIS out-of-sample, no learnable edge exists beyond the best free
    public model -> no +EV system. This is the test that matters.
  - NAIVE market = raw NBM Gaussian (M1). Generous upper bound (market trusts raw
    NBM and ignores its known biases/fat tails).
Real-price confirmation against live execution is an explicit FUTURE GATE.

Outcome space = integer degF (CLI settles on integers). Kalshi grid = 1 degF
interior buckets (Bxx.5) + open tails (Txx).

Run capped per CLAUDE.md:
    .\\scripts\\run_capped.ps1 python scripts\\weather_edge_analysis.py
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

DB = "data/trading_corp.db"

# Time split (MANDATED): train 2021-2024, validate 2025, holdout >= 2026.
TRAIN_MAX = "2024-12-31"
VAL_MIN, VAL_MAX = "2025-01-01", "2025-12-31"
HOLD_MIN = "2026-01-01"

# Decision horizon bands (hours before target). NBM cycles at 01/07/13/19z so
# horizons cluster at ~8h, ~32h, ~56h with gaps between. Bands match the clusters.
# One decision per market per band (cycle closest to band midpoint).
HORIZON_BANDS = {
    "day_before": (24.0, 40.0),   # ~32h cluster — primary clean day-before decision
    "morning_of": (1.0, 14.0),    # ~8h cluster — same-day (leak-screened individually)
    "two_day": (44.0, 60.0),      # ~56h cluster
}

MIN_CELL_N = 150              # min train z-samples for a (station,season,kind) empirical cell
SQRT2 = math.sqrt(2.0)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_joined():
    c = sqlite3.connect(DB)
    q = """
    SELECT r.station_id, r.target_date, r.season, r.kind,
           r.cycle_iso, r.target_iso, o.horizon_hours,
           o.temp_p10_f, o.temp_p20_f, o.temp_p50_f, o.temp_p70_f, o.temp_p90_f,
           o.temp_sigma_f, o.temp_mean_f, r.actual_temp_f, r.icao_source
    FROM weather_nbm_observations o
    JOIN weather_forecast_residuals r
      ON o.station_id=r.station_id AND o.kind=r.kind
     AND o.cycle_iso=r.cycle_iso AND o.valid_iso=r.target_iso
    WHERE r.forecast_source='nbm_p50'
      AND r.logic_era!='pre_station_fix'
      AND r.actual_temp_f IS NOT NULL
      AND o.temp_p10_f IS NOT NULL AND o.temp_p90_f IS NOT NULL
      AND o.temp_sigma_f IS NOT NULL AND o.temp_sigma_f > 0
    """
    rows = c.execute(q).fetchall()
    c.close()
    cols = ["station", "tdate", "season", "kind", "cycle", "tiso", "h",
            "p10", "p20", "p50", "p70", "p90", "sig", "mean", "actual", "icao"]
    d = {k: [] for k in cols}
    for row in rows:
        for k, v in zip(cols, row):
            d[k].append(v)
    out = {}
    for k in cols:
        if k in ("station", "tdate", "season", "kind", "cycle", "tiso", "icao"):
            out[k] = np.array(d[k], dtype=object)
        else:
            out[k] = np.array(d[k], dtype=float)
    return out


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def assert_inviolable(d):
    # station verification + clean logic_era already enforced in SQL WHERE.
    assert set(np.unique(d["icao"])) <= {"registry_yaml"}, "non-verified station present"
    leak = sum(1 for cyc, ti in zip(d["cycle"], d["tiso"]) if _parse(cyc) >= _parse(ti))
    print(f"[assert] station-verified + clean logic_era OK on {len(d['actual'])} rows. "
          f"raw-corpus look-ahead rows (cycle>=target, near-settlement): {leak} "
          f"(excluded by horizon-band selection below)")


def enforce_no_leak(sub):
    """Drop any selected decision whose forecast cycle is not strictly before the
    target timestamp. For the day-before bands this should be ~zero."""
    keep = np.array([_parse(c) < _parse(t) for c, t in zip(sub["cycle"], sub["tiso"])])
    dropped = int((~keep).sum())
    if dropped:
        print(f"[no-leak] dropped {dropped}/{len(keep)} selected decisions with "
              f"cycle>=target")
    assert dropped / max(1, len(keep)) < 0.02, "too many look-ahead rows in selection"
    return {k: sub[k][keep] for k in sub}


# ---------------------------------------------------------------------------
# One decision per (station, target_date, kind) within a horizon band:
# pick the cycle whose horizon is closest to the band midpoint.
# ---------------------------------------------------------------------------
def select_decisions(d, band):
    lo, hi = band
    mid = 0.5 * (lo + hi)
    mask = (d["h"] >= lo) & (d["h"] < hi)
    idx = np.where(mask)[0]
    best = {}
    for i in idx:
        key = (d["station"][i], d["tdate"][i], d["kind"][i])
        score = abs(d["h"][i] - mid)
        if key not in best or score < best[key][0]:
            best[key] = (score, i)
    sel = np.array(sorted(v[1] for v in best.values()), dtype=int)
    sub = {k: d[k][sel] for k in d}
    return sub


def split_masks(sub):
    td = sub["tdate"]
    tr = td <= TRAIN_MAX
    va = (td >= VAL_MIN) & (td <= VAL_MAX)
    ho = td >= HOLD_MIN
    return tr, va, ho


# ---------------------------------------------------------------------------
# Models: each returns CDF P(continuous high/low <= x) as a vectorized fn over a
# scalar x given per-row params. We implement bucket_probs(model, edges) instead.
# ---------------------------------------------------------------------------
def _ncdf(z):
    # vectorized standard normal cdf
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / SQRT2))


def norm_cdf_at(x, mu, sd):
    return 0.5 * (1.0 + np.vectorize(math.erf)((x - mu) / (sd * SQRT2)))


def sigma_heuristic(h):
    # production sigma_for_horizon, then sqrt(sig^2 + source_div^2), source_div=2
    base = np.where(h <= 24, 1.5, np.where(h <= 48, 2.5, np.where(h <= 72, 3.5, 5.0)))
    return np.sqrt(base ** 2 + 4.0)


def monotonic_pctls(sub):
    P = np.stack([sub["p10"], sub["p20"], sub["p50"], sub["p70"], sub["p90"]], axis=1)
    P = np.sort(P, axis=1)  # enforce monotone
    return P


def pctl_cdf_at(x, P):
    """Piecewise-linear CDF through (P10..P90)->(.1,.2,.5,.7,.9), linear tail
    extrapolation using the nearest interior slope; clamp [0,1].
    x is per-row array (n,), P is (n,5). Vectorized tails; loop only the middle."""
    qs = np.array([0.10, 0.20, 0.50, 0.70, 0.90])
    n = P.shape[0]
    xss = P.astype(float).copy()
    for j in range(1, 5):  # enforce strictly increasing per row
        bad = xss[:, j] <= xss[:, j - 1]
        xss[bad, j] = xss[bad, j - 1] + 1e-3
    x = np.asarray(x, dtype=float)
    out = np.empty(n)
    below = x <= xss[:, 0]
    above = x >= xss[:, 4]
    s0 = (qs[1] - qs[0]) / (xss[:, 1] - xss[:, 0])
    sN = (qs[4] - qs[3]) / (xss[:, 4] - xss[:, 3])
    out[below] = qs[0] + s0[below] * (x[below] - xss[below, 0])
    out[above] = qs[4] + sN[above] * (x[above] - xss[above, 4])
    mid = ~(below | above)
    for i in np.where(mid)[0]:
        out[i] = np.interp(x[i], xss[i], qs)
    return np.clip(out, 0.0, 1.0)


class EmpiricalZ:
    """Empirical CDF of z=(actual-p50)/nbm_sigma, fit per (station,season,kind) on
    TRAIN. Captures bias + fat tails + skew, with horizon folded into nbm_sigma.
    Fallback to pooled-all when a cell is thin."""

    def __init__(self):
        self.cells = {}
        self.pooled = None

    def fit(self, sub, trmask):
        z = (sub["actual"] - sub["p50"]) / sub["sig"]
        ztr = z[trmask]
        self.pooled = np.sort(ztr)
        st, se, ki = sub["station"][trmask], sub["season"][trmask], sub["kind"][trmask]
        buckets = defaultdict(list)
        for zi, a, b, cc in zip(ztr, st, se, ki):
            buckets[(a, b, cc)].append(zi)
        for k, v in buckets.items():
            if len(v) >= MIN_CELL_N:
                self.cells[k] = np.sort(np.array(v))
        return self

    def cdf_at(self, x, sub):
        """P(high <= x): fraction of cell train-z <= (x-p50)/nbm_sigma, per row."""
        n = len(sub["p50"])
        out = np.empty(n)
        zt = (x - sub["p50"]) / sub["sig"]
        for i in range(n):
            key = (sub["station"][i], sub["season"][i], sub["kind"][i])
            arr = self.cells.get(key, self.pooled)
            out[i] = np.searchsorted(arr, zt[i], side="right") / len(arr)
        return out


# ---------------------------------------------------------------------------
# Bucket probability vectors over integer degF grid around each row's forecast.
# ---------------------------------------------------------------------------
def integer_grid(sub, half_width=14):
    centers = np.round(sub["p50"]).astype(int)
    return centers, half_width


def compute_bias_cells(sub, trmask):
    """Per (station,season,kind) mean residual (actual-p50) on TRAIN. Used to build
    a 'debiased' Gaussian market proxy = a market that knows the station's mean
    bias but assumes Gaussian shape. Isolates shape/tail edge from bias edge."""
    resid = sub["actual"] - sub["p50"]
    rt = resid[trmask]
    st, se, ki = sub["station"][trmask], sub["season"][trmask], sub["kind"][trmask]
    agg = defaultdict(list)
    for r, a, b, cc in zip(rt, st, se, ki):
        agg[(a, b, cc)].append(r)
    cells = {k: float(np.mean(v)) for k, v in agg.items() if len(v) >= MIN_CELL_N}
    pooled = float(np.mean(rt))
    bias = np.array([cells.get((sub["station"][i], sub["season"][i], sub["kind"][i]), pooled)
                     for i in range(len(resid))])
    return bias


def model_cdf(model, x, sub, P=None, emp=None):
    if model == "M0_prod":
        return norm_cdf_at(x, sub["p50"], sigma_heuristic(sub["h"]))
    if model == "M1_nbm_gauss":
        return norm_cdf_at(x, sub["p50"], sub["sig"])
    if model == "M1_debiased":
        return norm_cdf_at(x, sub["p50"] + sub["bias"], sub["sig"])
    if model == "M2_nbm_pctl":
        return pctl_cdf_at(x, P)
    if model == "M3_emp_z":
        return emp.cdf_at(x, sub)
    raise ValueError(model)


def bucket_probs(model, sub, P, emp, hw=14):
    """Return (probs[n, 2*hw+1], centers[n], lo_int) plus outcome bucket index.
    Bucket k center = round(p50)+offset; prob = CDF(k+0.5)-CDF(k-0.5). End buckets
    absorb the tails (first = CDF(lo+0.5), last = 1-CDF(hi-0.5))."""
    centers = np.round(sub["p50"]).astype(int)
    offsets = np.arange(-hw, hw + 1)
    n = len(centers)
    width = len(offsets)
    # cumulative CDF at half-integer edges
    edges = np.arange(-hw - 0.5, hw + 0.5 + 1e-9, 1.0)  # length width+1, relative
    cdf_cols = []
    for e in edges:
        x = centers + e
        cdf_cols.append(model_cdf(model, x, sub, P=P, emp=emp))
    C = np.stack(cdf_cols, axis=1)  # (n, width+1)
    probs = np.diff(C, axis=1)      # (n, width)
    # absorb tails into end buckets
    probs[:, 0] = C[:, 1]                      # = CDF(lo+0.5) - 0 -> include lower tail
    probs[:, -1] = 1.0 - C[:, -2]              # upper tail
    probs = np.clip(probs, 1e-9, 1.0)
    probs = probs / probs.sum(axis=1, keepdims=True)
    out_bucket = np.round(sub["actual"]).astype(int) - centers + hw
    out_bucket = np.clip(out_bucket, 0, width - 1)
    return probs, centers, out_bucket


def rps(probs, out_bucket):
    """Ranked probability score (lower better), averaged."""
    n, w = probs.shape
    cum = np.cumsum(probs, axis=1)
    obs = np.zeros((n, w))
    for i in range(n):
        obs[i, out_bucket[i]:] = 1.0
    return np.mean(np.sum((cum - obs) ** 2, axis=1))


def logloss(probs, out_bucket):
    n = probs.shape[0]
    p = probs[np.arange(n), out_bucket]
    return float(-np.mean(np.log(np.clip(p, 1e-12, 1.0))))


# ---------------------------------------------------------------------------
# Calibration table
# ---------------------------------------------------------------------------
def precompute_probs(sub, P, emp, hw=14):
    """Compute bucket-prob matrix once per model + shared centers/out_bucket."""
    PROBS = {}
    centers = ob = None
    for model in ["M0_prod", "M1_nbm_gauss", "M1_debiased", "M2_nbm_pctl", "M3_emp_z"]:
        probs, centers, ob = bucket_probs(model, sub, P, emp, hw=hw)
        PROBS[model] = probs
    return PROBS, centers, ob


def run_calibration(PROBS, ob, masks, label):
    tr, va, ho = masks
    print(f"\n=== CALIBRATION [{label}]  (train n={tr.sum()}, val n={va.sum()}, hold n={ho.sum()}) ===")
    print(f"{'model':<14}{'split':<8}{'RPS':>9}{'logloss':>10}")
    res = {}
    for model in ["M0_prod", "M1_nbm_gauss", "M1_debiased", "M2_nbm_pctl", "M3_emp_z"]:
        probs = PROBS[model]
        for sname, m in [("train", tr), ("val", va), ("hold", ho)]:
            if m.sum() == 0:
                continue
            r = rps(probs[m], ob[m])
            ll = logloss(probs[m], ob[m])
            res[(model, sname)] = (r, ll)
            print(f"{model:<14}{sname:<8}{r:>9.4f}{ll:>10.4f}")
    return res


# ---------------------------------------------------------------------------
# EV backtest: our model = M3_emp_z. Market proxy = M2 (sophisticated) or M1 (naive).
# Interior 1degF bucket bets (YES/NO) + tail threshold bets (>=X, <=X).
# ---------------------------------------------------------------------------
def kalshi_fee(price, contracts=1):
    # Kalshi: fee = roundup(0.07 * C * P * (1-P)) dollars
    raw = 0.07 * contracts * price * (1.0 - price)
    return math.ceil(raw * 100) / 100.0


def ev_backtest(sub, PROBS, centers, ob, mask, proxy_model, our_model="M3_emp_z",
                tau=0.05, spread=0.02, pmin=0.05, pmax=0.95, label=""):
    """Returns dict of aggregate stats for decisions in mask."""
    our_probs = PROBS[our_model]
    mkt_probs = PROBS[proxy_model]
    n, w = our_probs.shape
    idxs = np.where(mask)[0]

    stats = defaultdict(lambda: [0, 0.0, 0])  # bettype -> [n_bets, pnl, n_wins]
    by_station = defaultdict(lambda: [0, 0.0])
    by_season = defaultdict(lambda: [0, 0.0])

    half = spread / 2.0
    for i in idxs:
        # ---- interior bucket bets ----
        for b in range(w):
            mp = mkt_probs[i, b]
            op = our_probs[i, b]
            if mp < pmin or mp > pmax:
                continue
            edge = op - mp
            won_yes = (ob[i] == b)
            if edge > tau:  # buy YES
                price = mp + half
                if price >= 1.0:
                    continue
                fee = kalshi_fee(price)
                pnl = (1.0 - price if won_yes else -price) - fee
                k = "interior_YES"
                stats[k][0] += 1; stats[k][1] += pnl; stats[k][2] += int(won_yes)
                by_station[sub["station"][i]][0] += 1; by_station[sub["station"][i]][1] += pnl
                by_season[sub["season"][i]][0] += 1; by_season[sub["season"][i]][1] += pnl
            elif edge < -tau:  # buy NO (price = 1-mp)
                price = (1.0 - mp) + half
                if price >= 1.0:
                    continue
                fee = kalshi_fee(price)
                won_no = not won_yes
                pnl = (1.0 - price if won_no else -price) - fee
                k = "interior_NO"
                stats[k][0] += 1; stats[k][1] += pnl; stats[k][2] += int(won_no)
                by_station[sub["station"][i]][0] += 1; by_station[sub["station"][i]][1] += pnl
                by_season[sub["season"][i]][0] += 1; by_season[sub["season"][i]][1] += pnl

        # ---- tail threshold bets (>=X above, <=X below the mode) ----
        # Uses cumulative sums of the integer-bucket prob vectors (end buckets
        # already absorb the far tails), so >=X / <=X aggregate correctly.
        cen = centers[i]
        for off in range(2, 9):  # thresholds 2..8 degF out
            # upper tail: market "high >= cen+off"
            X = cen + off
            bidx = X - (cen - 14)
            if 0 <= bidx < w:
                mge = our_probs[i, bidx:].sum()
                mkge = mkt_probs[i, bidx:].sum()
                if pmin <= mkge <= pmax:
                    edge = mge - mkge
                    won = (np.round(sub["actual"][i]).astype(int) >= X)
                    if edge > tau:
                        price = mkge + half
                        if price < 1.0:
                            fee = kalshi_fee(price)
                            pnl = (1.0 - price if won else -price) - fee
                            k = "tail_high_YES"
                            stats[k][0] += 1; stats[k][1] += pnl; stats[k][2] += int(won)
                            by_station[sub["station"][i]][0] += 1; by_station[sub["station"][i]][1] += pnl
                            by_season[sub["season"][i]][0] += 1; by_season[sub["season"][i]][1] += pnl
            # lower tail: market "high <= cen-off"
            X = cen - off
            bidx = X - (cen - 14)
            if 0 <= bidx < w:
                mle = our_probs[i, :bidx + 1].sum()
                mkle = mkt_probs[i, :bidx + 1].sum()
                if pmin <= mkle <= pmax:
                    edge = mle - mkle
                    won = (np.round(sub["actual"][i]).astype(int) <= X)
                    if edge > tau:
                        price = mkle + half
                        if price < 1.0:
                            fee = kalshi_fee(price)
                            pnl = (1.0 - price if won else -price) - fee
                            k = "tail_low_YES"
                            stats[k][0] += 1; stats[k][1] += pnl; stats[k][2] += int(won)
                            by_station[sub["station"][i]][0] += 1; by_station[sub["station"][i]][1] += pnl
                            by_season[sub["season"][i]][0] += 1; by_season[sub["season"][i]][1] += pnl

    return stats, by_station, by_season


def sub_row(sub, i):
    return {k: sub[k][i:i + 1] for k in sub}


def tail_reliability(sub, PROBS, centers, mask, label):
    """For cold/hot tail thresholds, compare each model's predicted P(<=cen-k) /
    P(>=cen+k) against the ACTUAL realized frequency on this split. Tells us whether
    a model's tail probabilities are calibrated (real skill) or just lucky."""
    print(f"\n--- TAIL RELIABILITY [{label}]  (pred prob vs actual freq) ---")
    idxs = np.where(mask)[0]
    hw = 14
    models = ["M1_nbm_gauss", "M1_debiased", "M2_nbm_pctl", "M3_emp_z"]
    for k in (3, 5):
        # cold tail: high/low <= cen-k
        actual_cold = np.mean([1.0 if np.round(sub["actual"][i]) <= centers[i] - k else 0.0
                               for i in idxs])
        actual_hot = np.mean([1.0 if np.round(sub["actual"][i]) >= centers[i] + k else 0.0
                              for i in idxs])
        line_c = f"  <=cen-{k}: actual={actual_cold:.3f} |"
        line_h = f"  >=cen+{k}: actual={actual_hot:.3f} |"
        for m in models:
            pr = PROBS[m]
            bidx_c = (centers - k) - (centers - hw)  # = hw-k constant
            pc = np.mean(pr[idxs, :hw - k + 1].sum(axis=1))
            ph = np.mean(pr[idxs, hw + k:].sum(axis=1))
            line_c += f" {m.split('_')[-1]}={pc:.3f}"
            line_h += f" {m.split('_')[-1]}={ph:.3f}"
        print(line_c)
        print(line_h)


def print_ev(stats, label):
    print(f"\n--- EV [{label}] ---")
    print(f"{'bettype':<16}{'n':>7}{'pnl$':>10}{'ev/ct':>9}{'winrate':>9}")
    tot_n = 0; tot_pnl = 0.0
    for k in sorted(stats):
        nb, pnl, wins = stats[k]
        if nb == 0:
            continue
        tot_n += nb; tot_pnl += pnl
        print(f"{k:<16}{nb:>7}{pnl:>10.2f}{pnl/nb:>9.4f}{wins/nb:>9.3f}")
    if tot_n:
        print(f"{'TOTAL':<16}{tot_n:>7}{tot_pnl:>10.2f}{tot_pnl/tot_n:>9.4f}")
    return tot_n, tot_pnl


# ---------------------------------------------------------------------------
# FROZEN CANDIDATE SYSTEM: WX-EMP-1
# Decision rules / sizing / live-pricing spec are documented in
# reports/2026-05-28_kalshi_weather_candidate_system_WX-EMP-1.md. This serializes
# the model parameters (fit on TRAIN 2021-2024 ONLY) so the candidate can be
# backtested against REAL Kalshi prices later without re-fitting (the future gate).
FROZEN_CONFIG = {
    "system_id": "WX-EMP-1",
    "fit_train_window": ["2021-01-16", TRAIN_MAX],
    "decision_horizon_band": "day_before",
    "decision_horizon_hours": list(HORIZON_BANDS["day_before"]),
    "primary_model": "M3_emp_z",
    "conservative_variant": "M1_debiased",
    "edge_threshold_tau": 0.05,
    "kelly_fraction": 0.25,
    "liquidity_price_band": [0.05, 0.95],
    "min_cell_n": MIN_CELL_N,
    "sigma_source": "nbm_temp_sigma_f",
    "bucket_width_f": 1.0,
    "outcome": "integer_degF_CLI_settlement",
    "live_pricing_note": (
        "edge computed against the REAL Kalshi quote at decision time "
        "(yes_ask/no_ask dollars); the M1/M2 proxies were a HISTORICAL "
        "stand-in only because the deep corpus has no prices."
    ),
}


def freeze_model(out_path):
    band = HORIZON_BANDS["day_before"]
    d = load_joined()
    assert_inviolable(d)
    sub = select_decisions(d, band)
    sub = enforce_no_leak(sub)
    tr, va, ho = split_masks(sub)
    sub["bias"] = compute_bias_cells(sub, tr)
    emp = EmpiricalZ().fit(sub, tr)

    # per-cell bias (M1_debiased) + empirical z-quantile grid (M3_emp_z), train-only.
    resid = sub["actual"] - sub["p50"]
    z_all = resid / sub["sig"]
    qgrid = list(range(0, 101))  # percentiles 0..100
    cells = {}
    keyset = set(zip(sub["station"][tr], sub["season"][tr], sub["kind"][tr]))
    for (st, se, ki) in sorted(keyset):
        m = tr & (sub["station"] == st) & (sub["season"] == se) & (sub["kind"] == ki)
        if m.sum() < MIN_CELL_N:
            continue
        zc = z_all[m]
        cells[f"{st}|{se}|{ki}"] = {
            "n_train": int(m.sum()),
            "bias_f": float(np.mean(resid[m])),
            "z_quantiles": [round(float(np.percentile(zc, q)), 4) for q in qgrid],
        }
    pooled_z = z_all[tr]
    artifact = {
        "config": FROZEN_CONFIG,
        "z_quantile_percentiles": qgrid,
        "pooled_fallback": {
            "n_train": int(tr.sum()),
            "bias_f": float(np.mean(resid[tr])),
            "z_quantiles": [round(float(np.percentile(pooled_z, q)), 4) for q in qgrid],
        },
        "cells": cells,
        "n_cells": len(cells),
    }
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=1)
    print(f"[freeze] wrote {out_path}: {len(cells)} cells, "
          f"pooled n={int(tr.sum())}, fit window {FROZEN_CONFIG['fit_train_window']}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "freeze":
        out = sys.argv[2] if len(sys.argv) > 2 else "data/weather_emp_model_WX-EMP-1.json"
        freeze_model(out)
        return
    band_name = sys.argv[1] if len(sys.argv) > 1 else "day_before"
    spread = float(sys.argv[2]) if len(sys.argv) > 2 else 0.02
    band = HORIZON_BANDS[band_name]
    print(f"### LOADING corpus, horizon band {band_name}={band} ###")
    d = load_joined()
    assert_inviolable(d)
    sub = select_decisions(d, band)
    sub = enforce_no_leak(sub)
    print(f"[decisions] {len(sub['actual'])} markets (one cycle each) in band {band_name}")
    tr, va, ho = split_masks(sub)
    print(f"[split] train(<= {TRAIN_MAX})={tr.sum()}  val(2025)={va.sum()}  hold(>=2026)={ho.sum()}")

    P = monotonic_pctls(sub)
    sub["bias"] = compute_bias_cells(sub, tr)
    emp = EmpiricalZ().fit(sub, tr)
    print(f"[emp] fit {len(emp.cells)} per-cell empirical CDFs (>= {MIN_CELL_N} train z), "
          f"pooled fallback n={len(emp.pooled)}")

    print("[precompute] bucket-prob matrices for all models ...")
    PROBS, centers, ob = precompute_probs(sub, P, emp)
    run_calibration(PROBS, ob, (tr, va, ho), band_name)

    # Tail reliability on holdout: is M3's tail edge real calibration skill?
    if ho.sum():
        tail_reliability(sub, PROBS, centers, ho, f"{band_name}/hold")

    # EV backtests. Proxies, in order of market sophistication:
    #   M1_nbm_gauss  = NAIVE (raw NBM, ignores bias+fat tails) -> generous upper bound
    #   M1_debiased   = SEMI  (knows station mean bias, assumes Gaussian) -> isolates shape/tail edge
    #   M2_nbm_pctl   = SOPHISTICATED proxy (NBM's own distribution)
    for proxy in ["M1_nbm_gauss", "M1_debiased", "M2_nbm_pctl"]:
        print(f"\n######## EV vs proxy={proxy} (tau=0.05, spread={spread}) ########")
        for sname, m in [("train", tr), ("val", va), ("hold", ho)]:
            if m.sum() == 0:
                continue
            stats, byst, byse = ev_backtest(sub, PROBS, centers, ob, m, proxy,
                                            spread=spread, label=f"{proxy}/{sname}")
            print_ev(stats, f"{proxy}/{sname}")
            if sname == "hold":
                print("   by-season (hold):", {k: f"n={v[0]},pnl={v[1]:.1f}" for k, v in byse.items()})


if __name__ == "__main__":
    main()
