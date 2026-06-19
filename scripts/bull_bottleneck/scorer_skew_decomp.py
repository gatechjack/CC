"""Scorer 9:1 buy/sell skew decomposition (read-only) — regime vs structural?

Decomposes the cleared 1,469 buy : 13,669 sell skew into stages:
  raw webhook signal counts -> realized weighted-volume -> eval-winning side -> cleared.
If the skew traces to which signals the MARKET PRINTED (frequency of the high-weight bear
Cypher-A cluster) rather than to asymmetric weights, it's REGIME, not a structural bug.
Also quantifies the mc_a_yellow_x miscategorization (config side:buy, actually a bear signal).

Inputs (read-only extracts):
  --ledger        bitunix_signal_ledger.csv (ts,signal,source,tf)
  --score-decided score_decided.csv (ts,side,tier,net_score,outcome)
"""
from __future__ import annotations
import argparse, csv
from collections import Counter
from pathlib import Path

# weights from config/strategies.yaml bitunix_futures.scoring.factors (per bull-starvation diag)
WEIGHT = {
    # buy-side
    "mc_a_bluetriangle": 3, "mc_a_longema": 2, "mc_a_yellow_x": 2, "mc_b_gold_buy": 5,
    "mc_b_buy_circle_div": 4, "mc_b_buy_circle": 3, "mc_b_buy_dot": 2, "otter_buy": 3,
    "money_bag_bottom": 2, "water_buy_large": 2, "water_buy_small": 1, "spoon_bull": 2,
    "cvd_bull_flip": 2, "bias_bull": 2, "pink_box_bull": 1,
    # sell-side
    "mc_a_red_diamond": 4, "mc_a_blood_diamond": 5, "mc_a_redx": 2, "mc_b_sell_circle_div": 4,
    "mc_b_sell_circle": 3, "mc_b_sell_dot": 2, "otter_sell": 3, "money_bag_top": 2,
    "water_sell_large": 2, "water_sell_small": 1, "spoon_bear": 2, "cvd_bear_flip": 2,
    "bias_bear": 2, "pink_box_bear": 1,
}
# config side (as the factor-map declares it — note yellow_x is BUY here, the known bug)
CFG_SIDE = {s: ("buy" if s in (
    "mc_a_bluetriangle","mc_a_longema","mc_a_yellow_x","mc_b_gold_buy","mc_b_buy_circle_div",
    "mc_b_buy_circle","mc_b_buy_dot","otter_buy","money_bag_bottom","water_buy_large",
    "water_buy_small","spoon_bull","cvd_bull_flip","bias_bull","pink_box_bull") else "sell")
    for s in WEIGHT}
TRUE_BEAR = {"mc_a_yellow_x"}  # signals whose TRUE side is bear but cfg says buy


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", required=True)
    p.add_argument("--score-decided", required=True)
    args = p.parse_args(argv)

    sig = Counter()
    with open(args.ledger, newline="") as f:
        for r in csv.DictReader(f):
            sig[(r["signal"] or "").strip()] += 1

    def tally(side_map):
        cnt = {"buy": 0, "sell": 0}; wvol = {"buy": 0, "sell": 0}
        for s, n in sig.items():
            sd = side_map.get(s)
            if sd is None:
                continue
            cnt[sd] += n; wvol[sd] += n * WEIGHT.get(s, 0)
        return cnt, wvol

    cnt_cfg, wvol_cfg = tally(CFG_SIDE)
    fixed_side = {s: ("sell" if s in TRUE_BEAR else CFG_SIDE[s]) for s in CFG_SIDE}
    cnt_fix, wvol_fix = tally(fixed_side)

    ev = {"buy": 0, "sell": 0}; cl = {"buy": 0, "sell": 0}
    with open(args.score_decided, newline="") as f:
        for r in csv.DictReader(f):
            sd = (r.get("side") or "").strip().lower()
            if sd not in ("buy", "sell"):
                continue
            ev[sd] += 1
            if (r.get("tier") or "").strip().upper() in ("STANDARD", "PREMIUM"):
                cl[sd] += 1

    def ratio(b, s):
        return f"1 : {s/b:.2f}" if b else "n/a"

    print("=== STAGE-BY-STAGE buy:sell skew ===")
    print(f"raw webhook signal COUNT (cfg side):   buy={cnt_cfg['buy']}  sell={cnt_cfg['sell']}  ({ratio(cnt_cfg['buy'],cnt_cfg['sell'])})")
    print(f"realized WEIGHTED-VOLUME (cfg side):    buy={wvol_cfg['buy']}  sell={wvol_cfg['sell']}  ({ratio(wvol_cfg['buy'],wvol_cfg['sell'])})")
    print(f"score-eval WINNING side:                buy={ev['buy']}  sell={ev['sell']}  ({ratio(ev['buy'],ev['sell'])})")
    print(f"CLEARED (tier>=STANDARD):               buy={cl['buy']}  sell={cl['sell']}  ({ratio(cl['buy'],cl['sell'])})")
    print(f"clear RATE: buy={100.0*cl['buy']/max(1,ev['buy']):.1f}%  sell={100.0*cl['sell']/max(1,ev['sell']):.1f}%")
    print()
    print("=== yellow_x bug effect (true side = bear) ===")
    print(f"weighted-vol AS-CONFIG (yellow_x=buy):  buy={wvol_cfg['buy']}  sell={wvol_cfg['sell']}  ({ratio(wvol_cfg['buy'],wvol_cfg['sell'])})")
    print(f"weighted-vol FIXED (yellow_x=bear):     buy={wvol_fix['buy']}  sell={wvol_fix['sell']}  ({ratio(wvol_fix['buy'],wvol_fix['sell'])})")
    print(f"yellow_x fires={sig.get('mc_a_yellow_x',0)} (×w2) -> fixing shifts {2*sig.get('mc_a_yellow_x',0)} weighted-vol buy->sell (skew worsens for bull, marginally)")
    print()
    print("=== top bear weighted-vol contributors (the amplifiers) ===")
    contrib = sorted(((s, sig.get(s,0)*WEIGHT[s], sig.get(s,0), WEIGHT[s])
                      for s in WEIGHT if CFG_SIDE[s]=="sell"), key=lambda x:-x[1])[:5]
    for s, wv, n, w in contrib:
        print(f"  {s:<22} count={n:<5} ×w{w} = {wv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
