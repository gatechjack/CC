"""Intraday morning-obs NOWCAST edge test for kalshi_weather daily HIGH.

Hypothesis: at a mid-morning decision (16Z), accumulated ASOS obs constrain the
day's CLI high. If the Kalshi market is SLOW to incorporate that obs, a nowcast
beats the market -> real edge. If the market already prices the obs -> no edge.

Model (frozen, train 2021-2024 ONLY): residual r = CLI_high - obs_temp(16Z),
empirical CDF per (station, season). Holdout = spring 2026 (real prices).
Leak-safe: obs strictly <= 16Z; CLI high realized afternoon; market price read at
16Z. NWS CLI = settlement truth; ASOS used only as a forecast feature (constraint
honored). Costs: pay the ask + Kalshi fee.

Run capped: .\\scripts\\run_capped.ps1 python scripts\\weather_nowcast_ev.py
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DB = "data/trading_corp.db"
ASOS = "tmp/asos_decision_temps.jsonl"
CANDLES = "tmp/kalshi_realprice_candles.jsonl"
DEC_HOUR = 16          # 16Z decision
TRAIN_MAX = "2024-12-31"
HOLD_MIN = "2026-01-01"
TAU = 0.05
SQRT2 = math.sqrt(2.0)


def derive_season(month):
    if month in (12, 1, 2): return "winter"
    if month in (3, 4, 5): return "spring"
    if month in (6, 7, 8): return "summer"
    return "fall"


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100.0


def load_cli_high():
    """(station,date) -> CLI daily_max actual (settlement truth), clean rows only."""
    c = sqlite3.connect(DB)
    q = ("SELECT DISTINCT station_id, target_date, actual_temp_f FROM "
         "weather_forecast_residuals WHERE kind='daily_max' AND forecast_source='nbm_p50' "
         "AND logic_era!='pre_station_fix' AND actual_temp_f IS NOT NULL")
    out = {}
    for st, d, a in c.execute(q):
        out[(st, d)] = float(a)
    c.close()
    return out


def load_obs():
    out = {}
    for l in open(ASOS, encoding="utf-8"):
        r = json.loads(l)
        v = r.get(f"t{DEC_HOUR}")
        if v is not None:
            out[(r["icao"], r["date"])] = float(v)
    return out


def price_at_hour(candles, date_str):
    """Latest valid two-sided quote at/just-before DEC_HOUR Z on the target date."""
    cutoff = datetime(int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10]),
                      DEC_HOUR, 5, tzinfo=timezone.utc).timestamp()
    floor_ts = cutoff - 6 * 3600
    best = None
    for ts, yb, ya, px in candles:
        if yb is None or ya is None or (yb <= 0 and ya >= 1.0):
            continue
        if ts < floor_ts or ts > cutoff:
            continue
        if best is None or ts > best[0]:
            best = (ts, yb, ya)
    return None if best is None else (best[1], best[2])


def main():
    cli = load_cli_high()
    obs = load_obs()
    print(f"[load] CLI daily_max {len(cli)} (station,date); ASOS t{DEC_HOUR}Z {len(obs)}")

    # ---- train residual r = CLI_high - obs(16Z), per (station,season), 2021-2024 ----
    cells = defaultdict(list)
    for (st, d), a in cli.items():
        if d > TRAIN_MAX and not (d >= HOLD_MIN):
            pass
        o = obs.get((st, d))
        if o is None:
            continue
        se = derive_season(int(d[5:7]))
        if d <= TRAIN_MAX:
            cells[(st, se)].append(a - o)
    cell_arr = {k: np.sort(np.array(v)) for k, v in cells.items() if len(v) >= 150}
    pooled = np.sort(np.array([r for v in cells.values() for r in v]))
    print(f"[train] {len(cell_arr)} (station,season) residual cells (>=150), pooled n={len(pooled)}")

    def cdf_r(x, st, se):
        arr = cell_arr.get((st, se), pooled)
        return float(np.searchsorted(arr, x, side="right") / len(arr))

    # ---- holdout: daily_max B-markets w/ real 16Z price ----
    rows = [json.loads(l) for l in open(CANDLES, encoding="utf-8")]
    M, NC, YB, YA, WON, ST, DATE = [], [], [], [], [], [], []
    # B resolution rule confirmed 'incl' upstream
    for r in rows:
        if r["kind"] != "daily_max" or r["floor"] is None or r["cap"] is None:
            continue
        if r["result"] not in ("yes", "no") or r["date"] < HOLD_MIN:
            continue
        o = obs.get((r["icao"], r["date"]))
        if o is None:
            continue
        pa = price_at_hour(r["candles"], r["date"])
        if pa is None:
            continue
        yb, ya = pa
        mid = 0.5 * (yb + ya)
        if mid < 0.05 or mid > 0.95:
            continue
        se = derive_season(int(r["date"][5:7]))
        p_nc = cdf_r(r["cap"] + 0.5 - o, r["icao"], se) - cdf_r(r["floor"] - 0.5 - o, r["icao"], se)
        p_nc = min(1.0, max(0.0, p_nc))
        M.append(mid); NC.append(p_nc); YB.append(yb); YA.append(ya)
        WON.append(int(r["result"] == "yes")); ST.append(r["icao"]); DATE.append(r["date"])
    M = np.array(M); NC = np.array(NC); YB = np.array(YB); YA = np.array(YA)
    WON = np.array(WON); ST = np.array(ST); DATE = np.array(DATE)
    print(f"[holdout] {len(M)} daily_max B-markets w/ real 16Z quote + obs")
    if len(M) == 0:
        return

    # ---- is the nowcast sharper than the market at 16Z? (Brier) ----
    print("\n=== NOWCAST vs MARKET at 16Z (Brier vs realized; lower=better) ===")
    print(f"  Brier(market)  = {np.mean((M-WON)**2):.4f}")
    print(f"  Brier(nowcast) = {np.mean((NC-WON)**2):.4f}")
    print(f"  mean|nowcast-market| = {np.mean(np.abs(NC-M)):.4f}   "
          f"corr = {np.corrcoef(NC,M)[0,1]:.3f}")
    print("  (if Brier(nowcast) < Brier(market) by a margin, the market lags the obs.)")

    # ---- EV: nowcast bet rule at real prices, tau sweep ----
    no_price = 1.0 - YB
    print("\n=== NOWCAST real-price EV (daily_max, holdout spring-2026) — tau sweep ===")
    print(f"{'tau':>5}{'n':>7}{'YES':>6}{'NO':>6}{'pnl$':>10}{'ev/ct':>9}{'winrate':>9}")
    for tau in (0.03, 0.05, 0.08, 0.12, 0.18):
        buy_yes = (NC - YA) >= tau
        buy_no = (~buy_yes) & ((YB - NC) >= tau)
        fee_y = np.ceil(0.07 * YA * (1 - YA) * 100) / 100
        fee_n = np.ceil(0.07 * no_price * (1 - no_price) * 100) / 100
        pnl = (buy_yes * (np.where(WON == 1, 1 - YA, -YA) - fee_y) +
               buy_no * (np.where(WON == 0, 1 - no_price, -no_price) - fee_n))
        n = int(buy_yes.sum() + buy_no.sum())
        if n == 0:
            print(f"{tau:>5.2f}{0:>7}"); continue
        wins = int((buy_yes & (WON == 1)).sum() + (buy_no & (WON == 0)).sum())
        ndays = len(set(DATE[buy_yes | buy_no]))
        se = pnl[buy_yes | buy_no].std() / math.sqrt(max(1, ndays))
        print(f"{tau:>5.2f}{n:>7}{int(buy_yes.sum()):>6}{int(buy_no.sum()):>6}"
              f"{pnl.sum():>10.2f}{pnl.sum()/n:>9.4f}{wins/n:>9.3f}   (2*SE={2*se:.4f})")

    print("\n  Verdict: nowcast +EV only if EV>0 AND > 2*SE AND Brier(nowcast)<Brier(market).")


if __name__ == "__main__":
    main()
