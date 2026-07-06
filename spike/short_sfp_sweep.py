"""SHORT SFP via price reflection — reuses the VALIDATED long-only detector.

Reflect each coin's bars around its midpoint M: new = 2M - old, swapping high/low.
A LONG SFP in the reflected series == a SHORT SFP in the real series (the fire
condition low<swing AND close>swing maps exactly to real high>swing AND close<swing),
and the R-outcome (win=+tp_r / loss=-1) is invariant under reflection. So running the
existing SfpModeBDetector + sim on reflected bars gives real-market SHORT-SFP results.
Prints LONG vs SHORT pooled side-by-side + per-coin SHORT. Read-only."""
from __future__ import annotations
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar
import backtest as bt
import rr_sweep as rr

COINS  = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PIVOTS = [5, 8, 10, 15, 20]
TP_RS  = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]


def reflect(bars):
    M2 = max(b.high for b in bars) + min(b.low for b in bars)   # 2M, M=midpoint
    # SfpBar(ts, open, high, low, close); high/low SWAP under reflection
    return [SfpBar(b.ts_ms, M2 - b.open, M2 - b.low, M2 - b.high, M2 - b.close) for b in bars]


def pooled(all3, all15):
    """{(pivot,tp): (n,wr,exp,tot)} pooled over all coins."""
    sigs = {(c, pl): bt.get_signals(all15[c], all3[c], pl) for c in COINS for pl in PIVOTS}
    out = {}
    for pl in PIVOTS:
        for tp in TP_RS:
            trades = []
            for c in COINS:
                trades.extend(rr.one_open(all3[c], sigs[(c, pl)], tp))
            out[(pl, tp)] = rr.stats(trades)
    return out, sigs


def per_coin_short(all3, all15, sigs):
    print("\n== PER-COIN SHORT ==")
    print(f"{'coin':8s} {'piv':>3s} {'tp_r':>4s} {'n':>4s} {'WR%':>6s} {'expR':>7s} {'totR':>7s} {'be%':>5s}")
    for c in COINS:
        for pl in PIVOTS:
            for tp in TP_RS:
                n, wr, exp, tot = rr.stats(rr.one_open(all3[c], sigs[(c, pl)], tp))
                wrs = f"{wr*100:5.1f}" if not math.isnan(wr) else "  nan"
                exs = f"{exp:+.3f}" if not math.isnan(exp) else "   nan"
                print(f"{c:8s} {pl:3d} {tp:4.2f} {n:4d} {wrs:>6s} {exs:>7s} {tot:+7.2f} {100/(tp+1):4.1f}")


def main():
    all3 = {c: bt.load_3m(c) for c in COINS}
    all15 = {c: bt.resample_15m(all3[c]) for c in COINS}

    r3 = {c: reflect(all3[c]) for c in COINS}
    r15 = {c: bt.resample_15m(r3[c]) for c in COINS}

    longp, _ = pooled(all3, all15)
    shortp, ssigs = pooled(r3, r15)

    print("== POOLED LONG vs SHORT (all 4 coins) ==")
    print(f"{'piv':>3s} {'tp_r':>4s} | {'L_n':>4s} {'L_WR%':>6s} {'L_expR':>7s} | {'S_n':>4s} {'S_WR%':>6s} {'S_expR':>7s} | {'be%':>5s}")
    for pl in PIVOTS:
        for tp in TP_RS:
            ln, lwr, lex, _ = longp[(pl, tp)]
            sn, swr, sex, _ = shortp[(pl, tp)]
            lwrs = f"{lwr*100:5.1f}" if not math.isnan(lwr) else "  nan"
            lexs = f"{lex:+.3f}" if not math.isnan(lex) else "   nan"
            swrs = f"{swr*100:5.1f}" if not math.isnan(swr) else "  nan"
            sexs = f"{sex:+.3f}" if not math.isnan(sex) else "   nan"
            print(f"{pl:3d} {tp:4.2f} | {ln:4d} {lwrs:>6s} {lexs:>7s} | {sn:4d} {swrs:>6s} {sexs:>7s} | {100/(tp+1):4.1f}")

    per_coin_short(r3, r15, ssigs)


if __name__ == "__main__":
    main()
