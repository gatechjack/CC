"""T4 retro-test: align the T3a SFP UP-signals to Kalshi 15-min up/down settled
windows and measure directional accuracy vs. the UP base rate, with a flat-window
bucket (sensitivity 0.02/0.05/0.10%) and a pseudo-EV(candle) RANKING proxy.

STRUCTURAL READ ONLY (n=23): ranks + warns; cannot green-light. No verdict.
pseudo-EV(candle) is TRADE-PRICE-BASED (candle mid), understates the ask you'd
pay -> never EV-at-fill (that is the T2 forward corpus's job). Fees via the
canonical kalshi_fee.

Mapping: an up/down window [T, T+900] sets floor_strike = BRTI(T) at open and
settles YES iff BRTI(T+900) >= floor_strike (i.e., price rose over the 15 min).
A long SFP entry at entry_ts -> the NEXT window opening at ceil(entry/900)*900,
predicting YES. move% = (expiration_value - floor_strike)/floor_strike.

READ-ONLY signed GET, in-memory creds.
Usage: run_capped python research/kalshi_crypto_v2/t4_retro.py
"""
from __future__ import annotations

import csv
import math
import os
import statistics
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, _ROOT)
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402
from trading_corp.agents.strategies._sports_math import kalshi_fee  # noqa: E402

SIGNALS_CSV = os.path.join(_HERE, "signals_retro.csv")
OUT_CSV = os.path.join(_HERE, "t4_alignment.csv")
ASSET_SERIES = {"BTCUSDT": "KXBTC15M", "ETHUSDT": "KXETH15M",
                "SOLUSDT": "KXSOL15M", "XRPUSDT": "KXXRP15M"}
OVERLAP_START = "2026-06-23T00:00:00Z"   # Bitunix 15m bars begin
OVERLAP_END = "2026-08-02T00:00:00Z"
FLAT_THRESHOLDS = [0.0002, 0.0005, 0.0010]  # 0.02% / 0.05% / 0.10%
WIN = 900


def epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def mkt_move(m: dict) -> float | None:
    try:
        fl, ev = float(m["floor_strike"]), float(m["expiration_value"])
        return (ev - fl) / fl if fl else None
    except (TypeError, ValueError, KeyError):
        return None


def base_rate(rest: KalshiRest, series: str) -> tuple[int, float, list[float]]:
    ms = rest.paginated("/markets", "markets",
                        {"series_ticker": series, "status": "settled",
                         "min_close_ts": epoch(OVERLAP_START),
                         "max_close_ts": epoch(OVERLAP_END), "limit": 1000}, 20)
    res = [m for m in ms if m.get("result") in ("yes", "no")]
    up = sum(1 for m in res if m["result"] == "yes")
    moves = [mv for m in res if (mv := mkt_move(m)) is not None]
    return len(res), (up / len(res) if res else float("nan")), moves


def market_at_close(rest: KalshiRest, series: str, close_ts: int) -> dict | None:
    r = rest.get("/markets", {"series_ticker": series, "status": "settled",
                              "min_close_ts": close_ts - 45, "max_close_ts": close_ts + 45,
                              "limit": 10})
    ms = [m for m in r.get("markets", []) if m.get("result") in ("yes", "no")]
    return ms[0] if ms else None


def entry_yes_mid(rest: KalshiRest, series: str, ticker: str, win_open: int) -> float | None:
    """Candle yes-mid at the window's first minute (trade-price proxy)."""
    try:
        cs = rest.get(f"/series/{series}/markets/{ticker}/candlesticks",
                      {"period_interval": 1, "start_ts": win_open - 120, "end_ts": win_open + 240})
    except KalshiAuthError:
        return None
    arr = cs.get("candlesticks", []) or []
    if not arr:
        return None
    cand = min(arr, key=lambda c: abs(c.get("end_period_ts", 0) - (win_open + 60)))
    yb = (cand.get("yes_bid") or {}).get("close_dollars")
    ya = (cand.get("yes_ask") or {}).get("close_dollars")
    try:
        yb, ya = float(yb), float(ya)
    except (TypeError, ValueError):
        return None
    if yb <= 0 and ya <= 0:
        return None
    if yb <= 0 or ya <= 0:
        return max(yb, ya)
    return (yb + ya) / 2.0


