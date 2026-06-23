"""Fee COUPLED-correction verification (Decision A) — ANALYSIS DRIVER, not a sim.

Empirically verifies the algebraic identity behind the COUPLED fee correction:
correcting the venue-actual taker rate (0.0004 -> 0.00019, round_trip 0.0009 ->
0.00048) WHILE bumping tp1_min_profit_multiplier (2.0 -> 3.75) holds the
fees_too_high_for_risk gate's TP1 fee-floor constant for EVERY entry:

    tp1_fee_floor = mult * round_trip_cost_pct * entry
    baseline : 2.00 * 0.00090 * entry = 0.00180 * entry
    coupled  : 3.75 * 0.00048 * entry = 0.00180 * entry   <-- identical

=> the gate skips the SAME signals and places TP1 at the SAME distance, so the
admitted BOOK (which trades, gross-R / TP placement) is byte-identical to today's
baseline. Net-R legitimately IMPROVES (the realised fee genuinely fell), so the
book is unchanged but NOT worsened / not more conservative.

Runs three configs on the SAME 6 lockbox windows at a FIXED redeem cap=2:
  A. BASELINE  taker 0.0004,  tp1_mult 2.0   (today's behaviour)
  B. RATE-ONLY taker 0.00019, tp1_mult 2.0   (rejected standalone: re-admits ~183)
  C. COUPLED   taker 0.00019, tp1_mult 3.75  (the fix)

Read-only; clean btc_scalping.db corpus only.

Run:
  PYTHONPATH=<worktree> python scripts/research_scoring/fee_coupled_verify.py
  PYTHONPATH=<worktree> python scripts/research_scoring/fee_coupled_verify.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.run_redeem_sim import run_redeem_sim, _resolve_db, load_inputs, _to_dt

CAP = 2                      # fixed; the cap question is closed NULL — this is FEE
FEE_BASE = 0.0004
FEE_CORR = 0.00019
MULT_BASE = 2.0
MULT_COUPLED = 3.75

WINDOWS = [
    ("TRAIN", "2026-04-01", "2026-04-15"),
    ("TRAIN", "2026-04-15", "2026-04-29"),
    ("TRAIN", "2026-05-01", "2026-05-15"),
    ("VALIDATE", "2026-05-15", "2026-05-29"),
    ("VALIDATE", "2026-05-20", "2026-06-03"),
    ("VALIDATE", "2026-06-03", "2026-06-17"),
]

_TOL = 1e-9


def _key(t):
    return (t["signal_ts"], t["entry_ts"], t["side"])


def _fee_skips(res):
    """Set of signals plan-skipped specifically for fees_too_high_for_risk."""
    return {
        _key(t) for t in res["trades"]
        if t["result"] == "plan_skip" and t["skip_reason"] == "fees_too_high_for_risk"
    }


def _walked(res):
    """{key: trade} for R-resolved (walked) trades."""
    return {_key(t): t for t in res["trades"] if t["net_R"] is not None}


def analyse_window(db, label, start, end):
    alerts, bars, config = load_inputs(db, _to_dt(start), _to_dt(end))
    pre = (alerts, bars, config, (_to_dt(start), _to_dt(end)))

    A = run_redeem_sim(cap=CAP, fee_mode="taker", taker_pct=FEE_BASE,
                       tp1_mult=MULT_BASE, _preloaded=pre)               # BASELINE
    B = run_redeem_sim(cap=CAP, fee_mode="taker", taker_pct=FEE_CORR,
                       tp1_mult=MULT_BASE, _preloaded=pre)               # RATE-ONLY
    C = run_redeem_sim(cap=CAP, fee_mode="taker", taker_pct=FEE_CORR,
                       tp1_mult=MULT_COUPLED, _preloaded=pre)            # COUPLED

    A_skips, B_skips, C_skips = _fee_skips(A), _fee_skips(B), _fee_skips(C)
    A_w, B_w, C_w = _walked(A), _walked(B), _walked(C)

    # FLIPPED cohort = baseline fee-skip that becomes a walked trade in the variant
    flip_A_to_C = sorted(A_skips & set(C_w))   # should be EMPTY (identity)
    flip_A_to_B = sorted(A_skips & set(B_w))   # rate-only re-admits (rig sanity)

    # book-composition identity A vs C: same admitted set + same gross-R
    same_admitted_set = set(A_w) == set(C_w)
    gross_max_diff = 0.0
    net_max_diff = 0.0
    for k in set(A_w) & set(C_w):
        ag, cg = A_w[k]["gross_R"], C_w[k]["gross_R"]
        if ag is not None and cg is not None:
            gross_max_diff = max(gross_max_diff, abs(ag - cg))
        net_max_diff = max(net_max_diff, abs(A_w[k]["net_R"] - C_w[k]["net_R"]))
    skip_set_identical = (C_skips == A_skips)

    return {
        "lockbox": label,
        "window": [start, end],
        # funnel sizes
        "n_score_fire": A["n_score_fire"],
        "n_walked_A": A["n"], "n_walked_B": B["n"], "n_walked_C": C["n"],
        "n_fee_skip_A": len(A_skips), "n_fee_skip_B": len(B_skips),
        "n_fee_skip_C": len(C_skips),
        # FLIP counts
        "n_flip_A_to_C": len(flip_A_to_C),     # MUST be 0
        "n_flip_A_to_B": len(flip_A_to_B),     # rate-only re-admit (sanity)
        # identity checks A vs C
        "skip_set_identical_A_C": skip_set_identical,
        "admitted_set_identical_A_C": same_admitted_set,
        "gross_R_max_diff_A_C": gross_max_diff,        # ~0 -> TP placement unchanged
        "net_R_max_diff_A_C": net_max_diff,            # >0 expected (lower fee)
        # net-R book numbers
        "total_net_R_A": round(A["total_net_R"], 4),
        "total_net_R_B": round(B["total_net_R"], 4),
        "total_net_R_C": round(C["total_net_R"], 4),
        "net_R_per_trade_A": round(A["net_R_per_trade"], 5),
        "net_R_per_trade_C": round(C["net_R_per_trade"], 5),
        # rate-only re-admitted cohort net edge (the rejected case)
        "B_readmit_total_net_R": round(
            sum(B_w[k]["net_R"] for k in flip_A_to_B), 4) if flip_A_to_B else 0.0,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default=None, help="also write full results JSON here")
    args = ap.parse_args()

    db = _resolve_db(None)
    out = {
        "cap": CAP, "fee_base": FEE_BASE, "fee_corr": FEE_CORR,
        "mult_base": MULT_BASE, "mult_coupled": MULT_COUPLED,
        "corpus": str(db), "generated_utc": datetime.now(timezone.utc).isoformat(),
        "windows": [],
    }
    for label, s, e in WINDOWS:
        print(f"... {label} {s}..{e}", file=sys.stderr)
        out["windows"].append(analyse_window(db, label, s, e))

    # roll-ups
    rows = out["windows"]
    out["totals"] = {
        "n_flip_A_to_C": sum(r["n_flip_A_to_C"] for r in rows),     # MUST be 0
        "n_flip_A_to_B": sum(r["n_flip_A_to_B"] for r in rows),     # ~183 expected
        "all_skip_sets_identical_A_C": all(r["skip_set_identical_A_C"] for r in rows),
        "all_admitted_sets_identical_A_C": all(r["admitted_set_identical_A_C"] for r in rows),
        "max_gross_R_diff_A_C": max(r["gross_R_max_diff_A_C"] for r in rows),
        "total_net_R_A": round(sum(r["total_net_R_A"] for r in rows), 4),
        "total_net_R_B": round(sum(r["total_net_R_B"] for r in rows), 4),
        "total_net_R_C": round(sum(r["total_net_R_C"] for r in rows), 4),
        "B_readmit_total_net_R": round(sum(r["B_readmit_total_net_R"] for r in rows), 4),
    }
    # overall PASS/FAIL
    t = out["totals"]
    out["verdict"] = {
        "flip_A_to_C_is_zero": t["n_flip_A_to_C"] == 0,
        "skip_sets_identical": t["all_skip_sets_identical_A_C"],
        "admitted_sets_identical": t["all_admitted_sets_identical_A_C"],
        "gross_R_identical": t["max_gross_R_diff_A_C"] <= _TOL,
        "rig_works_B_readmits": t["n_flip_A_to_B"] > 0
            and t["B_readmit_total_net_R"] < 0,
        "PASS": (
            t["n_flip_A_to_C"] == 0
            and t["all_skip_sets_identical_A_C"]
            and t["all_admitted_sets_identical_A_C"]
            and t["max_gross_R_diff_A_C"] <= _TOL
            and t["n_flip_A_to_B"] > 0
        ),
    }
    print(json.dumps(out, indent=2, default=str))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=str),
                                   encoding="utf-8")
        print(f"\nwrote -> {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
