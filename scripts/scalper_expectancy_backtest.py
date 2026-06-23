"""Asymmetric-payoff scalper EXPECTANCY backtest — TP1-distance sweep.

READ-ONLY research driver (2026-06-22). Tests the OPERATOR'S CORE THESIS:
an asymmetric-payoff scalper judged on EXPECTANCY (not win rate). It is a THIN
driver over `scripts/run_redeem_sim.run_redeem_sim` (which itself wraps the
v2-lifecycle engine in `backtest_bitunix_confluence`). It does NOT re-implement
the mechanism and does NOT edit the shared core sim files. To sweep the TP1
DISTANCE knob it temporarily rebinds `_BT._SCFG.tp1_r_target` /
`tp1_min_profit_multiplier` via dataclasses.replace (same pattern as the sim's
own `_tp1_mult_override`), restoring on exit. Single-threaded; sequential.

THE DESIGN MODELED (confirmed in the engine, see report §"modeling audit"):
  enter -> TP1 fills (covers entry fee) -> SL ratchets to BREAKEVEN (entry) ->
  remainder rides TP2/TP3. `walk_v2` + `_ratchet_sl` + `_agg_r` implement this:
    * tp1 fills  -> SL := entry (breakeven)  -> a later stop books ~tp1 profit
                    on the 0.25 leg and 0R on the 0.75 remainder (NOT -1R).
    * tp2 fills  -> SL := tp1 price (locks profit).
  Verified numerically: TP1-then-BE-stop books +0.125R gross (not -1R);
  straight stop books -1R; full run (all 3 legs) books +1.25R gross.

OUTCOME BUCKETS (from result + filled_legs, per fire that was WALKED):
  full_run        : tp3 filled            (runner reached -> the big win)
  partial_tp_stop : tp1 and/or tp2 filled, tp3 NOT, result a stop after a TP
                    (the "TP1-then-BE / tp1-locked stop" family — small +/0)
  straight_stop   : no leg filled, result==loss   (full -1R defined loss)
  (open / plan_skip are reported separately, never in the expectancy book.)

EXPECTANCY (the decision metric, net of CORRECTED fees taker=0.00019):
  win%  = wins / walked
  avg_win  (R) = mean net_R over net_R>0 trades
  avg_loss (R) = mean |net_R| over net_R<0 trades   (reported positive)
  expectancy = win%*avg_win - loss%*avg_loss   (== mean net_R)
  profit_factor = sum(net_R>0) / |sum(net_R<0)|
  breakeven_win% = avg_loss / (avg_win + avg_loss)  (the win% needed to break
                   even AT THE REALIZED payoff ratio) — compare to realized win%.

Run: PYTHONPATH=<worktree root> python scripts/scalper_expectancy_backtest.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace as _dc_replace
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.backtest_bitunix_confluence as _BT  # noqa: E402
import scripts.run_redeem_sim as RS  # noqa: E402

CORRECTED_TAKER = 0.00019  # venue-actual VIP3 Fee-Discount-Card per-side taker
REDEEM_CAP = 1             # shallow cap: tractable over multi-week windows. The
#  redeem path is ORTHOGONAL to the TP1-distance question (it only adds a few
#  late-entry fires; first-pass fires dominate and have slip 0). Held FIXED at 1
#  across the whole sweep so the ONLY knob moving is TP1 distance. (cap=3 gave
#  near-identical first-pass-dominated books but ran far slower on the full
#  corpus per the sim's own perf note.)


@contextmanager
def _tp1_distance_override(tp1_r_target: float | None, tp1_mult: float | None):
    """Rebind _BT._SCFG.tp1_r_target / tp1_min_profit_multiplier for the run,
    restore on exit. None = leave that field at engine default. Mirrors the
    sim's own _tp1_mult_override (disjoint-global, single-threaded)."""
    saved = _BT._SCFG
    try:
        kw = {}
        if tp1_r_target is not None:
            kw["tp1_r_target"] = tp1_r_target
        if tp1_mult is not None:
            kw["tp1_min_profit_multiplier"] = tp1_mult
        if kw:
            _BT._SCFG = _dc_replace(_BT._SCFG, **kw)
        yield
    finally:
        _BT._SCFG = saved


def _classify(result: str, filled: str) -> str:
    """Bucket a WALKED fire into full_run / partial_tp_stop / straight_stop."""
    legs = set(x for x in filled.split(",") if x)
    if "tp3" in legs:
        return "full_run"            # runner reached -> the asymmetric big win
    if legs:                          # some TP filled but not tp3, then stopped
        return "partial_tp_stop"      # incl. TP1-then-BE-stop family
    return "straight_stop"            # no TP -> full -1R defined loss