def load_signals() -> list[dict]:
    with open(SIGNALS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    try:
        rest = KalshiRest()
    except KalshiAuthError as e:
        print(f"STOP - creds: {e}")
        return 2
    print(f"creds source={rest.source}\n")

    print("Fetching UP base rate over overlap (2026-06-23..08-01), per asset ...")
    base: dict[str, dict] = {}
    for a, s in ASSET_SERIES.items():
        n, br, moves = base_rate(rest, s)
        base[a] = {"n": n, "up_rate": br, "moves": moves}
        print(f"  {a:8} {s:10} n={n:5} UP_base={br:.3f}")

    signals = load_signals()
    print(f"\nAligning {len(signals)} SFP UP-signals to next 15-min window ...")
    recs = []
    for sg in signals:
        asset = sg["asset"]
        series = ASSET_SERIES[asset]
        entry = int(sg["entry_ts_ms"]) // 1000
        win_open = int(math.ceil(entry / WIN) * WIN)
        close_ts = win_open + WIN
        m = market_at_close(rest, series, close_ts)
        rec = {"asset": asset, "sfp_mode": sg["sfp_mode"], "bos_tf": sg["bos_tf"],
               "entry_utc": sg["entry_utc"], "win_open_ts": win_open,
               "market": None, "result": None, "move_pct": None, "correct": None,
               "yes_price": None, "pseudo_ev": None}
        if m:
            rec["market"] = m["ticker"]
            rec["result"] = m["result"]
            mv = mkt_move(m)
            rec["move_pct"] = mv
            rec["correct"] = 1 if m["result"] == "yes" else 0   # UP-signal predicts YES
            yp = entry_yes_mid(rest, series, m["ticker"], win_open)
            if yp is not None:
                rec["yes_price"] = round(yp, 4)
                payoff = 1.0 if m["result"] == "yes" else 0.0
                rec["pseudo_ev"] = round(payoff - yp - kalshi_fee(1, yp), 4)
        recs.append(rec)

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)

    aligned = [r for r in recs if r["result"] is not None]
    print(f"aligned {len(aligned)}/{len(recs)} signals to a settled window "
          f"({len(recs) - len(aligned)} unmatched)\n")

    # overall UP base rate (pooled) + per-asset
    pooled_n = sum(base[a]["n"] for a in base)
    pooled_up = sum(base[a]["n"] * base[a]["up_rate"] for a in base if not math.isnan(base[a]["up_rate"]))
    pooled_base = pooled_up / pooled_n if pooled_n else float("nan")

    print("=" * 68)
    print("SIGNAL ACCURACY vs UP BASE RATE  (flat-window sensitivity)")
    print("=" * 68)
    for thr in FLAT_THRESHOLDS:
        directional = [r for r in aligned if r["move_pct"] is not None and abs(r["move_pct"]) >= thr]
        flat = [r for r in aligned if r["move_pct"] is not None and abs(r["move_pct"]) < thr]
        n = len(directional)
        acc = statistics.mean(r["correct"] for r in directional) if n else float("nan")
        evs = [r["pseudo_ev"] for r in directional if r["pseudo_ev"] is not None]
        ev_mean = statistics.mean(evs) if evs else float("nan")
        skill = acc - pooled_base if n else float("nan")
        flat_acc = statistics.mean(r["correct"] for r in flat) if flat else float("nan")
        print(f"\nflat threshold |move| < {thr*100:.2f}%   (flat={len(flat)}, directional n={n})")
        print(f"  directional accuracy (predict UP=YES): {acc:.3f}")
        print(f"  pooled UP base rate:                   {pooled_base:.3f}")
        print(f"  signal skill (accuracy - base):        {skill:+.3f}")
        print(f"  pseudo-EV(candle) mean [ranking proxy, fees in]: "
              f"{ev_mean:+.4f}  (n_priced={len(evs)})")
        print(f"  EXCLUDED flat bucket: n={len(flat)}, accuracy={flat_acc:.3f}")

    print("\n" + "=" * 68)
    print("BREAKDOWN at 0.05% flat threshold  (per asset x mode x bos_tf)")
    print("=" * 68)
    thr = 0.0005
    directional = [r for r in aligned if r["move_pct"] is not None and abs(r["move_pct"]) >= thr]
    print(f"{'asset':8} {'mode':12} {'bos':4} {'n':>3} {'acc':>5} {'base':>5} {'skill':>6} {'pEV(candle)':>11}")
    print("-" * 60)
    keys = sorted({(r["asset"], r["sfp_mode"], r["bos_tf"]) for r in directional})
    for a, mode, tf in keys:
        grp = [r for r in directional if (r["asset"], r["sfp_mode"], r["bos_tf"]) == (a, mode, tf)]
        acc = statistics.mean(r["correct"] for r in grp)
        br = base[a]["up_rate"]
        evs = [r["pseudo_ev"] for r in grp if r["pseudo_ev"] is not None]
        ev = statistics.mean(evs) if evs else float("nan")
        print(f"{a:8} {mode:12} {tf:4} {len(grp):>3} {acc:>5.2f} {br:>5.2f} "
              f"{acc-br:>+6.2f} {ev:>+11.4f}")

    print(f"\nper-signal detail -> {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
