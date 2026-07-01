"""Piece-3 SIDE-GATE ROUTING TRACE (GROSS/routing-only; read-only).

Replays the EXACT side-gate decision (regime picks side; never counter-trend) over
offline long + short signals per coin, and tabulates (side x regime -> allow/skip).
Proves the gate routes correctly BEFORE any live fire:
  long allowed iff regime in {up,range}; short iff regime in {down,range};
  regime None -> skip(regime_warmup); else skip(counter_trend).
So: long-in-DOWN and short-in-UP MUST skip; aligned + RANGE-both MUST allow.

Regime is looked up at the LAST FULLY-CLOSED 15m bar before entry ((t-t%900k)-900k),
matching the deployed _compute_regime (k=1 causal).
"""
import os, sqlite3, sys

DEPLOY   = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
RESEARCH = r"C:\Users\AA Incorporado\cc-sfp-research-wt\spike_pivot_degree"
DATA     = r"C:\Users\AA Incorporado\cc\data"
sys.path.insert(0, RESEARCH)
sys.path.insert(0, DEPLOY)

from trading_corp.agents.divisions.bitunix_sfp_observer import reflect_neg
import backtest as bt
import regime_filter as rf
from bitunix_sfp import SfpBar

COINS = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
PIVOTS = [5, 8, 10]
_15M = 900_000


def load(coin, table):
    con = sqlite3.connect(os.path.join(DATA, f"{COINS[coin]}_scalping.db"))
    rows = con.execute(f"SELECT ts,open,high,low,close FROM {table} "
                       "WHERE close IS NOT NULL ORDER BY ts").fetchall()
    con.close()
    return [SfpBar(int(t) * 1000, float(o), float(h), float(l), float(c))
            for t, o, h, l, c in rows]


def gate(regime, side):
    if regime is None:
        return "SKIP", "regime_warmup"
    aligned = (regime in ("up", "range")) if side == "long" else (regime in ("down", "range"))
    return ("ALLOW", "") if aligned else ("SKIP", "counter_trend")


def main():
    print("SIDE-GATE ROUTING TRACE  (long: up/range ALLOW, down SKIP | short: down/range ALLOW, up SKIP)")
    print("=" * 90)
    all_ok = True
    for coin in COINS:
        n15, n3 = load(coin, "bars_15m"), load(coin, "bars_3m")
        gt = rf.regime_series(n15, "ema200_pos_slope")
        win15 = [b for b in n15 if n3[0].ts_ms <= b.ts_ms <= n3[-1].ts_ms]
        r15, r3 = reflect_neg(win15), reflect_neg(n3)
        sigs = []
        for pl in PIVOTS:
            sigs += [(s, "long") for s in bt.get_signals(win15, n3, pl)]
            sigs += [(s, "short") for s in bt.get_signals(r15, r3, pl)]
        mat = {}; samples = []
        for s, side in sigs:
            if s.entry_bar_index >= len(n3):
                continue
            ets = n3[s.entry_bar_index].ts_ms
            regime = gt.get((ets - ets % _15M) - _15M)     # last CLOSED 15m bar (k=1)
            dec, reason = gate(regime, side)
            mat[(side, regime, dec)] = mat.get((side, regime, dec), 0) + 1
            if len(samples) < 8 and (side, regime, dec) not in {(x[1], x[2], x[3]) for x in samples}:
                samples.append((ets, side, regime, dec, reason))
        # rule violations
        bad = (sum(v for (sd, rg, dc), v in mat.items() if sd == "long" and rg == "down" and dc == "ALLOW")
               + sum(v for (sd, rg, dc), v in mat.items() if sd == "short" and rg == "up" and dc == "ALLOW")
               + sum(v for (sd, rg, dc), v in mat.items()
                     if dc == "SKIP" and rg in ("up", "down", "range")
                     and ((sd == "long" and rg in ("up", "range")) or (sd == "short" and rg in ("down", "range")))))
        ok = (bad == 0); all_ok &= ok

        def c(sd, rg, dc):
            return mat.get((sd, rg, dc), 0)
        print(f"\n{coin}: {len(sigs)} signals")
        print(f"  LONG   up: {c('long','up','ALLOW')}A/{c('long','up','SKIP')}S   "
              f"range: {c('long','range','ALLOW')}A/{c('long','range','SKIP')}S   "
              f"down: {c('long','down','ALLOW')}A/{c('long','down','SKIP')}S  (down must be 0A)")
        print(f"  SHORT  up: {c('short','up','ALLOW')}A/{c('short','up','SKIP')}S   "
              f"range: {c('short','range','ALLOW')}A/{c('short','range','SKIP')}S   "
              f"down: {c('short','down','ALLOW')}A/{c('short','down','SKIP')}S  (up must be 0A)")
        print(f"  warmup(regime None) skips: long {c('long',None,'SKIP')}  short {c('short',None,'SKIP')}")
        for ets, side, rg, dec, rs in samples:
            print(f"    sample: side={side:5s} regime={str(rg):5s} -> {dec:5s} {rs}")
        print(f"  -> {'PASS' if ok else '*** FAIL (rule violation) ***'}")
    print("\n" + "=" * 90)
    print(f"SIDE-GATE ROUTING: {'ALL PASS — no long-in-DOWN, no short-in-UP; aligned+range allowed' if all_ok else '*** FAIL ***'}")


if __name__ == "__main__":
    main()