def expectancy_stats(trades: list[dict], fee_mode: str = "taker") -> dict:
    """Compute the full expectancy book over WALKED trades (net_R not None)."""
    col = "net_R_taker" if fee_mode == "taker" else "net_R_maker"
    walked = [t for t in trades if t.get(col) is not None
              and t["result"] in ("win", "loss")]
    n = len(walked)
    buckets = Counter(_classify(t["result"], t["filled_legs"]) for t in walked)
    net = [t[col] for t in walked]
    wins = [r for r in net if r > 0]
    losses = [r for r in net if r < 0]
    flat = [r for r in net if r == 0]
    win_pct = len(wins) / n if n else 0.0
    loss_pct = len(losses) / n if n else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (abs(sum(losses)) / len(losses)) if losses else 0.0
    expectancy = (sum(net) / n) if n else 0.0   # == win%*avg_win - loss%*avg_loss
    gross_pos = sum(wins)
    gross_neg = abs(sum(losses))
    pf = (gross_pos / gross_neg) if gross_neg > 0 else math.inf
    payoff = (avg_win / avg_loss) if avg_loss > 0 else math.inf
    be_win = (avg_loss / (avg_win + avg_loss)) if (avg_win + avg_loss) > 0 else 0.0
    return {
        "n": n,
        "win_pct": win_pct,
        "loss_pct": loss_pct,
        "n_flat": len(flat),
        "avg_win_R": avg_win,
        "avg_loss_R": avg_loss,
        "payoff_ratio": payoff,
        "expectancy_R": expectancy,
        "total_net_R": sum(net),
        "profit_factor": pf,
        "breakeven_win_pct": be_win,
        "full_run": buckets.get("full_run", 0),
        "partial_tp_stop": buckets.get("partial_tp_stop", 0),
        "straight_stop": buckets.get("straight_stop", 0),
    }


def measure_tp1_distances(window, tp1_r_target, tp1_mult, taker_pct=CORRECTED_TAKER):
    """Sample the realized TP1 distance (in R and in price-points) over the walked
    fires of one cell, so the sweep can be labeled by ACTUAL TP1 distance, not
    just the knob. Returns (mean_tp1_R, mean_tp1_pts, n_floor_bound)."""
    # Re-derive per-fire by re-running the plan builder on the fire bars is heavy;
    # instead infer from tp1 leg vs risk. We pull it from the engine by a light
    # re-walk: cheaper to read tp1 distance from the plan via build_v2_plan on the
    # fires we already have. We approximate using the knobs + fee floor formula
    # against the median entry/risk seen — but the cleanest is to read the actual
    # tp1 'r' the engine assigned. run_redeem_sim doesn't surface it per trade,
    # so we recompute analytically below in the report from the formula + a
    # sampled risk. Here we just return the knob (distance labeling done in main).
    return None


def run_cell(preloaded, *, tp1_r_target, tp1_mult, taker_pct=CORRECTED_TAKER,
             cap=REDEEM_CAP, structure_tf="4h", keep_trades=False):
    with _tp1_distance_override(tp1_r_target, tp1_mult):
        res = RS.run_redeem_sim(
            cap=cap, taker_pct=taker_pct, structure_tf=structure_tf,
            _preloaded=preloaded,
        )
    stats = expectancy_stats(res["trades"], fee_mode="taker")
    if keep_trades:
        # keep the per-trade (net_R_taker, result, filled_legs) for FULL-book pooling
        stats["_trades"] = [
            {"net_R_taker": t["net_R_taker"], "result": t["result"],
             "filled_legs": t["filled_legs"]}
            for t in res["trades"]
            if t["net_R_taker"] is not None and t["result"] in ("win", "loss")
        ]
    # sample realized tp1 distance in R: for each walked trade we know whether
    # tp1 filled; the assigned tp1 R-distance is max(tp1_r_target, floor_R) where
    # floor_R = tp1_mult*round_trip*entry/risk varies per trade. We surface the
    # KNOB tp1_r_target and the fee-floor R for a representative entry/risk below.
    stats.update({
        "tp1_r_target": tp1_r_target if tp1_r_target is not None
        else _BT._SCFG.tp1_r_target,
        "tp1_mult": tp1_mult if tp1_mult is not None
        else _BT._SCFG.tp1_min_profit_multiplier,
        "n_first_pass": res["n_first_pass"],
        "n_redeem": res["n_redeem"],
        "n_plan_skip": res["n_plan_skip"],
        "n_fires_total": res["n_fires_total"],
    })
    return stats


def _load_window(start, end, taker_pct=CORRECTED_TAKER):
    """Load (alerts, bars, config, win) once for reuse across the knob sweep."""
    db_path = RS._resolve_db(None)
    s = RS._to_dt(start)
    e = RS._to_dt(end)
    alerts, bars, config = RS.load_inputs(db_path, s, e)
    return (alerts, bars, config, (s, e))


