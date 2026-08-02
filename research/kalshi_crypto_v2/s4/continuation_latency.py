"""LATENCY / DECAY sensitivity for the executable continuation edge (2026-08-02,
on-disk). READ-ONLY. NO order/placement surface.

continuation_exec.py entered at the qualifying minute m itself -- but the
qualifying MOVE only completes at minute m's CLOSE, while the minute-m taker
price (price_high) can PREDATE it (occur earlier in the minute). That is an
intra-minute ordering risk: the backtest may be filling at a price recorded
before the signal was knowable.

This resolves it by DELAYING entry to minute m+1 / m+2 / m+3 -- candles that lie
STRICTLY AFTER minute m's close, so the fill price is unambiguously post-signal.
The decay curve of the edge across delay = 0,1,2,3 is the test:
  - edge roughly persists at delay >= 1  -> a real multi-minute continuation
    (not an intra-minute artifact), and it tolerates execution latency;
  - edge collapses at delay = 1          -> it was intra-minute ordering / a
    single-tick effect not harvestable with any realistic latency.

Same discipline as continuation_exec: chronological holdout, both legs, fees,
maker fill_rate + adverse-selection, threshold band. ★ T5 basis caveat carries
(move = Binance, settlement = RTI). Evidence only -- no verdict.
"""
from __future__ import annotations

import os
import sys

_S4 = os.path.dirname(os.path.abspath(__file__))
if _S4 not in sys.path:
    sys.path.insert(0, _S4)

from ev_forensic import (  # noqa: E402
    taker_price, maker_fill, realized, _agg, _new_macc, _finalize_maker, _valid_price,
)
from continuation_exec import (  # noqa: E402  reuse loaders + config, leave its results intact
    ASSETS, THRESHOLDS, PRIMARY, HOLDOUT_FRAC, load_windows, _cand_at, _ro,
)

DELAYS = [0, 1, 2, 3]


def eval_delay(windows: list[dict], thr: float, delay: int) -> dict:
    """Continuation at threshold `thr`, entering `delay` minutes AFTER the
    qualifying minute (delay=0 == enter at the qualifying minute)."""
    taker_traded, taker_quote = [], []
    mk = _new_macc()
    n_trades = win = n_up = n_down = 0
    for w in windows:
        ot, ct, cands, bcl, u_open = (w["open_ts"], w["close_ts"], w["cands"],
                                      w["bcl"], w["u_open"])
        qm = side = None
        for m in (1, 2, 3):                          # qualify on the move at minute m
            u_m = bcl.get(ot + m * 60)
            if u_m is None:
                continue
            move = (u_m - u_open) / u_open
            if abs(move) >= thr:
                qm = m
                side = "yes" if move > 0 else "no"
                break
        if qm is None:
            continue
        ec = _cand_at(cands, ot, qm + delay)          # ENTER delay minutes later (post-signal)
        if ec is None:
            continue
        n_trades += 1
        n_up += side == "yes"
        n_down += side == "no"
        y = w["y"]
        won = (y == 1) if side == "yes" else (y == 0)
        if won:
            win += 1
        tp = taker_price(side, ec, "traded")
        if _valid_price(tp):
            taker_traded.append(realized(tp, won))
        tq = taker_price(side, ec, "quote")
        if _valid_price(tq):
            taker_quote.append(realized(tq, won))
        later = [c for c in cands if ec["ts"] < c["ts"] <= ct]
        filled, fprice, fts = maker_fill(side, ec, later, rest_kind="traded")
        if _valid_price(fprice):
            mk["n_attempt"] += 1
            if filled:
                mk["n_fill"] += 1
                pnl = realized(fprice, won)
                mk["real"].append(pnl)
                mk["per_attempt"].append(pnl)
                mk["fill_won"].append(1 if won else 0)
                mk["fill_min"].append((fts - ot) / 60.0 if fts is not None else float("nan"))
            else:
                mk["per_attempt"].append(0.0)
                mk["unfill_won"].append(1 if won else 0)
    return {"thr": thr, "delay": delay, "n_trades": n_trades, "n_up": n_up, "n_down": n_down,
            "taker_win_rate": (win / n_trades) if n_trades else None,
            "taker_traded": _agg(taker_traded), "taker_quote": _agg(taker_quote),
            "maker": _finalize_maker(mk)}


def run_asset(asset: str, conn) -> dict:
    print(f"\n== {asset} ==", flush=True)
    windows = load_windows(asset, conn)
    windows.sort(key=lambda w: w["open_ts"])
    cut = int(len(windows) * (1 - HOLDOUT_FRAC))
    hold = windows[cut:]
    out = {"asset": asset, "n_windows": len(windows), "holdout": {}}
    for thr in THRESHOLDS:
        out["holdout"][thr] = {d: eval_delay(hold, thr, d) for d in DELAYS}
    # console: primary threshold decay
    row = out["holdout"][PRIMARY]
    s = " ".join(f"d{d}={_m(row[d]['taker_traded'])}" for d in DELAYS)
    print(f"  holdout thr=0.10% taker@traded decay: {s}", flush=True)
    return out


