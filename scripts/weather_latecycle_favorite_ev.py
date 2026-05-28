"""Late-cycle ULTRA-HIGH-CONFIDENCE favorite-buying test (model-free, real prices).

Hypothesis (Board): at very late cycle AND very high confidence (implied >= 0.90),
are favorites underpriced enough to clear fees + spread + tail risk? Bands:
0.90-0.93, 0.93-0.96, 0.96-0.99 (+ 0.99-1.0 reported separately).

This is DISTINCT from the broader favorite-longshot scan (kalshi_market_calibration.py)
because it isolates the LATE + ULTRA-HIGH-CONFIDENCE subset, and from the WX-EMP-1
real-price gate (weather_realprice_ev.py) because it is MODEL-FREE: no NBM, no forecast.
Decision = "buy the favored side as a taker (pay the ask) when its implied >= 0.90 at a
late candle"; outcome = Kalshi settlement.

DATA LIMITATION (surfaced, not hidden): tmp/kalshi_realprice_candles.jsonl caps candles
at target-midnight + 18h UTC and stores NO market close timestamp. So "final 6-12h before
SETTLEMENT" is NOT directly available. We proxy "late cycle" by offset-from-target-midnight
windows; the latest available (midday_12_18 = d0+[12,18]h UTC ~= midday/early-afternoon
local) is the closest-to-settlement price the corpus holds. For daily_min (realized at
dawn) this window is genuinely post-realization "last-mile"; for daily_max (peak mid-
afternoon local) it can precede full realization. Reported per-kind so the reader can tell.

NO leak: the entry uses only the price available at that candle; settlement is the real
outcome. Buying a near-determined favorite is a real tradeable scenario, not look-ahead.

Costs: pay the ASK (taker). Kalshi fee = ceil(0.07*p*(1-p)*100)/100 per contract. The
half-spread (ask-mid) IS the slippage and is charged by construction (band by mid, fill
at ask). Break-even WR = fill + fee.

Splits (real prices are spring-2026 only; no train/validate history): chronological
half (by date) AND even/odd target-day. A band's edge must survive BOTH to be flagged.

Run capped: .\\scripts\\run_capped.ps1 python scripts\\weather_latecycle_favorite_ev.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

CANDLES = "tmp/kalshi_realprice_candles.jsonl"

# offset-from-target-midnight (UTC) windows, hours
WINDOWS = [
    ("overnight_00_06", 0.0, 6.0),
    ("morning_06_12", 6.0, 12.0),
    ("midday_12_18", 12.0, 18.0),   # latest available = closest-to-settlement proxy
]
# bands on the FAVORED side's implied (mid)
BANDS = [(0.90, 0.93), (0.93, 0.96), (0.96, 0.99), (0.99, 1.0001)]


def fee(p):
    return math.ceil(0.07 * p * (1.0 - p) * 100) / 100.0


def band_of(imp):
    for lo, hi in BANDS:
        if lo <= imp < hi:
            return f"{lo:.2f}-{min(hi,1.0):.2f}"
    return None


def latest_in_window(candles, t0, lo_h, hi_h):
    """Latest valid two-sided quote with offset in [lo_h, hi_h) hours from target midnight."""
    best = None
    for ts, yb, ya, px in candles:
        if yb is None or ya is None:
            continue
        if yb <= 0 and ya >= 1.0:        # empty-book sentinel
            continue
        off = (ts - t0) / 3600.0
        if off < lo_h or off >= hi_h:
            continue
        if best is None or ts > best[0]:
            best = (ts, yb, ya)
    return best


def make_bet(yb, ya):
    """Return (side, implied, fill) for the favored side if implied>=0.90, else None.
    YES favorite: buy YES at ask=ya. NO favorite: buy NO at no_ask = 1-yb."""
    mid = 0.5 * (yb + ya)
    if mid >= 0.90:
        return ("YES", mid, ya)
    if mid <= 0.10:
        return ("NO", 1.0 - mid, 1.0 - yb)
    return None


def main():
    rows = [json.loads(l) for l in open(CANDLES, encoding="utf-8")]
    rows = [r for r in rows if r.get("result") in ("yes", "no") and r.get("candles")]
    print(f"[load] {len(rows)} settled yes/no markets with candles")

    # bet records: dict per window
    recs = []  # each: (window, side, band, kind, date, day_int, implied, fill, win, pnl_net, pnl_gross, spread)
    for r in rows:
        t0 = datetime.fromisoformat(r["date"] + "T00:00:00+00:00").timestamp()
        day_int = int(datetime.fromisoformat(r["date"] + "T00:00:00+00:00").toordinal())
        won_yes = (r["result"] == "yes")
        for wname, lo_h, hi_h in WINDOWS:
            c = latest_in_window(r["candles"], t0, lo_h, hi_h)
            if c is None:
                continue
            _, yb, ya = c
            bet = make_bet(yb, ya)
            if bet is None:
                continue
            side, implied, fill = bet
            b = band_of(implied)
            if b is None:
                continue
            win = won_yes if side == "YES" else (not won_yes)
            f = fee(fill)
            pnl_gross = (1.0 - fill) if win else (-fill)
            pnl_net = pnl_gross - f
            recs.append({
                "window": wname, "side": side, "band": b, "kind": r["kind"],
                "date": r["date"], "day": day_int, "implied": implied, "fill": fill,
                "win": int(win), "pnl_net": pnl_net, "pnl_gross": pnl_gross,
                "spread": ya - yb,
            })
    print(f"[bets] {len(recs)} favorite-side entries (implied>=0.90) across windows\n")

    def summarize(sub, label, indent=""):
        if not sub:
            return
        n = len(sub)
        ndays = len(set(x["date"] for x in sub))
        imp = np.array([x["implied"] for x in sub])
        fillv = np.array([x["fill"] for x in sub])
        feev = np.array([fee(x["fill"]) for x in sub])
        win = np.array([x["win"] for x in sub])
        net = np.array([x["pnl_net"] for x in sub])
        gross = np.array([x["pnl_gross"] for x in sub])
        spr = np.array([x["spread"] for x in sub])
        wr = win.mean()
        be_price = fillv.mean()            # price/payout break-even (ignore fee)
        be_full = (fillv + feev).mean()    # break-even incl fee
        losses = net[win == 0]
        nloss = int((win == 0).sum())
        meanloss = float(losses.mean()) if nloss else 0.0
        se_days = net.std() / math.sqrt(max(1, ndays))
        flag = ""
        if net.mean() > 2 * se_days and ndays >= 10 and net.mean() > 0:
            flag = "  <<ROBUST+EV?"
        elif net.mean() > 0:
            flag = "  (+ev weak)"
        print(f"{indent}{label:<22} n={n:<5} nd={ndays:<3} "
              f"impl={imp.mean():.3f} WR={wr:.3f} (be_px={be_price:.3f} be+fee={be_full:.3f}) "
              f"gap={wr-be_full:+.3f} | gross/ct={gross.mean():+.4f} net/ct={net.mean():+.4f} "
              f"2SE={2*se_days:.4f} | losses={nloss} meanloss={meanloss:+.3f} "
              f"tot=${net.sum():+.2f} medspr={np.median(spr):.3f}{flag}")

    # ============ MAIN GRID: window x band (all favorites; then YES-only) ============
    print("=" * 140)
    print("GRID 1 — ALL FAVORITES (YES-fav = bucket likely to hit; NO-fav = longshot-fade), by window x band")
    print("  WR=actual settle rate of favored side; be+fee=break-even WR after taker-ask + Kalshi fee; gap=WR-(be+fee)=net edge")
    print("=" * 140)
    for wname, _, _ in WINDOWS:
        wsub = [x for x in recs if x["window"] == wname]
        print(f"\n[{wname}]  (n={len(wsub)})")
        for lo, hi in BANDS:
            b = f"{lo:.2f}-{min(hi,1.0):.2f}"
            summarize([x for x in wsub if x["band"] == b], f"band {b}", indent="  ")

    # ============ GRID 2: YES-favorites only (the bucket-likely-to-hit = matches the trade) ============
    print("\n" + "=" * 140)
    print("GRID 2 — YES-FAVORITES ONLY (implied YES >= 0.90: a 1F bucket the market thinks WILL contain the temp)")
    print("=" * 140)
    for wname, _, _ in WINDOWS:
        wsub = [x for x in recs if x["window"] == wname and x["side"] == "YES"]
        print(f"\n[{wname}]  (n={len(wsub)})")
        for lo, hi in BANDS:
            b = f"{lo:.2f}-{min(hi,1.0):.2f}"
            summarize([x for x in wsub if x["band"] == b], f"band {b}", indent="  ")

    # ============ GRID 3: NO-favorites only (longshot-fade) ============
    print("\n" + "=" * 140)
    print("GRID 3 — NO-FAVORITES ONLY (implied NO >= 0.90: fading a longshot 1F bucket)")
    print("=" * 140)
    for wname, _, _ in WINDOWS:
        wsub = [x for x in recs if x["window"] == wname and x["side"] == "NO"]
        print(f"\n[{wname}]  (n={len(wsub)})")
        for lo, hi in BANDS:
            b = f"{lo:.2f}-{min(hi,1.0):.2f}"
            summarize([x for x in wsub if x["band"] == b], f"band {b}", indent="  ")

    # ============ KIND split on the late window (daily_min = post-realization last-mile) ============
    print("\n" + "=" * 140)
    print("GRID 4 — late window (midday_12_18) x band x kind  [daily_min @ this offset is post-dawn = purest last-mile]")
    print("=" * 140)
    late = [x for x in recs if x["window"] == "midday_12_18"]
    for kind in ("daily_min", "daily_max"):
        ksub = [x for x in late if x["kind"] == kind]
        print(f"\n[{kind}]  (n={len(ksub)})")
        for lo, hi in BANDS:
            b = f"{lo:.2f}-{min(hi,1.0):.2f}"
            summarize([x for x in ksub if x["band"] == b], f"band {b}", indent="  ")

    # ============ HOLDOUT SPLITS on the late window, all favorites ============
    print("\n" + "=" * 140)
    print("HOLDOUT — late window (midday_12_18), all favorites. Edge must survive BOTH splits to be real.")
    print("=" * 140)
    late_dates = sorted(set(x["date"] for x in late))
    mid_idx = len(late_dates) // 2
    train_dates = set(late_dates[:mid_idx])
    hold_dates = set(late_dates[mid_idx:])
    print(f"\nChronological: TRAIN {late_dates[0]}..{late_dates[mid_idx-1]} ({len(train_dates)}d) | "
          f"HOLDOUT {late_dates[mid_idx]}..{late_dates[-1]} ({len(hold_dates)}d)")
    for lo, hi in BANDS:
        b = f"{lo:.2f}-{min(hi,1.0):.2f}"
        print(f"  -- band {b} --")
        summarize([x for x in late if x["band"] == b and x["date"] in train_dates], "TRAIN(1st half)", indent="    ")
        summarize([x for x in late if x["band"] == b and x["date"] in hold_dates], "HOLDOUT(2nd half)", indent="    ")
    print("\nEven/odd target-day:")
    for lo, hi in BANDS:
        b = f"{lo:.2f}-{min(hi,1.0):.2f}"
        print(f"  -- band {b} --")
        summarize([x for x in late if x["band"] == b and x["day"] % 2 == 0], "EVEN days", indent="    ")
        summarize([x for x in late if x["band"] == b and x["day"] % 2 == 1], "ODD days", indent="    ")

    # ============ AGGREGATE last-mile favorites overall ============
    print("\n" + "=" * 140)
    print("AGGREGATE — all late-window (midday_12_18) favorites pooled, and YES-fav pooled")
    print("=" * 140)
    summarize(late, "ALL fav (late)", indent="  ")
    summarize([x for x in late if x["side"] == "YES"], "YES-fav (late)", indent="  ")
    summarize([x for x in late if x["side"] == "NO"], "NO-fav (late)", indent="  ")


if __name__ == "__main__":
    main()
