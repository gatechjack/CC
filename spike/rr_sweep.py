"""R:R sweep on the SFP Mode-B signals — reuses the validated backtest harness.
For each (coin, pivot_len, tp_r): win=+tp_r, loss=-1.0, stop-first same-bar,
one-open-at-a-time (re-run per tp_r since hold time changes concurrency).
Also POOLS all coins per (pivot_len, tp_r) for a less-noisy R:R read.
Read-only; no prod, no commit."""
from __future__ import annotations
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import STOP_BUFFER_PCT
import backtest as bt

COINS  = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PIVOTS = [5, 8, 10, 15, 20]
TP_RS  = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
MAX_HOLD = bt.MAX_HOLD_BARS


def sim(bars3, entry_idx, swept_low, tp_r):
    if entry_idx >= len(bars3):
        return None
    entry = bars3[entry_idx].open
    stop = swept_low - STOP_BUFFER_PCT * entry
    r = entry - stop
    if r <= 0:
        return None
    tp = entry + tp_r * r
    for i in range(entry_idx + 1, min(entry_idx + MAX_HOLD + 1, len(bars3))):
        b = bars3[i]
        if b.low <= stop:                      # stop-first on same bar (conservative)
            return (-1.0, "loss", i - entry_idx)
        if b.high >= tp:
            return (tp_r, "win", i - entry_idx)
    last = bars3[min(entry_idx + MAX_HOLD, len(bars3) - 1)]
    return ((last.close - entry) / r, "timeout", MAX_HOLD)


def one_open(bars3, signals, tp_r):
    trades = []
    open_until = -1
    for s in signals:
        idx = s.entry_bar_index
        if idx <= open_until or idx >= len(bars3):
            continue
        res = sim(bars3, idx, s.swept_low, tp_r)
        if res is None:
            continue
        rp, out, hold = res
        trades.append({"r": rp, "outcome": out})
        open_until = idx + hold
    return trades


def stats(trades):
    n = len(trades)
    if n == 0:
        return (0, float("nan"), float("nan"), 0.0)
    wins = sum(1 for t in trades if t["outcome"] == "win")
    rs = [t["r"] for t in trades]
    return (n, wins / n, sum(rs) / n, sum(rs))


def main():
    all3 = {c: bt.load_3m(c) for c in COINS}
    all15 = {c: bt.resample_15m(all3[c]) for c in COINS}
    sigs = {(c, pl): bt.get_signals(all15[c], all3[c], pl) for c in COINS for pl in PIVOTS}

    print("== PER-COIN ==")
    print(f"{'coin':8s} {'piv':>3s} {'tp_r':>4s} {'n':>4s} {'WR%':>6s} {'expR':>7s} {'totR':>7s} {'be_WR%':>6s}")
    for coin in COINS:
        for pl in PIVOTS:
            for tp in TP_RS:
                tr = one_open(all3[coin], sigs[(coin, pl)], tp)
                n, wr, exp, tot = stats(tr)
                be = 1.0 / (tp + 1)
                wrs = f"{wr*100:5.1f}" if not math.isnan(wr) else "  nan"
                exs = f"{exp:+.3f}" if not math.isnan(exp) else "   nan"
                print(f"{coin:8s} {pl:3d} {tp:4.2f} {n:4d} {wrs:>6s} {exs:>7s} {tot:+7.2f} {be*100:5.1f}")

    print("\n== POOLED (all 4 coins) ==")
    print(f"{'piv':>3s} {'tp_r':>4s} {'n':>4s} {'WR%':>6s} {'expR':>7s} {'totR':>8s} {'be_WR%':>6s}")
    for pl in PIVOTS:
        for tp in TP_RS:
            pooled = []
            for coin in COINS:
                pooled.extend(one_open(all3[coin], sigs[(coin, pl)], tp))
            n, wr, exp, tot = stats(pooled)
            be = 1.0 / (tp + 1)
            wrs = f"{wr*100:5.1f}" if not math.isnan(wr) else "  nan"
            exs = f"{exp:+.3f}" if not math.isnan(exp) else "   nan"
            print(f"{pl:3d} {tp:4.2f} {n:4d} {wrs:>6s} {exs:>7s} {tot:+8.2f} {be*100:5.1f}")


if __name__ == "__main__":
    main()
