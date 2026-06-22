"""Fee-gate flip analysis (fee-vs-edge Step 2) — ANALYSIS DRIVER, not a sim.

Runs the redeem-aware sim at the CURRENT fee (taker 0.0004) vs the CORRECTED
venue-actual fee (taker 0.00019) on the SAME lockbox-split windows, isolates the
FLIPPED cohort (signals that are plan_skip `fees_too_high_for_risk` at 0.0004 but
become walked trades at 0.00019), and reports their net-R.

This is a thin orchestrator over scripts.run_redeem_sim.run_redeem_sim — it does
NOT re-implement the engine and adds no trading logic. Read-only; clean corpus.

Run:
  PYTHONPATH=<worktree> python scripts/research_scoring/fee_gate_flip_analysis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sqlite3
from datetime import datetime, timezone

from scripts.run_redeem_sim import run_redeem_sim, _resolve_db, load_inputs, _to_dt

CAP = 2                 # fixed; the cap question is closed NULL — this is about FEE
FEE_CURRENT = 0.0004
FEE_CORRECT = 0.00019

WINDOWS = [
    ("TRAIN", "2026-04-01", "2026-04-15"),
    ("TRAIN", "2026-04-15", "2026-04-29"),
    ("TRAIN", "2026-05-01", "2026-05-15"),
    ("VALIDATE", "2026-05-15", "2026-05-29"),
    ("VALIDATE", "2026-05-20", "2026-06-03"),
    ("VALIDATE", "2026-06-03", "2026-06-17"),
]


def _regime_note(db, start, end):
    """Crude directional regime label from window open->close drift on 3m close."""
    s = int(_to_dt(start).timestamp())
    e = int(_to_dt(end).timestamp())
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT close FROM bars_3m WHERE ts>=? AND ts<? ORDER BY ts",
            (s, e)).fetchall()
    finally:
        con.close()
    if not rows:
        return "no-data", 0.0
    c0, c1 = rows[0][0], rows[-1][0]
    pct = (c1 - c0) / c0 * 100.0
    if pct >= 3.0:
        lab = "bull"
    elif pct <= -3.0:
        lab = "bear"
    else:
        lab = "neutral"
    return lab, pct


def _trade_key(t):
    # identity of a fire: original signal bar + side (a plan_skip and the walked
    # trade it flips into share the same signal_ts + side + entry bar)
    return (t["signal_ts"], t["entry_ts"], t["side"])


def analyse_window(db, label, start, end):
    alerts, bars, config = load_inputs(db, _to_dt(start), _to_dt(end))
    pre = (alerts, bars, config, (_to_dt(start), _to_dt(end)))
    cur = run_redeem_sim(cap=CAP, fee_mode="taker", taker_pct=FEE_CURRENT, _preloaded=pre)
    cor = run_redeem_sim(cap=CAP, fee_mode="taker", taker_pct=FEE_CORRECT, _preloaded=pre)

    # fees_too_high_for_risk skips at the CURRENT fee
    cur_fee_skips = {
        _trade_key(t): t for t in cur["trades"]
        if t["result"] == "plan_skip" and t["skip_reason"] == "fees_too_high_for_risk"
    }
    # walked (R-resolved) trades at the CORRECTED fee, keyed identically
    cor_walked = {
        _trade_key(t): t for t in cor["trades"] if t["net_R"] is not None
    }

    # FLIPPED cohort: a current fee-skip that becomes a walked trade at corrected fee
    flipped = []
    for k, sk in cur_fee_skips.items():
        if k in cor_walked:
            flipped.append(cor_walked[k])

    n_fee_skip = len(cur_fee_skips)
    n_flip = len(flipped)
    flip_pct = (n_flip / n_fee_skip * 100.0) if n_fee_skip else 0.0

    flip_net = [t["net_R"] for t in flipped]
    flip_total = sum(flip_net) if flip_net else 0.0
    flip_per = (flip_total / n_flip) if n_flip else 0.0
    flip_wins = sum(1 for t in flipped if t["result"] == "win")
    flip_win_pct = (flip_wins / n_flip * 100.0) if n_flip else 0.0

    reg, pct = _regime_note(db, start, end)

    return {
        "lockbox": label,
        "window": [start, end],
        "regime": reg,
        "drift_pct": round(pct, 2),
        "n_score_fire": cor["n_score_fire"],
        # whole admitted set at corrected fee
        "n_walked_current": cur["n"],
        "n_walked_corrected": cor["n"],
        "whole_total_net_R_corrected": round(cor["total_net_R"], 4),
        "whole_net_R_per_trade_corrected": round(cor["net_R_per_trade"], 4),
        "whole_total_net_R_current": round(cur["total_net_R"], 4),
        "whole_net_R_per_trade_current": round(cur["net_R_per_trade"], 4),
        # fee-skip + flip
        "n_fee_skip_current": n_fee_skip,
        "n_plan_skip_current": cur["n_plan_skip"],
        "n_plan_skip_corrected": cor["n_plan_skip"],
        "n_flip": n_flip,
        "flip_pct_of_fee_skips": round(flip_pct, 1),
        # FLIPPED cohort net edge at corrected fee (the KEY numbers)
        "flip_total_net_R": round(flip_total, 4),
        "flip_net_R_per_trade": round(flip_per, 4),
        "flip_win_pct_diag": round(flip_win_pct, 1),
        "flipped_trades": [
            {
                "signal_ts": t["signal_ts"], "entry_ts": t["entry_ts"],
                "side": t["side"], "result": t["result"],
                "bars_waited": t["bars_waited"], "redeemed": t["redeemed"],
                "gross_R": round(t["gross_R"], 4) if t["gross_R"] is not None else None,
                "net_R": round(t["net_R"], 4),
            }
            for t in flipped
        ],
    }


def main():
    db = _resolve_db(None)
    out = {"cap": CAP, "fee_current": FEE_CURRENT, "fee_corrected": FEE_CORRECT,
           "corpus": str(db), "generated_utc": datetime.now(timezone.utc).isoformat(),
           "windows": []}
    for label, s, e in WINDOWS:
        print(f"... {label} {s}..{e}", file=sys.stderr)
        out["windows"].append(analyse_window(db, label, s, e))

    # roll-ups by lockbox split + overall
    def _rollup(rows):
        n_flip = sum(r["n_flip"] for r in rows)
        n_skip = sum(r["n_fee_skip_current"] for r in rows)
        tot = sum(r["flip_total_net_R"] for r in rows)
        wins = sum(t["result"] == "win" for r in rows for t in r["flipped_trades"])
        return {
            "n_fee_skip": n_skip, "n_flip": n_flip,
            "flip_pct": round(n_flip / n_skip * 100.0, 1) if n_skip else 0.0,
            "flip_total_net_R": round(tot, 4),
            "flip_net_R_per_trade": round(tot / n_flip, 4) if n_flip else 0.0,
            "flip_win_pct_diag": round(wins / n_flip * 100.0, 1) if n_flip else 0.0,
        }

    train = [r for r in out["windows"] if r["lockbox"] == "TRAIN"]
    val = [r for r in out["windows"] if r["lockbox"] == "VALIDATE"]
    out["rollup"] = {
        "TRAIN": _rollup(train),
        "VALIDATE": _rollup(val),
        "ALL": _rollup(out["windows"]),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