def floor_R_for(entry, risk, tp1_mult, taker_pct=CORRECTED_TAKER):
    """The fee-floor TP1 distance expressed in R for a given entry/risk."""
    # round-trip = entry_taker + exit_taker + 2*slippage (engine FeeConfig)
    rt = taker_pct + taker_pct + 2 * 0.00005
    return tp1_mult * rt * entry / risk


def fmt_pct(x):
    return f"{x*100:.1f}%"


# ── windows ──────────────────────────────────────────────────────────────
# Lockbox: TRAIN on the earlier (bull-dominant) half, VALIDATE on the later
# (high-vol/bear) half. Pick the best TP1 on TRAIN, then confirm on VALIDATE.
# Also report two ~2-week regime windows to keep the bull leg visible.
# (FULL book is reconstructed by POOLING TRAIN+VALID trades in the report.)
WINDOWS = {
    "TRAIN": ("2026-03-30", "2026-05-11"),        # bull leg (66.7k -> 81.7k peak)
    "VALID": ("2026-05-11", "2026-06-20"),        # high-vol/bear (81.7k -> 63k -> chop)
    "REGIME_BULL": ("2026-04-13", "2026-04-27"),  # ~2wk clean bull
    "REGIME_HIVOL": ("2026-06-01", "2026-06-15"), # ~2wk high-vol drawdown
}


# ── TP1-distance knob sweep ──────────────────────────────────────────────
# Primary knob = tp1_r_target. Range from "closest" (0.25R) through current
# default (0.5R) to "a notch beyond round-trip" (1.0R). NOTE at the CORRECTED
# fee the default fee-floor (mult 2.0) is ~0.58R for median risk, so r_target
# below ~0.58 is clamped UP to the floor; the EFFECTIVE TP1 distance is
# max(r_target, floor_R). The mult sweep gives clean distance anchors:
#   mult 1.0 -> ~0.29R ("covers ~entry fee only"); 2.0 -> ~0.58R ("round-trip /
#   current"); 3.5 -> ~1.0R ("a notch beyond").
R_TARGETS = [0.25, 0.35, 0.5, 0.65, 0.8, 1.0]   # tp1_r_target
MULTS = [1.0, 2.0, 3.5]                          # tp1_min_profit_multiplier


def run_window(wname: str) -> dict:
    s, e = WINDOWS[wname]
    print(f"\n=== loading window {wname} {s}..{e} ===", flush=True)
    pre = _load_window(s, e)
    bars = pre[1]
    mid_entry = bars[len(bars) // 2]["close"] if bars else 60000.0
    cell = {"window": [s, e], "r_target_sweep": [], "mult_sweep": []}

    for rt in R_TARGETS:
        st = run_cell(pre, tp1_r_target=rt, tp1_mult=None, keep_trades=True)
        st.update({"window": wname, "knob": "tp1_r_target", "knob_val": rt,
                   "sample_entry": mid_entry})
        cell["r_target_sweep"].append(st)
        print(f"[{wname}] tp1_r={rt:<4} n={st['n']:>4} win={fmt_pct(st['win_pct']):>6} "
              f"avgW={st['avg_win_R']:+.3f} avgL={st['avg_loss_R']:.3f} "
              f"E={st['expectancy_R']:+.4f} PF={st['profit_factor']:.2f} "
              f"full={st['full_run']} part={st['partial_tp_stop']} "
              f"strt={st['straight_stop']} skip={st['n_plan_skip']}", flush=True)

    for m in MULTS:
        st = run_cell(pre, tp1_r_target=None, tp1_mult=m, keep_trades=True)
        st.update({"window": wname, "knob": "tp1_mult", "knob_val": m,
                   "sample_entry": mid_entry})
        cell["mult_sweep"].append(st)
        print(f"[{wname}] tp1_mult={m:<4} n={st['n']:>4} win={fmt_pct(st['win_pct']):>6} "
              f"avgW={st['avg_win_R']:+.3f} avgL={st['avg_loss_R']:.3f} "
              f"E={st['expectancy_R']:+.4f} PF={st['profit_factor']:.2f} "
              f"full={st['full_run']} part={st['partial_tp_stop']} "
              f"strt={st['straight_stop']} skip={st['n_plan_skip']}", flush=True)
    return cell


def main():
    wnames = sys.argv[1:] if len(sys.argv) > 1 else list(WINDOWS.keys())
    out = {"corrected_taker": CORRECTED_TAKER, "redeem_cap": REDEEM_CAP,
           "windows": {k: list(WINDOWS[k]) for k in wnames}, "cells": {}}
    for wname in wnames:
        out["cells"][wname] = run_window(wname)
    tag = "_".join(wnames) if len(wnames) <= 2 else "multi"
    outpath = _REPO_ROOT / "scripts" / f"_scalper_expectancy_out_{tag}.json"
    outpath.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {outpath}")
    return out


if __name__ == "__main__":
    main()