def _m(a):
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"{a['mean']:+.3f}(t{a['mean']/se:+.1f})" if se else f"{a['mean']:+.3f}"


def _pct(x): return "n/a" if x is None else f"{x*100:.1f}%"


def write_report(results: list[dict], path: str) -> None:
    L = []
    L.append("# S4 Continuation — Latency / Decay Sensitivity")
    L.append("")
    L.append("**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; "
             "evidence only — no verdict.")
    L.append("")
    L.append("Same continuation rule as `continuation_exec` (buy the continuation "
             "side after a ≥threshold Binance move in minutes 1-3), but **entry is "
             "delayed `delay` minutes after the qualifying minute** — so at delay≥1 "
             "the fill price is a candle STRICTLY AFTER the signal completes (removing "
             "the intra-minute ordering risk). Holdout (last 20%), fees in, taker + "
             "maker legs, maker fill_rate shown. **delay=0 = enter at the qualifying "
             "minute** (≈ the continuation_exec baseline).")
    L.append("")
    L.append("> ★ **T5 basis caveat:** move = Binance, settlement = CF-Benchmarks RTI. "
             "The proxy mismatch sits under every number; if the edge IS a "
             "Binance→RTI lead-lag, its decay across delay also traces how long that "
             "lead persists.")
    L.append("")
    for res in results:
        a = res["asset"]
        L.append(f"## {a}")
        L.append("")
        L.append("HOLDOUT, primary threshold **0.10%** — decay across entry delay:")
        L.append("")
        L.append("| delay | n trades | taker win% | taker@traded (t) | taker@quote (t) "
                 "| maker per-ATTEMPT (t) | maker fill_rate |")
        L.append("|---|---|---|---|---|---|---|")
        for d in DELAYS:
            r = res["holdout"][PRIMARY][d]
            mk = r["maker"]
            L.append(f"| m+{d} | {r['n_trades']} | {_pct(r['taker_win_rate'])} | "
                     f"{_cell(r['taker_traded'])} | {_cell(r['taker_quote'])} | "
                     f"{_cell(mk['per_attempt'])} | {_pct(mk['fill_rate'])} |")
        L.append("")
        # taker@traded decay grid across the threshold band
        L.append("taker@traded (holdout) across threshold × delay:")
        L.append("")
        L.append("| Threshold | m+0 | m+1 | m+2 | m+3 |")
        L.append("|---|---|---|---|---|")
        for thr in THRESHOLDS:
            cells = " | ".join(_cell(res["holdout"][thr][d]["taker_traded"]) for d in DELAYS)
            star = " ★" if abs(thr - PRIMARY) < 1e-9 else ""
            L.append(f"| {thr*100:.2f}%{star} | {cells} |")
        L.append("")
    L.append("## Reading this (evidence, not verdict)")
    L.append("")
    L.append("- **Edge persists at delay≥1** ⇒ a real multi-minute continuation, "
             "post-signal, tolerant of some execution latency (not an intra-minute "
             "ordering artifact). **Edge collapses at delay=1** ⇒ it was intra-minute "
             "/ single-tick and not harvestable with realistic latency.")
    L.append("- The decay RATE (m+0→m+3) also bounds how much latency the edge can "
             "absorb, and — under the T5 lens — how long any Binance→RTI lead lasts.")
    L.append("- Maker per-ATTEMPT (no-fills@$0) with its fill_rate remains the honest "
             "maker number; taker@traded is the spread-crossing executable leg.")
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nReport written: {path}", flush=True)


def _cell(a):
    if not a or a.get("mean") is None:
        return "n/a"
    se = a.get("se")
    return f"{a['mean']:+.4f} (t={a['mean']/se:+.1f})" if se else f"{a['mean']:+.4f}"


def main() -> int:
    print("=" * 70)
    print("  CONTINUATION LATENCY / DECAY — kalshi_crypto_v2 (on-disk, no pulls)")
    print("=" * 70)
    args = sys.argv[1:]
    assets = ASSETS
    if "--assets" in args:
        assets = [a.strip().upper() for a in args[args.index("--assets") + 1].split(",")]
    conn = _ro()
    try:
        results = [run_asset(a, conn) for a in assets]
    finally:
        conn.close()
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_S4))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, "2026-08-02_kalshi_crypto_v2_continuation_latency.md")
    write_report(results, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
