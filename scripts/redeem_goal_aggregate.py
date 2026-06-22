"""Aggregate the per-window cap-sweep JSONs into train/validate halves.

Reads scripts/_redeem_goal_out/caps_*.json (NOT the slip/regime files) and
produces, per cap: total walked N, total_net_R (sum across windows), and
net_R/trade = total_net_R / total_N (a TRADE-WEIGHTED aggregate, the honest
pooled expectancy — NOT a mean of per-window means, which would over-weight
thin windows). Splits the 6 windows chronologically into a train half (first 3)
and a validate/lockbox half (last 3).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
OUT = _ROOT / "scripts" / "_redeem_goal_out"

# chronological order (must match redeem_goal_batch.WINDOWS)
WINDOWS = [
    "2026-04-01_2026-04-15",
    "2026-04-15_2026-04-29",
    "2026-05-01_2026-05-15",
    "2026-05-15_2026-05-29",
    "2026-05-20_2026-06-03",
    "2026-06-03_2026-06-17",
]
CAPS = ["0", "1", "2", "3", "inf"]


def _load(tag):
    p = OUT / f"caps_{tag}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _agg(tags):
    """Pool windows -> per cap {n, total_net_R, net_R_per_trade, wins}."""
    acc = {c: {"n": 0, "total_net_R": 0.0, "wins": 0.0, "windows": 0} for c in CAPS}
    for tag in tags:
        data = _load(tag)
        if data is None:
            print(f"  [missing] {tag}", file=sys.stderr)
            continue
        for r in data:
            c = r["cap_label"]
            acc[c]["n"] += r["n"]
            acc[c]["total_net_R"] += r["total_net_R"]
            acc[c]["wins"] += r["win_rate_pct"] / 100.0 * r["n"]
            acc[c]["windows"] += 1
    for c in CAPS:
        n = acc[c]["n"]
        acc[c]["net_R_per_trade"] = acc[c]["total_net_R"] / n if n else 0.0
        acc[c]["win_pct"] = acc[c]["wins"] / n * 100.0 if n else 0.0
    return acc


def _print(title, acc):
    print(f"\n=== {title} ===")
    print(f"{'cap':>5}{'N':>6}{'net_R/trade':>14}{'total_net_R':>14}{'win%':>8}")
    best = min(CAPS, key=lambda c: -1e9 if acc[c]["n"] == 0 else acc[c]["net_R_per_trade"])
    # best per-trade = max net_R_per_trade among caps with N>0
    best = max((c for c in CAPS if acc[c]["n"] > 0),
               key=lambda c: acc[c]["net_R_per_trade"], default=None)
    for c in CAPS:
        a = acc[c]
        star = "  <- best/trade" if c == best else ""
        print(f"{c:>5}{a['n']:>6}{a['net_R_per_trade']:>+14.4f}"
              f"{a['total_net_R']:>+14.3f}{a['win_pct']:>8.1f}{star}")
    return best


def main():
    train = WINDOWS[:3]
    val = WINDOWS[3:]
    a_all = _agg(WINDOWS)
    a_tr = _agg(train)
    a_va = _agg(val)
    b_all = _print(f"ALL 6 WINDOWS POOLED", a_all)
    b_tr = _print(f"TRAIN (first 3: {', '.join(train)})", a_tr)
    b_va = _print(f"VALIDATE/LOCKBOX (last 3: {', '.join(val)})", a_va)
    print(f"\nbest-per-trade cap:  all={b_all}  train={b_tr}  validate={b_va}")
    print("cap==2 holds as per-trade optimum?  "
          f"train={'YES' if b_tr=='2' else 'NO'}  "
          f"validate={'YES' if b_va=='2' else 'NO'}")
    allneg = all(a_all[c]["net_R_per_trade"] < 0 for c in CAPS if a_all[c]["n"])
    print(f"ALL caps net-NEGATIVE pooled?  {'YES (NULL holds)' if allneg else 'NO'}")


if __name__ == "__main__":
    main()
