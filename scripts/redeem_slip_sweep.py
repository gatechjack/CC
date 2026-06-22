"""Slippage-guard sweep for the redeem-cap /goal (task 2) — IN-ENGINE (correct).

The max-slip entry guard rejects a REDEEM whose fill has drifted
|fire_price - signal_bar_close| > threshold. NOTE: a post-hoc filter over a
fixed trade list is WRONG — dropping a redeem changes downstream cooldown state
(last_fire_ts_*), which changes which later signals fire. So the guard MUST run
in-engine. (A validation assertion caught this; hence this in-engine version.)

Runs, per window, the guard at thresholds {25,50,75,100} pt at a chosen cap
(--cap 2 or inf). slip=off baseline is already in caps_*.json. Pools
trade-weighted and prints net-R/trade per slip. Split by cap so the two cap
arms can run as parallel background jobs (cap=inf is the slow one).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from scripts.run_redeem_sim import (  # noqa: E402
    run_redeem_sim, load_inputs, _resolve_db, _to_dt, _parse_cap,
)

OUT = _ROOT / "scripts" / "_redeem_goal_out"
OUT.mkdir(exist_ok=True)

WINDOWS = [
    ("2026-04-01", "2026-04-15"),
    ("2026-04-15", "2026-04-29"),
    ("2026-05-01", "2026-05-15"),
    ("2026-05-15", "2026-05-29"),
    ("2026-05-20", "2026-06-03"),
    ("2026-06-03", "2026-06-17"),
]
SLIPS = [None, 25.0, 50.0, 75.0, 100.0]   # None = guard off baseline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", default="inf")
    args = ap.parse_args()
    cap = _parse_cap(args.cap)
    cap_lbl = "inf" if cap >= 10 ** 9 else str(cap)

    db = _resolve_db(None)
    pooled = {sp: {"n": 0, "net": 0.0, "redeem": 0, "slip_drop": 0} for sp in SLIPS}
    per_window = {}
    for (s, e) in WINDOWS:
        sd, ed = _to_dt(s), _to_dt(e)
        alerts, bars, config = load_inputs(db, sd, ed)
        pre = (alerts, bars, config, (sd, ed))
        rows = []
        for sp in SLIPS:
            r = run_redeem_sim(cap=cap, max_slip_pt=sp, _preloaded=pre)
            pooled[sp]["n"] += r["n"]
            pooled[sp]["net"] += r["total_net_R"]
            pooled[sp]["redeem"] += r["n_redeem"]
            pooled[sp]["slip_drop"] += r["n_slip_guard_drop"]
            rows.append({k: v for k, v in r.items() if k != "trades"})
            print(f"[{s}..{e}] cap={cap_lbl:>3} slip={str(sp):>5}  "
                  f"n={r['n']:>3} redeem={r['n_redeem']:>3} "
                  f"slip_drop={r['n_slip_guard_drop']:>3}  "
                  f"net_R/trade={r['net_R_per_trade']:+.4f}", flush=True)
        per_window[f"{s}_{e}"] = rows
        (OUT / f"slip_cap{cap_lbl}_{s}_{e}.json").write_text(
            json.dumps(rows, indent=2, default=str), encoding="utf-8")

    print(f"\n===== POOLED cap={cap_lbl}  net-R/trade by slip =====", flush=True)
    print(f"{'slip':>7}{'N':>6}{'redeem':>8}{'slip_drop':>10}{'net_R/trade':>14}{'total':>11}")
    for sp in SLIPS:
        p = pooled[sp]
        nrt = p["net"] / p["n"] if p["n"] else 0.0
        print(f"{str(sp):>7}{p['n']:>6}{p['redeem']:>8}{p['slip_drop']:>10}"
              f"{nrt:>+14.4f}{p['net']:>+11.3f}", flush=True)
    (OUT / f"slip_pooled_cap{cap_lbl}.json").write_text(
        json.dumps({str(k): v for k, v in pooled.items()}, indent=2, default=str),
        encoding="utf-8")


if __name__ == "__main__":
    main()
