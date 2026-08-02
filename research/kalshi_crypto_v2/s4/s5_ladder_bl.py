"""S5 LADDER — Breeden-Litzenberger consistency + bucket-density vs HAR-RV fit
(2026-08-02, on-disk). READ-ONLY. NO order/placement surface.

The 34,098 ladder strike-snaps (hourly KX{BTC,ETH,SOLE,XRP}, window-open) are
DISJOINT RANGE buckets ([floor,cap], ~$100 wide for BTC; open tails), so each
bucket's YES price IS the implied probability mass -- the market's option-implied
distribution, read directly. The 15m EFFICIENCY findings do NOT transfer here;
this is the last unopened dataset.

B-L CONSISTENCY (per event snapshot, using lab/breeden_litzenberger.py):
  - BOUNDS: each bucket mid in [0,1];
  - SUM-TO-ONE: sum(bucket mids) ~ 1; ARBITRAGEABLE iff sum(ask) < 1 (buy-all
    lock) or sum(bid) > 1 (sell-all lock); otherwise the deviation is
    INSIDE-SPREAD (overround, not tradeable);
  - MONOTONICITY / density >= 0: the cumulative survival p_above(X) must be
    non-increasing in strike (equivalently every bucket mass >= 0).
  Every violation is tallied WITH spread context.

BUCKET DENSITY vs HAR-RV: from the bucket mids (normalized) compute the implied
mean + std over settlement price; convert to an implied 1h return vol and compare
to a trailing realized-vol (HAR-style) forecast at the event open. The ratio
implied/realized is a first read on the ladder's vol risk premium. Evidence only
-- no verdict.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_S4 = os.path.dirname(os.path.abspath(__file__))
_LAB = os.path.join(os.path.dirname(_S4), "lab")
LAB_DB = os.path.join(_LAB, "kcv2_lab.db")
if _LAB not in sys.path:
    sys.path.insert(0, _LAB)
from breeden_litzenberger import check_bucket_sum, check_monotonic_ladder  # noqa: E402

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
LADDER_SERIES = {"BTC": "KXBTC", "ETH": "KXETH", "SOL": "KXSOLE", "XRP": "KXXRP"}
SUM_TOL = 1e-3
WINDOW_SEC = 3600            # hourly ladder horizon


def _ro(db: str = LAB_DB):
    c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _mass(bid, ask, pmean, vol):
    """Best point estimate of a bucket's implied probability.
    A bucket with NO bid (bid<=0) has ~0 probability regardless of its token ask
    -- the far-OTM ask (0.02-0.06) is a market-maker's minimum offer, not a real
    implied prob; using its (bid+ask)/2 across ~180 dead buckets blows up the sum
    and the implied variance. Use the two-sided MID only where someone actually
    bids (bid>0); else the real traded price (if volume>0); else 0."""
    if bid is not None and ask is not None and bid > 0.0 and bid <= ask <= 1.0:
        return (bid + ask) / 2.0
    if pmean is not None and 0.0 < pmean < 1.0 and vol and vol > 0:
        return pmean
    return 0.0


def _center(floor, cap, w):
    if floor is not None and cap is not None:
        return (floor + cap) / 2.0
    if floor is not None:          # upper open tail
        return floor + w / 2.0
    if cap is not None:            # lower open tail
        return cap - w / 2.0
    return None


def _realized_vol_1h(conn, asset: str) -> pd.Series:
    """Trailing 1h realized-vol forecast (HAR-style proxy): rolling std of 1m log
    returns over the last day, scaled to the 1h horizon. Indexed by ts_sec."""
    df = pd.read_sql_query("SELECT ts_ms, close FROM lab_bars_binance WHERE asset=? "
                           "ORDER BY ts_ms", conn, params=(asset,))
    df["lr"] = np.log(df["close"] / df["close"].shift(1))
    df["sigma1m"] = df["lr"].rolling(1440, min_periods=120).std()      # ~1 day of 1m
    df["sigma1h"] = df["sigma1m"] * np.sqrt(60.0)                       # scale to 1h
    df["ts_sec"] = (df["ts_ms"] // 1000).astype("int64")
    return df.set_index("ts_sec")["sigma1h"]


def analyze_asset(asset: str, conn) -> dict:
    series = LADDER_SERIES[asset]
    events = [r[0] for r in conn.execute(
        "SELECT DISTINCT event_ticker FROM lab_kalshi_ladder_snap WHERE asset=? "
        "ORDER BY event_ticker", (asset,)).fetchall()]
    rv1h = _realized_vol_1h(conn, asset)
    rv_ts = rv1h.index.to_numpy()
    rv_val = rv1h.to_numpy()
    sums_mid, n_boundsv, n_sumdev_arb, n_sumdev_inside, n_monov_trade, n_monov_inside = \
        [], 0, 0, 0, 0, 0
    vol_ratios = []
    n_events_used = 0
    for ev in events:
        rows = conn.execute(
            "SELECT floor_strike,cap_strike,yes_bid,yes_ask,price_mean,volume,ref_ts "
            "FROM lab_kalshi_ladder_snap WHERE event_ticker=? ORDER BY "
            "COALESCE(floor_strike, cap_strike)", (ev,)).fetchall()
        widths = [r["cap_strike"] - r["floor_strike"] for r in rows
                  if r["floor_strike"] is not None and r["cap_strike"] is not None]
        if not widths:
            continue
        w = float(np.median(widths))
        buckets = []
        for r in rows:
            m = _mass(r["yes_bid"], r["yes_ask"], r["price_mean"], r["volume"])
            buckets.append({"floor": r["floor_strike"], "cap": r["cap_strike"],
                            "bid": r["yes_bid"], "ask": r["yes_ask"], "mid": m,
                            "center": _center(r["floor_strike"], r["cap_strike"], w)})
        mids = [b["mid"] for b in buckets if b["mid"] is not None]
        if sum(1 for m in mids if m > 0.0) < 3:      # need >=3 ACTIVE buckets
            continue
        n_events_used += 1
        # --- sum-to-one (bounds + normalization) with arb context ---
        sbv = check_bucket_sum(mids, tol=SUM_TOL)
        n_boundsv += sum(1 for x in sbv if x["type"] == "bucket_bounds")
        s_mid = sum(mids)
        sums_mid.append(s_mid)
        if any(x["type"] == "sum_to_one" for x in sbv):
            s_ask = sum(b["ask"] for b in buckets if b["ask"] is not None)
            s_bid = sum(b["bid"] for b in buckets if b["bid"] is not None)
            arb = (s_ask < 1.0 - SUM_TOL) or (s_bid > 1.0 + SUM_TOL)
            if arb:
                n_sumdev_arb += 1
            else:
                n_sumdev_inside += 1
        # --- monotonicity of the survival ladder p_above (density >= 0) ---
        rungs = []
        cum = 0.0
        for b in sorted(buckets, key=lambda x: (x["floor"] is None, x["floor"] or -1e18),
                        reverse=True):                      # high strike -> low
            if b["mid"] is None:
                continue
            cum += b["mid"]
            rungs.append({"strike": b["floor"] if b["floor"] is not None else b["cap"],
                          "p_above": cum, "yes_bid": b["bid"], "yes_ask": b["ask"]})
        rungs = sorted(rungs, key=lambda x: (x["strike"] is None, x["strike"] or -1e18))
        mv = check_monotonic_ladder(rungs)
        for x in mv:
            if x["type"] == "monotonicity":
                if x["inside_spread"]:
                    n_monov_inside += 1
                else:
                    n_monov_trade += 1
        # --- implied moments vs HAR-RV ---
        norm = [(b["center"], b["mid"] / s_mid) for b in buckets
                if b["mid"] is not None and b["center"] is not None and s_mid > 0]
        if len(norm) >= 5 and 0.5 < s_mid < 2.0:
            ctr = np.array([c for c, _ in norm])
            mass = np.array([p for _, p in norm])
            mu = float((ctr * mass).sum())
            var = float((mass * (ctr - mu) ** 2).sum())
            sig_impl = np.sqrt(var)
            if mu > 0 and sig_impl > 0:
                impl_ret_vol = sig_impl / mu                # 1h return vol implied
                rt = int(rows[0]["ref_ts"]) if rows[0]["ref_ts"] else None
                if rt is not None and len(rv_ts):
                    j = np.searchsorted(rv_ts, rt) - 1      # last realized vol before open
                    if 0 <= j < len(rv_val) and np.isfinite(rv_val[j]) and rv_val[j] > 0:
                        vol_ratios.append(impl_ret_vol / rv_val[j])
    sums_mid = np.array(sums_mid) if sums_mid else np.array([np.nan])
    vr = np.array(vol_ratios) if vol_ratios else np.array([np.nan])
    return {
        "asset": asset, "n_events": n_events_used,
        "overround_median": float(np.nanmedian(sums_mid)),
        "overround_p10": float(np.nanpercentile(sums_mid, 10)),
        "overround_p90": float(np.nanpercentile(sums_mid, 90)),
        "n_bounds_viol": n_boundsv,
        "n_sumdev_arb": n_sumdev_arb, "n_sumdev_inside": n_sumdev_inside,
        "n_mono_tradeable": n_monov_trade, "n_mono_inside": n_monov_inside,
        "vol_ratio_median": float(np.nanmedian(vr)), "vol_ratio_n": int(np.isfinite(vr).sum()),
        "vol_ratio_p10": float(np.nanpercentile(vr, 10)), "vol_ratio_p90": float(np.nanpercentile(vr, 90)),
    }


def write_report(results: list[dict], path: str) -> None:
    L = []
    L.append("# S5 Ladder — Breeden-Litzenberger consistency + density vs HAR-RV")
    L.append("")
    L.append("**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; "
             "evidence only — no verdict.")
    L.append("")
    L.append("Hourly ladder snapshots at window-open (disjoint range buckets; bucket "
             "YES mid = implied probability mass). Sum-to-one deviation is "
             "**arbitrageable** only if Σask<1 (buy-all lock) or Σbid>1 (sell-all "
             "lock); else it is **inside-spread** overround. Monotonicity = the "
             "survival p_above(X) non-increasing (density≥0), violations flagged "
             "tradeable vs inside-spread. Implied 1h return vol from the bucket "
             "density vs a trailing HAR-style realized-vol forecast at open.")
    L.append("")
    L.append("### B-L consistency")
    L.append("")
    L.append("| Asset | events | overround Σmid (p10/med/p90) | bounds viol | "
             "sum!=1 arb / inside | monotonicity trade / inside |")
    L.append("|---|---|---|---|---|---|")
    for r in results:
        L.append(f"| {r['asset']} | {r['n_events']} | "
                 f"{r['overround_p10']:.3f} / {r['overround_median']:.3f} / "
                 f"{r['overround_p90']:.3f} | {r['n_bounds_viol']} | "
                 f"{r['n_sumdev_arb']} / {r['n_sumdev_inside']} | "
                 f"{r['n_mono_tradeable']} / {r['n_mono_inside']} |")
    L.append("")
    L.append("### Bucket density vs HAR-RV (implied 1h return vol / realized forecast)")
    L.append("")
    L.append("| Asset | n events | vol ratio (p10 / median / p90) |")
    L.append("|---|---|---|")
    for r in results:
        L.append(f"| {r['asset']} | {r['vol_ratio_n']} | "
                 f"{r['vol_ratio_p10']:.2f} / {r['vol_ratio_median']:.2f} / "
                 f"{r['vol_ratio_p90']:.2f} |")
    L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- **Overround Σmid > 1** is the ladder's total priced probability; the "
             "excess over 1 is the market's vig/spread. **Arbitrageable** sum/"
             "monotonicity violations (Σask<1 / Σbid>1 / tradeable monotonicity) are "
             "the only ones a taker could exploit; inside-spread ones are not.")
    L.append("- **Vol ratio > 1** ⇒ the ladder prices MORE 1h vol than recently "
             "realized (a variance risk premium); < 1 ⇒ less. This is a first HAR-RV "
             "read; a proper HAR fit (lagged day/week/month RV regression) is the "
             "obvious follow-up. Tail buckets are open — centers approximated by the "
             "median bucket width, so the implied σ is a lower bound if mass sits in "
             "the tails.")
    L.append("- The 15m efficiency result does NOT bind here: the ladder is a "
             "different instrument (full distribution, hourly). These are structural "
             "diagnostics, not an edge claim.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def main() -> int:
    print("=" * 70)
    print("  S5 LADDER B-L + DENSITY — kalshi_crypto_v2 (on-disk, no pulls)")
    print("=" * 70)
    conn = _ro()
    results = []
    try:
        for asset in ASSETS:
            r = analyze_asset(asset, conn)
            results.append(r)
            print(f"  {asset}: events={r['n_events']} overround_med={r['overround_median']:.3f} "
                  f"arb_sum={r['n_sumdev_arb']} mono_trade={r['n_mono_tradeable']} "
                  f"vol_ratio_med={r['vol_ratio_median']:.2f} (n={r['vol_ratio_n']})", flush=True)
    finally:
        conn.close()
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_S5_ladder_bl.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
