"""Summarize the per-window scalper-expectancy JSONs into the report tables:
the EXPECTANCY-vs-TP1-distance curve, outcome distributions, the FULL book
(TRAIN+VALID pooled), and the lockbox (best-on-TRAIN confirmed-on-VALID).

READ-ONLY. Reads scripts/_scalper_expectancy_out_<WIN>.json written by
scalper_expectancy_backtest.py. Prints markdown-ready tables to stdout.
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
SC = _REPO / "scripts"


def load(win):
    p = SC / f"_scalper_expectancy_out_{win}.json"
    return json.loads(p.read_text(encoding="utf-8"))["cells"][win]


def restats(trades):
    """Recompute the expectancy book from a pooled list of {net_R_taker,result,filled_legs}."""
    net = [t["net_R_taker"] for t in trades]
    wins = [r for r in net if r > 0]
    losses = [r for r in net if r < 0]
    n = len(net)
    win_pct = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses)) / len(losses) if losses else 0.0
    E = sum(net) / n if n else 0.0
    pf = (sum(wins) / abs(sum(losses))) if losses else math.inf
    be = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else 0.0

    def bucket(t):
        legs = set(x for x in t["filled_legs"].split(",") if x)
        if "tp3" in legs:
            return "full"
        if legs:
            return "part"
        return "strt"
    from collections import Counter
    bk = Counter(bucket(t) for t in trades)
    return dict(n=n, win=win_pct, avgW=avg_win, avgL=avg_loss, payoff=(avg_win/avg_loss if avg_loss else math.inf),
                E=E, total=sum(net), pf=pf, be=be,
                full=bk.get("full", 0), part=bk.get("part", 0), strt=bk.get("strt", 0))


def row(label, s):
    pf = f"{s['pf']:.2f}" if math.isfinite(s['pf']) else "inf"
    payoff = f"{s['payoff']:.2f}" if math.isfinite(s['payoff']) else "inf"
    return (f"| {label} | {s['n']} | {s['win']*100:.1f}% | {s['avgW']:+.3f} | {s['avgL']:.3f} | "
            f"{payoff} | **{s['E']:+.4f}** | {s['total']:+.2f} | {pf} | {s['be']*100:.1f}% | "
            f"{s['full']} | {s['part']} | {s['strt']} |")


HDR = ("| TP1 knob | N | win% | avgWin(R) | avgLoss(R) | payoff | **E(R)** | totNet(R) | PF | "
       "BE-win% | full | part | strt |")
SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def sweep_table(cells, key, knobname):
    print(f"\n### {cells['window']}  —  {knobname} sweep")
    print(HDR); print(SEP)
    best = None
    for st in cells[key]:
        s = dict(n=st["n"], win=st["win_pct"], avgW=st["avg_win_R"], avgL=st["avg_loss_R"],
                 payoff=st["payoff_ratio"], E=st["expectancy_R"], total=st["total_net_R"],
                 pf=st["profit_factor"], be=st["breakeven_win_pct"],
                 full=st["full_run"], part=st["partial_tp_stop"], strt=st["straight_stop"])
        lbl = f"{st['knob']}={st['knob_val']}"
        print(row(lbl, s))
        if st["n"] >= 15 and (best is None or st["expectancy_R"] > best["expectancy_R"]):
            best = st
    if best:
        print(f"\n_best (N>=15): {best['knob']}={best['knob_val']}  E={best['expectancy_R']:+.4f}R  "
              f"PF={best['profit_factor']:.2f}  win={best['win_pct']*100:.1f}%_")
    return best


def main():
    wins = sys.argv[1:] or ["TRAIN", "VALID", "REGIME_BULL", "REGIME_HIVOL"]
    cellsmap = {}
    for w in wins:
        try:
            cellsmap[w] = load(w)
        except FileNotFoundError:
            print(f"[missing] {w}")
    for w, cells in cellsmap.items():
        sweep_table(cells, "r_target_sweep", "tp1_r_target")
        sweep_table(cells, "mult_sweep", "tp1_mult")

    # FULL book = pool TRAIN + VALID per matching knob value
    if "TRAIN" in cellsmap and "VALID" in cellsmap:
        print("\n\n## FULL BOOK (TRAIN+VALID pooled)")
        for key, knobname in (("r_target_sweep", "tp1_r_target"), ("mult_sweep", "tp1_mult")):
            print(f"\n### FULL — {knobname} sweep")
            print(HDR); print(SEP)
            tr = {st["knob_val"]: st for st in cellsmap["TRAIN"][key]}
            va = {st["knob_val"]: st for st in cellsmap["VALID"][key]}
            best = None
            for kv in sorted(tr):
                pooled = (tr[kv].get("_trades", []) + va.get(kv, {}).get("_trades", []))
                if not pooled:
                    continue
                s = restats(pooled)
                lbl = f"{tr[kv]['knob']}={kv}"
                print(row(lbl, s))
                if s["n"] >= 30 and (best is None or s["E"] > best[1]["E"]):
                    best = (kv, s)
            if best:
                kv, s = best
                print(f"\n_FULL best (N>=30): {knobname}={kv}  E={s['E']:+.4f}R  PF={s['pf']:.2f}  "
                      f"win={s['win']*100:.1f}%  BE-win-needed={s['be']*100:.1f}%_")

    # LOCKBOX: best-on-TRAIN, then its OUT-OF-SAMPLE expectancy on VALID
    if "TRAIN" in cellsmap and "VALID" in cellsmap:
        print("\n\n## LOCKBOX (pick best on TRAIN by E with N>=15; confirm on VALID)")
        for key, knobname in (("r_target_sweep", "tp1_r_target"), ("mult_sweep", "tp1_mult")):
            tr = cellsmap["TRAIN"][key]
            cand = [st for st in tr if st["n"] >= 15]
            if not cand:
                continue
            bt = max(cand, key=lambda x: x["expectancy_R"])
            kv = bt["knob_val"]
            vmatch = next((st for st in cellsmap["VALID"][key] if st["knob_val"] == kv), None)
            print(f"\n{knobname}: TRAIN-best = {kv}  ->  TRAIN E={bt['expectancy_R']:+.4f} "
                  f"(N={bt['n']}, PF={bt['profit_factor']:.2f})")
            if vmatch:
                print(f"   OUT-OF-SAMPLE on VALID @ {kv}: E={vmatch['expectancy_R']:+.4f} "
                      f"(N={vmatch['n']}, PF={vmatch['profit_factor']:.2f}, "
                      f"win={vmatch['win_pct']*100:.1f}%)")


if __name__ == "__main__":
    main()
