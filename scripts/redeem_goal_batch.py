"""Batch driver for the redeem-cap /goal verdict (2026-06-22).

Drives scripts/run_redeem_sim.run_sweep over a list of non-overlapping ~2-week
windows, dumps each window's per-cap aggregate to JSON, and (optionally) the
slippage-guard sweep. NOT a re-implementation — a thin loop over the validated
sim so a single command produces the full window matrix reproducibly.

Usage:
    PYTHONPATH=<worktree> python scripts/redeem_goal_batch.py --task caps
    PYTHONPATH=<worktree> python scripts/redeem_goal_batch.py --task slip
    PYTHONPATH=<worktree> python scripts/redeem_goal_batch.py --task regime --start ... --end ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.run_redeem_sim import run_sweep  # noqa: E402

OUT = _ROOT / "scripts" / "_redeem_goal_out"
OUT.mkdir(exist_ok=True)

# Non-overlapping ~2-week windows spanning corpus 2026-03-30 .. 06-19.
WINDOWS = [
    ("2026-04-01", "2026-04-15"),   # (orig TRAIN)
    ("2026-04-15", "2026-04-29"),
    ("2026-05-01", "2026-05-15"),
    ("2026-05-15", "2026-05-29"),
    ("2026-05-20", "2026-06-03"),   # (orig VALIDATE) -- overlaps 05-15..05-29 tail
    ("2026-06-03", "2026-06-17"),
]
CAPS = [0, 1, 2, 3, "inf"]


def _slim(r: dict) -> dict:
    """Drop per-trade list to keep the JSON aggregate compact."""
    return {k: v for k, v in r.items() if k != "trades"}


def run_caps(windows, caps=CAPS, slip_pt=None, fee_mode="taker"):
    allres = {}
    for (s, e) in windows:
        tag = f"{s}_{e}" + (f"_slip{slip_pt}" if slip_pt is not None else "")
        kw = {}
        if slip_pt is not None:
            kw["max_slip_pt"] = slip_pt
        res = run_sweep(caps, start=s, end=e, fee_mode=fee_mode, **kw)
        allres[tag] = [_slim(r) for r in res]
        outp = OUT / f"caps_{tag}.json"
        outp.write_text(json.dumps(allres[tag], indent=2, default=str), encoding="utf-8")
        print(f"[done] {tag} -> {outp}")
        for r in res:
            print(f"   cap={r['cap_label']:>3}  n={r['n']:>3}  "
                  f"net_R/trade={r['net_R_per_trade']:+.4f}  "
                  f"total_net_R={r['total_net_R']:+.3f}  win%={r['win_rate_pct']:.1f}")
    return allres


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["caps", "slip", "regime"], default="caps")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--slip", default=None, help="comma list of slip thresholds in pt, or 'none'")
    ap.add_argument("--fee-mode", default="taker")
    args = ap.parse_args()

    if args.task == "caps":
        run_caps(WINDOWS, fee_mode=args.fee_mode)
    elif args.task == "regime":
        assert args.start and args.end
        run_caps([(args.start, args.end)], fee_mode=args.fee_mode)
    elif args.task == "slip":
        slips = [None] if not args.slip else [
            (None if t.strip().lower() == "none" else int(t)) for t in args.slip.split(",")
        ]
        wins = WINDOWS if not (args.start and args.end) else [(args.start, args.end)]
        for sp in slips:
            print(f"\n===== SLIP GUARD = {sp} pt =====")
            run_caps(wins, slip_pt=sp, fee_mode=args.fee_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
