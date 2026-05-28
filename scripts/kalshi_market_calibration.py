"""Model-free market-efficiency test for kalshi_weather (real prices, holdout).

Independent of any forecast model: does the MARKET's own decision-time price match
realized frequency? A systematic gap (favorite-longshot bias) would be an exploitable
edge with NO forecast at all — the last untested avenue and exactly the "different
bet-direction logic" the mandate invited.

Uses tmp/kalshi_realprice_candles.jsonl (all 14,346 settled markets, B + T strikes).
Decision price = latest valid two-sided quote strictly before target-day 00:00Z
(leak-safe for both daily max & min at every US station). Outcome = Kalshi `result`.

Reports: calibration curve (price bin -> realized YES freq), and the net-of-cost EV of
mechanically betting the side the calibration gap implies (pay the ask + Kalshi fee).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime

import numpy as np

CANDLES = "tmp/kalshi_realprice_candles.jsonl"


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100.0


def decision_quote(candles, target_date_str):
    cutoff = datetime.fromisoformat(target_date_str + "T00:00:00+00:00").timestamp()
    best = None
    for ts, yb, ya, px in candles:
        if yb is None or ya is None:
            continue
        if yb <= 0 and ya >= 1.0:
            continue
        if ts > cutoff:
            continue
        if best is None or ts > best[0]:
            best = (ts, yb, ya)
    return None if best is None else (best[1], best[2])


def main():
    rows = [json.loads(l) for l in open(CANDLES, encoding="utf-8")]
    P, YB, YA, WON, MON = [], [], [], [], []
    for r in rows:
        if r["result"] not in ("yes", "no"):
            continue
        q = decision_quote(r["candles"], r["date"])
        if q is None:
            continue
        yb, ya = q
        mid = 0.5 * (yb + ya)
        if mid <= 0.0 or mid >= 1.0:
            continue
        P.append(mid); YB.append(yb); YA.append(ya)
        WON.append(int(r["result"] == "yes")); MON.append(r["date"][:7])
    P = np.array(P); YB = np.array(YB); YA = np.array(YA); WON = np.array(WON)
    MON = np.array(MON)
    print(f"[load] {len(P)} markets with leak-safe decision quote (B+T strikes, holdout)")

    # ---- calibration curve: market price vs realized YES frequency ----
    print("\n=== MARKET CALIBRATION (decision-time yes-mid vs realized YES freq) ===")
    print(f"{'price bin':>12}{'n':>7}{'mean_px':>9}{'realized':>10}{'gap(real-px)':>13}")
    edges = [0, .05, .10, .20, .30, .40, .50, .60, .70, .80, .90, .95, 1.0]
    overall_gap = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (P >= lo) & (P < hi)
        if m.sum() < 20:
            continue
        mp = P[m].mean(); rf = WON[m].mean()
        print(f"{lo:.2f}-{hi:.2f}".rjust(12) + f"{int(m.sum()):>7}{mp:>9.3f}{rf:>10.3f}"
              f"{rf-mp:>+13.3f}")

    # ---- net-of-cost EV of betting the calibration-implied side ----
    # Favorite-longshot: if longshots (low yes price) are OVERpriced, realized<price ->
    # buy NO on cheap-YES; if favorites underpriced, realized>price -> buy YES on rich-YES.
    # Test mechanically across YES-price thresholds, paying the ask + fee.
    print("\n=== NET-OF-COST EV of mechanical market-structure bets ===")
    no_price = 1.0 - YB
    print(f"{'strategy':<34}{'n':>7}{'pnl$':>10}{'ev/ct':>9}{'winrate':>9}")

    def report(name, mask, side):
        if mask.sum() == 0:
            print(f"{name:<34}{0:>7}"); return
        if side == "yes":
            price = YA[mask]; win = WON[mask] == 1
        else:
            price = no_price[mask]; win = WON[mask] == 0
        fee = np.ceil(0.07 * price * (1 - price) * 100) / 100
        pnl = np.where(win, 1 - price, -price) - fee
        n = int(mask.sum()); tot = float(pnl.sum())
        print(f"{name:<34}{n:>7}{tot:>10.2f}{tot/n:>9.4f}{win.mean():>9.3f}")

    for thr in (0.05, 0.10, 0.15, 0.20):
        report(f"buy NO  when yes_mid < {thr:.2f}", P < thr, "no")
    for thr in (0.80, 0.85, 0.90, 0.95):
        report(f"buy YES when yes_mid > {thr:.2f}", P > thr, "yes")
    # mid-range both sides for completeness
    report("buy YES when 0.40<=mid<0.60", (P >= 0.40) & (P < 0.60), "yes")
    report("buy NO  when 0.40<=mid<0.60", (P >= 0.40) & (P < 0.60), "no")

    # ---- month stability (is any apparent gap stable across the 3 holdout months?) ----
    print("\n=== month stability of the cheapest-longshot fade (yes_mid<0.10, buy NO) ===")
    m = P < 0.10
    for mo in sorted(set(MON[m])):
        mm = m & (MON == mo)
        price = no_price[mm]; win = WON[mm] == 0
        fee = np.ceil(0.07 * price * (1 - price) * 100) / 100
        pnl = np.where(win, 1 - price, -price) - fee
        if mm.sum():
            print(f"  {mo}: n={int(mm.sum())} ev/ct={pnl.sum()/mm.sum():+.4f} winrate={win.mean():.3f}")

    print("\n  (Costs: pay the ask + Kalshi fee. A robust +EV row must be positive AND "
          "stable across months, else it is in-sample noise.)")


if __name__ == "__main__":
    main()
