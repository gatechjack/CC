"""Regime-conditional SFP: does trend-alignment separate winners from losers?

Compute candidate REGIME formulas on REAL 15m prices, tag every long SFP (real bars)
and short SFP (reflected bars) with the regime at its entry, and compare:
  trend-aligned  = long-in-UP  + short-in-DOWN
  counter-trend  = long-in-DOWN + short-in-UP
If aligned >> counter within this window, a regime filter works (independent of the
overall bear). tp_r=2.0 fixed to isolate the regime effect from the R:R effect.
Read-only; pools all 4 coins. n is thin in the rare up-regime — read directionally."""
from __future__ import annotations
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar
import backtest as bt
import rr_sweep as rr
from short_sfp_sweep import reflect

COINS  = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PIVOTS = [5, 8, 10]          # pool a few mid pivots for n (short edge was pivot-insensitive)
TP     = 2.0
_15M   = 900_000


def sma(vals, n):
    out = [None] * len(vals); s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n: s -= vals[i - n]
        if i >= n - 1: out[i] = s / n
    return out


def ema(vals, span):
    a = 2.0 / (span + 1); e = None; out = []
    for c in vals:
        e = c if e is None else a * c + (1 - a) * e
        out.append(e)
    return out


def regime_series(bars15, defn):
    """{15m_ts_ms: 'up'|'down'|'range'} on REAL prices."""
    closes = [b.close for b in bars15]
    lab = {}
    if defn == "mom5d":                        # sign of 5-day (480x15m) momentum
        N = 480
        for i, b in enumerate(bars15):
            if i >= N:
                lab[b.ts_ms] = "up" if closes[i] > closes[i - N] else "down"
    elif defn == "sma100_slope12h":            # SMA100 rising/falling over 48 bars (12h)
        m = sma(closes, 100); K = 48
        for i, b in enumerate(bars15):
            if m[i] is not None and i >= K and m[i - K] is not None:
                lab[b.ts_ms] = "up" if m[i] > m[i - K] else "down"
    elif defn == "ema200_pos_slope":           # close vs EMA200 + slope -> up/down/range
        em = ema(closes, 200); K = 32
        for i, b in enumerate(bars15):
            if i >= K:
                rising = em[i] > em[i - K]
                if closes[i] > em[i] and rising:       lab[b.ts_ms] = "up"
                elif closes[i] < em[i] and not rising: lab[b.ts_ms] = "down"
                else:                                  lab[b.ts_ms] = "range"
    return lab


def regime_at(lab, ts):
    return lab.get(ts - (ts % _15M))


def trades_tagged(bars3, sigs, lab):
    """One-open-at-a-time; return list of (regime, r)."""
    out = []; open_until = -1
    for s in sigs:
        idx = s.entry_bar_index
        if idx <= open_until or idx >= len(bars3): continue
        res = rr.sim(bars3, idx, s.swept_low, TP)
        if res is None: continue
        r, oc, hold = res
        out.append((regime_at(lab, bars3[idx].ts_ms), r))
        open_until = idx + hold
    return out


def agg(rs):
    rs = [r for r in rs]
    n = len(rs)
    if n == 0: return (0, float("nan"), float("nan"))
    return (n, sum(1 for r in rs if r > 0) / n, sum(rs) / n)  # (n, win-ish rate, expR)


def fmt(t):
    n, wr, ex = t
    return f"n={n:3d} WR~{wr*100:4.1f}% expR={ex:+.3f}" if n else f"n=  0  --"


def main():
    all3  = {c: bt.load_3m(c) for c in COINS}
    all15 = {c: bt.resample_15m(all3[c]) for c in COINS}
    r3    = {c: reflect(all3[c]) for c in COINS}
    r15   = {c: bt.resample_15m(r3[c]) for c in COINS}

    longsigs  = {(c, pl): bt.get_signals(all15[c], all3[c], pl) for c in COINS for pl in PIVOTS}
    shortsigs = {(c, pl): bt.get_signals(r15[c], r3[c], pl)     for c in COINS for pl in PIVOTS}

    for defn in ["mom5d", "sma100_slope12h", "ema200_pos_slope"]:
        # regime distribution
        dist = {}
        for c in COINS:
            for v in regime_series(all15[c], defn).values():
                dist[v] = dist.get(v, 0) + 1
        buckets = {}
        for side in ("long", "short"):
            for reg in ("up", "down", "range"):
                buckets[(side, reg)] = []
        for c in COINS:
            lab = regime_series(all15[c], defn)          # REAL regime
            for pl in PIVOTS:
                for reg, r in trades_tagged(all3[c], longsigs[(c, pl)], lab):
                    if reg: buckets[("long", reg)].append(r)
                for reg, r in trades_tagged(r3[c], shortsigs[(c, pl)], lab):
                    if reg: buckets[("short", reg)].append(r)

        print(f"\n{'='*74}\nREGIME DEF: {defn}   (15m-bar regime distribution: {dist})")
        print(f"{'':12s} {'UP':>30s} {'DOWN':>30s}")
        for side in ("long", "short"):
            up = agg(buckets[(side, "up")]); dn = agg(buckets[(side, "down")])
            print(f"  {side:10s} {fmt(up):>30s} {fmt(dn):>30s}")
        if "range" in dist:
            print(f"  {'(range)':10s} long {fmt(agg(buckets[('long','range')]))} | short {fmt(agg(buckets[('short','range')]))}")

        aligned = buckets[("long", "up")] + buckets[("short", "down")]
        counter = buckets[("long", "down")] + buckets[("short", "up")]
        uncond_long  = sum((buckets[("long", r)] for r in ("up", "down", "range")), [])
        uncond_short = sum((buckets[("short", r)] for r in ("up", "down", "range")), [])
        print(f"  --> TREND-ALIGNED (long-up + short-down): {fmt(agg(aligned))}")
        print(f"  --> COUNTER-TREND (long-down + short-up): {fmt(agg(counter))}")
        print(f"      unconditional long : {fmt(agg(uncond_long))}")
        print(f"      unconditional short: {fmt(agg(uncond_short))}")


if __name__ == "__main__":
    main()
